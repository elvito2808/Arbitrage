"""Public read-shaped integration through virtual admission and restart recovery."""

import asyncio
import socket
import sqlite3
from datetime import timedelta

import pytest

from arbitrage.config import load_config
from arbitrage.market_data import Unavailable
from arbitrage.models import EvaluationStatus, Holding, Reason
from arbitrage.simulation import SimulationEngine
from arbitrage.storage import Storage
from test_market_data import Exchange, Hedera, POOL_TIME
from test_simulation import CONFIG, admit, state


class RecordedMarket(Hedera, Exchange):
    def __init__(self, estimate):
        super().__init__()
        self.estimate = estimate
        self.estimates = []
        self.depth = {
            "sequence": "61",
            "time": int(POOL_TIME.timestamp() * 1000),
            "asks": [["0.05510", "2000"]],
            "bids": [["0.05500", "2000"]],
        }

    async def rpc(self, method, *params):
        if method == "eth_estimateGas":
            self.estimates.append(params[0])
            if self.estimate is None:
                raise Unavailable("recorded estimate reverted")
            return hex(self.estimate)
        return await super().rpc(method, *params)


@pytest.mark.parametrize("estimate", [150_000, None])
def test_public_replay_reservation_restart_and_reconciliation(tmp_path, monkeypatch, estimate):
    def reject_socket(*args, **kwargs):
        pytest.fail("unexpected socket connection")

    monkeypatch.setattr(socket.socket, "connect", reject_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_socket)
    config = load_config(CONFIG)
    settings = config.simulation.model_copy(update={
        "hedera_estimate_sender": "0x0000000000000000000000000000000000001234",
    })
    store = Storage(tmp_path / "simulation.db")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(config.balances.as_balances())
    market = RecordedMarket(estimate)
    opening = state(store)
    cycle = admit(SimulationEngine.from_public(store, settings, market))

    assert len(market.estimates) == 2
    assert len({request["data"][:10] for request in market.estimates}) == 2
    if estimate is None:
        assert all(item.reason in (Reason.GAS_UNKNOWN, Reason.GAS_INVALID)
                   for item in cycle.evaluations)
        assert state(store) == opening
        return

    eligible, = (item for item in cycle.evaluations if item.status == EvaluationStatus.ELIGIBLE)
    assert eligible.gas_evidence_id
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
