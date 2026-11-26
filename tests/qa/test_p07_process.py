"""SIGKILL at selected SQLite and workbook persistence boundaries."""

import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arbitrage import reporting
from arbitrage.config import load_config
from arbitrage.models import Holding
from arbitrage.runtime import Runtime
from arbitrage.simulation import Event
from arbitrage.storage import Storage, Transaction
from test_coordinator import CONFIG
from test_simulation import OfflineEngine, OfflineMarket, admit, journal, state, totals

START = datetime(2026, 11, 26, tzinfo=UTC)
HERE = Path(__file__).resolve()


def fixture_engine(store):
    settings = load_config(CONFIG)
    market = OfflineMarket(START, settings.simulation.cex_fee_fraction)
    market.estimate = 250_000
    engine = OfflineEngine(store, settings.simulation, market, market.cex, clock=market.clock)

    async def advance(seconds):
        market.now += timedelta(seconds=seconds)

    engine._sleep = advance
    return engine


def crash():
    os.kill(os.getpid(), signal.SIGKILL)


def fixture_trade_process(directory, boundary, timing):
    store = Storage(directory / "state.sqlite3")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(load_config(CONFIG).balances.as_balances())
    engine = fixture_engine(store)
    marked = False
    original_event = Transaction.add_simulation_event
    original_transaction = store.transaction

    def add_event(tx, event_id, trade_id, payload):
        nonlocal marked
        original_event(tx, event_id, trade_id, payload)
        event = Event.model_validate_json(payload)
        selected = (
            (boundary == "reservation" and event.venue is None)
            or (event.venue == boundary and event.fill is not None)
        )
        if selected:
            if timing == "before":
                crash()
            marked = True

    @contextmanager
    def transaction():
        with original_transaction() as tx:
            yield tx
        if marked:
            crash()

    Transaction.add_simulation_event = add_event
    store.transaction = transaction
    admit(engine)
    if boundary == "confirmation":
        async def terminate_during_wait(seconds):
            assert seconds > 0
            crash()

        engine._sleep = terminate_during_wait
    asyncio.run(engine.recover())


def terminate_at(directory, boundary, timing):
    env = {**os.environ, "PYTHONPATH": str(HERE.parents[1])}
    result = subprocess.run(
        [sys.executable, str(HERE), str(directory), boundary, timing],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr


@pytest.mark.parametrize("boundary,timing,entries", [
    ("reservation", "before", 0), ("reservation", "after", 0),
    ("confirmation", "during", 0),
    ("dex", "before", 0), ("dex", "after", 3),
    ("cex", "before", 3), ("cex", "after", 5),
])
def test_sigkill_and_recover_exactly_once(tmp_path, boundary, timing, entries):
    terminate_at(tmp_path, boundary, timing)
    store = Storage(tmp_path / "state.sqlite3")
    _, ledger, trades, holds = state(store)
    assert len(ledger) == entries
    engine = fixture_engine(store)
    if boundary == "reservation" and timing == "before":
        assert (trades, holds) == (0, 0)
        assert asyncio.run(engine.recover()) is None
        return
    assert trades == 1
    with sqlite3.connect(store.path) as db:
        trade_id, = db.execute("SELECT id FROM simulated_trades").fetchone()
    assert asyncio.run(engine.recover()) == (None if entries == 5 else "filled")
    events = journal(store, trade_id)
    assert [event.venue for event in events if event.fill is not None] == ["dex", "cex"]
    assert asyncio.run(engine.recover()) is None
    assert state(store)[2:] == (1, 0)
    assert len(state(store)[1]) == 5
    assert totals(store) == (5_100_000_000_000, 2_947_238_238, 99_975_000_000,
                             4_900_000_000_000, 3_053_746_200)
    assert len(Holding) == 5


def test_sigkill_during_migration(tmp_path):
    terminate_at(tmp_path, "migration", "before")
    store = Storage(tmp_path / "state.sqlite3")
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM sqlite_schema").fetchone() == (0,)
    store.initialize()
    with store.transaction() as tx:
        assert tx.ledger() == ()


@pytest.mark.parametrize("timing", ["before", "after"])
def test_sigkill_at_workbook_replace(tmp_path, timing):
    runtime = Runtime(tmp_path)
    runtime.initialize(load_config(CONFIG))
    destination = tmp_path / "report.xlsx"
    reporting.export(runtime.storage.path, destination)
    original = destination.read_bytes()
    runtime.control("emergency")
    terminate_at(tmp_path, "workbook", timing)
    from openpyxl import load_workbook

    book = load_workbook(destination)
    health = dict(list(book["Health"].values)[1:])
    book.close()
    assert health["emergency latched"] is (timing == "after")
    if timing == "before":
        assert destination.read_bytes() == original


if __name__ == "__main__":
    directory, boundary, timing = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if boundary == "migration":
        import arbitrage.storage as storage

        # Invoke the kill through a SQLite callback in the same migration transaction.
        connection = storage.sqlite3.connect

        def connect(*args, **kwargs):
            db = connection(*args, **kwargs)
            db.create_function("qa_terminate", 0, crash)
            return db

        storage.sqlite3.connect = connect
        storage._ANALYSIS_SCHEMA = ("SELECT qa_terminate()", *storage._ANALYSIS_SCHEMA)
        Storage(directory / "state.sqlite3").initialize()
    elif boundary == "workbook":
        replace = reporting.os.replace

        def at_replace(source, target):
            if timing == "after":
                replace(source, target)
            crash()

        reporting.os.replace = at_replace
        reporting.export(directory / "state.sqlite3", directory / "report.xlsx")
    else:
        fixture_trade_process(directory, boundary, timing)
