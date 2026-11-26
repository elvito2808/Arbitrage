import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from arbitrage.models import (
    Balance, Execution, Experiment, Holding, LedgerEntry, Outcome, Reservation, Worker, WorkerState,
)
from arbitrage.storage import SCHEMA_VERSION, Storage


HOLDING = Holding.DEX_USDC


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "simulation.sqlite3")
    storage.initialize()
    with storage.transaction() as tx:
        tx.seed_balances((Balance(holding=HOLDING, total_units=100),))
    return storage


def test_schema_and_future_version(store):
    store.initialize()
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="unsupported SQLite schema"):
        store.initialize()
    with pytest.raises(ValueError, match="initialize storage"):
        with store.transaction():
            pytest.fail("unsupported schema accepted")


def test_restart_round_trip(store, opportunity, now):
    worker = Worker(id=opportunity.worker_id, state=WorkerState.PAUSED)
    reservation = Reservation(id="reserve-1", holding=HOLDING, units=30)
    experiment = Experiment(id="experiment-1", started_at=now, deadline_at=now + timedelta(hours=24))
    entry = LedgerEntry(id="ledger-1", holding=HOLDING, delta_units=-25, recorded_at=now)
    execution = Execution(
        id="execution-1", opportunity_id=opportunity.id, outcome=Outcome.PARTIAL,
        observed_at=now, dex_fill=opportunity.requested_dex,
    )
    with store.transaction() as tx:
        tx.save_worker(worker)
        tx.add_opportunity(opportunity)
        tx.reserve(reservation)
        tx.post(entry)
        tx.start_experiment(experiment)
        tx.save_execution(execution)

    restarted = Storage(store.path)
    restarted.initialize()
    with restarted.transaction() as tx:
        tx.seed_balances((Balance(holding=HOLDING, total_units=999),))
        assert tx.balance(HOLDING).available_units == 45
        assert tx.reservation(reservation.id) == reservation
        assert tx.worker(worker.id) == worker
        assert tx.opportunity(opportunity.id) == opportunity
        assert tx.experiment(experiment.id) == experiment
        assert tx.ledger() == (entry,)
        assert tx.execution(execution.id) == execution
        assert tx.release(reservation.id)
        assert not tx.release(reservation.id)
    with store.transaction() as tx:
        assert tx.balance(HOLDING).available_units == 75


def test_atomic_rollback(store, opportunity, now):
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as tx:
            tx.save_worker(Worker(id=opportunity.worker_id, state=WorkerState.RUNNING))
            tx.add_opportunity(opportunity)
            tx.reserve(Reservation(id="r", holding=HOLDING, units=30))
            tx.post(LedgerEntry(id="l", holding=HOLDING, delta_units=-60, recorded_at=now))
            tx.add_opportunity(opportunity)
    with store.transaction() as tx:
        assert tx.worker(opportunity.worker_id) is None
        assert tx.opportunity(opportunity.id) is None
        assert tx.reservation("r") is None
        assert tx.ledger() == ()
        assert tx.balance(HOLDING).available_units == 100


def test_competing_reservations_cannot_overdraw(store):
    def reserve(identifier):
        try:
            with store.transaction() as tx:
                tx.reserve(Reservation(id=identifier, holding=HOLDING, units=80))
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(reserve, ("first", "second"))) == [False, True]
    with store.transaction() as tx:
        assert tx.balance(HOLDING).available_units == 20


def test_worker_limits(store):
    with store.transaction() as tx:
        tx.save_worker(Worker(id="a", state=WorkerState.RUNNING))
        with pytest.raises(ValueError, match="another worker"):
            tx.save_worker(Worker(id="b", state=WorkerState.RUNNING))
        tx.save_worker(Worker(id="b", state=WorkerState.STOPPED))
        tx.save_worker(Worker(id="c", state=WorkerState.PAUSED))
        with pytest.raises(ValueError, match="capacity"):
            tx.save_worker(Worker(id="d", state=WorkerState.STOPPED))
        tx.save_worker(Worker(id="a", state=WorkerState.PAUSED))
        tx.save_worker(Worker(id="b", state=WorkerState.RUNNING))


def test_ledger_cannot_spend_reserved_units(store, now):
    with store.transaction() as tx:
        tx.reserve(Reservation(id="r", holding=HOLDING, units=60))
        with pytest.raises(ValueError, match="available"):
            tx.post(LedgerEntry(id="l", holding=HOLDING, delta_units=-41, recorded_at=now))
        assert tx.ledger() == ()
        assert tx.balance(HOLDING).available_units == 40


@pytest.mark.parametrize("original_version", [0, 3])
def test_posted_ledger_is_append_only_for_direct_writers(tmp_path, now, original_version):
    from arbitrage.storage import _ANALYSIS_SCHEMA, _SCHEMA, _SIMULATION_SCHEMA

    path = tmp_path / "ledger.sqlite3"
    storage = Storage(path)
    if original_version == 3:
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            for statement in _SCHEMA + _ANALYSIS_SCHEMA + _SIMULATION_SCHEMA:
                db.execute(statement)
            db.execute("PRAGMA user_version = 3")
            db.execute("INSERT INTO balances VALUES (?, 95, 0)", (HOLDING,))
            db.execute("INSERT INTO ledger VALUES (?, ?, -5, ?)", ("first", HOLDING, now.isoformat()))
    storage.initialize()
    first = LedgerEntry(id="first", holding=HOLDING, delta_units=-5, recorded_at=now)
    with storage.transaction() as tx:
        if original_version == 0:
            tx.seed_balances((Balance(holding=HOLDING, total_units=100),))
            tx.post(first)
        assert tx.ledger() == (first,)
        assert tx.balance(HOLDING).total_units == 95
        tx.post(LedgerEntry(id="second", holding=HOLDING, delta_units=3, recorded_at=now))

    statements = (
        "UPDATE ledger SET delta_units=9 WHERE id='first'",
        "DELETE FROM ledger WHERE id='first'",
        "INSERT OR REPLACE INTO ledger VALUES ('first', 'dex.USDC', 9, 'changed')",
        "REPLACE INTO ledger VALUES ('first', 'dex.USDC', 9, 'changed')",
        "INSERT INTO ledger VALUES ('first', 'dex.USDC', 9, 'changed') "
        "ON CONFLICT(id) DO UPDATE SET delta_units=excluded.delta_units",
        "INSERT OR REPLACE INTO ledger(rowid,id,holding,delta_units,recorded_at) "
        "SELECT rowid,'third',holding,9,'changed' FROM ledger WHERE id='first'",
        "UPDATE OR REPLACE ledger SET id='first' WHERE id='second'",
    )
    for recursive_triggers in (0, 1):
        with sqlite3.connect(path) as db:
            db.execute(f"PRAGMA recursive_triggers = {recursive_triggers}")
            for statement in statements:
                with pytest.raises(sqlite3.IntegrityError, match="posted ledger is append-only"):
                    db.execute(statement)

    storage.initialize()
    with storage.transaction() as tx:
        deltas = tuple(entry.delta_units for entry in tx.ledger())
        assert deltas == (-5, 3)
        assert tx.balance(HOLDING).total_units == 100 + sum(deltas) == 98
        assert tx._db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v3_ledger_migration_rolls_back_trigger_ddl_on_collision(tmp_path):
    from arbitrage.storage import _ANALYSIS_SCHEMA, _SCHEMA, _SIMULATION_SCHEMA

    path = tmp_path / "collision.sqlite3"
    with sqlite3.connect(path) as db:
        for statement in _SCHEMA + _ANALYSIS_SCHEMA + _SIMULATION_SCHEMA:
            db.execute(statement)
        db.execute("PRAGMA user_version = 3")
        db.execute("CREATE TRIGGER keep_ledger_delete BEFORE DELETE ON ledger BEGIN SELECT 1; END")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Storage(path).initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
        assert db.execute("SELECT name FROM sqlite_schema WHERE name='keep_ledger_update'").fetchall() == []
