"""Best-effort foreground stderr sink for closed operational events."""

import json
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Event, Thread

from .operational_log import Level

LEVELS = tuple(Level)
LABELS = {
    "startup": "SYSTEM", "configuration": "CONFIG", "readiness_state": "HEALTH",
    "readiness_check": "HEALTH", "connection_status": "KUCOIN", "analysis_start": "ARB",
    "analysis": "ARB", "evaluation": "ARB", "simulated_execution": "SIMULATION",
    "balance": "BALANCE", "lifecycle": "SYSTEM", "session_summary": "SYSTEM",
}


def render(record):
    at = datetime.fromisoformat(record["at"])
    event = record["event"]
    prefix = f"[{at:%H:%M:%S}.{at.microsecond // 1000:03d}] {record['level'].upper()} [{LABELS.get(event, 'SYSTEM')}]"
    labels = {
        "startup": f"Starting SIMULATION ONLY ({at:%Y-%m-%d} UTC); foreground logger active; real trading disabled",
        "configuration": "Applied simulation configuration",
        "readiness_state": record.get("state", "UNKNOWN"),
        "readiness_check": "Readiness check",
        "analysis_start": "Analysis started",
        "analysis": "Analysis completed",
        "evaluation": "Direction evaluated",
        "simulated_execution": "SIMULATED execution",
        "balance": "Persisted balance",
        "connection_status": "KuCoin source changed",
        "lifecycle": "Session lifecycle",
        "session_summary": "Session summary",
    }
    fields = " ".join(f"{key}={record[key] if record[key] is not None else 'UNKNOWN'}"
                      for key in record if key not in {"at", "level", "event", "state"})
    return f"{prefix} {labels.get(event, event)}{(' ' + fields) if fields else ''}"


class ConsoleSink:
    """A blocked stream can exhaust only this queue and this daemon thread."""

    def __init__(self, stream, level=Level.INFO, *, capacity=256):
        self.stream = stream
        self.level = LEVELS.index(level)
        self.queue = Queue(maxsize=capacity)
        self.closed = Event()
        self.dropped = 0
        self.failed = 0
        self._thread = Thread(target=self._drain, name="arbitrage-console", daemon=True)
        self._thread.start()

    def offer(self, line, level):
        if LEVELS.index(level) < self.level:
            return
        try:
            self.queue.put_nowait(line)
        except Full:
            self.dropped += 1

    def _drain(self):
        outstanding = 0
        while not self.closed.is_set() or not self.queue.empty():
            try:
                line = self.queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                if missing := self.dropped + self.failed - outstanding:
                    at = datetime.fromisoformat(json.loads(line)["at"])
                    print(f"[{at:%H:%M:%S}.{at.microsecond // 1000:03d}] WARNING [SYSTEM] "
                          f"Console sink resumed; {missing} event(s) lost", file=self.stream, flush=True)
                    outstanding += missing
                print(render(json.loads(line)), file=self.stream, flush=True)
            except Exception:
                self.failed += 1

    def close(self):
        self.closed.set()
        self._thread.join(timeout=0.25)
