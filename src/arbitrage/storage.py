"""SQLite units of work for durable simulation state."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .models import (
    MAX_UNITS, WORKER_CAPACITY, AnalysisCycle, Balance, Execution, Experiment, Holding,
    LedgerEntry, Opportunity, Reason, Reservation, Worker, WorkerState,
)

SCHEMA_VERSION = 3

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


_ANALYSIS_SCHEMA = (
    """CREATE TABLE analysis_cycles (
        id TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL REFERENCES workers(id),
        payload TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE analyzed_snapshots (
        network TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        cycle_id TEXT NOT NULL REFERENCES analysis_cycles(id),
        PRIMARY KEY (network, snapshot_id)
    ) STRICT""",
    """CREATE TABLE analysis_slots (
        network TEXT PRIMARY KEY,
        cycle_id TEXT NOT NULL,
        worker_id TEXT NOT NULL REFERENCES workers(id),
        expires_at TEXT NOT NULL,
        next_at TEXT NOT NULL
    ) STRICT""",
)


_SIMULATION_SCHEMA = (
    """CREATE TABLE simulated_trades (
        id TEXT PRIMARY KEY REFERENCES analysis_cycles(id),
        network TEXT NOT NULL,
        request TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN
            ('active', 'filled', 'failed', 'incomplete', 'manual_review'))
    ) STRICT""",
    """CREATE UNIQUE INDEX one_unresolved_trade ON simulated_trades(network)
       WHERE status IN ('active', 'manual_review')""",
    """CREATE TABLE simulation_events (
        id TEXT PRIMARY KEY,
        trade_id TEXT NOT NULL REFERENCES simulated_trades(id),
        payload TEXT NOT NULL
    ) STRICT""",
    """CREATE TRIGGER keep_simulation_request
       BEFORE UPDATE OF id, network, request ON simulated_trades
       BEGIN SELECT RAISE(ABORT, 'simulation request is immutable'); END""",
    """CREATE TRIGGER keep_simulation_event
       BEFORE UPDATE ON simulation_events
       BEGIN SELECT RAISE(ABORT, 'simulation event is immutable'); END""",
    """CREATE TRIGGER keep_simulation_event_history
       BEFORE DELETE ON simulation_events
       BEGIN SELECT RAISE(ABORT, 'simulation event is immutable'); END""",
    """CREATE TABLE simulation_misses (
        cycle_id TEXT PRIMARY KEY REFERENCES analysis_cycles(id),
        reason TEXT NOT NULL
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
            if version not in (0, 1, 2):
                raise ValueError(f"unsupported SQLite schema version {version}")
            for minimum_version, statements in enumerate((_SCHEMA, _ANALYSIS_SCHEMA, _SIMULATION_SCHEMA)):
                if version <= minimum_version:
                    for statement in statements:
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

    def claim_analysis(
        self, network: str, cycle_id: str, worker_id: str, now: datetime,
        lease: timedelta, interval: timedelta,
    ) -> Reason | None:
        worker = self.worker(worker_id)
        if worker is None or worker.state != WorkerState.RUNNING:
            return Reason.WORKER_INACTIVE
        slot = self._analysis_slot(network)
        if slot:
            if now < datetime.fromisoformat(slot["expires_at"]):
                return Reason.CYCLE_BUSY
            if now < datetime.fromisoformat(slot["next_at"]):
                return Reason.NETWORK_QUOTA
        self._db.execute(
            """INSERT INTO analysis_slots VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(network) DO UPDATE SET cycle_id = excluded.cycle_id,
               worker_id = excluded.worker_id, expires_at = excluded.expires_at,
               next_at = excluded.next_at""",
            (network, cycle_id, worker_id, (now + lease).isoformat(), (now + interval).isoformat()),
        )
        return None

    def _analysis_slot(self, network: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM analysis_slots WHERE network = ?", (network,)
        ).fetchone()

    def worker_has_analysis(self, network: str, worker_id: str, now: datetime) -> bool:
        slot = self._analysis_slot(network)
        return bool(slot and slot["worker_id"] == worker_id
                    and now < datetime.fromisoformat(slot["expires_at"]))

    def owns_analysis(self, network: str, cycle_id: str, now: datetime) -> bool:
        slot = self._analysis_slot(network)
        return bool(slot and slot["cycle_id"] == cycle_id and now < datetime.fromisoformat(slot["expires_at"]))

    def release_analysis(self, network: str, cycle_id: str, now: datetime) -> None:
        self._db.execute(
            "UPDATE analysis_slots SET expires_at = ? WHERE network = ? AND cycle_id = ?",
            (now.isoformat(), network, cycle_id),
        )

    def seen_snapshot(self, network: str, snapshot_id: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM analyzed_snapshots WHERE network = ? AND snapshot_id = ?",
            (network, snapshot_id),
        ).fetchone() is not None

    def add_analysis(
        self, network: str, cycle: AnalysisCycle, snapshot_ids: tuple[str, ...] = (),
    ) -> None:
        self._db.execute(
            "INSERT INTO analysis_cycles VALUES (?, ?, ?)",
            (cycle.id, cycle.worker_id, cycle.model_dump_json()),
        )
        self._db.executemany(
            "INSERT INTO analyzed_snapshots VALUES (?, ?, ?)",
            [(network, snapshot_id, cycle.id) for snapshot_id in snapshot_ids],
        )

    def analysis(self, cycle_id: str) -> AnalysisCycle | None:
        row = self._db.execute("SELECT payload FROM analysis_cycles WHERE id = ?", (cycle_id,)).fetchone()
        return AnalysisCycle.model_validate_json(row["payload"]) if row else None

    def owns_snapshots(self, network: str, cycle_id: str, ids: tuple[str, str]) -> bool:
        rows = self._db.execute(
            """SELECT snapshot_id FROM analyzed_snapshots
               WHERE network = ? AND cycle_id = ?""", (network, cycle_id),
        )
        return {row[0] for row in rows} == set(ids)

    def unresolved_simulation(self, network: str) -> sqlite3.Row | None:
        return self._db.execute(
            """SELECT * FROM simulated_trades WHERE network = ?
               AND status IN ('active', 'manual_review')""", (network,),
        ).fetchone()

    def add_simulation(self, trade_id: str, network: str, request: str) -> None:
        self._db.execute(
            "INSERT INTO simulated_trades VALUES (?, ?, ?, 'active')",
            (trade_id, network, request),
        )

    def simulation_status(self, trade_id: str) -> str | None:
        row = self._db.execute(
            "SELECT status FROM simulated_trades WHERE id = ?", (trade_id,),
        ).fetchone()
        return row[0] if row else None

    def finish_simulation(self, trade_id: str, status: str) -> None:
        result = self._db.execute(
            "UPDATE simulated_trades SET status = ? WHERE id = ? AND status = 'active'",
            (status, trade_id),
        )
        if result.rowcount != 1:
            raise ValueError("simulation is already resolved")

    def simulation_miss(self, cycle_id: str, reason: str) -> None:
        self._db.execute("INSERT INTO simulation_misses VALUES (?, ?)", (cycle_id, reason))

    def add_simulation_event(self, event_id: str, trade_id: str, payload: str) -> None:
        self._db.execute(
            "INSERT INTO simulation_events VALUES (?, ?, ?)", (event_id, trade_id, payload),
        )

    def simulation_events(self, trade_id: str) -> tuple[str, ...]:
        rows = self._db.execute(
            "SELECT payload FROM simulation_events WHERE trade_id = ? ORDER BY rowid", (trade_id,),
        )
        return tuple(row[0] for row in rows)

    def simulation_ledger(self, trade_id: str) -> tuple[LedgerEntry, ...]:
        rows = self._db.execute(
            "SELECT * FROM ledger WHERE id GLOB ? ORDER BY rowid", (f"sim:{trade_id}:*",),
        )
        return tuple(LedgerEntry.model_validate(dict(row)) for row in rows)

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
