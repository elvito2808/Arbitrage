"""Financial integrity checks on fresh production-schema SQLite databases."""

import errno
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from openpyxl import load_workbook

from arbitrage import reporting
from arbitrage.config import load_config
from arbitrage.models import Holding, LedgerEntry, Reservation
from arbitrage.runtime import Runtime
from arbitrage.storage import Storage

CONFIG = Path(__file__).resolve().parents[2] / "config" / "simulation.toml"


@pytest.fixture
def runtime(tmp_path):
    instance = Runtime(tmp_path)
    instance.initialize(load_config(CONFIG))
    return instance


@pytest.mark.parametrize("statement", [
    "UPDATE ledger SET delta_units=1",
    "DELETE FROM ledger",
])
def test_database_rejects_rewriting_posted_ledger(runtime, statement):
    with runtime.storage.transaction() as tx:
        tx.post(LedgerEntry(id="qa-post", holding=Holding.DEX_HBAR,
                            delta_units=-5, recorded_at=datetime(2026, 11, 26, tzinfo=UTC)))
    with sqlite3.connect(runtime.storage.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(statement)


def test_atomic_reservation_against_shared_database(runtime):
    holding = Holding.CEX_HBAR
    barrier = Barrier(4)
    with runtime.storage.transaction() as tx:
        opening = tx.balance(holding).total_units

    def reserve(index):
        barrier.wait(timeout=5)
        try:
            with Storage(runtime.storage.path).transaction() as tx:
                tx.reserve(Reservation(id=f"qa-{index}", holding=holding, units=opening))
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(reserve, range(4))).count(True) == 1
    with runtime.storage.transaction() as tx:
        assert tx.balance(holding).available_units == 0
        assert tx.balance(holding).total_units == opening
        assert tx._db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 1


def test_export_race_and_enospc_preserve_reconciled_workbook(runtime, monkeypatch):
    destination = runtime.directory / "report.xlsx"
    barrier = Barrier(2)

    def export():
        barrier.wait(timeout=5)
        reporting.export(runtime.storage.path, destination)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(export), pool.submit(export)
        first.result(timeout=5)
        second.result(timeout=5)

    book = load_workbook(destination)
    assert all(row[7:9] == (0, 0) for row in list(book["Balances"].values)[1:])
    book.close()
    committed = destination.read_bytes()

    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, "injected no space")

    monkeypatch.setattr(reporting.os, "fsync", no_space)
    with pytest.raises(OSError) as failure:
        reporting.export(runtime.storage.path, destination)
    assert failure.value.errno == errno.ENOSPC
    assert destination.read_bytes() == committed
    assert list(runtime.directory.glob("*.xlsx")) == [destination]
