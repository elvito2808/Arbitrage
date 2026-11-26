"""Independent production-shaped replay against the foreground runtime."""

import asyncio
import json
import socket
import sqlite3
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from arbitrage.config import load_config
from arbitrage.kucoin import KuCoin
from arbitrage.market_data import KUCOIN, RPC
from arbitrage.models import EvaluationStatus, Holding
from arbitrage.runtime import Runtime
from arbitrage.simulation import SimulationEngine

from test_market_data import POOL_TIME
from test_p07_integration import ReplaySession, Response


CONFIG = Path(__file__).resolve().parents[2] / "config" / "simulation.toml"
SENDER = "0x000000000000000000000000000000000000aa55"
USDC = 0x6F89A
WHBAR = 0x163B5A
GAS_BUY = 156_000
GAS_SELL = 153_192
ROUTER = "0x00000000000000000000000000000000002e7a5d"
DEPTH_TOPIC = "/spotMarket/level2Depth5:HBAR-USDT"


class PublicReplay(ReplaySession):
    def __init__(self, *, reject_gas=False):
        super().__init__()
        self.estimates = []
        self.expect_token = "ephemeral-public-bootstrap-fixture"
        self.reject_gas = reject_gas
        self.websocket_closed = False
        self.corrupt = asyncio.Event()

    @asynccontextmanager
    async def request(self, method, url, *, json, allow_redirects):
        if url == RPC and json["method"] == "eth_estimateGas":
            assert not allow_redirects
            assert method == "POST"
            self.requests.append((method, url))
            call = json["params"][0]
            assert call["to"] == ROUTER
            assert call["from"] == SENDER
            assert "value" not in call or int(call["value"], 16) == 0
            selector = call["data"][:10]
            abi = [int(call["data"][i:i + 64], 16) for i in range(10, len(call["data"]), 64)]
            assert len(abi) == 8
            assert abi[0] == 100_000_000_000
            assert abi[2:4] == [160, int(SENDER, 16)]
            assert abi[4] > int(POOL_TIME.timestamp())
            assert abi[5] == 2
            if selector == "0x8803dbee":
                assert abi[1] == 53_025_571 and abi[6:] == [USDC, WHBAR]
                gas = GAS_BUY
            else:
                assert selector == "0x38ed1739"
                assert abi[1] == 52_155_926 and abi[6:] == [WHBAR, USDC]
                gas = GAS_SELL
            self.estimates.append(selector)
            response = {"jsonrpc": "2.0", "id": json["id"]}
            if self.reject_gas:
                response["error"] = {"code": 3, "message": "fixture estimate reverted"}
            else:
                response["result"] = hex(gas)
            yield Response(response)
            return
        if url == f"{KUCOIN}/api/v1/bullet-public":
            assert method == "POST"
            self.requests.append((method, url))
            yield Response({
                "code": "200000",
                "data": {
                    "token": self.expect_token,
                    "instanceServers": [{
                        "endpoint": "wss://ws-api-spot.kucoin.com",
                        "pingInterval": 18000, "pingTimeout": 10000,
                    }],
                },
            })
            return
        async with super().request(method, url, json=json, allow_redirects=allow_redirects) as response:
            yield response

    async def ws_connect(self, endpoint, *, params, **options):
        assert endpoint == "wss://ws-api-spot.kucoin.com"
        assert params["token"] == self.expect_token
        connect_id = params["connectId"]
        await asyncio.sleep(0)
        return SocketReplay(self, connect_id)


class SocketReplay:
    def __init__(self, session, connect_id):
        self.session = session
        self.connect_id = connect_id
        self.delivered = 0

    async def receive_json(self):
        self.delivered += 1
        if self.delivered == 1:
            return {"type": "welcome", "id": self.connect_id}
        if self.delivered == 2:
            return {"type": "ack", "id": self.connect_id}
        if self.delivered == 3:
            return {
                "type": "message", "topic": DEPTH_TOPIC, "subject": "level2",
                "data": {
                    "timestamp": int(POOL_TIME.timestamp() * 1000) + 700,
                    "bids": [["0.05500", "300"], ["0.05490", "1000"]],
                    "asks": [["0.05510", "1500"]],
                },
            }
        await self.session.corrupt.wait()
        return {"type": "message", "topic": "/spotMarket/level2Depth5:BTC-USDT", "data": {}}

    async def send_json(self, payload):
        assert payload == {
            "id": self.connect_id, "type": "subscribe", "topic": DEPTH_TOPIC,
            "privateChannel": False, "response": True,
        }

    async def close(self):
        self.session.websocket_closed = True


def test_independent_foreground_websocket_and_route_gas_reconcile(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("expected deterministic transport")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    fake = PublicReplay()
    monkeypatch.setattr("arbitrage.runtime.aiohttp.ClientSession", lambda **kwargs: fake)

    settings = load_config(CONFIG)
    config = settings.model_copy(update={"simulation": settings.simulation.model_copy(
        update={"hedera_estimate_sender": SENDER},
    )})
    clock = [POOL_TIME + timedelta(seconds=1)]
    before_recovery = asyncio.Event()
    completed_sleep = asyncio.Event()
    release_watch = asyncio.Event()

    async def runtime_sleep(delay):
        if delay == 0.25:
            await release_watch.wait()
        else:
            completed_sleep.set()
            await asyncio.Future()

    async def delay_dex(delay):
        before_recovery.set()
        await asyncio.Future()

    monkeypatch.setattr(SimulationEngine, "_sleep", staticmethod(delay_dex))
    runtime = Runtime(tmp_path, clock=lambda: clock[0], sleep=runtime_sleep)
    runtime.initialize(config)

    def snapshot_balances(db):
        return dict(db.execute("SELECT holding, total_units FROM balances"))

    def assert_integrity(db):
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    with sqlite3.connect(runtime.storage.path) as db:
        opening = snapshot_balances(db)

    async def run():
        shutdown = asyncio.Event()
        with runtime.owner():
            activation = runtime.activate("start", "worker-1", config)
            worker = asyncio.create_task(runtime.run(*activation, shutdown))
            try:
                await asyncio.wait_for(before_recovery.wait(), 3)
                with sqlite3.connect(runtime.storage.path) as db:
                    assert snapshot_balances(db) == opening
                    assert db.execute("SELECT COUNT(*) FROM reservations").fetchone() == (3,)
                    assert db.execute(
                        "SELECT units FROM reservations WHERE holding = ?", (Holding.DEX_HBAR.value,),
                    ).fetchone() == (3 * GAS_BUY * 326,)
                    assert db.execute("SELECT COUNT(*) FROM ledger").fetchone() == (0,)
                    assert_integrity(db)
                status = runtime.status()
                assert status["health"]["kucoin_source"] == "websocket"
                assert not any("/orderbook/level2_20" in url for _, url in fake.requests)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        assert fake.websocket_closed

        async def advance(delay):
            clock[0] += timedelta(seconds=delay)

        monkeypatch.setattr(SimulationEngine, "_sleep", staticmethod(advance))
        restarted = Runtime(tmp_path, clock=lambda: clock[0], sleep=runtime_sleep)
        with restarted.owner():
            activation = restarted.activate("recover", None)
            worker = asyncio.create_task(restarted.run(*activation, shutdown))
            try:
                await asyncio.wait_for(completed_sleep.wait(), 3)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
    gas_tinybars = GAS_BUY * 163
    assert set(fake.estimates[:2]) == {
        "0x8803dbee", "0x38ed1739",
    }
    expected = {
        Holding.DEX_HBAR.value: -gas_tinybars,
        Holding.DEX_WHBAR.value: 100_000_000_000,
        Holding.DEX_USDC.value: -52_761_762,
        Holding.CEX_HBAR.value: -100_000_000_000,
        Holding.CEX_USDT.value: 54_875_070,
    }
    with sqlite3.connect(runtime.storage.path) as db:
        assert_integrity(db)
        final = snapshot_balances(db)
        assert {key: final[key] - initial for key, initial in opening.items()} == expected
        assert db.execute("SELECT COUNT(*) FROM reservations").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM ledger").fetchone() == (5,)
        deltas = dict(db.execute("SELECT holding, SUM(delta_units) FROM ledger GROUP BY holding"))
        assert deltas == expected
        assert db.execute("SELECT status FROM simulated_trades").fetchall() == [("filled",)]
        payload, = db.execute("SELECT payload FROM analysis_cycles ORDER BY rowid LIMIT 1").fetchone()
        evaluations = json.loads(payload)["evaluations"]
        eligible = [item for item in evaluations if item["status"] == EvaluationStatus.ELIGIBLE]
        assert len(eligible) == 1
        assert eligible[0]["direction"] == "buy_dex"
        assert eligible[0]["requested_cex"]["quote_units"] == 54_875_070


def test_independent_foreground_websocket_and_failed_route_estimates_do_not_post(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("expected deterministic transport")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    fake = PublicReplay(reject_gas=True)
    monkeypatch.setattr("arbitrage.runtime.aiohttp.ClientSession", lambda **kwargs: fake)
    settings = load_config(CONFIG)
    config = settings.model_copy(update={"simulation": settings.simulation.model_copy(
        update={"hedera_estimate_sender": SENDER},
    )})
    cycle_finished = asyncio.Event()

    async def block_after_cycle(delay):
        if delay != 0.25:
            cycle_finished.set()
        await asyncio.Future()

    runtime = Runtime(tmp_path, clock=lambda: POOL_TIME + timedelta(seconds=1), sleep=block_after_cycle)
    runtime.initialize(config)
    with sqlite3.connect(runtime.storage.path) as db:
        opening = db.execute("SELECT * FROM balances ORDER BY holding").fetchall()

    async def run():
        with runtime.owner():
            activation = runtime.activate("start", "worker-1", config)
            worker = asyncio.create_task(runtime.run(*activation, asyncio.Event()))
            try:
                await asyncio.wait_for(cycle_finished.wait(), 3)
                assert runtime.status()["health"]["kucoin_source"] == "websocket"
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
    assert set(fake.estimates) == {"0x8803dbee", "0x38ed1739"}
    assert fake.websocket_closed
    with sqlite3.connect(runtime.storage.path) as db:
        assert db.execute("SELECT * FROM balances ORDER BY holding").fetchall() == opening
        for table in ("simulated_trades", "simulation_events", "reservations", "ledger", "executions"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
        payload, = db.execute("SELECT payload FROM analysis_cycles").fetchone()
        evaluations = json.loads(payload)["evaluations"]
        assert {item["reason"] for item in evaluations} <= {"gas_unknown", "gas_invalid"}
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_independent_disconnected_snapshot_cannot_be_admitted(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("expected deterministic transport")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    fake = PublicReplay()
    monkeypatch.setattr("arbitrage.runtime.aiohttp.ClientSession", lambda **kwargs: fake)
    settings = load_config(CONFIG)
    config = settings.model_copy(update={"simulation": settings.simulation.model_copy(
        update={"hedera_estimate_sender": SENDER},
    )})
    cycle_finished = asyncio.Event()
    snapshot = KuCoin.snapshot

    async def captured_then_invalidated(adapter):
        book = await snapshot(adapter)
        assert book.source == "websocket"
        fake.corrupt.set()
        while adapter.depth_stream.generation == book.generation:
            await asyncio.sleep(0)
        return book

    monkeypatch.setattr(KuCoin, "snapshot", captured_then_invalidated)

    async def block_after_cycle(delay):
        if delay != 0.25:
            cycle_finished.set()
        await asyncio.Future()

    runtime = Runtime(tmp_path, clock=lambda: POOL_TIME + timedelta(seconds=1), sleep=block_after_cycle)
    runtime.initialize(config)
    with sqlite3.connect(runtime.storage.path) as db:
        opening = db.execute("SELECT * FROM balances ORDER BY holding").fetchall()

    async def run():
        with runtime.owner():
            activation = runtime.activate("start", "worker-1", config)
            worker = asyncio.create_task(runtime.run(*activation, asyncio.Event()))
            try:
                await asyncio.wait_for(cycle_finished.wait(), 3)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
    assert set(fake.estimates) == {"0x8803dbee", "0x38ed1739"}
    assert fake.websocket_closed
    with sqlite3.connect(runtime.storage.path) as db:
        assert db.execute("SELECT * FROM balances ORDER BY holding").fetchall() == opening
        for table in ("simulated_trades", "reservations", "ledger", "analyzed_snapshots"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
        payload, = db.execute("SELECT payload FROM analysis_cycles").fetchone()
        evaluations = json.loads(payload)["evaluations"]
        assert {item["reason"] for item in evaluations} == {"invalid_market"}
