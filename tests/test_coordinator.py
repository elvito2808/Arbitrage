import asyncio
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from arbitrage.config import load_config
from arbitrage.coordinator import LEASE, MIN_INTERVAL, NETWORK, InvalidCycle, OpportunityCoordinator
from arbitrage.kucoin import Book, KuCoin, Level
from arbitrage.market_data import TRADE_UNITS, MarketDataError, Unavailable
from arbitrage.models import (
    Asset, Direction, EvaluationStatus, Holding, MarketQuote, Reason, Reservation, Side, Venue,
)
from arbitrage.saucerswap import GasEstimate, PoolSnapshot, RouteGasEvidence, amounts
from arbitrage.storage import _SCHEMA, SCHEMA_VERSION, Storage

BLOCK_HASH = "0x" + "a1" * 32
CONFIG = Path(__file__).resolve().parents[1] / "config" / "simulation.toml"


class MarketFixture:
    def __init__(self, now, fee):
        self.now = now
        self.calls = 0
        self.block = 123
        self.sequence = 456
        self.dex_age = self.cex_age = timedelta(seconds=1)
        self.dex_receipt_age = self.cex_receipt_age = timedelta(0)
        self.bid = Fraction("0.0538")
        self.ask = Fraction("0.0539")
        self.quantity = Fraction(1000)
        self.estimate = None
        self.price_range = (10**12, 2 * 10**12)
        self.bad_evidence = False
        self.route_evidence = True
        self.reverse = False
        self.bad_quote = False
        self.bad_size = False
        self.down = False
        self.cex = KuCoin(self, fee=fee)
        self.cex.snapshot = self.book

    def clock(self):
        return self.now

    async def snapshot(self):
        self.calls += 1
        if self.down:
            raise Unavailable("simulated RPC outage")
        buy, sell = amounts(199162090589, 378710131341665)
        snapshot_id = f"{NETWORK}:{self.block}:{BLOCK_HASH}"
        received = self.now - self.dex_receipt_age
        source = self.now - self.dex_age
        gas = []
        quotes = []
        for side, total in ((Side.BUY, buy), (Side.SELL, sell)):
            evidence = RouteGasEvidence(
                id=f"synthetic-route-measurement-{side}", side=side,
                snapshot_id="wrong" if self.bad_evidence else snapshot_id,
                execution_gas=self.estimate if type(self.estimate) is int and self.estimate > 0 else 1,
            ) if self.estimate is not None and self.route_evidence else None
            gas.append(GasEstimate(
                side, received, self.price_range, self.estimate, route_evidence=evidence,
            ))
            quotes.append(MarketQuote(
                venue=Venue.DEX, side=side, base_asset=Asset.WHBAR, quote_asset=Asset.USDC,
                base_units=TRADE_UNITS - self.bad_size, quote_units=total + self.bad_quote,
                snapshot_id=snapshot_id, observed_at=received, source_at=source,
            ))
        if self.reverse:
            quotes.reverse()
        return PoolSnapshot(
            self.block, BLOCK_HASH, source, received, 199162090589, 378710131341665,
            0, True, tuple(quotes), tuple(gas),
        )

    async def book(self):
        return Book(
            self.sequence, self.now - self.cex_age, self.now - self.cex_receipt_age,
            (Level(self.bid, self.quantity),), (Level(self.ask, self.quantity),),
        )


@pytest.fixture
def setup(tmp_path, now):
    config = load_config(CONFIG)
    store = Storage(tmp_path / "analysis.sqlite")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(config.balances.as_balances())
    market = MarketFixture(now, config.simulation.cex_fee_fraction)
    coordinator = OpportunityCoordinator(store, config.simulation, market, market.cex, clock=market.clock)
    return store, market, coordinator


def run(coordinator):
    worker = coordinator.worker("hb-main")
    worker.start()
    return asyncio.run(worker.analyze())


def test_unknown_p03_gas_zero_eligible_and_included_fees_once(setup):
    store, market, coordinator = setup
    result = run(coordinator)
    assert [entry.direction for entry in result.evaluations] == list(Direction)
    assert all(entry.status == EvaluationStatus.MARKET_CANDIDATE for entry in result.evaluations)
    assert all(entry.reason == Reason.GAS_UNKNOWN for entry in result.evaluations)
    assert all(entry.gas_cost_usdt is None and entry.net_profit_usdt is None
               for entry in result.evaluations)
    buy, sell = result.evaluations
    assert (buy.requested_dex.quote_units, buy.requested_cex.quote_units) == (52_761_762, 53_746_200)
    assert (sell.requested_dex.quote_units, sell.requested_cex.quote_units) == (52_418_017, 53_953_900)
    assert buy.indicative_profit_usdt == Decimal("0.984438")
    assert sell.indicative_profit_usdt == Decimal("-1.535883")
    assert buy.requested_dex.snapshot_id == sell.requested_dex.snapshot_id == result.dex_snapshot_id
    assert buy.requested_cex.snapshot_id == sell.requested_cex.snapshot_id == result.cex_snapshot_id
    assert market.calls == 1
    with store.transaction() as tx:
        assert tx.analysis(result.id) == result
        assert tx.ledger() == ()
        assert tx.balance(Holding.CEX_HBAR).reserved_units == 0


@pytest.mark.parametrize("direction", list(Direction))
def test_known_gas_and_bidirectional_net_math(setup, direction):
    _, market, coordinator = setup
    market.estimate = 250_000
    if direction == Direction.SELL_DEX:
        market.bid, market.ask = Fraction("0.0511"), Fraction("0.0512")
    with localcontext() as context:
        context.prec = 2
        first, second = run(coordinator).evaluations
    chosen = first if direction == Direction.BUY_DEX else second
    assert chosen.status == EvaluationStatus.ELIGIBLE
    assert chosen.gas_budget_tinybars == 50_000_000
    if direction == Direction.BUY_DEX:
        assert chosen.gas_cost_usdt == Decimal("0.02695")
        assert chosen.net_profit_usdt == Decimal("0.957488")
    else:
        assert chosen.gas_cost_usdt == Decimal("0.0256")
        assert chosen.net_profit_usdt == Decimal("1.141217")


@pytest.mark.parametrize(("offset", "status"), [
    (-1, EvaluationStatus.REJECTED),
    (0, EvaluationStatus.REJECTED),
    (1, EvaluationStatus.ELIGIBLE),
])
def test_profit_must_strictly_exceed_ten_cents(setup, offset, status):
    _, market, coordinator = setup
    market.estimate = 250_000
    market.ask = Fraction("0.053")
    # Choose a one-level gross price whose fee-adjusted SELL rounds exactly to the target.
    target = 52_761_762 + 26_500 + 100_000 + offset
    market.bid = Fraction(target, 1000 * 10**6) / Fraction("0.999")
    settings = load_config(CONFIG).simulation.model_copy(update={"slippage_fraction": Decimal(0)})
    coordinator = OpportunityCoordinator(
        coordinator.storage, settings, market, market.cex, clock=market.clock,
    )
    buy, _ = run(coordinator).evaluations
    assert buy.net_profit_usdt == Decimal(f"{100_000 + offset}e-6")
    assert buy.status == status
    if offset <= 0:
        assert buy.reason == Reason.PROFIT_THRESHOLD


def test_slippage_stress_and_loss_boundary(setup):
    _, market, coordinator = setup
    market.estimate = 250_000
    market.ask = Fraction("0.053")
    market.bid = Fraction(52_761_762 + 26_500 + 110_000, 10**9) / Fraction("0.999")
    buy, _ = run(coordinator).evaluations
    assert buy.net_profit_usdt == Decimal("0.11")
    assert buy.stressed_profit_usdt == Decimal("-0.418301")
    assert buy.reason == Reason.MAXIMUM_LOSS


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("dex_age", timedelta(seconds=11), Reason.STALE_OR_INVALID_CLOCK),
    ("dex_receipt_age", timedelta(seconds=-1), Reason.STALE_OR_INVALID_CLOCK),
    ("dex_age", timedelta(seconds=-3), Reason.STALE_OR_INVALID_CLOCK),
    ("reverse", True, Reason.PROVENANCE_MISMATCH),
    ("bad_quote", True, Reason.PROVENANCE_MISMATCH),
    ("bad_size", True, Reason.PROVENANCE_MISMATCH),
    ("quantity", Fraction(999), Reason.INSUFFICIENT_DEPTH),
    ("bid", Fraction("0.06"), Reason.INVALID_MARKET),
    ("down", True, Reason.MARKET_UNAVAILABLE),
])
def test_incomplete_and_invalid_cycles_are_recorded(setup, field, value, reason):
    store, market, coordinator = setup
    setattr(market, field, value)
    cycle = run(coordinator)
    assert all(item.reason == reason and item.status == EvaluationStatus.INSUFFICIENT_DATA
               for item in cycle.evaluations)
    with store.transaction() as tx:
        assert tx.analysis(cycle.id) == cycle
        if field != "down":
            assert cycle.dex_snapshot.source_at == market.now - market.dex_age
            assert cycle.cex_snapshot.source_at == market.now - market.cex_age


@pytest.mark.parametrize(("field", "value"), [
    ("estimate", 34_133), ("estimate", 244_829), ("estimate", 0),
    ("estimate", 25.0), ("bad_evidence", True), ("route_evidence", False),
    ("price_range", (10**12, 2 * 10**12 + 1)),
])
def test_invalid_gas_fails_closed(setup, field, value):
    _, market, coordinator = setup
    market.estimate = 250_000
    setattr(market, field, value)
    assert all(item.reason == Reason.GAS_INVALID for item in run(coordinator).evaluations)


def test_stricter_gas_cap_is_used(setup):
    store, market, _ = setup
    market.estimate = 250_000
    settings = load_config(CONFIG).simulation.model_copy(update={"gas_price_cap_multiple": Decimal("1.5")})
    coordinator = OpportunityCoordinator(store, settings, market, market.cex, clock=market.clock)
    buy, _ = run(coordinator).evaluations
    assert buy.gas_budget_tinybars == 37_500_000
    assert buy.gas_cost_usdt == Decimal("0.020213")


def test_existing_reservations_reduce_available_balances_without_mutation(setup):
    store, market, coordinator = setup
    market.estimate = 250_000
    with store.transaction() as tx:
        funds = tx.balance(Holding.CEX_HBAR)
        tx.reserve(Reservation(id="prior", holding=funds.holding, units=funds.available_units))
    buy, _ = run(coordinator).evaluations
    assert buy.reason == Reason.INSUFFICIENT_BALANCE
    with store.transaction() as tx:
        assert tx.balance(Holding.CEX_HBAR).reserved_units == funds.available_units
        assert tx.ledger() == ()


def test_duplicate_individual_snapshot_persists_across_restart(setup):
    store, market, coordinator = setup
    first = run(coordinator)
    immediate = run(coordinator)
    assert immediate.evaluations[0].reason == Reason.NETWORK_QUOTA
    assert market.calls == 1
    market.now += MIN_INTERVAL
    market.sequence += 1
    settings = load_config(CONFIG).simulation
    restarted = OpportunityCoordinator(Storage(store.path), settings, market, market.cex, clock=market.clock)
    second = run(restarted)
    assert all(entry.reason == Reason.DUPLICATE_SNAPSHOT for entry in second.evaluations)
    market.now += MIN_INTERVAL
    market.block += 1
    third = run(restarted)
    assert third.evaluations[0].reason == Reason.GAS_UNKNOWN
    with store.transaction() as tx:
        assert tx.analysis(second.id) == second
        assert tx.seen_snapshot(NETWORK, first.dex_snapshot_id)
        assert tx.seen_snapshot(NETWORK, third.cex_snapshot_id)


def test_workers_and_concurrent_cycles_across_coordinators(setup, monkeypatch):
    store, market, coordinator = setup

    async def exercise():
        ready = asyncio.Event()
        release = asyncio.Event()
        original = market.snapshot

        async def held_read():
            ready.set()
            await release.wait()
            return await original()

        monkeypatch.setattr(market, "snapshot", held_read)
        a, b = coordinator.worker("a"), coordinator.worker("b")
        coordinator.worker("c")
        with pytest.raises(ValueError, match="capacity"):
            coordinator.worker("d")
        a.start()
        with pytest.raises(ValueError, match="another worker"):
            b.start()
        task = asyncio.create_task(a.analyze())
        await ready.wait()
        # A second coordinator instance on the same database cannot claim the slot.
        other = OpportunityCoordinator(
            Storage(store.path), load_config(CONFIG).simulation, market, market.cex, clock=market.clock,
        )
        competing = await other.worker("a").analyze()
        assert competing.evaluations[0].reason == Reason.CYCLE_BUSY
        a.stop()
        with pytest.raises(ValueError, match="prior worker cycle"):
            a.start()
        b.start()
        assert (await b.analyze()).evaluations[0].reason == Reason.CYCLE_BUSY
        release.set()
        assert (await task).evaluations[0].reason == Reason.WORKER_INACTIVE
        assert market.calls == 1

    asyncio.run(exercise())


def test_lease_fences_cycle_after_deadline(setup, monkeypatch):
    _, market, coordinator = setup
    original = market.snapshot

    async def late():
        market.now += LEASE
        return await original()

    monkeypatch.setattr(market, "snapshot", late)
    assert run(coordinator).evaluations[0].reason == Reason.CYCLE_EXPIRED


def test_schema_one_upgrade_retains_worker_and_balance(tmp_path):
    path = tmp_path / "v1.sqlite"
    with sqlite3.connect(path) as db:
        for statement in _SCHEMA:
            db.execute(statement)
        db.execute("PRAGMA user_version = 1")
        db.execute("INSERT INTO workers VALUES ('old', 'paused')")
        db.execute("INSERT INTO balances VALUES ('dex.USDC', 100, 20)")
    storage = Storage(path)
    storage.initialize()
    with storage.transaction() as tx:
        assert tx.worker("old").state == "paused"
        assert tx.balance(Holding.DEX_USDC).available_units == 80
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_snapshot_conflict_rolls_back_both_sources_and_cycle(setup):
    store, _, coordinator = setup
    first = run(coordinator)
    duplicate = first.model_copy(update={"id": "conflicting-cycle"})
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as tx:
            tx.add_analysis(NETWORK, duplicate, ("would-be-new", first.cex_snapshot_id))
    with store.transaction() as tx:
        assert tx.analysis(duplicate.id) is None
        assert not tx.seen_snapshot(NETWORK, "would-be-new")
        assert tx.analysis(first.id) == first


def test_cancellation_releases_lease_but_preserves_network_interval(setup, monkeypatch):
    _, market, coordinator = setup

    async def exercise():
        ready = asyncio.Event()
        original = market.snapshot

        async def held():
            ready.set()
            await asyncio.Future()

        monkeypatch.setattr(market, "snapshot", held)
        worker = coordinator.worker("a")
        worker.start()
        task = asyncio.create_task(worker.analyze())
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await worker.analyze()).evaluations[0].reason == Reason.NETWORK_QUOTA
        market.now += MIN_INTERVAL
        monkeypatch.setattr(market, "snapshot", original)
        assert (await worker.analyze()).evaluations[0].reason == Reason.GAS_UNKNOWN

    asyncio.run(exercise())


def test_market_deadline_records_miss(setup, monkeypatch):
    _, market, coordinator = setup

    async def held():
        await asyncio.Future()

    monkeypatch.setattr("arbitrage.coordinator.READ_DEADLINE", 0.01)
    monkeypatch.setattr(market, "snapshot", held)
    assert run(coordinator).evaluations[0].reason == Reason.MARKET_UNAVAILABLE


def test_market_reads_are_sequential_dex_before_kucoin(setup, monkeypatch):
    _, market, coordinator = setup
    order = []
    dex_snapshot = market.snapshot
    cex_snapshot = market.cex.snapshot

    async def read_dex():
        order.append("dex:start")
        await asyncio.sleep(0)
        result = await dex_snapshot()
        order.append("dex:done")
        return result

    async def read_kucoin():
        order.append("kucoin:start")
        result = await cex_snapshot()
        order.append("kucoin:done")
        return result

    monkeypatch.setattr(coordinator.dex, "snapshot", read_dex)
    monkeypatch.setattr(coordinator.cex, "snapshot", read_kucoin)
    asyncio.run(coordinator._read())
    assert order == ["dex:start", "dex:done", "kucoin:start", "kucoin:done"]

@pytest.mark.parametrize("field", ["source_at", "received_at"])
@pytest.mark.parametrize("seconds", [0, 2, 5, 9, 10, 10.000001])
def test_cross_venue_timestamp_boundaries(setup, field, seconds):
    _, market, coordinator = setup
    pool, book, _ = asyncio.run(coordinator._read())
    # A fresh receipt cannot differ by more than ten seconds. For source
    # timestamps an otherwise valid source-ahead value can cross the boundary.
    later = market.now + timedelta(microseconds=1) if field == "source_at" and seconds > 10 else market.now
    earlier = later - timedelta(seconds=seconds)
    pool = replace(pool, source_at=market.now - timedelta(seconds=min(seconds, 10)),
                   received_at=market.now - timedelta(seconds=min(seconds, 10)))
    book = replace(book, source_at=market.now, received_at=market.now)
    pool = replace(pool, **{field: earlier})
    book = replace(book, **{field: later})
    if seconds <= 10:
        coordinator._timing(pool, book, market.now)
    else:
        with pytest.raises(InvalidCycle) as caught:
            coordinator._timing(pool, book, market.now)
        expected = Reason.CROSS_VENUE_SKEW if field == "source_at" else Reason.STALE_OR_INVALID_CLOCK
        assert caught.value.reason == expected


@pytest.mark.parametrize("error", [TimeoutError(), Unavailable("HTTP failure"),
                                    MarketDataError("invalid DEX response")])
def test_failed_dex_read_never_starts_kucoin(setup, monkeypatch, error):
    _, _, coordinator = setup

    async def fail():
        raise error

    async def unexpected():
        pytest.fail("KuCoin started after a failed DEX read")

    monkeypatch.setattr(coordinator.dex, "snapshot", fail)
    monkeypatch.setattr(coordinator.cex, "snapshot", unexpected)
    expected = Reason.INVALID_MARKET if type(error) is MarketDataError else Reason.MARKET_UNAVAILABLE
    assert all(item.reason == expected for item in run(coordinator).evaluations)


def test_sequential_market_reads_share_one_deadline(setup, monkeypatch):
    _, market, coordinator = setup
    pool = asyncio.run(market.snapshot())
    deadlines = []
    media = asyncio.timeout

    def observe_timeout(delay):
        context = media(delay)
        deadlines.append(context)
        return context

    async def dex():
        await asyncio.sleep(0)
        return pool

    cleaned_up = False

    async def kucoin():
        nonlocal cleaned_up
        assert len(deadlines) == 1
        deadlines[0].reschedule(asyncio.get_running_loop().time())
        try:
            await asyncio.Future()
        finally:
            cleaned_up = True

    monkeypatch.setattr("arbitrage.coordinator.asyncio.timeout", observe_timeout)
    monkeypatch.setattr(coordinator.dex, "snapshot", dex)
    monkeypatch.setattr(coordinator.cex, "snapshot", kucoin)
    with pytest.raises(TimeoutError):
        asyncio.run(coordinator._read())
    assert len(deadlines) == 1
    assert cleaned_up
