"""SQLite units of work for durable simulation state."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from .models import (
    MAX_UNITS, WORKER_CAPACITY, Balance, Execution, Experiment, Holding,
    LedgerEntry, Opportunity, Reservation, Worker, WorkerState,
)

SCHEMA_VERSION = 1

_SCHEMA = (
    """CREATE TABLE balances (
        holding TEXT PRIMARY KEY,
        total_units INTEGER NOT NULL CHECK (total_units >= 0),
        reserved_units INTEGER NOT NULL DEFAULT 0 CHECK (reserved_units BETWEEN 0 AND total_units)
    ) STRICT""",
    """CREATE TABLE reservations (
        id TEXT PRIMARY KEY,
        holding TEXT NOT NULL REFERENCES balances(holding),
        units INTEGER NOT NULL CHECK (units > 0)
    ) STRICT""",
    """CREATE TABLE ledger (
        id TEXT PRIMARY KEY,
        holding TEXT NOT NULL REFERENCES balances(holding),
        delta_units INTEGER NOT NULL,
        recorded_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE workers (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL CHECK (state IN ('stopped', 'running', 'paused'))
    ) STRICT""",
    "CREATE UNIQUE INDEX one_running_worker ON workers(state) WHERE state = 'running'",
    """CREATE TABLE opportunities (
        id TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL REFERENCES workers(id),
        payload TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE executions (
        id TEXT PRIMARY KEY,
        opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
        payload TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE experiments (
        id TEXT PRIMARY KEY,
        payload TEXT NOT NULL
    ) STRICT""",
)


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            if version != 0:
                raise ValueError(f"unsupported SQLite schema version {version}")
            for statement in _SCHEMA:
                db.execute(statement)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db

    @contextmanager
    def transaction(self) -> Iterator["Transaction"]:
        """Commit on normal exit and roll back everything on an escaping exception."""
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ValueError(f"initialize storage before use (schema version {version})")
            yield Transaction(db)


class Transaction:
    def __init__(self, connection: sqlite3.Connection):
        self._db = connection

    def seed_balances(self, balances: tuple[Balance, ...]) -> None:
        """Insert opening balances once, leaving persisted balances intact."""
        if any(balance.reserved_units for balance in balances):
            raise ValueError("opening balances must be unreserved")
        self._db.executemany(
            "INSERT INTO balances(holding, total_units) VALUES (?, ?) ON CONFLICT(holding) DO NOTHING",
            [(balance.holding, balance.total_units) for balance in balances],
        )

    def balance(self, holding: Holding) -> Balance:
        row = self._db.execute("SELECT * FROM balances WHERE holding = ?", (holding,)).fetchone()
        if row is None:
            raise KeyError(holding)
        return Balance.model_validate(dict(row))

    def reserve(self, reservation: Reservation) -> None:
        balance = self.balance(reservation.holding)
        if reservation.units > balance.available_units:
            raise ValueError("insufficient available balance")
        self._db.execute(
            "INSERT INTO reservations VALUES (?, ?, ?)",
            (reservation.id, reservation.holding, reservation.units),
        )
        self._db.execute(
            "UPDATE balances SET reserved_units = reserved_units + ? WHERE holding = ?",
            (reservation.units, reservation.holding),
        )

    def reservation(self, reservation_id: str) -> Reservation | None:
        row = self._db.execute(
            "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
        ).fetchone()
        return Reservation.model_validate(dict(row)) if row else None

    def release(self, reservation_id: str) -> bool:
        row = self._db.execute(
            "DELETE FROM reservations WHERE id = ? RETURNING holding, units", (reservation_id,)
        ).fetchone()
        if row is None:
            return False
        self._db.execute(
            "UPDATE balances SET reserved_units = reserved_units - ? WHERE holding = ?",
            (row["units"], row["holding"]),
        )
        return True

    def post(self, entry: LedgerEntry) -> None:
        balance = self.balance(entry.holding)
        new_total = balance.total_units + entry.delta_units
        if not balance.reserved_units <= new_total <= MAX_UNITS:
            raise ValueError("ledger entry would exceed available balance or range")
        self._db.execute(
            "INSERT INTO ledger VALUES (?, ?, ?, ?)",
            (entry.id, entry.holding, entry.delta_units, entry.recorded_at.isoformat()),
        )
        self._db.execute(
            "UPDATE balances SET total_units = ? WHERE holding = ?", (new_total, entry.holding)
        )

    def ledger(self) -> tuple[LedgerEntry, ...]:
        rows = self._db.execute("SELECT * FROM ledger ORDER BY rowid")
        return tuple(LedgerEntry.model_validate(dict(row)) for row in rows)

    def save_worker(self, worker: Worker) -> None:
        existing = self.worker(worker.id)
        if existing is None:
            count = self._db.execute("SELECT count(*) FROM workers").fetchone()[0]
            if count >= WORKER_CAPACITY:
                raise ValueError("worker capacity reached")
        if worker.state == WorkerState.RUNNING:
            running = self._db.execute(
                "SELECT 1 FROM workers WHERE state = 'running' AND id != ?", (worker.id,)
            ).fetchone()
            if running:
                raise ValueError("another worker is running")
        self._db.execute(
            """INSERT INTO workers VALUES (?, ?)
               ON CONFLICT(id) DO UPDATE SET state = excluded.state""",
            (worker.id, worker.state),
        )

    def worker(self, worker_id: str) -> Worker | None:
        row = self._db.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)).fetchone()
        return Worker.model_validate(dict(row)) if row else None

    def add_opportunity(self, opportunity: Opportunity) -> None:
        self._db.execute(
            "INSERT INTO opportunities VALUES (?, ?, ?)",
            (opportunity.id, opportunity.worker_id, opportunity.model_dump_json()),
        )

    def opportunity(self, opportunity_id: str) -> Opportunity | None:
        row = self._db.execute(
            "SELECT payload FROM opportunities WHERE id = ?", (opportunity_id,)
        ).fetchone()
        return Opportunity.model_validate_json(row["payload"]) if row else None

    def save_execution(self, execution: Execution) -> None:
        previous = self.execution(execution.id)
        if previous and previous.opportunity_id != execution.opportunity_id:
            raise ValueError("execution opportunity cannot change")
        self._db.execute(
            """INSERT INTO executions VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET payload = excluded.payload""",
            (execution.id, execution.opportunity_id, execution.model_dump_json()),
        )

    def execution(self, execution_id: str) -> Execution | None:
        row = self._db.execute(
            "SELECT payload FROM executions WHERE id = ?", (execution_id,)
        ).fetchone()
        return Execution.model_validate_json(row["payload"]) if row else None

    def start_experiment(self, experiment: Experiment) -> None:
        self._db.execute(
            "INSERT INTO experiments VALUES (?, ?)", (experiment.id, experiment.model_dump_json())
        )

    def experiment(self, experiment_id: str) -> Experiment | None:
        row = self._db.execute(
            "SELECT payload FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        return Experiment.model_validate_json(row["payload"]) if row else None
