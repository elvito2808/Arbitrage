"""Independent correct-behavior assertions for RC1. Run explicitly via reproduce.sh."""

import asyncio
import json
import os
import socket
from pathlib import Path

import pytest
from openpyxl import load_workbook
from conftest import funded_sender  # noqa: F401

from arbitrage.config import load_config
from arbitrage.coordinator import NETWORK
from arbitrage.models import Holding
from arbitrage.readiness import StartupBlocked
from arbitrage.reporting import export, reference
from arbitrage.coordinator import OpportunityCoordinator
from arbitrage.simulation import Attempt, Effect, Event, Scenario, SimulationEngine
from arbitrage.storage import Storage
from test_console_logging import capture
from test_coordinator import CONFIG
from test_public_simulation import RecordedMarket
from test_startup_readiness import startup  # noqa: F401


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("offline reproduction attempted network access")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def record(name, *, stream=None, **fields):
    directory = os.environ.get("QA_REPRO_CAPTURE_DIR")
    if directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{name}.json").write_text(
            json.dumps({"mode": "offline synthetic", **fields}, indent=2) + "\n", encoding="utf-8",
        )
        if stream is not None:
            (root / f"{name}.txt").write_text(
                "OFFLINE SYNTHETIC QA-REPRO-01 EVIDENCE\n" + stream.getvalue(), encoding="utf-8",
            )


def counts(store):
    tables = ("analysis_cycles", "simulated_trades", "reservations", "ledger")
    with store.transaction() as tx:
        return {table: tx._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}


@pytest.mark.parametrize("outage", [True, False], ids=["outage", "profitable"])
def test_qa_r4_01_direct_requires_readiness(tmp_path, outage):
    config = load_config(CONFIG)
    store = Storage(tmp_path / "direct.sqlite3")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(config.balances.as_balances())
    provider = RecordedMarket(150_000)
    provider.down = outage
    engine = SimulationEngine.from_public(store, config.simulation, provider)
    worker = engine.worker("worker-1")
    worker.start()

    async def run():
        assert await engine.recover() is None
        return await worker.analyze()

    try:
        cycle = asyncio.run(run())
    except StartupBlocked:
        cycle = None
    actual = counts(store)
    record("r4-01-direct-outage" if outage else "r4-01-direct-profitable", counts=actual,
           reasons=[item.reason for item in cycle.evaluations] if cycle else [], estimator_calls=len(provider.estimates))
    assert actual == dict.fromkeys(actual, 0)


def test_qa_r4_01_foreground_outage_control(startup, monkeypatch):
    startup[0].down = True
    runtime, stream, _ = capture(startup, monkeypatch, blocked=True)
    actual = counts(runtime.storage)
    readiness = runtime.status()["readiness"]["state"]
    record("r4-01-foreground-outage", stream=stream, counts=actual, readiness=readiness)
    assert readiness == "STARTUP_BLOCKED"
    assert actual == dict.fromkeys(actual, 0)


def test_qa_r4_02_operational_worker_unready(startup, monkeypatch):
    startup[0].timeout_attempts = {1, 2, 3, 4}
    observed = []

    def observe(runtime, status):
        observed.append({"readiness": status["readiness"]["state"], "intent": status["session"]["state"],
                         "worker": status["workers"]["worker-1"]})

    runtime, stream, _ = capture(startup, monkeypatch, blocked=True, change=observe)
    observe(runtime, runtime.status())
    record("r4-02-worker-status", stream=stream, observations=observed, counts=counts(runtime.storage))
    assert [item["readiness"] for item in observed] == ["WAITING_RETRY"] * 3 + ["STARTUP_BLOCKED"]
    assert counts(runtime.storage)["analysis_cycles"] == 0
    assert all(item["worker"] != "running" for item in observed), observed


def test_qa_r4_03_preserve_valid_data_when_sell_gas_invalid(startup, monkeypatch):
    verified = []
    validate = OpportunityCoordinator._market

    def observe_market(engine, pool, book, observations, now):
        market = validate(engine, pool, book, observations, now)
        if startup[0].reject is not None:
            verified.append({
                "quote_base_units": [quote.base_units for quote in (*pool.quotes, *market.cex_quotes)],
                "quote_counter_units": [quote.quote_units for quote in (*pool.quotes, *market.cex_quotes)],
                "trusted_buy": engine.dex.verifies_route_gas(pool.gas[0], pool),
                "valid_sell": pool.gas[1].cost_valid,
            })
        return market

    monkeypatch.setattr(OpportunityCoordinator, "_market", observe_market)

    def inject(runtime, status):
        if status["readiness"]["state"] == "READY":
            startup[0].reject = "0x38ed1739"

    runtime, stream, _ = capture(startup, monkeypatch, change=inject)
    with runtime.storage.transaction() as tx:
        row, = tx._db.execute("SELECT id FROM analysis_cycles").fetchall()
        cycle = tx.analysis(row[0])
    buy, sell = cycle.evaluations
    events = [event for event in runtime.status()["health"]["recent_events"]
              if event["event"] == "evaluation" and event.get("cycle") == cycle.id]
    observations = [
        {"direction": item.direction, "reason": item.reason,
         "dex_quote_units": item.requested_dex.quote_units if item.requested_dex else None,
         "cex_quote_units": item.requested_cex.quote_units if item.requested_cex else None,
         "indicative_usdt": str(item.indicative_profit_usdt) if item.indicative_profit_usdt is not None else None,
         "gas_observed": item.gas_rpc is not None}
        for item in (buy, sell)
    ]
    record("r4-03-route-data", stream=stream, evaluations=observations, structured_events=events,
           counts=counts(runtime.storage), estimate_selectors=startup[0].estimates, validated_market=verified)
    validated, = verified
    assert validated["quote_base_units"] == [100_000_000_000] * 4
    assert validated["trusted_buy"] and not validated["valid_sell"]
    assert counts(runtime.storage)["simulated_trades"] == 0
    assert counts(runtime.storage)["reservations"] == 0
    assert len(events) == 2
    assert all(item["dex_quote_units"] and item["cex_quote_units"]
               and item["indicative_usdt"] is not None for item in observations), observations
    assert buy.gas_rpc is not None
    assert sell.gas_rpc is None and sell.net_profit_usdt is None
    for event in events:
        assert event["dex_usdc"] and event["cex_usdt"] and event["indicative_usdt"] is not None
    assert "gas_observed_hbar=UNKNOWN" in stream.getvalue()


def test_qa_r4_04_unknown_cex_fee_is_unknown(startup, monkeypatch, tmp_path):
    startup[0].depth.update(asks=[["0.05510", "2000"]], bids=[["0.05500", "2000"]])
    scenario = Scenario(cex=(Attempt(effect=Effect.UNKNOWN),))
    runtime, stream, _ = capture(startup, monkeypatch, scenario=scenario, blocked=True)
    with runtime.storage.transaction() as tx:
        trade = tx.unresolved_simulation(NETWORK)
        events = tuple(Event.model_validate_json(row) for row in tx.simulation_events(trade["id"]))
        ledger = {entry.holding: entry.delta_units for entry in tx.ledger()}
    event = events[-1]
    xlsx = tmp_path / "test-only.xlsx"
    export(runtime.storage.path, xlsx)
    with xlsx.open("rb") as file:
        workbook = load_workbook(file, read_only=True, data_only=True)
        rows = list(workbook["Execution events"].values)
        workbook.close()
    result, = [row for row in rows[1:] if row[1] == reference(event.id)]
    excel_fee = result[9]
    line, = [line for line in stream.getvalue().splitlines()
             if "venue=cex" in line and "phase=result result=unknown" in line]
    record("r4-04-unknown-fee", stream=stream, outcome=event.outcome, raw_fee_units=event.fee_units,
           holds={hold.holding: hold.units for hold in event.remaining}, ledger=ledger, excel_fee=excel_fee,
           console_fee=line.split("fee_usdt=")[1].split()[0], status=trade["status"])
    assert event.outcome == Effect.UNKNOWN and trade["status"] == "manual_review"
    assert {hold.holding: hold.units for hold in event.remaining} == {Holding.CEX_HBAR: 100_000_000_000}
    assert Holding.CEX_HBAR not in ledger and Holding.CEX_USDT not in ledger and not event.ledger
    assert excel_fee is None or str(excel_fee).upper() == "UNKNOWN"
    assert "fee_usdt=UNKNOWN" in line, line


def test_qa_r4_05_chain_mismatch_fails_hedera_check(startup, monkeypatch):
    startup[0].chain = "0x128"
    runtime, stream, _ = capture(startup, monkeypatch, blocked=True)
    status = runtime.status()["readiness"]
    actual = counts(runtime.storage)
    record("r4-05-chain-mismatch", stream=stream, readiness=status["state"],
           reason=status["reason"], hedera=status["checks"]["hedera"],
           snapshots=status["checks"]["snapshots"], counts=actual)
    assert status["state"] == "STARTUP_BLOCKED" and actual == dict.fromkeys(actual, 0)
    check = status["checks"]["hedera"]
    assert (check["outcome"], check["reason"], bool(check["at"])) == ("FAIL", "market_identity_mismatch", True)
    assert any("check=hedera" in line and "outcome=FAIL" in line for line in stream.getvalue().splitlines())
