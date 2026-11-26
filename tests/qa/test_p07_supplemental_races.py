"""Pinned export snapshots across a paused market read and session transitions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from openpyxl import load_workbook

from arbitrage import reporting
from arbitrage.config import load_config
from arbitrage.models import Holding, Reason
from arbitrage.runtime import Runtime
from arbitrage.simulation import SimulationEngine
from test_coordinator import CONFIG, MarketFixture

WAIT = 5


def workbook_state(path):
    with path.open("rb") as stream:
        book = load_workbook(stream, read_only=True)
        try:
            sessions = [row[2] for row in list(book["Sessions"].values)[1:]]
            cache_settings = [row[4] for row in list(book["Configuration"].values)[1:]
                              if row[3] == "cache_ttl_seconds"]
            observations = list(book["Observations"].values)[1:]
            balances = list(book["Balances"].values)[1:]
            return sessions, cache_settings, observations, balances
        finally:
            book.close()


@pytest.mark.parametrize("transition,expected,terminal", [
    ("stop", Reason.WORKER_INACTIVE, "stopped"),
    ("deadline", Reason.SESSION_DEADLINE, "expired"),
    ("quote_expiry", Reason.STALE_OR_INVALID_CLOCK, "stopped"),
])
def test_inflight_quote_and_export_across_config_restart(tmp_path, monkeypatch,
                                                        transition, expected, terminal):
    clock = [datetime(2026, 11, 26, tzinfo=UTC)]
    original = load_config(CONFIG)
    runtime = Runtime(tmp_path, clock=lambda: clock[0])
    runtime.initialize(original)
    config_path = tmp_path / "simulation.toml"
    config_path.write_bytes(CONFIG.read_bytes())
    reopened = Runtime(tmp_path, clock=runtime.clock)
    market = MarketFixture(clock[0], original.simulation.cex_fee_fraction)
    old_report = tmp_path / "old.xlsx"
    new_report = tmp_path / "new.xlsx"
    export_pinned = Event()
    finish_export = Event()
    observations = reporting.observations

    def pause_export(db, sheets):
        observations(db, sheets)  # This SELECT pins the actual SQLite read snapshot.
        export_pinned.set()
        assert finish_export.wait(WAIT), "export did not receive its release event"

    async def interleave(pool):
        read_complete = asyncio.Event()
        finish_read = asyncio.Event()
        with runtime.owner():
            worker_id, config = runtime.activate("start", "worker-1", original)
            engine = SimulationEngine(runtime.storage, config.simulation, market, market.cex,
                                      clock=runtime.clock, gate=runtime.gate)
            read = engine._read

            async def pause_read():
                snapshots = await read()
                read_complete.set()
                await asyncio.wait_for(finish_read.wait(), WAIT)
                return snapshots

            engine._read = pause_read
            task = asyncio.create_task(engine.worker(worker_id).analyze())
            try:
                await asyncio.wait_for(read_complete.wait(), WAIT)
                export = pool.submit(reporting.export, runtime.storage.path, old_report)
                assert await asyncio.to_thread(export_pinned.wait, WAIT)
                config_path.write_bytes(CONFIG.read_bytes().replace(
                    b"cache_ttl_seconds = 10", b"cache_ttl_seconds = 5",
                ))
                assert load_config(config_path).simulation.cache_ttl_seconds == 5
                assert engine.age == timedelta(seconds=10)
                with pytest.raises(ValueError, match="foreground worker still owns"):
                    with reopened.owner():
                        pytest.fail("second runtime acquired the foreground owner lock")
                if transition == "deadline":
                    clock[0] += timedelta(hours=24)
                    assert not runtime.tick(worker_id)
                elif transition == "quote_expiry":
                    clock[0] += timedelta(seconds=11)
                    assert runtime.tick(worker_id)
                else:
                    runtime.control("stopped")
                finish_read.set()
                cycle = await asyncio.wait_for(task, WAIT)
                assert all(item.reason == expected for item in cycle.evaluations)
                if transition == "quote_expiry":
                    runtime.control("stopped")
                assert runtime.status()["session"]["state"] == terminal
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        with reopened.owner():
            assert reopened.activate("recover", None) is None
            _, selected = reopened.activate("start", "worker-2", load_config(config_path))
            assert selected.simulation.cache_ttl_seconds == 5
        finish_export.set()
        await asyncio.wait_for(asyncio.wrap_future(export), WAIT)

    with ThreadPoolExecutor(max_workers=1) as pool, monkeypatch.context() as patch:
        patch.setattr(reporting, "observations", pause_export)
        try:
            asyncio.run(interleave(pool))
        finally:
            finish_export.set()

    reporting.export(runtime.storage.path, new_report)
    old_sessions, old_settings, old_observations, old_balances = workbook_state(old_report)
    new_sessions, new_settings, new_observations, new_balances = workbook_state(new_report)
    assert old_sessions == ["running"]
    assert old_settings == [10, 10]
    assert old_observations == []
    assert new_sessions == [terminal, "running"]
    assert new_settings == [10, 10, 5]
    assert len(new_observations) == 2
    assert {row[7] for row in new_observations} == {expected.value}
    assert old_balances == new_balances
    assert all(row[7:9] == (0, 0) for row in new_balances)
    with reopened.storage.transaction() as tx:
        assert tx.ledger() == ()
        counts = tx._db.cursor()
        counts.row_factory = None
        for table in ("reservations", "simulated_trades", "simulation_events", "executions",
                      "analyzed_snapshots"):
            assert counts.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert tuple(tx.balance(holding) for holding in Holding) == original.balances.as_balances()
        assert counts.execute("SELECT count(*) FROM workers WHERE state='running'").fetchone() == (1,)
