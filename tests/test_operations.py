import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from arbitrage import cli, reporting
from arbitrage.config import load_config
from arbitrage.coordinator import LEASE, MIN_INTERVAL, NETWORK
from arbitrage.market_data import PublicConnections, Unavailable
from arbitrage.models import Holding, Reason, Reservation
from arbitrage.operational_log import Event, OperationalLog
from arbitrage.runtime import Runtime
from arbitrage.simulation import Attempt, Effect, Scenario, SimulationEngine
from test_coordinator import CONFIG, MarketFixture
from test_market_data import POOL_TIME, Exchange, Hedera
from test_simulation import admit, offline, state


@pytest.fixture
def operations(tmp_path, now):
    config = load_config(CONFIG)
    clock = [now, 0.0]
    runtime = Runtime(tmp_path, clock=lambda: clock[0], timer=lambda: clock[1])
    runtime.initialize(config)
    return runtime, config, clock


def test_installed_cli_commands_and_private_permissions(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "private"
    args = ["--data-dir", str(folder)]
    assert cli.main([*args, "init", "--config", str(CONFIG)]) == 0
    assert folder.stat().st_mode & 0o777 == 0o700
    assert (folder / "state.sqlite3").stat().st_mode & 0o777 == 0o600
    assert cli.main([*args, "status"]) == 0
    assert '"worker-3": "stopped"' in capsys.readouterr().out

    async def inspect(runtime, worker, config):
        assert runtime.status()["session"]["state"] == "running"
        assert runtime.status()["foreground_owner_present"]
        assert worker == "worker-2"

    monkeypatch.setattr(cli, "foreground", inspect)
    assert cli.main([*args, "start", "--worker", "worker-2"]) == 0
    assert cli.main([*args, "start"]) == 1
    assert cli.main([*args, "pause"]) == 0
    assert cli.main([*args, "recover"]) == 0
    assert cli.main([*args, "resume"]) == 0
    assert cli.main([*args, "stop"]) == 0
    assert cli.main([*args, "emergency-stop"]) == 0
    assert cli.main([*args, "start"]) == 1
    assert "emergency stop is latched" in capsys.readouterr().err
    assert cli.main([*args, "report"]) == 0
    report = folder / "report.xlsx"
    assert report.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SystemExit) as error:
        cli.main([*args, "start", "--worker", "sensitive-input"])
    assert error.value.code == 2
    assert "sensitive-input" not in capsys.readouterr().err


def test_config_validation_and_symlink_rejection(tmp_path, capsys):
    path = tmp_path / "bad.toml"
    path.write_text('password = "sensitive-input"')
    folder = tmp_path / "private"
    assert cli.main(["--data-dir", str(folder), "init", "--config", str(path)]) == 1
    assert "invalid simulation configuration" in capsys.readouterr().err
    assert list(folder.iterdir()) == [folder / "owner.lock"]
    alias = tmp_path / "alias"
    alias.symlink_to(folder, target_is_directory=True)
    assert cli.main(["--data-dir", str(alias), "status"]) == 1
    assert "sensitive-input" not in capsys.readouterr().err


def test_foreground_lock_fences_another_process_and_pause_race(operations):
    runtime, config, _ = operations
    with runtime.owner():
        runtime.activate("start", "worker-1", config)
        runtime.control("paused")
        result = subprocess.run(
            [sys.executable, "-m", "arbitrage.cli", "--data-dir", str(runtime.directory), "resume"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 1
        assert "a foreground worker still owns the runtime" in result.stderr
        assert runtime.status()["workers"] == {
            "worker-1": "paused", "worker-2": "stopped", "worker-3": "stopped",
        }
    with runtime.owner():
        assert runtime.activate("resume", None, config)[0] == "worker-1"
        assert sum(value == "running" for value in runtime.status()["workers"].values()) == 1


def test_unexpected_exit_freezes_config_and_records_abandoned_cycle(operations):
    runtime, config, clock = operations
    with runtime.owner():
        runtime.activate("start", "worker-3", config)
        with runtime.storage.transaction() as tx:
            assert tx.claim_analysis(NETWORK, "lost-cycle", "worker-3", clock[0], LEASE, MIN_INTERVAL) is None
    restarted = Runtime(runtime.directory, clock=runtime.clock, timer=runtime.timer)
    changed = config.model_copy(update={"simulation": config.simulation.model_copy(
        update={"cache_ttl_seconds": 5},
    )})
    with restarted.owner():
        assert restarted.activate("recover", None) == ("worker-3", config)
        with restarted.storage.transaction() as tx:
            assert all(item.reason == Reason.CYCLE_INTERRUPTED for item in tx.analysis("lost-cycle").evaluations)
        restarted.control("paused")
    with restarted.owner():
        assert restarted.activate("recover", None) is None
        assert restarted.activate("resume", None, changed) == ("worker-3", changed)
        restarted.control("stopped")
    assert restarted.activate("recover", None) is None


@pytest.mark.parametrize("seconds,expected", [(86_399, True), (86_400, False), (86_401, False)])
def test_deadline_timezone_and_repeated_restart(operations, seconds, expected):
    runtime, config, clock = operations
    start = clock[0]
    clock[0] = start.astimezone(timezone(timedelta(hours=5, minutes=30)))
    runtime.activate("start", "worker-1", config)
    clock[0] += timedelta(seconds=seconds)
    for _ in range(2):
        runtime = Runtime(runtime.directory, clock=runtime.clock, timer=runtime.timer)
        activation = runtime.activate("recover", None)
        status = runtime.status()
        assert bool(activation) == expected
        assert datetime.fromisoformat(status["session"]["start"]) == start
        assert datetime.fromisoformat(status["session"]["deadline"]) == start + timedelta(hours=24)
        assert status["session"]["state"] == ("running" if expected else "expired")


def test_clock_rollback_and_monotonic_deadline(operations):
    runtime, config, clock = operations
    runtime.activate("start", "worker-1", config)
    clock[0] += timedelta(seconds=3)
    assert runtime.tick("worker-1")
    clock[0] -= timedelta(seconds=2)
    restarted = Runtime(runtime.directory, clock=runtime.clock, timer=runtime.timer)
    assert restarted.activate("recover", None) is None
    restarted.activate("start", "worker-2", config)
    clock[1] += 86_400
    assert not restarted.tick("worker-2")
    assert restarted.status()["session"]["state"] == "expired"


@pytest.mark.parametrize("control,reason", [
    ("emergency", Reason.WORKER_INACTIVE), ("deadline", Reason.SESSION_DEADLINE),
])
def test_running_cycle_cancelled_and_transport_closed(operations, monkeypatch, control, reason):
    original, config, clock = operations

    async def run():
        observed = asyncio.Event()
        cancelled = asyncio.Event()
        transports = []

        async def hold(adapter):
            transports.append(adapter.public.session)
            observed.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        async def sleep(seconds):
            try:
                await asyncio.sleep(0)
                if not observed.is_set():
                    return
                if control == "deadline":
                    clock[1] = 86_400
                else:
                    original.control(control)
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise

        monkeypatch.setattr("arbitrage.saucerswap.SaucerSwap.snapshot", hold)
        monkeypatch.setattr("arbitrage.kucoin.KuCoin.snapshot", hold)
        runtime = Runtime(original.directory, clock=original.clock, timer=original.timer, sleep=sleep)
        with runtime.owner():
            activation = runtime.activate("start", "worker-1", config)
            await asyncio.wait_for(runtime.run(*activation, asyncio.Event()), timeout=2)
        assert cancelled.is_set()
        assert len(transports) == 2 and transports[0] is transports[1] and transports[0].closed
        with runtime.storage.transaction() as tx:
            rows = tx._db.execute("SELECT payload FROM analysis_cycles").fetchall()
            assert len(rows) == 1 and reason.value in rows[0]["payload"]
        after = Runtime(runtime.directory, clock=runtime.clock, timer=runtime.timer)
        assert after.activate("recover", None) is None

    asyncio.run(run())


def test_deadline_cancels_cycle_interval_sleep(operations, monkeypatch):
    original, config, clock = operations

    async def run():
        sleeping = asyncio.Event()
        cancelled = asyncio.Event()

        async def sleep(seconds):
            if seconds == MIN_INTERVAL.total_seconds():
                sleeping.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            else:
                await sleeping.wait()
                clock[1] = 86_400

        async def unavailable(adapter):
            raise Unavailable("synthetic")

        monkeypatch.setattr("arbitrage.saucerswap.SaucerSwap.snapshot", unavailable)
        monkeypatch.setattr("arbitrage.kucoin.KuCoin.snapshot", unavailable)
        runtime = Runtime(original.directory, clock=original.clock, timer=original.timer, sleep=sleep)
        with runtime.owner():
            activation = runtime.activate("start", "worker-1", config)
            await asyncio.wait_for(runtime.run(*activation, asyncio.Event()), timeout=2)
        assert cancelled.is_set()
        assert runtime.status()["analysis_cycles"] == 1

    asyncio.run(run())


def test_actual_p03_adapters_and_production_engine_unknown_gas(operations, monkeypatch):
    original, config, clock = operations
    clock[0] = POOL_TIME + timedelta(seconds=1)
    hedera, exchange = Hedera(), Exchange()
    exchange.depth["time"] = int(POOL_TIME.timestamp() * 1000)
    monkeypatch.setattr(PublicConnections, "rpc", lambda self, *args: hedera.rpc(*args))
    monkeypatch.setattr(PublicConnections, "mirror_block", lambda self, *args: hedera.mirror_block(*args))
    async def kucoin(public, resource):
        if resource == "bootstrap":
            raise Unavailable("fixture WebSocket unavailable")
        return await exchange.kucoin(resource)

    monkeypatch.setattr(PublicConnections, "kucoin", kucoin)
    before = state(original.storage)

    async def run():
        async def sleep(seconds):
            if seconds == MIN_INTERVAL.total_seconds():
                original.control("stopped")
            await asyncio.sleep(0)

        original.sleep = sleep
        with original.owner():
            activation = original.activate("start", "worker-1", config)
            await asyncio.wait_for(original.run(*activation, asyncio.Event()), timeout=2)

    asyncio.run(run())
    assert state(original.storage) == before
    with original.storage.transaction() as tx:
        payload, = tx._db.execute("SELECT payload FROM analysis_cycles").fetchone()
        assert [row["reason"] for row in json.loads(payload)["evaluations"]] == ["gas_unknown"] * 2
    health = original.status()["health"]
    assert health["ws"] == "idle"
    assert health["kucoin_source"] == "rest"
    assert any(event.get("source") == "rest" for event in health["recent_events"])
    assert health["snapshots"]["dex"]["source_at"].endswith("+00:00")
    assert any(event["event"] == "analysis" and event["worker"] == "worker-1"
               and len(event["cycle"]) == 32 for event in health["recent_events"])


def test_log_bounds_and_redaction():
    log = OperationalLog(1)
    assert log.emit(Event.ANALYSIS, worker="https://private.invalid", cycle="/private",
                    password="sensitive-input", exception=ValueError("private payload"))
    assert not log.emit(Event.ANALYSIS)
    assert log.dropped == 1
    assert set(json.loads(log.take())) == {"event", "at", "level"}
    assert log.take() is None


def test_complete_workbook_and_failed_replace(operations, offline, monkeypatch):
    original, config, _ = operations
    store, market, make = offline
    engine = make(Scenario(cex=(Attempt(effect=Effect.PARTIAL, partial_units=50_000_000_000),)))
    cycle = admit(engine)
    assert asyncio.run(engine.recover()) == "incomplete"
    runtime = Runtime(original.directory)
    runtime.storage = store
    runtime.initialize(config)
    runtime.clock = market.clock
    runtime.activate("start", "worker-1", config)
    public = SimulationEngine(store, config.simulation, market, market.cex, clock=market.clock)
    worker = public.worker("worker-1")

    def observe():
        market.now += MIN_INTERVAL
        return asyncio.run(worker.analyze())

    assert observe().evaluations[0].reason == Reason.DUPLICATE_SNAPSHOT
    market.block += 1
    market.sequence += 1
    market.dex_age = timedelta(seconds=11)
    assert observe().evaluations[0].reason == Reason.STALE_OR_INVALID_CLOCK
    market.dex_age = timedelta(seconds=1)
    market.down = True
    assert observe().evaluations[0].reason == Reason.MARKET_UNAVAILABLE
    market.down = False
    with store.transaction() as tx:
        tx.reserve(Reservation(id="=2+2", holding=Holding.DEX_USDC,
                               units=tx.balance(Holding.DEX_USDC).available_units))
    assert observe().evaluations[0].reason == Reason.INSUFFICIENT_BALANCE
    destination = original.directory / "report.xlsx"
    reporting.export(store.path, destination)
    book = load_workbook(destination)
    assert {"Observations", "Trades", "Execution events", "Balances", "Configuration", "Health",
            "Current holds", "Reservations", "Sessions", "Ledger"} <= set(book.sheetnames)
    rows = list(book["Observations"].values)
    assert rows[0][5:10] == ("direction", "evaluation", "reason", "admitted", "trade status")
    assert len(rows) == 11
    assert {row[5] for row in rows[1:]} == {"buy_dex", "sell_dex"}
    assert {"duplicate_snapshot", "stale_or_invalid_clock", "market_unavailable",
            "insufficient_balance"} <= {row[7] for row in rows[1:]}
    assert (reporting.reference(cycle.id), "buy_dex", "incomplete") == list(book["Trades"].values)[1][:3]
    event = next(row for row in book["Execution events"].values if row[7] == "partial")
    assert event[9] > 0 and event[15] == 50_000_000_000
    assert list(book["Balances"].values)[1][2] == 5_000_000_000_000
    assert all(row[7:9] == (0, 0) for row in list(book["Balances"].values)[1:])
    assert "parity" in {row[0] for row in book["Guide"].values}
    assert "=2+2" not in str(list(book["Current holds"].values))
    assert not any(cell.data_type == "f" for sheet in book for row in sheet for cell in row)
    book.close()
    before = state(store), destination.read_bytes()

    def fail(*args):
        raise OSError("write failed")

    monkeypatch.setattr(reporting.os, "replace", fail)
    with pytest.raises(OSError, match="write failed"):
        reporting.export(store.path, destination)
    assert (state(store), destination.read_bytes()) == before


def test_excel_exact_numbers_and_formula_cells(tmp_path):
    from decimal import Decimal
    from openpyxl import Workbook

    destination = tmp_path / "precision.xlsx"
    book = Workbook(write_only=True)
    reporting.append(book.create_sheet("test"), [2**63 - 1, Decimal("0.1234567890123456789"), "=2+2", 42])
    book.save(destination)
    book = load_workbook(destination)
    values = list(book.active.values)[0]
    assert values == ("9223372036854775807", "0.1234567890123456789", "=2+2", 42)
    assert book.active["C1"].data_type == "s"
    book.close()


def test_systemd_template_is_inert_and_recovery_only():
    repository = Path(__file__).resolve().parents[1]
    template = repository / "deploy" / "templates" / "arbitrage.service.example"
    text = template.read_text()
    assert "NOT DEPLOYED" in text
    assert "--data-dir /var/lib/arbitrage recover" in text
    assert "User=arbitrage" in text and "StateDirectoryMode=0700" in text
    assert "TimeoutStopSec=20s" in text and "Restart=on-failure" in text
    assert "StandardOutput=journal" in text and not template.with_suffix("").exists()


def test_emergency_preserves_unsettled_fixture_journal(operations, offline):
    original, config, _ = operations
    store, market, make = offline
    cycle = admit(make())
    before = state(store)
    with store.transaction() as tx:
        events = tx.simulation_events(cycle.id)
    runtime = Runtime(original.directory, clock=market.clock)
    runtime.storage = store
    runtime.initialize(config)
    runtime.activate("start", "worker-1", config)
    runtime.control("emergency")
    assert runtime.status()["unsettled"] == "manual review required"
    assert runtime.activate("recover", None) is None
    assert state(store) == before
    with store.transaction() as tx:
        assert tx.simulation_events(cycle.id) == events
        assert tx.simulation_status(cycle.id) == "active"
