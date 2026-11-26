"""Exercise SQLite rollback without exhausting the host filesystem."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from arbitrage.models import Balance, Holding, LedgerEntry, Reservation
from arbitrage.storage import Storage

HOLDING = Holding.DEX_USDC
NOW = datetime(2026, 11, 26, tzinfo=UTC)


@pytest.mark.parametrize("fault,expected", [
    ("page_limit", sqlite3.SQLITE_FULL),
    ("write_denied", sqlite3.SQLITE_AUTH),
])
def test_posting_fault_rolls_back_entire_unit_of_work(tmp_path, monkeypatch, fault, expected):
    path = tmp_path / "simulation.sqlite3"
    store = Storage(path)
    store.initialize()
    first = LedgerEntry(id="first", holding=HOLDING, delta_units=-5, recorded_at=NOW)
    second = LedgerEntry(id="second", holding=HOLDING, delta_units=3, recorded_at=NOW)
    hold = Reservation(id="second-hold", holding=HOLDING, units=8)
    with store.transaction() as tx:
        tx.seed_balances((Balance(holding=HOLDING, total_units=100),))
        tx.post(first)

    connect = sqlite3.connect
    if fault == "page_limit":
        # A test-only trigger forces page allocation during the balance update,
        # after Transaction.post has inserted its ledger entry.
        with closing(connect(path)) as db, db:
            db.execute("CREATE TABLE qa_pages (payload BLOB)")
            db.execute("""
                CREATE TRIGGER qa_force_growth AFTER UPDATE OF total_units ON balances
                BEGIN INSERT INTO qa_pages VALUES (zeroblob(1048576)); END
            """)

    def authorizer(operation, table, column, database, source):
        if (operation, table, column) == (sqlite3.SQLITE_UPDATE, "balances", "total_units"):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def constrained_connection(*args, **kwargs):
        db = connect(*args, **kwargs)
        if fault == "page_limit":
            used, = db.execute("PRAGMA page_count").fetchone()
            assert db.execute(f"PRAGMA max_page_count={used}").fetchone() == (used,)
        else:
            db.set_authorizer(authorizer)
        return db

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", constrained_connection)
        with pytest.raises(sqlite3.DatabaseError) as error, store.transaction() as tx:
            tx.reserve(hold)
            with closing(connect(path)) as reader:
                assert reader.execute("SELECT count(*) FROM reservations").fetchone() == (0,)
                assert reader.execute("SELECT id FROM ledger").fetchall() == [("first",)]
            tx.post(second)
        assert error.value.sqlite_errorcode == expected

    reopened = Storage(path)
    reopened.initialize()
    with reopened.transaction() as tx:
        assert tx.ledger() == (first,)
        assert tx.reservation(hold.id) is None
        assert tx.balance(HOLDING) == Balance(holding=HOLDING, total_units=95)
        assert tx._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert tx._db.execute("PRAGMA foreign_key_check").fetchall() == []
        if fault == "page_limit":
            assert tx._db.execute("SELECT count(*) FROM qa_pages").fetchone()[0] == 0
            tx._db.execute("DROP TRIGGER qa_force_growth")
        tx.reserve(hold)
        tx.post(second)

    with Storage(path).transaction() as tx:
        assert tx.ledger() == (first, second)
        assert tx.reservation(hold.id) == hold
        assert tx.balance(HOLDING).total_units == 100 + sum(entry.delta_units for entry in tx.ledger()) == 98
        assert tx.balance(HOLDING).available_units == 90
