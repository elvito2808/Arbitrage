"""Bounded in-memory structured logging with a closed output schema."""

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from queue import Empty, Full, Queue


class Event(StrEnum):
    ANALYSIS = "analysis"
    WORKER_STATE = "worker_state"
    CONNECTION_STATUS = "connection_status"
    STORAGE_STATUS = "storage_status"
    EXPERIMENT_STATUS = "experiment_status"


class Level(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class OperationalLog:
    """Producers never perform sink I/O or wait for space in the buffer."""

    def __init__(self, capacity: int = 256):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("log capacity must be between 1 and 4096")
        self.dropped = 0
        self._buffer: Queue[str] = Queue(maxsize=capacity)

    def emit(self, event: object, level: object = Level.INFO, **details: object) -> bool:
        # Free-form text, exception messages and metadata never reach the sink.
        if type(event) is not Event or type(level) is not Level:
            return False
        record = {"at": datetime.now(UTC).isoformat(), "event": event.value, "level": level.value}
        worker, cycle = details.get("worker"), details.get("cycle")
        if type(worker) is str and worker in ("worker-1", "worker-2", "worker-3"):
            record["worker"] = worker
        if type(cycle) is str and re.fullmatch(r"[a-f0-9]{32}", cycle):
            record["cycle"] = cycle
        if event is Event.CONNECTION_STATUS:
            source, reason = details.get("source"), details.get("reason")
            if type(source) is str and source in ("websocket", "rest", "unavailable"):
                record["source"] = source
            if type(reason) is str and reason in (
                "fresh_depth", "no_fresh_depth", "websocket_unavailable", "stale_depth",
                "invalid_depth", "stream_stopped",
            ):
                record["reason"] = reason
        line = json.dumps(record)
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
