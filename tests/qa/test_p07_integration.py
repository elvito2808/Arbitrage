"""Production adapters and CLI tested with recorded shapes and a fake HTTP session."""

import asyncio
import json
import socket
import sqlite3
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook

from arbitrage import cli
from arbitrage.config import load_config
from arbitrage.kucoin import KuCoin
from arbitrage.market_data import MarketDataError, PublicConnections
from arbitrage.runtime import Runtime
from test_market_data import POOL_TIME, Exchange, Hedera

CONFIG = Path(__file__).resolve().parents[2] / "config" / "simulation.toml"


class Response:
    status = 200
    headers = {}

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class ReplaySession:
    def __init__(self, incorrect_symbol=False):
        self.hedera = Hedera()
        self.exchange = Exchange()
        self.exchange.depth["time"] = int(POOL_TIME.timestamp()) * 1000
        self.incorrect_symbol = incorrect_symbol
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    @asynccontextmanager
    async def request(self, method, url, *, json, allow_redirects):
        assert not allow_redirects
        self.requests.append((method, url))
        if url == "https://mainnet.hashio.io/api":
            assert method == "POST"
            assert json["method"] in {"eth_chainId", "eth_getBlockByNumber", "eth_call", "eth_gasPrice"}
            result = await self.hedera.rpc(json["method"], *json["params"])
            body = {"jsonrpc": "2.0", "id": json["id"], "result": result}
        elif url.startswith("https://mainnet-public.mirrornode.hedera.com/api/v1/blocks/"):
            assert method == "GET"
            body = await self.hedera.mirror_block(int(url.rsplit("/", 1)[1]))
        else:
            assert method == "GET"
            resource = {
                "https://api.kucoin.com/api/v2/symbols/HBAR-USDT": "symbol",
                "https://api.kucoin.com/api/v1/market/orderbook/level2_20?symbol=HBAR-USDT": "depth",
            }[url]
            data = await self.exchange.kucoin(resource)
            if resource == "symbol":
                data = {**data, "symbol": "BTC-USDT" if self.incorrect_symbol else "HBAR-USDT",
                        "baseCurrency": "BTC" if self.incorrect_symbol else "HBAR",
                        "quoteCurrency": "USDT"}
            body = {"code": "200000", "data": data}
        yield Response(body)


def test_wrong_kucoin_symbol_is_rejected():
    session = ReplaySession(incorrect_symbol=True)
    public = PublicConnections(session, clock=lambda: POOL_TIME + timedelta(seconds=1))
    with pytest.raises(MarketDataError):
        asyncio.run(KuCoin(public).snapshot())


def test_recorded_shapes_through_foreground_and_cli(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        pytest.fail("unexpected socket connection")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    data = tmp_path / "state"
    args = ["--data-dir", str(data)]
    assert cli.main([*args, "init", "--config", str(CONFIG)]) == 0
    session = ReplaySession()
    monkeypatch.setattr("arbitrage.runtime.aiohttp.ClientSession", lambda **options: session)
    now = POOL_TIME + timedelta(seconds=1)
    persisted = asyncio.Event()

    class SingleCycleRuntime(Runtime):
        def _health(self, public, engine, cycle=None):
            super()._health(public, engine, cycle)
            if cycle is not None:
                persisted.set()

    controlled = asyncio.Event()

    async def sleep(seconds):
        if seconds == 0.25:
            await controlled.wait()
        else:
            await asyncio.Future()

    runtime = SingleCycleRuntime(data, clock=lambda: now, sleep=sleep)
    config = load_config(CONFIG)

    def balances():
        with runtime.storage.transaction() as tx:
            return tx._db.execute("SELECT * FROM balances ORDER BY holding").fetchall()

    opening = [tuple(row) for row in balances()]

    async def cycle_then_pause():
        with runtime.owner():
            activation = runtime.activate("start", "worker-2", config)
            task = asyncio.create_task(runtime.run(*activation, asyncio.Event()))
            try:
                await asyncio.wait_for(persisted.wait(), 2)
                runtime.control("paused")
                controlled.set()
                await asyncio.wait_for(task, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(cycle_then_pause())
    assert session.requests
    assert runtime.status()["session"]["state"] == "paused"
    with runtime.owner():
        assert runtime.activate("recover", None) is None
        assert runtime.activate("resume", None, config)[0] == "worker-2"
    assert cli.main([*args, "stop"]) == 0
    assert cli.main([*args, "status"]) == 0
    assert cli.main([*args, "report"]) == 0
    assert cli.main([*args, "emergency-stop"]) == 0
    assert runtime.status()["emergency_latched"]
    assert [tuple(row) for row in balances()] == opening
    with sqlite3.connect(runtime.storage.path) as db:
        payload, = db.execute("SELECT payload FROM analysis_cycles").fetchone()
        evaluations = json.loads(payload)["evaluations"]
        assert {item["direction"] for item in evaluations} == {"buy_dex", "sell_dex"}
        assert {(item["status"], item["reason"]) for item in evaluations} == {
            ("market_candidate", "gas_unknown"),
        }
        assert all(item["requested_dex"]["base_units"] == 100_000_000_000 for item in evaluations)
        for table in ("simulated_trades", "simulation_events", "reservations", "ledger", "executions"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    with (data / "report.xlsx").open("rb") as stream:
        book = load_workbook(stream)
        assert len(list(book["Observations"].values)) == 3
        assert all(row[7:9] == (0, 0) for row in list(book["Balances"].values)[1:])
        book.close()
