"""Persistent foreground supervision for the simulation engine."""

import asyncio
import fcntl
import json
import re
import signal
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from random import uniform
from pathlib import Path
from urllib.parse import urlsplit
from time import monotonic
from uuid import uuid4

import aiohttp

from .console import ConsoleSink
from .config import Config, load_config
from .coordinator import MIN_INTERVAL, NETWORK, InvalidCycle, OpportunityCoordinator
from .market_data import (
    KUCOIN, MIRROR, RPC, MarketDataError, ProviderRateLimited, ProviderTimeout, PublicConnections, Unavailable, utc_now,
)
from .monitor import Monitor
from .models import EXPERIMENT_DURATION, Holding, Reason, Side, Worker, WorkerState, normalize_time
from .operational_log import Event, Level, OperationalLog
from .readiness import CRITICAL_GAS, Check, Diagnostic, Outcome, ReadinessState, StartupBlocked
from .saucerswap import CHAIN_ID, PAIR, ROUTER, USDC, WHBAR
from .simulation import MAX_ATTEMPTS, SimulationEngine
from .storage import Storage

WORKERS = ("worker-1", "worker-2", "worker-3")
POLL_SECONDS = 0.25
STARTUP_RETRY_DELAYS = (5, 10, 20)
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
    def __init__(self, directory: Path, *, clock=utc_now, timer=monotonic, sleep=asyncio.sleep, jitter=uniform):
        self.directory = directory
        self.storage = Storage(directory / "state.sqlite3")
        self.clock = clock
        self.timer = timer
        self.sleep = sleep
        self.jitter = jitter
        self.log = OperationalLog(clock=self.now)
        self.monitor = None
        self.session_id = None
        self.anchor = None
        self.readiness = None

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
            if not tx._db.execute("SELECT 1 FROM runtime_configs LIMIT 1").fetchone():
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
                session_id=self.latest(tx)["id"] if self.latest(tx) else None,
            ))
            tx.release_analysis(NETWORK, slot["cycle_id"], now)

    @staticmethod
    def _saved_health(tx):
        row = tx._db.execute("SELECT payload FROM runtime_health WHERE id=1").fetchone()
        return json.loads(row["payload"]) if row else {}

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
            saved_readiness = self._saved_health(tx).get("readiness") or {}
            blocked = (row is not None and saved_readiness.get("session_id") == row["id"]
                       and saved_readiness.get("state") == ReadinessState.STARTUP_BLOCKED)
            if blocked and not drain_expired:
                if action != "start":
                    raise StartupBlocked()
                if row["state"] in ("running", "paused"):
                    self._transition(tx, row, "stopped", now)
                    row = self.latest(tx)
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
            if not drain_expired:
                health = self._saved_health(tx)
                if health.pop("readiness", None) is not None:
                    tx._db.execute("INSERT OR REPLACE INTO runtime_health VALUES (1, ?)", (json.dumps(health),))
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
            health = self._saved_health(tx)
            readiness = health.get("readiness")
            if readiness and (not row or readiness["session_id"] != row["id"]):
                readiness = None
            if readiness and readiness["state"] != ReadinessState.STARTUP_BLOCKED and (
                not owner_present or session["state"] != "running"
            ):
                readiness = {**readiness, "state": ReadinessState.CANCELLED, "next_attempt_at": None}
            if "readiness" in health:
                health["readiness"] = readiness
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
                "health": health or None,
                "readiness": readiness,
            }

    def _health(self, public, engine, cycle=None):
        now = self.now()
        with self.storage.transaction() as tx:
            previous = self._saved_health(tx)
            last_ws_update = engine.cex.depth_stream.last_valid_received_at
            health = {
                **previous,
                "readiness": self.readiness or previous.get("readiness"),
                "ws_last_valid_received_at": last_ws_update.isoformat() if last_ws_update else None,
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

    def _readiness_state(self, public, engine, worker, state, *, reason=None, next_at=None, exhausted=False):
        self.readiness.update(state=state, reason=reason, observed_at=self.now().isoformat(),
                              next_attempt_at=next_at.isoformat() if next_at else None)
        level = (Level.ERROR if state == ReadinessState.STARTUP_BLOCKED else
                 Level.WARNING if state == ReadinessState.WAITING_RETRY else Level.INFO)
        self.log.emit(Event.READINESS_STATE, level, worker=worker, session=self.session_id,
                      state=state, diagnostic=reason, attempt=self.readiness["attempt"],
                      next_attempt_at=next_at,
                      delay_seconds=Decimal(str(max(0, (next_at - self.now()).total_seconds()))) if next_at else None,
                      retry_exhausted=exhausted)
        self._health(public, engine)

    def _check(self, worker, check, outcome, reason=None, **evidence):
        self.readiness["checks"][check] = {
            "outcome": outcome, "reason": reason, "at": self.now().isoformat(), **evidence,
        }
        level = (Level.ERROR if outcome == Outcome.FAIL else
                 Level.WARNING if outcome == Outcome.DEGRADED else Level.INFO)
        details = {key: evidence[key] for key in ("source", "gas_units", "capped_tinybars", "hbar_tinybars")
                   if key in evidence}
        if check == Check.HEDERA and outcome == Outcome.PASS:
            details.update(chain_id=CHAIN_ID, router_number=int(ROUTER, 16),
                           pool=PAIR, pair="WHBAR-USDC", rpc="PASS", mirror="PASS")
        if check == Check.SENDER:
            for token, fields in evidence.get("tokens", {}).items():
                details.update({f"{token}_associated": fields["associated"], f"{token}_balance": fields["balance_units"],
                                f"{token}_allowance": fields["allowance_units"]})
        self.log.emit(Event.READINESS_CHECK, level, worker=worker, session=self.session_id, check=check,
                      outcome=outcome, diagnostic=reason, attempt=self.readiness["attempt"], **details)

    @staticmethod
    def _reconciled(tx):
        for holding in Holding:
            opening = tx._db.execute("SELECT units FROM runtime_openings WHERE holding=?", (holding,)).fetchone()
            balance = tx.balance(holding)
            delta = tx._db.execute(
                "SELECT coalesce(sum(delta_units),0) FROM ledger WHERE holding=?", (holding,),
            ).fetchone()[0]
            held = tx._db.execute(
                "SELECT coalesce(sum(units),0) FROM reservations WHERE holding=?", (holding,),
            ).fetchone()[0]
            if (opening is None or opening[0] < 0 or opening[0] + delta != balance.total_units
                    or held != balance.reserved_units):
                return False
        return True

    def _storage_check(self):
        with self.storage.transaction() as tx:
            return ([tuple(row) for row in tx._db.execute("PRAGMA quick_check")] == [("ok",)]
                    and tx._db.execute("PRAGMA foreign_key_check").fetchone() is None
                    and self._reconciled(tx))

    def _block(self, public, engine, worker, reason, *, exhausted=False):
        self._readiness_state(public, engine, worker, ReadinessState.STARTUP_BLOCKED,
                              reason=reason, exhausted=exhausted)
        raise StartupBlocked()

    def _cycle_market_gate(self, engine, tx, market, now):
        try:
            consistent = self._reconciled(tx)
        except (sqlite3.Error, ValueError, KeyError):
            consistent = False
        if not consistent:
            self._critical_cycle = Diagnostic.STORAGE_INCONSISTENT
            return Reason.READINESS_BLOCKED
        for gas in market.pool.gas:
            if gas.estimation_error in CRITICAL_GAS:
                self._critical_cycle = Diagnostic(gas.estimation_error)
                return Reason.READINESS_BLOCKED
        if len(market.pool.gas) != 2:
            return Reason.GAS_INVALID
        for side, gas in zip(Side, market.pool.gas, strict=True):
            try:
                engine._gas(gas, side, market, now)
            except InvalidCycle as error:
                return error.reason
        return None

    async def _network_slot(self, worker, *, reserve=False):
        """Use a durable preflight slot without creating an analysis lease or cycle."""
        while not self._shutdown.is_set() and self.tick(worker):
            now = self.now()
            with self.storage.transaction() as tx:
                if self.gate(tx, worker, now) is not None:
                    return False
                health = self._saved_health(tx)
                slot = tx._analysis_slot(NETWORK)
                limits = [now]
                if slot:
                    limits.extend(datetime.fromisoformat(slot[key]) for key in ("next_at", "expires_at"))
                if saved := health.get("preflight_network_not_before"):
                    limits.append(datetime.fromisoformat(saved))
                due = max(limits)
                if due <= now:
                    if reserve:
                        health["preflight_network_not_before"] = (now + MIN_INTERVAL).isoformat()
                        tx._db.execute("INSERT OR REPLACE INTO runtime_health VALUES (1, ?)",
                                       (json.dumps(health),))
                    return True
            await self.sleep((due - now).total_seconds())
        return False

    @staticmethod
    def _sender_quantities(account, relationships, allowances, address):
        router_id = f"0.0.{int(ROUTER, 16)}"
        account_id = account.get("account")
        if (account.get("evm_address") != address or account.get("deleted") is not False
                or not isinstance(account_id, str) or not re.fullmatch(r"0\.0\.[1-9][0-9]*", account_id)):
            raise MarketDataError("sender identity unverified")
        if any(not isinstance(page.get("links"), dict) or page["links"].get("next") is not None
               for page in (relationships, allowances)):
            raise MarketDataError("sender relationships or allowances incomplete")
        tokens = {item["token_id"]: item for item in relationships["tokens"]}
        approvals = {item["token_id"]: item for item in allowances["allowances"]
                     if item.get("spender") == router_id and item.get("owner") == account_id}
        hbar = account["balance"]["balance"]
        values = {}
        for token_address in (USDC, WHBAR):
            token = f"0.0.{int(token_address, 16)}"
            relation = tokens.get(token)
            values[token_address] = (relation is not None, relation["balance"] if relation else 0,
                               approvals.get(token, {}).get("amount", 0))
        if any(type(units) is not int or units < 0
               for units in (hbar, *(amount for _, balance, approval in values.values()
                                     for amount in (balance, approval)))):
            raise MarketDataError("invalid sender balance or allowance")
        return hbar, values

    async def _sender_check(self, engine, public, worker, pool, budgets):
        address = engine.dex.estimate_sender
        try:
            async with asyncio.timeout(5):
                data = await public.mirror_sender(address, f"0.0.{int(ROUTER, 16)}")
            hbar, amounts = self._sender_quantities(*data, address)
        except (MarketDataError, TimeoutError, KeyError, TypeError, AttributeError, ExceptionGroup) as error:
            if isinstance(error, ExceptionGroup):
                engine._failure(error)
            errors = error.exceptions if isinstance(error, ExceptionGroup) else (error,)
            diagnostic = Diagnostic.SENDER_UNVERIFIED
            if any(isinstance(item, (TimeoutError, ProviderTimeout)) for item in errors):
                diagnostic = Diagnostic.PROVIDER_TIMEOUT
            elif any(isinstance(item, ProviderRateLimited) for item in errors):
                diagnostic = Diagnostic.PROVIDER_RATE_LIMITED
            self._check(worker, Check.SENDER, Outcome.UNKNOWN, diagnostic)
            return diagnostic, False

        issues = []
        tokens = {}
        for token, name, required, shortfall in (
            (USDC, "usdc", pool.quotes[0].quote_units, Diagnostic.INSUFFICIENT_SENDER_USDC),
            (WHBAR, "whbar", pool.quotes[1].base_units, Diagnostic.INSUFFICIENT_SENDER_WHBAR),
        ):
            associated, balance, allowance = amounts[token]
            tokens[name] = {"associated": associated, "balance_units": balance,
                            "allowance_units": allowance, "required_units": required}
            if not associated:
                issues.append(Diagnostic.MISSING_TOKEN_ASSOCIATION)
            if balance < required:
                issues.append(shortfall)
            if allowance < required:
                issues.append(Diagnostic.INSUFFICIENT_ALLOWANCE)
        required_hbar = max(budgets.values()) if len(budgets) == 2 else None
        if required_hbar is not None and hbar < required_hbar:
            issues.append(Diagnostic.INSUFFICIENT_SENDER_HBAR)
        issues = list(dict.fromkeys(issues))
        if Diagnostic.MISSING_TOKEN_ASSOCIATION in issues:
            reason = Diagnostic.MISSING_TOKEN_ASSOCIATION
        else:
            reason = issues[0] if issues else None
        self._check(worker, Check.SENDER, Outcome.FAIL if reason else
                    Outcome.PASS if required_hbar is not None else Outcome.UNKNOWN, reason,
                    hbar_tinybars=hbar, required_hbar_tinybars=required_hbar, tokens=tokens, diagnostics=issues)
        return (reason, True) if reason else None

    async def _preflight_market(self, engine, public, worker):
        try:
            pool, book, completed = await engine._read()
            self._check(worker, Check.HEDERA, Outcome.PASS if pool.router_verified else Outcome.UNKNOWN)
            observations = engine._observations(pool, book, completed)
            market = engine._market(pool, book, observations, self.now())
            self._check(worker, Check.KUCOIN, Outcome.PASS, source=book.source)
            self._check(worker, Check.WEBSOCKET,
                        Outcome.PASS if book.source == "websocket" else Outcome.DEGRADED)
            self._check(worker, Check.REST, Outcome.PASS if book.source == "rest" else Outcome.UNKNOWN)
            self._check(worker, Check.SNAPSHOTS, Outcome.PASS,
                        dex=observations[0].model_dump(mode="json"), cex=observations[1].model_dump(mode="json"))
        except (InvalidCycle, MarketDataError, TimeoutError, ExceptionGroup) as error:
            reason = engine._failure(error)
            diagnostic = {
                Reason.MARKET_IDENTITY_MISMATCH: Diagnostic.MARKET_IDENTITY_MISMATCH,
                Reason.MARKET_UNAVAILABLE: Diagnostic.MARKET_UNAVAILABLE,
            }.get(reason, Diagnostic.MARKET_UNVERIFIED)
            errors = error.exceptions if isinstance(error, ExceptionGroup) else (error,)
            if reason != Reason.MARKET_IDENTITY_MISMATCH:
                if any(isinstance(item, ProviderRateLimited) for item in errors):
                    diagnostic = Diagnostic.PROVIDER_RATE_LIMITED
                elif any(isinstance(item, (ProviderTimeout, TimeoutError)) for item in errors):
                    diagnostic = Diagnostic.PROVIDER_TIMEOUT
            self._check(worker, Check.SNAPSHOTS, Outcome.FAIL if reason == Reason.MARKET_IDENTITY_MISMATCH
                        else Outcome.UNKNOWN, diagnostic)
            return diagnostic, reason == Reason.MARKET_IDENTITY_MISMATCH

        failures = []
        budgets = {}
        for index, (side, check) in enumerate(((Side.BUY, Check.BUY_GAS), (Side.SELL, Check.SELL_GAS))):
            gas = pool.gas[index] if len(pool.gas) == 2 else None
            try:
                if (gas is None or gas.route_evidence is None or gas.route_evidence.rpc is None
                        or gas.route_evidence.rpc.sender != engine.dex.estimate_sender or not pool.router_verified):
                    raise InvalidCycle(Reason.GAS_UNKNOWN)
                budgets[side], _, evidence_id = engine._gas(gas, side, market, self.now())
            except InvalidCycle:
                value = gas.estimation_error if gas else None
                diagnostic = (Diagnostic(value) if value in Diagnostic._value2member_map_
                              else Diagnostic.ROUTE_ESTIMATE_INVALID)
                critical = diagnostic in CRITICAL_GAS
                failures.append((diagnostic, critical))
                self._check(worker, check, Outcome.FAIL if critical else Outcome.UNKNOWN, diagnostic)
            else:
                self._check(worker, check, Outcome.PASS, evidence_id=evidence_id,
                            gas_units=gas.execution_gas, capped_tinybars=budgets[side])
        sender_failure = await self._sender_check(engine, public, worker, pool, budgets)
        sender = self.readiness["checks"][Check.SENDER]
        if "tokens" in sender and any(reason == Diagnostic.SENDER_ACCOUNT_NOT_FOUND for reason, _ in failures):
            failures = [(Diagnostic.PROVIDER_DISAGREEMENT, False)
                        if reason == Diagnostic.SENDER_ACCOUNT_NOT_FOUND else (reason, critical)
                        for reason, critical in failures]
            for check in (Check.BUY_GAS, Check.SELL_GAS):
                if self.readiness["checks"][check]["reason"] == Diagnostic.SENDER_ACCOUNT_NOT_FOUND:
                    self._check(worker, check, Outcome.UNKNOWN, Diagnostic.PROVIDER_DISAGREEMENT)
        if sender_failure:
            failures.append(sender_failure)
        if failures:
            return next((failure for failure in failures if failure[1]), failures[0])
        try:
            engine._timing(pool, book, self.now())
            for index, side in enumerate((Side.BUY, Side.SELL)):
                engine._gas(pool.gas[index], side, market, self.now())
        except InvalidCycle:
            for check in (Check.SNAPSHOTS, Check.BUY_GAS, Check.SELL_GAS):
                self._check(worker, check, Outcome.UNKNOWN, Diagnostic.MARKET_UNVERIFIED)
            return Diagnostic.MARKET_UNVERIFIED, False

        with self.storage.transaction() as tx:
            required = {
                Holding.DEX_USDC: ceil(pool.quotes[0].quote_units * (1 + engine.slippage)),
                Holding.CEX_USDT: ceil(market.cex_quotes[0].quote_units * (1 + engine.slippage)),
                Holding.DEX_WHBAR: pool.quotes[1].base_units,
                Holding.CEX_HBAR: market.cex_quotes[1].base_units,
                Holding.DEX_HBAR: MAX_ATTEMPTS * max(budgets.values()),
            }
            funded = engine._available(tx, required)
        self._check(worker, Check.BALANCES, Outcome.PASS if funded else Outcome.FAIL,
                    None if funded else Diagnostic.INSUFFICIENT_VIRTUAL_BALANCE)
        return None if funded else (Diagnostic.INSUFFICIENT_VIRTUAL_BALANCE, True)

    async def _startup(self, engine, public, worker, config):
        self.readiness = {"session_id": self.session_id, "attempt": 1,
                          "checks": {check: {"outcome": Outcome.UNKNOWN, "reason": None, "at": None}
                                     for check in Check}}
        self._readiness_state(public, engine, worker, ReadinessState.STARTING)
        try:
            config = Config.model_validate_json(config.model_dump_json())
            self.now()
        except ValueError:
            self._check(worker, Check.CONFIGURATION, Outcome.FAIL, Diagnostic.INVALID_CONFIGURATION)
            self._block(public, engine, worker, Diagnostic.INVALID_CONFIGURATION)
        self._check(worker, Check.CONFIGURATION, Outcome.PASS)
        try:
            consistent = self._storage_check()
        except (sqlite3.Error, ValueError, KeyError):
            consistent = False
        self._check(worker, Check.STORAGE, Outcome.PASS if consistent else Outcome.FAIL,
                    None if consistent else Diagnostic.STORAGE_INCONSISTENT)
        if not consistent:
            self._block(public, engine, worker, Diagnostic.STORAGE_INCONSISTENT)
        self._observe("opening")
        await self._recover(engine)
        if self._shutdown.is_set() or not self.tick(worker):
            return False
        with self.storage.transaction() as tx:
            unresolved = tx.unresolved_simulation(NETWORK)
            holds = tx._db.execute("SELECT 1 FROM reservations LIMIT 1").fetchone()
        if unresolved or holds:
            self._check(worker, Check.RECOVERY, Outcome.FAIL, Diagnostic.RECOVERY_UNRESOLVED)
            self._block(public, engine, worker, Diagnostic.RECOVERY_UNRESOLVED)
        self._check(worker, Check.RECOVERY, Outcome.PASS)
        if not config.simulation.hedera_estimate_sender:
            for check in (Check.SENDER, Check.BUY_GAS, Check.SELL_GAS):
                self._check(worker, check, Outcome.FAIL, Diagnostic.SENDER_UNCONFIGURED)
            self._block(public, engine, worker, Diagnostic.SENDER_UNCONFIGURED)

        for retry in range(len(STARTUP_RETRY_DELAYS) + 1):
            if not await self._network_slot(worker, reserve=True):
                return False
            self.readiness["attempt"] = retry + 1
            for check in (Check.HEDERA, Check.KUCOIN, Check.WEBSOCKET, Check.REST, Check.SNAPSHOTS,
                          Check.SENDER, Check.BUY_GAS, Check.SELL_GAS, Check.BALANCES):
                self.readiness["checks"][check] = {"outcome": Outcome.UNKNOWN, "reason": None, "at": None}
            self._readiness_state(public, engine, worker, ReadinessState.PREFLIGHT_CHECKING)
            failure = await self._preflight_market(engine, public, worker)
            if self._shutdown.is_set() or not self.tick(worker):
                return False
            if failure is None:
                self._readiness_state(public, engine, worker, ReadinessState.READY)
                return True
            reason, critical = failure
            if critical or retry == len(STARTUP_RETRY_DELAYS):
                self._block(public, engine, worker, reason, exhausted=not critical)
            base = STARTUP_RETRY_DELAYS[retry]
            now = self.now()
            with self.storage.transaction() as tx:
                earliest = datetime.fromisoformat(self._saved_health(tx)["preflight_network_not_before"])
                deadline = datetime.fromisoformat(self.latest(tx)["deadline"])
            remaining = max(0, (deadline - self._effective(now)).total_seconds())
            delay = max(base + self.jitter(0, base / 5), public.cooldown_seconds(),
                        (earliest - now).total_seconds())
            next_at = now + timedelta(seconds=delay) if delay < remaining else None
            self._readiness_state(public, engine, worker, ReadinessState.WAITING_RETRY, reason=reason,
                                  next_at=next_at)
            await self.sleep(min(delay, remaining))
        return False

    async def _cycles(self, engine, public, worker_id, config):
        if not await self._startup(engine, public, worker_id, config):
            return
        if not await self._network_slot(worker_id) or self._shutdown.is_set():
            return
        self._readiness_state(public, engine, worker_id, ReadinessState.RUNNING)
        self.log.emit(Event.WORKER_STATE, worker=worker_id, session=self.session_id)
        while not self._shutdown.is_set() and self.tick(worker_id):
            cycle = await engine.worker(worker_id).analyze()
            severity = Level.WARNING if cycle.dex_snapshot is None or cycle.cex_snapshot is None else Level.INFO
            if self.monitor is None:
                self.log.emit(Event.ANALYSIS, severity, worker=worker_id, cycle=cycle.id)
            else:
                self._observe("cycle_finished", cycle)
                self.log.emit(Event.NETWORK_SAMPLE, Level.DEBUG, worker=worker_id, session=self.session_id,
                              cycle=cycle.id, http_retries=public.retries,
                              cache_hits=engine.dex.cache_hits + engine.cex.cache_hits)
            self._health(public, engine, cycle)
            await self._recover(engine)
            if any(item.reason == Reason.MARKET_IDENTITY_MISMATCH for item in cycle.evaluations):
                self._critical_cycle = Diagnostic.MARKET_IDENTITY_MISMATCH
            if self._critical_cycle:
                self._block(public, engine, worker_id, self._critical_cycle)
            with self.storage.transaction() as tx:
                if tx.unresolved_simulation(NETWORK):
                    if not self._recovery_allowed(tx, self._shutdown):
                        return
                    self._critical_cycle = Diagnostic.RECOVERY_UNRESOLVED
            if self._critical_cycle:
                self._block(public, engine, worker_id, self._critical_cycle)
            await self.sleep(MIN_INTERVAL.total_seconds())

    def _observe(self, method, *args):
        if self.monitor is None:
            return
        try:
            getattr(self.monitor, method)(*args)
        except Exception:
            # An unavailable observer cannot interrupt recovery or reproduce a trade.
            self.log.emit(Event.STORAGE_STATUS, Level.WARNING, session=self.session_id)

    async def _recover(self, engine):
        self._observe("recovery_start")
        return await engine.recover()

    async def run(self, worker_id, config, shutdown):
        self.monitor = Monitor(self, worker_id) if self.log.sink else None
        try:
            await self._run(worker_id, config, shutdown)
        finally:
            self._observe("summary", shutdown.is_set())
            if self.monitor:
                try:
                    with self.storage.transaction() as tx:
                        health = self._saved_health(tx)
                        if health and "recent_events" in health:
                            while (line := self.log.take()) is not None:
                                health["recent_events"].append(json.loads(line))
                            health["recent_events"] = health["recent_events"][-256:]
                            health["console_drops"] = self.log.sink.dropped
                            health["console_failures"] = self.log.sink.failed
                            tx._db.execute("INSERT OR REPLACE INTO runtime_health VALUES (1, ?)", (json.dumps(health),))
                except (sqlite3.Error, OSError, ValueError):
                    pass

    async def _run(self, worker_id, config, shutdown):
        self.readiness = None
        self._critical_cycle = None
        self._shutdown = shutdown

        def before_request():
            if shutdown.is_set() or not self.tick(worker_id):
                raise Unavailable("analysis session inactive")

        async with aiohttp.ClientSession(trust_env=False) as session:
            public = PublicConnections(session, clock=self.now)
            public.before_request = before_request
            engine = SimulationEngine.from_public(self.storage, config.simulation, public)
            engine.session_id = self.session_id
            if self.monitor:
                self._observe("startup", config, engine)
                engine.on_cycle_start = lambda cycle: self._observe("cycle_start", cycle)
                engine.on_cycle_cancel = lambda cycle: self._observe("cycle_finished", cycle)
                engine.on_journal_commit = lambda execution: self._observe("journal", execution)

            def admission_gate(tx, worker, now):
                if shutdown.is_set():
                    return Reason.WORKER_INACTIVE
                reason = self.gate(tx, worker, now)
                if reason:
                    return reason
                readiness = self._saved_health(tx).get("readiness") or {}
                if (readiness.get("session_id") != self.session_id
                        or readiness.get("state") != ReadinessState.RUNNING):
                    return Reason.READINESS_BLOCKED
                return None

            engine.gate = admission_gate
            engine.market_gate = lambda tx, market, now: self._cycle_market_gate(engine, tx, market, now)
            if not self.tick(worker_id):
                await self._drain_expired(engine, public, shutdown)
                return
            engine.recovery_gate = lambda tx: self._recovery_allowed(tx, shutdown)
            engine.cex.on_source_change = lambda source, reason: self.log.emit(
                Event.CONNECTION_STATUS, worker=worker_id, session=self.session_id, source=source, reason=reason,
            )
            stream = asyncio.create_task(engine.cex.depth_stream.run())
            watch = asyncio.create_task(self._watch(worker_id, shutdown))
            cycles = asyncio.create_task(self._cycles(engine, public, worker_id, config))
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
                self.log.emit(Event.WORKER_STATE, worker=worker_id, session=self.session_id)
                if self.readiness and self.readiness["state"] != ReadinessState.STARTUP_BLOCKED:
                    self._readiness_state(public, engine, worker_id, ReadinessState.CANCELLED)
                else:
                    self._health(public, engine)
            await self._drain_expired(engine, public, shutdown)

    async def _drain_expired(self, engine, public, shutdown):
        with self.storage.transaction() as tx:
            drain = self._recovery_allowed(tx, shutdown, expired=True)
        if drain:
            engine.recovery_gate = lambda tx: self._recovery_allowed(tx, shutdown, expired=True)
            await self._recover(engine)
            self._health(public, engine)


async def foreground(runtime, worker_id, config, *, log_level=Level.INFO):
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    sink = ConsoleSink(sys.stderr, log_level)
    runtime.log.sink = sink
    try:
        await runtime.run(worker_id, config, shutdown)
    finally:
        sink.close()
        runtime.log.sink = None
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
