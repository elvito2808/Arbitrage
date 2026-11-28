"""Offline Mirror request, provenance, and readiness diagnostics."""

import asyncio
import json
from collections import deque
from datetime import timedelta

import pytest

from arbitrage.market_data import MIRROR, MarketDataError, PublicConnections
from arbitrage.operational_log import Level
from arbitrage.readiness import Diagnostic
from arbitrage.saucerswap import SaucerSwap
from test_console_logging import capture
from test_market_data import BLOCK, HASH, Hedera
from test_startup_readiness import startup  # noqa: F401


class Response:
    def __init__(self, result, status, headers):
        self.result = result
        self.status = status
        self.headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.result


class MirrorReplay(Hedera):
    def __init__(self, replies):
        super().__init__()
        self.replies = deque(replies)
        self.calls = []
        self.delays = []
        self.transport = PublicConnections(
            self, clock=self.clock, sleep=self.sleep, jitter=lambda *_: .2,
        )

    def request(self, method, url, *, json, allow_redirects):
        assert method == "GET" and json is None and not allow_redirects
        assert url.startswith(MIRROR)
        self.calls.append(url.removeprefix(MIRROR))
        return Response(*self.replies.popleft())

    @property
    def retries(self):
        return self.transport.retries

    @retries.setter
    def retries(self, value):
        self.transport.retries = value

    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += timedelta(seconds=delay)

    async def mirror_block(self, number):
        return await self.transport.mirror_block(number)

    async def mirror_head(self):
        return await self.transport.mirror_head()


def response(data, status=200, headers=None):
    return data, status, headers or {}


def block(api, **fields):
    return {
        "number": BLOCK, "hash": (HASH[2:] + "cd" * 16).upper(),
        "timestamp": {"to": f"{int(api.source.timestamp())}.617000001"},
        **fields,
    }


def test_proven_index_delay_retries_the_same_block():
    api = MirrorReplay([])
    api.replies.extend((
        response({}, 404), response({"blocks": [{"number": BLOCK - 1}]}), response(block(api)),
    ))
    snap = asyncio.run(SaucerSwap(api).snapshot())
    assert snap.block == BLOCK and snap.block_hash == HASH
    assert api.delays == [.25] and api.retries == 1
    assert api.calls == [f"/blocks/{BLOCK}", "/blocks?limit=1&order=desc", f"/blocks/{BLOCK}"]


@pytest.mark.parametrize(("indexed", "age", "category"), [
    (BLOCK, 1, Diagnostic.BLOCK_NOT_FOUND),
    (BLOCK - 1, 10, Diagnostic.MIRROR_INDEXING_DELAY),
])
def test_missing_block_categories_keep_the_freshness_budget(indexed, age, category):
    api = MirrorReplay([response({}, 404), response({"blocks": [{"number": indexed}]})])
    api.now = api.source + timedelta(seconds=age)
    with pytest.raises(MarketDataError) as caught:
        asyncio.run(SaucerSwap(api).snapshot())
    failure = caught.value.failure
    assert (failure.category, failure.http_status, failure.expected_block, failure.observed_block) == (
        category, 404, BLOCK, indexed,
    )
    assert api.delays == []


@pytest.mark.parametrize(("change", "category", "field", "hash_class"), [
    ({"number": BLOCK + 1}, Diagnostic.BLOCK_NUMBER_MISMATCH, "number", None),
    ({"hash": "0x" + "ab" * 32 + "zz" * 16}, Diagnostic.INVALID_PROVIDER_RESPONSE, "hash",
     "invalid_mirror_48_byte_hash"),
    ({"hash": "0x" + "ee" * 48}, Diagnostic.BLOCK_HASH_MISMATCH, "hash", "prefix_mismatch"),
    ({"timestamp": {"to": 0}}, Diagnostic.INVALID_PROVIDER_RESPONSE, "timestamp.to", None),
    ({"timestamp": {"to": "0.000000000"}}, Diagnostic.BLOCK_TIMESTAMP_MISMATCH, "timestamp.to", None),
])
def test_invalid_block_does_not_poll(change, category, field, hash_class):
    api = MirrorReplay([])
    api.replies.append(response(block(api, **change)))
    with pytest.raises(MarketDataError) as caught:
        asyncio.run(SaucerSwap(api).snapshot())
    failure = caught.value.failure
    assert (failure.category, failure.field, failure.hash_class) == (category, field, hash_class)
    assert api.calls == [f"/blocks/{BLOCK}"]


def test_mirror_transport_rejects_a_non_object_envelope():
    api = MirrorReplay([response(["payload omitted from diagnostics"])])
    with pytest.raises(MarketDataError) as caught:
        asyncio.run(SaucerSwap(api).snapshot())
    assert caught.value.failure.as_dict() == {
        "provider": "hedera_mirror", "operation": "block_by_number", "category": "invalid_provider_response",
        "http_status": 200, "expected_block": BLOCK, "field": "response",
    }


def test_mirror_http_throttle_is_not_indexing_delay():
    api = MirrorReplay([response({}, 429, {"Retry-After": "9"})])
    with pytest.raises(MarketDataError) as caught:
        asyncio.run(SaucerSwap(api).snapshot())
    failure = caught.value.failure
    assert (failure.category, failure.http_status, failure.expected_block) == (
        Diagnostic.PROVIDER_RATE_LIMITED, 429, BLOCK,
    )
    assert api.calls == [f"/blocks/{BLOCK}"] and not api.delays


def test_stale_mirror_reports_ages():
    api = MirrorReplay([])
    api.now = api.source + timedelta(seconds=11)
    api.replies.append(response(block(api)))
    with pytest.raises(MarketDataError) as caught:
        asyncio.run(SaucerSwap(api).snapshot())
    assert caught.value.failure.category == Diagnostic.STALE_SNAPSHOT
    assert caught.value.failure.source_age_ms == 10383
    assert caught.value.failure.receipt_age_ms == 0


def test_malformed_mirror_propagates_to_console_and_persisted_status(startup, monkeypatch):
    replay = startup[0]
    original = replay.mirror_block
    private_payload = "do not include this response value"

    async def malformed(number):
        return {**await original(number), "timestamp": {"to": [private_payload]}}

    monkeypatch.setattr(replay, "mirror_block", malformed)
    runtime, stream, _ = capture(startup, monkeypatch, blocked=True, level=Level.DEBUG)
    status = runtime.status()
    readiness = status["readiness"]
    assert status["runtime_state"] == "stopped" and status["session_intent"] == "running"
    assert status["analysis_cycles"] == status["simulated_trades"] == 0
    assert readiness["state"] == "STARTUP_BLOCKED"
    assert readiness["reason"] == Diagnostic.INVALID_PROVIDER_RESPONSE
    checks = readiness["checks"]
    assert checks["hedera_rpc"]["outcome"] == checks["kucoin_rest"]["outcome"] == "PASS"
    assert checks["hedera_mirror"]["outcome"] == "FAIL"
    failure, = readiness["market_failures"]
    assert failure["provider"] == "hedera_mirror"
    assert failure["operation"] == "block_match"
    assert failure["field"] == "timestamp.to" and failure["http_status"] == 200
    assert failure["expected_block"] == failure["observed_block"]
    output = stream.getvalue()
    assert "Hedera Mirror · match RPC block" in output
    assert '"field": "timestamp.to"' in output and '"expected_block":' in output
    assert private_payload not in output and private_payload not in json.dumps(status)
