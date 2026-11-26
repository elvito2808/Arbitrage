"""Persistent foreground supervision for the simulation engine."""

import asyncio
import fcntl
import json
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from time import monotonic
from uuid import uuid4

import aiohttp

from .config import Config, load_config
from .coordinator import MIN_INTERVAL, NETWORK, OpportunityCoordinator
from .market_data import KUCOIN, MIRROR, RPC, PublicConnections, Unavailable, utc_now
from .models import EXPERIMENT_DURATION, Holding, Reason, Worker, WorkerState, normalize_time
from .operational_log import Event, Level, OperationalLog
from .simulation import SimulationEngine
from .storage import Storage

WORKERS = ("worker-1", "worker-2", "worker-3")
POLL_SECONDS = 0.25
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS runtime_sessions (
        id TEXT PRIMARY KEY, worker_id TEXT NOT NULL REFERENCES workers(id),
        start TEXT NOT NULL, deadline TEXT NOT NULL, high_water TEXT NOT NULL,
        state TEXT NOT NULL, ended TEXT
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS runtime_configs (
        session_id TEXT REFERENCES runtime_sessions(id),
        applied_at TEXT NOT NULL, config TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS runtime_health (
        id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS runtime_stop_latch (
        id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS runtime_openings (
        holding TEXT PRIMARY KEY REFERENCES balances(holding), units INTEGER NOT NULL
    ) STRICT""",
    """CREATE TRIGGER IF NOT EXISTS runtime_immutable_deadline
       BEFORE UPDATE OF start, deadline ON runtime_sessions
       BEGIN SELECT RAISE(ABORT, 'runtime start and deadline are immutable'); END""",
)


class Runtime:
    def __init__(self, directory: Path, *, clock=utc_now, timer=monotonic, sleep=asyncio.sleep):
        self.directory = directory
        self.storage = Storage(directory / "state.sqlite3")
        self.clock = clock
        self.timer = timer
        self.sleep = sleep
        self.log = OperationalLog()
        self.session_id = None
        self.anchor = None

    def now(self) -> datetime:
        return normalize_time(self.clock())

    @contextmanager
    def owner(self) -> Iterator[None]:
        """The kernel releases this lock on process death, including SIGKILL."""
        with (self.directory / "owner.lock").open("ab") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("a foreground worker still owns the runtime") from None
            yield

    def initialize(self, config: Config) -> None:
        self.storage.initialize(wal=True)
        with self.storage.transaction() as tx:
            for statement in SCHEMA:
                tx._db.execute(statement)
            tx.seed_balances(config.balances.as_balances())
            tx._db.execute(
                """INSERT INTO runtime_configs
                   SELECT NULL, ?, ? WHERE NOT EXISTS (SELECT 1 FROM runtime_configs)""",
                (self.now().isoformat(), config.model_dump_json()),
            )
            if not tx._db.execute("SELECT 1 FROM runtime_openings LIMIT 1").fetchone():
                deltas = dict.fromkeys(Holding, 0)
                for entry in tx._db.execute("SELECT holding, delta_units FROM ledger"):
                    deltas[entry["holding"]] += entry["delta_units"]
                for holding in Holding:
                    tx._db.execute("INSERT INTO runtime_openings VALUES (?, ?)",
                                   (holding, tx.balance(holding).total_units - deltas[holding]))
            # Historical workers remain as foreign-key targets for their observations.
            # Exactly these three definitions are selectable by the foreground runtime.
            for worker_id in WORKERS:
                tx._db.execute("INSERT OR IGNORE INTO workers VALUES (?, 'stopped')", (worker_id,))
            if self.latest(tx) is None:
                tx._db.execute("UPDATE workers SET state='stopped' WHERE state!='stopped'")

    @staticmethod
    def latest(tx):
        return tx._db.execute("SELECT * FROM runtime_sessions ORDER BY rowid DESC LIMIT 1").fetchone()

    def _effective(self, now):
        if self.anchor is None:
            return now
        wall, tick = self.anchor
        return max(now, wall + timedelta(seconds=max(0, self.timer() - tick)))

    def _expired(self, row, now):
        return (
            now < datetime.fromisoformat(row["high_water"])
            or self._effective(now) >= datetime.fromisoformat(row["deadline"])
        )

    @staticmethod
    def _transition(tx, row, state, now):
        tx._db.execute(
            "UPDATE runtime_sessions SET state=?, ended=? WHERE id=?",
            (state, None if state in ("running", "paused") else now.isoformat(), row["id"]),
        )
        worker_state = WorkerState(state) if state in ("running", "paused") else WorkerState.STOPPED
        tx.save_worker(Worker(id=row["worker_id"], state=worker_state))

    def _interrupted(self, tx, now):
        # Only the exclusive owner can reap a slot left by a dead process.
        slot = tx._analysis_slot(NETWORK)
        if slot and tx.analysis(slot["cycle_id"]) is None:
            tx.add_analysis(NETWORK, OpportunityCoordinator._miss(
                slot["cycle_id"], slot["worker_id"], now, Reason.CYCLE_INTERRUPTED,
            ))
            tx.release_analysis(NETWORK, slot["cycle_id"], now)

    def activate(self, action, worker_id, config=None):
        if action == "start" and worker_id not in WORKERS:
            raise ValueError("unknown runtime worker")
        self.anchor = None
        now = self.now()
        with self.storage.transaction() as tx:
            self._interrupted(tx, now)
            row = self.latest(tx)
            if row and row["state"] in ("running", "paused") and self._expired(row, now):
                self._transition(tx, row, "expired", now)
                row = self.latest(tx)
            pending = tx.unresolved_simulation(NETWORK) if action == "recover" else None
            drain_expired = (row is not None and row["state"] == "expired"
                             and pending is not None and pending["status"] == "active")
            if action == "recover" and not drain_expired and (row is None or row["state"] != "running"):
                return None
            if tx._db.execute("SELECT 1 FROM runtime_stop_latch").fetchone():
                raise ValueError("emergency stop is latched")
            if action == "start":
                if row and row["state"] in ("running", "paused"):
                    raise ValueError("a session already exists, use pause, resume, recover or stop")
                self.session_id = uuid4().hex
                tx._db.execute(
                    "INSERT INTO runtime_sessions VALUES (?, ?, ?, ?, ?, 'running', NULL)",
                    (self.session_id, worker_id, now.isoformat(),
                     (now + EXPERIMENT_DURATION).isoformat(), now.isoformat()),
                )
                row = self.latest(tx)
                self._transition(tx, row, "running", now)
            else:
                expected = "paused" if action == "resume" else "running"
                if row is None or (row["state"] != expected and not drain_expired):
                    return None
                self.session_id = row["id"]
                worker_id = row["worker_id"]
                if action == "resume":
                    self._transition(tx, row, "running", now)
                else:
                    saved = tx._db.execute(
                        "SELECT config FROM runtime_configs WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                        (row["id"],),
                    ).fetchone()
                    config = Config.model_validate_json(saved["config"])
            if action != "recover":
                tx._db.execute(
                    "INSERT INTO runtime_configs VALUES (?, ?, ?)",
                    (self.session_id, now.isoformat(), config.model_dump_json()),
                )
            self.anchor = (now, self.timer())
        return worker_id, config

    def control(self, action):
        now = self.now()
        with self.storage.transaction() as tx:
            row = self.latest(tx)
            if action == "emergency":
                tx._db.execute("INSERT OR IGNORE INTO runtime_stop_latch VALUES (1, ?)", (now.isoformat(),))
                if row and row["state"] in ("running", "paused"):
                    self._transition(tx, row, "emergency", now)
                return
            if row is None:
                raise ValueError("there is no session")
            if row["state"] == "emergency":
                if action == "stopped":
                    return
                raise ValueError("emergency stop is latched")
            if action == "paused" and row["state"] != "running":
                raise ValueError("only a running session can be paused")
            if action == "stopped" and row["state"] in ("stopped", "expired"):
                return
            self._transition(tx, row, action, now)

    def gate(self, tx, worker_id, now):
        row = self.latest(tx)
        if row is None or row["id"] != self.session_id or row["worker_id"] != worker_id:
            return Reason.WORKER_INACTIVE
        if row["state"] == "expired" or self._expired(row, now):
            return Reason.SESSION_DEADLINE
        if row["state"] != "running":
            return Reason.WORKER_INACTIVE
        return None

    def tick(self, worker_id):
        now = self.now()
        with self.storage.transaction() as tx:
            reason = self.gate(tx, worker_id, now)
            row = self.latest(tx)
            if row["id"] == self.session_id and row["state"] == "running":
                if reason == Reason.SESSION_DEADLINE:
                    self._transition(tx, row, "expired", now)
                elif reason is None:
                    tx._db.execute("UPDATE runtime_sessions SET high_water=? WHERE id=?",
                                   (now.isoformat(), row["id"]))
            return reason is None

    def status(self):
        with (self.directory / "owner.lock").open("ab") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                owner_present = True
            else:
                owner_present = False
        with self.storage.transaction() as tx:
            row = self.latest(tx)
            session = dict(row) if row else None
            if row and row["state"] in ("running", "paused") and self._expired(row, self.now()):
                session["state"] = "expired"
            health = tx._db.execute("SELECT payload FROM runtime_health WHERE id=1").fetchone()
            unresolved = tx.unresolved_simulation(NETWORK)
            emergency = bool(tx._db.execute("SELECT 1 FROM runtime_stop_latch").fetchone())
            unsettled = None
            if unresolved:
                unsettled = ("manual review required" if emergency or unresolved["status"] == "manual_review"
                             else "pending recovery")
            return {
                "mode": "simulation", "session": session,
                "foreground_owner_present": owner_present,
                "emergency_latched": emergency,
                "analysis_cycles": tx._db.execute("SELECT count(*) FROM analysis_cycles").fetchone()[0],
                "simulated_trades": tx._db.execute("SELECT count(*) FROM simulated_trades").fetchone()[0],
                "workers": {worker: tx.worker(worker).state for worker in WORKERS},
                "unsettled": unsettled,
                "execution_status": unresolved["status"] if unresolved else "idle",
                "health": json.loads(health["payload"]) if health else None,
            }

    def _health(self, public, engine, cycle=None):
        now = self.now()
        with self.storage.transaction() as tx:
            row = tx._db.execute("SELECT payload FROM runtime_health WHERE id=1").fetchone()
            previous = json.loads(row["payload"]) if row else {}
            health = {
                "observed_at": now.isoformat(),
                "ws": engine.cex.depth_stream.health,
                "kucoin_source": engine.cex.source,
                "rest": {name: public.health.get(urlsplit(url).hostname, "unknown")
                         for name, url in (("hedera_rpc", RPC), ("hedera_mirror", MIRROR), ("kucoin", KUCOIN))},
                "retries": previous.get("retries", 0) + public.retries,
                "cache_hits": previous.get("cache_hits", 0) + engine.dex.cache_hits + engine.cex.cache_hits,
                "queue_drops": previous.get("queue_drops", 0) + self.log.dropped,
                "snapshots": previous.get("snapshots"),
                "recent_events": previous.get("recent_events", []),
            }
            if cycle:
                health["snapshots"] = {
                    venue: {"received_at": snap.received_at.isoformat(),
                            "source_at": snap.source_at.isoformat(),
                            "age_seconds": max(0, (now - snap.source_at).total_seconds())} if snap else None
                    for venue, snap in (("dex", cycle.dex_snapshot), ("cex", cycle.cex_snapshot))
                }
            while (line := self.log.take()) is not None:
                health["recent_events"].append(json.loads(line))
            health["recent_events"] = health["recent_events"][-256:]
            tx._db.execute("INSERT OR REPLACE INTO runtime_health VALUES (1, ?)", (json.dumps(health),))
        public.retries = engine.dex.cache_hits = engine.cex.cache_hits = self.log.dropped = 0

    def _recovery_allowed(self, tx, shutdown, *, expired=False):
        row = self.latest(tx)
        return (not shutdown.is_set() and row is not None and row["id"] == self.session_id
                and row["state"] == ("expired" if expired else "running")
                and not tx._db.execute("SELECT 1 FROM runtime_stop_latch").fetchone())

    async def _watch(self, worker_id, shutdown):
        while not shutdown.is_set() and self.tick(worker_id):
            await self.sleep(POLL_SECONDS)

    async def _cycles(self, engine, public, worker_id):
        await engine.recover()
        while self.tick(worker_id):
            cycle = await engine.worker(worker_id).analyze()
            severity = Level.WARNING if cycle.dex_snapshot is None or cycle.cex_snapshot is None else Level.INFO
            self.log.emit(Event.ANALYSIS, severity, worker=worker_id, cycle=cycle.id)
            self._health(public, engine, cycle)
            await engine.recover()
            await self.sleep(MIN_INTERVAL.total_seconds())

    async def run(self, worker_id, config, shutdown):
        def before_request():
            if shutdown.is_set() or not self.tick(worker_id):
                raise Unavailable("analysis session inactive")

        async with aiohttp.ClientSession(trust_env=False) as session:
            public = PublicConnections(session, clock=self.now)
            public.before_request = before_request
            engine = SimulationEngine.from_public(self.storage, config.simulation, public)
            engine.gate = lambda tx, worker, now: (
                Reason.WORKER_INACTIVE if shutdown.is_set() else self.gate(tx, worker, now)
            )
            if not self.tick(worker_id):
                await self._drain_expired(engine, public, shutdown)
                return
            engine.recovery_gate = lambda tx: self._recovery_allowed(tx, shutdown)
            self.log.emit(Event.WORKER_STATE, worker=worker_id)
            engine.cex.on_source_change = lambda source, reason: self.log.emit(
                Event.CONNECTION_STATUS, worker=worker_id, source=source, reason=reason,
            )
            stream = asyncio.create_task(engine.cex.depth_stream.run())
            watch = asyncio.create_task(self._watch(worker_id, shutdown))
            cycles = asyncio.create_task(self._cycles(engine, public, worker_id))
            try:
                done, _ = await asyncio.wait((watch, cycles, stream), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in (watch, cycles, stream):
                    task.cancel()
                await asyncio.gather(watch, cycles, stream, return_exceptions=True)
                if shutdown.is_set():
                    with self.storage.transaction() as tx:
                        row = self.latest(tx)
                        if row["id"] == self.session_id and row["state"] == "running":
                            self._transition(tx, row, "stopped", self.now())
                self.log.emit(Event.WORKER_STATE, worker=worker_id)
                self._health(public, engine)
            await self._drain_expired(engine, public, shutdown)

    async def _drain_expired(self, engine, public, shutdown):
        with self.storage.transaction() as tx:
            drain = self._recovery_allowed(tx, shutdown, expired=True)
        if drain:
            engine.recovery_gate = lambda tx: self._recovery_allowed(tx, shutdown, expired=True)
            await engine.recover()
            self._health(public, engine)


async def foreground(runtime, worker_id, config):
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    try:
        await runtime.run(worker_id, config, shutdown)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
