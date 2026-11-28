"""Production adapters and CLI tested with recorded shapes and a fake HTTP session."""

import asyncio
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
from arbitrage.readiness import StartupBlocked
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
            body = {"jsonrpc": "2.0", "id": json["id"]}
            assert json["method"] in {"eth_chainId", "eth_getBlockByNumber", "eth_call", "eth_gasPrice"}
            body["result"] = ("0x0" if json["method"] == "eth_gasPrice" and self.hedera.gas_down
                              else await self.hedera.rpc(json["method"], *json["params"]))
        elif url == "https://api.kucoin.com/api/v1/bullet-public":
            assert method == "POST"
            body = {"code": "400100", "data": {}}
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
    session.hedera.gas_down = True
    monkeypatch.setattr("arbitrage.runtime.aiohttp.ClientSession", lambda **options: session)
    now = POOL_TIME + timedelta(seconds=1)
    async def advance(delay):
        nonlocal now
        if delay == 0.25:
            await asyncio.Future()
        now += timedelta(seconds=delay)
        session.hedera.source = now - timedelta(seconds=1)
        session.exchange.depth["time"] = int(session.hedera.source.timestamp() * 1000)
        await asyncio.sleep(0)

    runtime = Runtime(data, clock=lambda: now, sleep=advance, jitter=lambda *_: 0)
    config = load_config(CONFIG)

    def balances():
        with runtime.storage.transaction() as tx:
            return tx._db.execute("SELECT * FROM balances ORDER BY holding").fetchall()

    opening = [tuple(row) for row in balances()]
    with runtime.owner():
        activation = runtime.activate("start", "worker-2", config)
        with pytest.raises(StartupBlocked):
            asyncio.run(runtime.run(*activation, asyncio.Event()))
        readiness = runtime.status()["readiness"]
        assert readiness["state"] == "STARTUP_BLOCKED"
        assert readiness["reason"] == "gas_price_invalid"
        assert readiness["checks"]["buy_gas"]["outcome"] == "UNKNOWN"
        assert readiness["checks"]["sell_gas"]["outcome"] == "UNKNOWN"
        with pytest.raises(StartupBlocked):
            runtime.activate("recover", None)
    assert session.requests
    assert cli.main([*args, "stop"]) == 0
    assert cli.main([*args, "status"]) == 0
    assert cli.main([*args, "report"]) == 0
    assert cli.main([*args, "emergency-stop"]) == 0
    assert runtime.status()["emergency_latched"]
    assert [tuple(row) for row in balances()] == opening
    with sqlite3.connect(runtime.storage.path) as db:
        for table in ("analysis_cycles", "analyzed_snapshots", "simulated_trades", "simulation_events",
                      "reservations", "ledger", "executions"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    with (data / "report.xlsx").open("rb") as stream:
        book = load_workbook(stream)
        assert len(list(book["Observations"].values)) == 1
        assert all(row[7:9] == (0, 0) for row in list(book["Balances"].values)[1:])
        book.close()
