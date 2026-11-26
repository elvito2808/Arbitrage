"""Bounded in-memory structured logging with a closed output schema."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from queue import Empty, Full, Queue


class Event(StrEnum):
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
        self._buffer: Queue[str] = Queue(maxsize=capacity)

    def emit(self, event: object, level: object = Level.INFO, **details: object) -> bool:
        # Free-form text, exception messages and metadata never reach the sink.
        if type(event) is not Event or type(level) is not Level:
            return False
        line = json.dumps(
            {"at": datetime.now(UTC).isoformat(), "event": event.value, "level": level.value}
        )
        try:
            self._buffer.put_nowait(line)
        except Full:
            return False
        return True

    def take(self) -> str | None:
        """Take one JSON record for an external consumer, if available."""
        try:
            return self._buffer.get_nowait()
        except Empty:
            return None
