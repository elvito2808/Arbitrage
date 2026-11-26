import json

import pytest

from arbitrage.operational_log import Event, Level, OperationalLog


def test_structured_closed_schema_and_bounded_queue():
    log = OperationalLog(capacity=1)
    marker = "test-only-sensitive-value"
    assert not log.emit(marker)
    assert not log.emit(Event.WORKER_STATE, marker)
    assert log.emit(Event.WORKER_STATE, Level.WARNING, arbitrary=marker)
    assert not log.emit(Event.STORAGE_STATUS)
    record = log.take()
    assert marker not in record
    payload = json.loads(record)
    assert payload == {"at": payload["at"], "event": "worker_state", "level": "warning"}
    assert log.take() is None
    assert log.emit(Event.STORAGE_STATUS)


@pytest.mark.parametrize("capacity", [0, 4097, True])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError):
        OperationalLog(capacity)
