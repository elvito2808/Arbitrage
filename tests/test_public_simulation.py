"""Public read-shaped integration through virtual admission and restart recovery."""

import asyncio
import socket
import sqlite3
from datetime import timedelta

import pytest

from arbitrage.config import load_config
from arbitrage.market_data import Unavailable
from arbitrage.models import EvaluationStatus, Holding, Reason
from arbitrage.runtime import Runtime
from arbitrage.simulation import SimulationEngine
from arbitrage.storage import Storage
from test_market_data import Exchange, Hedera, POOL_TIME
from test_simulation import CONFIG, admit, state


class RecordedMarket(Hedera, Exchange):
    def __init__(self, price):
        super().__init__()
        self.price = price
        self.price_reads = 0
        self.depth = {
            "sequence": "61",
            "time": int(POOL_TIME.timestamp() * 1000),
            "asks": [["0.05510", "2000"]],
            "bids": [["0.05500", "2000"]],
        }

    async def rpc(self, method, *params):
        if method == "eth_gasPrice":
            assert not params
            self.price_reads += 1
            if self.price is None:
                raise Unavailable("recorded price unavailable")
            return hex(self.price)
        return await super().rpc(method, *params)


@pytest.mark.parametrize("price", [1_540_000_000_000, None])
def test_public_replay_reservation_restart_and_reconciliation(tmp_path, monkeypatch, price):
    def reject_socket(*args, **kwargs):
        pytest.fail("unexpected socket connection")

    monkeypatch.setattr(socket.socket, "connect", reject_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_socket)
    config = load_config(CONFIG)
    settings = config.simulation
    store = Storage(tmp_path / "simulation.db")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(config.balances.as_balances())
    market = RecordedMarket(price)
    opening = state(store)
    engine = SimulationEngine.from_public(store, settings, market)
    unready = admit(engine)
    assert all(item.reason == Reason.READINESS_BLOCKED for item in unready.evaluations)
    assert market.price_reads == 0 and state(store) == opening
    with store.transaction() as tx:
        assert tx.analysis(unready.id) is None
    engine.gate = lambda *_: None
    cycle = admit(engine)

    assert market.price_reads == 1
    if price is None:
        assert all(item.reason in (Reason.GAS_UNKNOWN, Reason.GAS_INVALID)
                   for item in cycle.evaluations)
        assert state(store) == opening
        return

    eligible, = (item for item in cycle.evaluations if item.status == EvaluationStatus.ELIGIBLE)
    assert eligible.gas_evidence_id
    for item in cycle.evaluations:
        assert str(item.estimated_gas_hbar) == "0.16859612"
        assert str(item.estimated_gas_usdc) == "0.009290"
        assert str(item.gas_cost_usdt) == "0.018580"
        assert item.gas_budget_tinybars == 33_719_224
    assert state(store)[2:] == (1, 3)
    assert state(store)[1] == opening[1]

    restarted = SimulationEngine.from_public(Storage(store.path), settings, market)

    async def advance(seconds):
        market.now += timedelta(seconds=seconds)

    restarted._sleep = advance
    assert asyncio.run(restarted.recover()) == "filled"
    assert asyncio.run(restarted.recover()) is None
    balances, ledger, trades, reservations = state(store)
    assert trades == 1 and reservations == 0
    for holding, before, after in zip(Holding, opening[0], balances, strict=True):
        assert after.total_units == before.total_units + sum(
            entry.delta_units for entry in ledger if entry.holding == holding
        )
    assert balances != opening[0]
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("emergency", [False, True])
def test_expired_session_drains_prior_admission_only(tmp_path, monkeypatch, emergency):
    market = RecordedMarket(1_540_000_000_000)
    config = load_config(CONFIG)
    clock = [market.now, 0.0]
    runtime = Runtime(tmp_path, clock=lambda: clock[0], timer=lambda: clock[1])
    runtime.initialize(config)
    engine = SimulationEngine.from_public(runtime.storage, config.simulation, market)
    create_engine = lambda *_: engine
    monkeypatch.setattr("arbitrage.runtime.SimulationEngine.from_public", create_engine)

    async def advance(seconds):
        market.now += timedelta(seconds=seconds)

    engine._sleep = advance
    with runtime.owner():
        runtime.activate("start", "worker-1", config)
        engine.gate = lambda *_: None
        cycle = asyncio.run(engine.worker("worker-1").analyze())
        assert any(item.status == EvaluationStatus.ELIGIBLE for item in cycle.evaluations)
        assert state(runtime.storage)[2:] == (1, 3)
        clock[0] += timedelta(hours=24)
        if emergency:
            runtime.control("emergency")
            assert runtime.activate("recover", None) is None
            assert state(runtime.storage)[2:] == (1, 3)
            return
        activation = runtime.activate("recover", None)
        assert activation is not None
        asyncio.run(runtime.run(*activation, asyncio.Event()))
        assert runtime.status()["session"]["state"] == "expired"
        assert runtime.status()["analysis_cycles"] == 1

    assert state(runtime.storage)[2:] == (1, 0)
    assert runtime.activate("recover", None) is None
