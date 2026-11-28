"""Bounded in-memory structured logging with a closed output schema."""

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from decimal import Decimal
from queue import Empty, Full, Queue


from .market_data import MarketFailure
from .models import GAS_SOURCE, Direction, EvaluationStatus, Holding, Reason, Venue
from .readiness import Check, Diagnostic, Outcome, ReadinessState


class Event(StrEnum):
    READINESS_STATE = "readiness_state"
    READINESS_CHECK = "readiness_check"
    STARTUP = "startup"
    CONFIGURATION = "configuration"
    ANALYSIS_START = "analysis_start"
    EVALUATION = "evaluation"
    SIMULATED_EXECUTION = "simulated_execution"
    BALANCE = "balance"
    LIFECYCLE = "lifecycle"
    NETWORK_SAMPLE = "network_sample"
    MARKET_FAILURE = "market_failure"
    SESSION_SUMMARY = "session_summary"
    ANALYSIS = "analysis"
    WORKER_STATE = "worker_state"
    CONNECTION_STATUS = "connection_status"
    STORAGE_STATUS = "storage_status"
    EXPERIMENT_STATUS = "experiment_status"


class Level(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


NUMBERS = {
    Event.CONFIGURATION: ("capacity", "active_limit", "chain_id", "trade_hbar", "profit_usdt", "loss_usdt",
                          "slippage", "fee", "gas_cap", "ttl_seconds", "interval_seconds",
                          "buy_gas_units", "sell_gas_units", "gas_limit"),
    Event.ANALYSIS_START: ("number",),
    Event.ANALYSIS: ("number", "duration_ms"),
    Event.EVALUATION: ("number", "trade_hbar", "dex_usdc", "cex_usdt", "gas_observed_hbar",
                       "gas_observed_usdt", "gas_estimated_hbar", "gas_estimated_usdt", "gas_price_weibars",
                       "gas_capped_hbar", "gas_capped_usdt", "indicative_usdt",
                       "net_usdt", "stressed_usdt"),
    Event.SIMULATED_EXECUTION: ("attempt", "filled_hbar", "fill_usdc", "fill_usdt", "fee_usdt", "gas_hbar",
                                "trade_hbar", "net_usdt", "stressed_usdt", "delta_usdc", "delta_usdt", *Holding),
    Event.BALANCE: ("previous", "delta", "total", "reserved", "available"),
    Event.SESSION_SUMMARY: ("cycles", "admissions", "filled", "failed", "incomplete", "manual_review",
                            "active", "cycle_errors", "console_drops", "duration_seconds", *Holding),
    Event.READINESS_STATE: ("delay_seconds",),
    Event.READINESS_CHECK: ("chain_id", "router_number", "gas_units", "gas_price_weibars", "capped_tinybars",
                            "estimated_gas_hbar", "maximum_gas_hbar"),
    Event.NETWORK_SAMPLE: ("http_retries", "cache_hits"),
}
ENUMS = {
    Event.EVALUATION: {"gas_method": (GAS_SOURCE,), "direction": tuple(Direction), "status": tuple(EvaluationStatus), "reason": tuple(Reason),
                       "gas_error": tuple(Diagnostic), "source": ("websocket", "rest"), "dex_side": ("buy", "sell"), "cex_side": ("buy", "sell"),
                       "admission": ("admitted", "declined", "not_eligible"),
                       "guard": (*Reason, "unresolved_network_execution", "cycle_fence_lost",
                                 "risk_revalidation_failed", "insufficient_credit_headroom")},
    Event.SIMULATED_EXECUTION: {"direction": tuple(Direction), "venue": tuple(Venue),
                                "phase": ("admitted", "attempt", "result", "review", "terminal", "recovery"),
                                "result": ("fill", "revert", "timeout", "failure", "partial", "unknown",
                                           "active", "filled", "failed", "incomplete", "manual_review")},
    Event.BALANCE: {"holding": tuple(Holding), "phase": ("opening", "commit"),
                    "reconciliation": ("PASS", "FAIL", "UNKNOWN")},
    Event.LIFECYCLE: {"intent": ("paused", "stopped", "expired", "emergency", "shutdown", "running",
                                "startup_blocked", "interrupted")},
    Event.ANALYSIS: {"result": ("admitted", "not_admitted"), "source": ("websocket", "rest"),
                     "conclusion": ("no_opportunity",)},
    Event.READINESS_CHECK: {"gas_method": (GAS_SOURCE,), "source": ("websocket", "rest"), "pair": ("WHBAR-USDC",),
                            "rpc": ("PASS",), "mirror": ("PASS",)},
    Event.CONFIGURATION: {"gas_method": (GAS_SOURCE,), "network": ("hedera:295",), "market": ("HBAR-USDT",),
                          "dex_market": ("WHBAR-USDC",), "mode": ("simulation",)},
}


class OperationalLog:
    """Producers never perform sink I/O or wait for space in the buffer."""

    def __init__(self, capacity: int = 256, *, clock=None):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("log capacity must be between 1 and 4096")
        self.dropped = 0
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sink = None
        self._buffer: Queue[str] = Queue(maxsize=capacity)

    def emit(self, event: object, level: object = Level.INFO, **details: object) -> bool:
        # Free-form text, exception messages and metadata never reach the sink.
        if type(event) is not Event or type(level) is not Level:
            return False
        record = {"at": self.clock().astimezone(UTC).isoformat(), "event": event.value, "level": level.value}
        worker, cycle = details.get("worker"), details.get("cycle")
        if type(worker) is str and worker in ("worker-1", "worker-2", "worker-3"):
            record["worker"] = worker
        if type(cycle) is str and re.fullmatch(r"[a-f0-9]{32}", cycle):
            record["cycle"] = cycle
        for key in ("session", "execution"):
            value = details.get(key)
            if type(value) is str and re.fullmatch(r"[a-f0-9]{32}", value):
                record[key] = value
        for key in NUMBERS.get(event, ()):
            if key in details:
                value = details[key]
                if type(value) is int and abs(value) < 10**25:
                    record[key] = value
                elif type(value) is Decimal and value.is_finite() and -30 <= value.adjusted() <= 25:
                    record[key] = format(value, "f")
                elif value is None:
                    record[key] = None
        for key, allowed in ENUMS.get(event, {}).items():
            value = details.get(key)
            if type(value) in (str, Direction, EvaluationStatus, Holding, Reason, Venue) and value in allowed:
                record[key] = str(value)
            elif key in details and value is None:
                record[key] = None
        if event in (Event.READINESS_CHECK, Event.MARKET_FAILURE):
            failure = details.get("market_failure")
            if type(failure) is MarketFailure:
                record["market_failure"] = failure.as_dict()
        if event is Event.READINESS_CHECK:
            if type(pool := details.get("pool")) is str and re.fullmatch(r"0x[0-9a-f]{40}", pool):
                record["pool"] = pool
        if event is Event.SIMULATED_EXECUTION:
            recorded_at = details.get("recorded_at")
            if type(recorded_at) is datetime and recorded_at.tzinfo is UTC:
                record["recorded_at"] = recorded_at.isoformat()
        if event is Event.CONFIGURATION:
            if type(details.get("parity")) is bool:
                record["parity"] = details["parity"]
            if type(deadline := details.get("deadline")) is datetime and deadline.tzinfo is UTC:
                record["deadline"] = deadline.isoformat()
        if event is Event.CONNECTION_STATUS:
            source, reason = details.get("source"), details.get("reason")
            if type(source) is str and source in ("websocket", "rest", "unavailable"):
                record["source"] = source
            if type(reason) is str and reason in (
                "fresh_depth", "no_fresh_depth", "websocket_unavailable", "stale_depth",
                "invalid_depth", "stream_stopped",
            ):
                record["reason"] = reason
        if event in (Event.READINESS_STATE, Event.READINESS_CHECK):
            session = details.get("session")
            if type(session) is str and re.fullmatch(r"[a-f0-9]{32}", session):
                record["session"] = session
            for key, enum in (("state", ReadinessState), ("check", Check),
                              ("outcome", Outcome), ("diagnostic", Diagnostic)):
                if type(value := details.get(key)) is enum:
                    record[key] = value.value
            attempt = details.get("attempt")
            if type(attempt) is int and attempt > 0:
                record["attempt"] = attempt
            if event is Event.READINESS_STATE and details.get("retry_exhausted") is True:
                record["retry_exhausted"] = True
            next_at = details.get("next_attempt_at")
            if type(next_at) is datetime and next_at.tzinfo is UTC:
                record["next_attempt_at"] = next_at.isoformat()
        line = json.dumps(record)
        if self.sink is not None:
            self.sink.offer(line, level)
        try:
            self._buffer.put_nowait(line)
        except Full:
            self.dropped += 1
            return False
        return True

    def take(self) -> str | None:
        """Take one JSON record for an external consumer, if available."""
        try:
            return self._buffer.get_nowait()
        except Empty:
            return None
