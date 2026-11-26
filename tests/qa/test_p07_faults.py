"""Offline transport, timing and local persistence fault injection."""

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest

from arbitrage.config import parse_config
from arbitrage.market_data import MarketDataError, PublicConnections, StaleSnapshot, require_fresh
from arbitrage.storage import Storage
from test_coordinator import CONFIG

NOW = datetime(2026, 11, 26, tzinfo=UTC)


def test_transport_retries_timeout_and_reset_then_accepts_response():
    attempts = []
    sleeps = []

    class Response:
        status = 200
        headers = {}

        async def json(self):
            return {"jsonrpc": "2.0", "id": 1, "result": "0x127"}

    class Session:
        @asynccontextmanager
        async def request(self, method, url, **options):
            attempts.append(options["json"])
            if len(attempts) == 1:
                raise TimeoutError
            if len(attempts) == 2:
                raise aiohttp.ClientConnectionError("synthetic reset")
            yield Response()

    async def sleep(delay):
        sleeps.append(delay)

    public = PublicConnections(Session(), clock=lambda: NOW, sleep=sleep,
                               jitter=lambda low, high: 0.2)
    assert asyncio.run(public.rpc("eth_chainId")) == "0x127"
    assert [payload["id"] for payload in attempts] == [1, 1, 1]
    assert sleeps == [0.2, 0.4] and public.retries == 2


def test_malformed_json_and_rpc_result_are_rejected_without_retries():
    class Response:
        status = 200
        headers = {}
        malformed = True

        async def json(self):
            if self.malformed:
                raise ValueError("arbitrary provider error")
            return {"jsonrpc": "2.0", "id": 2}

    response = Response()

    class Session:
        @asynccontextmanager
        async def request(self, method, url, **options):
            yield response

    async def read():
        public = PublicConnections(Session(), clock=lambda: NOW)
        with pytest.raises(MarketDataError, match="invalid public JSON"):
            await public.rpc("eth_chainId")
        response.malformed = False
        with pytest.raises(MarketDataError, match="RPC read failed"):
            await public.rpc("eth_chainId")
        assert public.retries == 0

    asyncio.run(read())


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(microseconds=1)])
def test_cache_source_cutoff_at_ten_seconds(offset):
    source = NOW - timedelta(seconds=10) - offset
    if offset:
        with pytest.raises(StaleSnapshot):
            require_fresh(source, NOW, NOW)
    else:
        require_fresh(source, NOW, NOW)


def test_sqlite_writer_lock_fails_before_transaction_body(tmp_path, monkeypatch):
    store = Storage(tmp_path / "lock.sqlite")
    store.initialize()
    original = sqlite3.connect
    with original(store.path) as owner:
        owner.execute("BEGIN IMMEDIATE")

        def connect(path, *, timeout, isolation_level):
            return original(path, timeout=0, isolation_level=isolation_level)

        monkeypatch.setattr("arbitrage.storage.sqlite3.connect", connect)
        with pytest.raises(sqlite3.OperationalError, match="locked"), store.transaction():
            pytest.fail("locked writer entered the transaction body")
    with store.transaction() as tx:
        assert tx.ledger() == ()


@pytest.mark.parametrize("setting", ["fixture_scope", "verify_fixture", "live_trading"])
def test_config_rejects_fixture_or_live_controls(setting):
    content = CONFIG.read_bytes().replace(b'[balances]', f'{setting} = true\n\n[balances]'.encode())
    with pytest.raises(ValueError, match="invalid simulation configuration"):
        parse_config(content)
