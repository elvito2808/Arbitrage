import asyncio
import hashlib
import hmac
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from arbitrage.config import load_config
from arbitrage.coordinator import MIN_INTERVAL, NETWORK
from arbitrage.kucoin import Level
from arbitrage.market_data import TRADE_UNITS
from arbitrage.models import (
    Direction, Execution, Holding, LedgerEntry, Outcome, Reason, Reservation,
    WorkerState,
)
from arbitrage.simulation import Attempt, Effect, Event, Scenario, SimulationEngine
from arbitrage.storage import SCHEMA_VERSION, _ANALYSIS_SCHEMA, _SCHEMA, Storage, Transaction
from test_coordinator import CONFIG, MarketFixture
from test_market_data import Exchange, Hedera


def signature(gas, side):
    evidence = gas.route_evidence
    message = "|".join(map(str, (
        "offline-p05", evidence.snapshot_id, side, evidence.execution_gas,
        gas.observed_at.isoformat(), gas.price_range_weibars,
    )))
    return hmac.new(b"offline-only deterministic test vector", message.encode(), hashlib.sha256).hexdigest()


class OfflineMarket(MarketFixture):
    async def snapshot(self):
        pool = await super().snapshot()
        gas = tuple(replace(
            estimate, route_evidence=estimate.route_evidence.model_copy(
                update={"id": signature(estimate, estimate.side)},
            ),
        ) for estimate in pool.gas)
        return replace(pool, gas=gas)


class OfflineEngine(SimulationEngine):
    _fixture_scope = "offline-p05"

    @staticmethod
    def _verify_fixture(gas, side, market, now):
        return hmac.compare_digest(gas.route_evidence.id, signature(gas, side))


@pytest.fixture
def offline(tmp_path, now, monkeypatch):
    def disallowed(*args, **kwargs):
        pytest.fail("simulation accessed an external network")

    monkeypatch.setattr(socket.socket, "connect", disallowed)
    monkeypatch.setattr(socket.socket, "connect_ex", disallowed)
    settings = load_config(CONFIG)
    store = Storage(tmp_path / "offline.db")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(settings.balances.as_balances())
    market = OfflineMarket(now, settings.simulation.cex_fee_fraction)
    market.estimate = 250_000

    def make(scenario=Scenario(), sleep=None):
        async def advance(seconds):
            market.now += timedelta(seconds=seconds)

        engine = OfflineEngine(Storage(store.path), settings.simulation, market, market.cex, clock=market.clock)
        engine._scenario = scenario
        engine._sleep = sleep or advance
        return engine

    return store, market, make


def admit(engine):
    worker = engine.worker("hedera")
    worker.start()
    return asyncio.run(worker.analyze())


def totals(store):
    with store.transaction() as tx:
        return tuple(tx.balance(holding).total_units for holding in Holding)


def journal(store, trade_id):
    with store.transaction() as tx:
        return tuple(Event.model_validate_json(item) for item in tx.simulation_events(trade_id))


def state(store):
    with store.transaction() as tx, sqlite3.connect(store.path) as db:
        return (tuple(tx.balance(holding) for holding in Holding), tx.ledger(),
                db.execute("SELECT count(*) FROM simulated_trades").fetchone()[0],
                db.execute("SELECT count(*) FROM reservations").fetchone()[0])


@pytest.mark.parametrize("direction", list(Direction))
def test_both_directions_exact_balance_and_no_cex_reread(offline, direction, monkeypatch):
    store, market, make = offline
    if direction == Direction.SELL_DEX:
        market.bid, market.ask = Fraction("0.0511"), Fraction("0.0512")
    engine = make()
    opening = totals(store)
    assert opening == (5_000_000_000_000, 3_000_000_000, 100_000_000_000, 5_000_000_000_000, 3_000_000_000)
    cycle = admit(engine)
    request_event, = journal(store, cycle.id)
    reservations = {entry.holding: entry.units for entry in request_event.remaining}
    expected = ({Holding.DEX_USDC: 53_025_571, Holding.CEX_HBAR: TRADE_UNITS}
                if direction == Direction.BUY_DEX else
                {Holding.DEX_WHBAR: TRADE_UNITS, Holding.CEX_USDT: 51_507_456})
    assert reservations == {**expected, Holding.DEX_HBAR: 150_000_000}
    assert totals(store) == opening

    async def no_read():
        pytest.fail("market was queried again after admission")

    monkeypatch.setattr(market, "snapshot", no_read)
    monkeypatch.setattr(market.cex, "snapshot", no_read)
    monkeypatch.setattr(market.cex, "price", no_read)
    assert asyncio.run(engine.recover()) == "filled"
    _, dex_start, dex_result, cex_start, cex_result = journal(store, cycle.id)
    assert dex_start.due_at - dex_start.at == timedelta(seconds=1)
    assert dex_result.at == dex_start.due_at <= cex_start.at <= cex_result.at
    assert (dex_result.fill.base_units, cex_result.fill.base_units) == (TRADE_UNITS, TRADE_UNITS)
    assert dex_result.gas_units == 25_000_000
    assert dex_result.fee_units == 0  # 0.3% embedded in the approved AMM quote
    assert cex_result.remaining == ()
    chosen = next(item for item in cycle.evaluations if item.direction == direction)
    assert chosen.requested_dex.observed_at < dex_result.fill.observed_at
    assert chosen.requested_dex.quote_units == dex_result.fill.quote_units
    assert chosen.requested_cex.quote_units == cex_result.fill.quote_units
    if direction == Direction.BUY_DEX:
        expected = (TRADE_UNITS, -52_761_762, -25_000_000, -TRADE_UNITS, 53_746_200)
        assert cex_result.fee_units == 53_800
    else:
        expected = (-TRADE_UNITS, 52_418_017, -25_000_000, TRADE_UNITS, -51_251_200)
        assert cex_result.fee_units == 51_200
    assert totals(store) == tuple(a + b for a, b in zip(opening, expected, strict=True))
    assert asyncio.run(make().recover()) is None
    assert state(store)[2:] == (1, 0)
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE simulated_trades SET request = '{}'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM simulation_events")


@pytest.mark.parametrize("effect", [Effect.REVERT, Effect.TIMEOUT, Effect.FAILURE])
def test_dex_failure_uses_at_most_three_proven_attempts_and_never_cex(offline, effect):
    store, _, make = offline
    opening = totals(store)
    engine = make(Scenario(dex=(Attempt(effect=effect, delay_ms=800),) * 3))
    cycle = admit(engine)
    assert asyncio.run(engine.recover()) == "failed"
    _, *events = journal(store, cycle.id)
    assert len(events) == 6
    for number in range(1, 4):
        start, resolved = events[2 * (number - 1):2 * number]
        assert start.attempt == resolved.attempt == number
        assert resolved.outcome == effect and resolved.venue == "dex"
        assert resolved.at - start.at == timedelta(milliseconds=800)
        if number > 1:
            assert events[2 * (number - 1) - 1].at <= start.at
    expected = list(opening)
    if effect == Effect.REVERT:
        expected[2] -= 75_000_000
    assert totals(store) == tuple(expected)
    assert state(store)[3] == 0


@pytest.mark.parametrize("direction", list(Direction))
def test_partial_cex_walks_initial_depth_with_adverse_slippage(offline, direction):
    store, market, make = offline
    if direction == Direction.SELL_DEX:
        market.ask, market.bid = Fraction("0.0512"), Fraction("0.0511")
    book = market.book

    async def multilevel():
        initial = await book()
        return replace(
            initial,
            bids=(Level(market.bid, Fraction(200)), Level(market.bid - Fraction("0.0001"), Fraction(800))),
            asks=(Level(market.ask, Fraction(200)), Level(market.ask + Fraction("0.0001"), Fraction(800))),
        )

    market.cex.snapshot = multilevel
    opening = totals(store)
    engine = make(Scenario(
        dex=(Attempt(adverse_bps=10),),
        cex=(Attempt(effect=Effect.PARTIAL, partial_units=TRADE_UNITS // 2, adverse_bps=20),),
    ))
    cycle = admit(engine)
    assert asyncio.run(engine.recover()) == "incomplete"
    _, _, dex, _, partial = journal(store, cycle.id)
    assert partial.fill.base_units == TRADE_UNITS // 2
    assert partial.outcome == Effect.PARTIAL
    if direction == Direction.BUY_DEX:
        assert dex.fill.quote_units == 52_814_524
        # Floor (200 × .0538 + 300 × .0537) × .998 × .999 in micro-USDT.
        assert partial.fill.quote_units == 26_789_443
        expected = (TRADE_UNITS, -52_814_524, -25_000_000, -TRADE_UNITS // 2, 26_789_443)
    else:
        assert dex.fill.quote_units == 52_365_598
        # Ceil (200 × .0512 + 300 × .0513) × 1.002 × 1.001 in micro-USDT.
        assert partial.fill.quote_units == 25_706_942
        expected = (-TRADE_UNITS, 52_365_598, -25_000_000, TRADE_UNITS // 2, -25_706_942)
    assert totals(store) == tuple(a + b for a, b in zip(opening, expected, strict=True))
    assert state(store)[3] == 0


def test_cex_can_fail_three_times_after_dex_fills_without_compensation(offline):
    store, _, make = offline
    opening = totals(store)
    scenario = Scenario(dex=(Attempt(effect=Effect.REVERT),), cex=(Attempt(effect=Effect.FAILURE),) * 3)
    engine = make(scenario)
    cycle = admit(engine)
    assert asyncio.run(engine.recover()) == "incomplete"
    outcomes = [e for e in journal(store, cycle.id) if e.outcome]
    assert [(e.venue, e.attempt, e.outcome) for e in outcomes] == [
        ("dex", 1, Effect.REVERT), ("dex", 2, Effect.FILL),
        ("cex", 1, Effect.FAILURE), ("cex", 2, Effect.FAILURE), ("cex", 3, Effect.FAILURE),
    ]
    assert totals(store) == (opening[0] + TRADE_UNITS, opening[1] - 52_761_762,
                             opening[2] - 50_000_000, *opening[3:])
    assert state(store)[3] == 0


@pytest.mark.parametrize("venue", ["dex", "cex"])
def test_unknown_does_not_retry_and_blocks_new_trade(offline, venue):
    store, market, make = offline
    engine = make(Scenario(**{venue: (Attempt(effect=Effect.UNKNOWN),)}))
    cycle = admit(engine)
    assert asyncio.run(engine.recover()) == "manual_review"
    before = state(store)
    last = journal(store, cycle.id)[-1]
    assert last.venue == venue and last.attempt == 1 and last.outcome == Effect.UNKNOWN
    assert {item.holding: item.units for item in last.remaining} == (
        {Holding.DEX_USDC: 53_025_571, Holding.DEX_HBAR: 50_000_000}
        if venue == "dex" else {Holding.CEX_HBAR: TRADE_UNITS}
    )
    assert len(before[1]) == (0 if venue == "dex" else 3)
    market.now += MIN_INTERVAL
    market.block += 1
    market.sequence += 1
    new = admit(make())
    assert asyncio.run(make().recover()) == "manual_review"
    assert state(store) == before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM simulation_misses").fetchall() == [
            (new.id, "unresolved_network_execution"),
        ]


def test_restart_during_delay_keeps_plan_and_owner_stop(offline):
    store, market, make = offline

    async def exercise():
        sleeping = asyncio.Event()

        async def pause(seconds):
            assert seconds == 5
            sleeping.set()
            await asyncio.Future()

        engine = make(Scenario(dex=(Attempt(delay_ms=5_000),)), sleep=pause)
        worker = engine.worker("hedera")
        worker.start()
        cycle = await worker.analyze()
        task = asyncio.create_task(engine.recover())
        await sleeping.wait()
        worker.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        _, pending = journal(store, cycle.id)
        market.now += timedelta(seconds=2)

        async def remaining(seconds):
            assert seconds == 3 or seconds == 0
            market.now += timedelta(seconds=seconds)

        restarted = make(Scenario(dex=(Attempt(effect=Effect.REVERT),)), sleep=remaining)
        assert await restarted.recover() == "filled"
        events = journal(store, cycle.id)
        assert events[1] == pending
        assert events[2].at == pending.due_at
        with store.transaction() as tx:
            assert tx.worker(worker.id).state == WorkerState.STOPPED
        blocked = await restarted.worker(worker.id).analyze()
        assert blocked.evaluations[0].reason == Reason.WORKER_INACTIVE
        assert await restarted.recover() is None

    asyncio.run(exercise())


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_restart_across_ledger_commit_has_exactly_once_effects(offline, boundary, monkeypatch):
    store, _, make = offline
    engine = make()
    cycle = admit(engine)
    opening = totals(store)
    assert engine._advance(cycle.id)[0] == "active"
    request = journal(store, cycle.id)[-1]
    assert request.due_at is not None
    with monkeypatch.context() as patch:
        if boundary == "before_commit":
            original = Transaction.post

            def interrupted(tx, entry):
                original(tx, entry)
                raise RuntimeError("interrupted during write")

            patch.setattr(Transaction, "post", interrupted)
            with pytest.raises(RuntimeError, match="interrupted"):
                asyncio.run(engine.recover())
            assert journal(store, cycle.id)[-1] == request
            assert totals(store) == opening
            assert state(store)[1] == ()
        else:
            # Stop between committed DEX result and first CEX start.
            engine.clock = lambda: request.due_at
            assert engine._advance(cycle.id) == ("active", None)
            assert len(state(store)[1]) == 3
    assert asyncio.run(make().recover()) == "filled"
    assert len(state(store)[1]) == 5
    assert totals(store)[0] == opening[0] + TRADE_UNITS
    assert asyncio.run(make().recover()) is None


def test_unexpected_ledger_entry_stops_recovery_without_replay(offline):
    store, market, make = offline
    engine = make()
    cycle = admit(engine)
    engine._advance(cycle.id)
    with store.transaction() as tx:
        tx.post(LedgerEntry(id=f"sim:{cycle.id}:dex:1:dex.HBAR", holding=Holding.DEX_HBAR,
                            delta_units=-5, recorded_at=market.now))
    before = state(store)
    assert asyncio.run(make().recover()) == "manual_review"
    assert journal(store, cycle.id)[-1].note == "journal mismatch"
    assert state(store) == before


def test_admission_rolls_back_all_reservations_on_error(offline, monkeypatch):
    store, market, make = offline
    engine = make()
    before = state(store)

    def fail(*args):
        raise RuntimeError("injected admission rollback")

    with monkeypatch.context() as patch:
        patch.setattr(Transaction, "add_simulation", fail)
        with pytest.raises(RuntimeError, match="rollback"):
            admit(engine)
    assert state(store) == before
    market.now += MIN_INTERVAL
    assert admit(make()).evaluations[0].status == "eligible_for_simulation"
    assert state(store)[2:] == (1, 3)


def test_concurrent_admission_and_recovery_do_not_duplicate_effects(offline):
    store, _, make = offline
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: admit(make()), range(2)))
    assert state(store)[2:] == (1, 3)
    assert sum(bool(journal(store, cycle.id)) for cycle in results) == 1
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: asyncio.run(make().recover()), range(2)))
    assert state(store)[2:] == (1, 0)
    assert len(state(store)[1]) == 5


def test_retry_gas_reservation_requires_all_funds(offline):
    store, _, make = offline
    with store.transaction() as tx:
        tx.reserve(Reservation(id="existing", holding=Holding.DEX_HBAR, units=99_925_000_000))
    before = state(store)
    cycle = admit(make())
    assert cycle.evaluations[0].status == "eligible_for_simulation"
    assert state(store) == before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM simulation_misses").fetchall() == [
            (cycle.id, Reason.INSUFFICIENT_BALANCE),
        ]


@pytest.mark.parametrize("forgery", [False, True])
def test_normal_public_engine_fails_closed_on_unknown_and_fixture_gas(offline, forgery):
    store, fixture, _ = offline
    settings = load_config(CONFIG).simulation

    class RecordedPublic(Hedera, Exchange):
        def __init__(self):
            Hedera.__init__(self)
            self.depth = {"sequence": "42", "time": int(self.now.timestamp()) * 1000,
                          "bids": [["0.0538", "1500"]], "asks": [["0.0539", "1500"]]}

    public = RecordedPublic()
    engine = SimulationEngine.from_public(store, settings, public)
    if forgery:
        # Correctly authenticated synthetic metadata still has no trust in runtime.
        fixture.now = public.now
        engine.dex.snapshot = fixture.snapshot
    before = state(store)
    cycle = admit(engine)
    expected = Reason.GAS_INVALID if forgery else Reason.GAS_UNKNOWN
    assert [item.reason for item in cycle.evaluations] == [expected, expected]
    assert asyncio.run(engine.recover()) is None
    assert state(store) == before


def test_fixture_rejects_unverified_metadata(offline, monkeypatch):
    store, market, make = offline
    snapshot = market.snapshot

    async def unsigned():
        result = await snapshot()
        altered = tuple(replace(gas, route_evidence=gas.route_evidence.model_copy(update={"id": "bogus"}))
                        for gas in result.gas)
        return replace(result, gas=altered)

    monkeypatch.setattr(market, "snapshot", unsigned)
    before = state(store)
    assert admit(make()).evaluations[0].reason == Reason.GAS_INVALID
    assert state(store) == before


def test_normal_engine_cannot_advance_offline_database(offline):
    store, market, make = offline
    admit(make())
    before = state(store)
    engine = SimulationEngine(store, load_config(CONFIG).simulation, market, market.cex, clock=market.clock)
    assert asyncio.run(engine.recover()) == "manual_review"
    assert state(store) == before


def test_v2_migration_preserves_all_tables_and_rejects_newer_schema(offline, tmp_path, now, opportunity):
    store, _, make = offline
    cycle = admit(make())
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        for statement in _SCHEMA + _ANALYSIS_SCHEMA:
            db.execute(statement)
        db.execute("PRAGMA user_version = 2")
        db.execute("INSERT INTO balances VALUES ('dex.USDC', 100, 20)")
        db.execute("INSERT INTO reservations VALUES ('legacy-hold', 'dex.USDC', 20)")
        db.execute("INSERT INTO ledger VALUES ('legacy-ledger', 'dex.USDC', -5, ?)", (now.isoformat(),))
        db.executemany("INSERT INTO workers VALUES (?, ?)", [
            (cycle.worker_id, "running"), (opportunity.worker_id, "paused"), ("legacy-stopped", "stopped"),
        ])
        db.execute("INSERT INTO opportunities VALUES (?, ?, ?)",
                   (opportunity.id, opportunity.worker_id, opportunity.model_dump_json()))
        execution = Execution(id="legacy", opportunity_id=opportunity.id, outcome=Outcome.PARTIAL, observed_at=now)
        db.execute("INSERT INTO executions VALUES (?, ?, ?)",
                   (execution.id, opportunity.id, execution.model_dump_json()))
        db.execute("INSERT INTO experiments VALUES ('legacy', '{\"legacy\":true}')")
        db.execute("INSERT INTO analysis_cycles VALUES (?, ?, ?)",
                   (cycle.id, cycle.worker_id, cycle.model_dump_json()))
        db.execute("INSERT INTO analyzed_snapshots VALUES (?, ?, ?)", (NETWORK, cycle.dex_snapshot_id, cycle.id))
        db.execute("INSERT INTO analysis_slots VALUES (?, ?, ?, ?, ?)",
                   (NETWORK, cycle.id, cycle.worker_id, now.isoformat(), now.isoformat()))
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")]
        before = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    legacy = Storage(path)
    legacy.initialize()
    legacy.initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in tables} == before
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM simulated_trades").fetchone() == (0,)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(ValueError, match=f"unsupported SQLite schema version {SCHEMA_VERSION + 1}"):
        legacy.initialize()


def test_v2_migration_rolls_back_on_schema_collision(tmp_path):
    path = tmp_path / "collision.db"
    with sqlite3.connect(path) as db:
        for statement in _SCHEMA + _ANALYSIS_SCHEMA:
            db.execute(statement)
        db.execute("CREATE TABLE simulation_events (legacy INTEGER)")
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Storage(path).initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'simulated_trades'").fetchone() is None


@pytest.mark.parametrize(("setting", "value", "reason"), [
    ("dex_age", timedelta(seconds=11), Reason.STALE_OR_INVALID_CLOCK),
    ("cex_age", timedelta(seconds=5), Reason.CROSS_VENUE_SKEW),
    ("quantity", Fraction(999), Reason.INSUFFICIENT_DEPTH),
    ("price_range", (10**12, 2 * 10**12 + 1), Reason.GAS_INVALID),
    ("bid", Fraction("0.05305"), Reason.MAXIMUM_LOSS),
])
def test_unsafe_fixture_markets_never_reserve(offline, setting, value, reason):
    store, market, make = offline
    setattr(market, setting, value)
    before = state(store)
    assert admit(make()).evaluations[0].reason == reason
    assert state(store) == before


def test_rejected_settlement_preserves_holds_and_requires_manual_review(offline, monkeypatch):
    store, _, make = offline
    engine = make()
    cycle = admit(engine)
    before = state(store)

    def refuse(*args):
        raise ValueError("injected ledger rejection")

    monkeypatch.setattr(Transaction, "post", refuse)
    assert asyncio.run(engine.recover()) == "manual_review"
    assert journal(store, cycle.id)[-1].note == "settlement rejected; existing holds retained"
    assert state(store) == before
