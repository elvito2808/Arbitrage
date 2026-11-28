"""CLM foreground acceptance tests using offline public and journal replays."""

import asyncio
import io
import json
import os
import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Event as ThreadEvent

import pytest

from arbitrage import cli
from arbitrage.console import ConsoleSink, render
from arbitrage.coordinator import NETWORK
from arbitrage.kucoin import KuCoin
from arbitrage.models import Holding
from arbitrage.operational_log import Event, Level, OperationalLog
from arbitrage.readiness import StartupBlocked
from arbitrage.runtime import Runtime
from arbitrage.simulation import Attempt, Effect, Scenario, SimulationEngine
from test_market_data import BLOCK, POOL_TIME
from test_startup_readiness import startup  # noqa: F401


def evidence(name, stream):
    if directory := os.environ.get("CLM_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.txt").write_text("OFFLINE SYNTHETIC TEST EVIDENCE\n" + stream.getvalue())


def capture(startup, monkeypatch, *, limit=1, blocked=False, scenario=None, level=Level.INFO, action="start",
            change=None, stream=None, capacity=256):
    replay, config, directory = startup
    stream = stream if stream is not None else io.StringIO()
    original_rpc, original_mirror = replay.rpc, replay.mirror_block

    def head():
        return BLOCK + int((replay.source - POOL_TIME).total_seconds())

    async def rpc(method, *params):
        if method == "eth_call":
            assert params[1] == hex(head())
            params = (params[0], hex(BLOCK))
        data = await original_rpc(method, *params)
        if method == "eth_getBlockByNumber" and data["number"] == hex(BLOCK):
            return {**data, "number": hex(head())}
        return data

    async def mirror(number):
        assert number == head()
        data = await original_mirror(BLOCK)
        return {**data, "number": number}

    monkeypatch.setattr(replay, "rpc", rpc)
    monkeypatch.setattr(replay, "mirror_block", mirror)
    if scenario:
        monkeypatch.setattr(SimulationEngine, "_scenario", scenario)

    clock = [replay.source + timedelta(seconds=1)]

    async def run():
        stop = asyncio.Event()

        async def sleep(seconds):
            if seconds == 0.25:
                await asyncio.Future()
            status = runtime.status()
            if change:
                change(runtime, status)
            if status["analysis_cycles"] >= limit:
                stop.set()
                return
            clock[0] += timedelta(seconds=seconds)
            replay.source = clock[0] - timedelta(seconds=1)
            await asyncio.sleep(0)

        async def execution_sleep(seconds):
            clock[0] += timedelta(seconds=seconds)

        monkeypatch.setattr(SimulationEngine, "_sleep", staticmethod(execution_sleep))
        runtime = Runtime(directory, clock=lambda: clock[0], timer=lambda: 0, sleep=sleep, jitter=lambda *_: 0)
        runtime.initialize(config)
        sink = ConsoleSink(stream, level, capacity=capacity)
        runtime.log.sink = sink
        try:
            with runtime.owner():
                activation = runtime.activate(action, "worker-1", config if action != "recover" else None)
                if blocked:
                    with pytest.raises(StartupBlocked):
                        await asyncio.wait_for(runtime.run(*activation, stop), 3)
                else:
                    await asyncio.wait_for(runtime.run(*activation, stop), 3)
        finally:
            sink.close()
        return runtime, sink

    runtime, sink = asyncio.run(run())
    return runtime, stream, sink


@pytest.mark.parametrize("case", ["retry", "no_wallet", "unavailable_gas"])
def test_clm_t01_startup(startup, monkeypatch, case):
    replay = startup[0]
    if case == "retry":
        replay.timeout_attempts = {1}
    elif case == "unavailable_gas":
        replay.gas_down = True
    runtime, stream, _ = capture(startup, monkeypatch, blocked=case == "unavailable_gas")
    output = stream.getvalue()
    evidence(case, stream)
    assert output.index("STARTING") < output.index("PREFLIGHT_CHECKING")
    if case == "retry":
        assert output.index("WAITING_RETRY") < output.index(" READY") < output.index(" RUNNING")
        assert "Retry in 15" in output and "Provider timeout" in output
    elif case == "unavailable_gas":
        assert "Retry budget exhausted." in output
        assert "STARTUP_BLOCKED" in output and " RUNNING" not in output and "Analysis started" not in output
        assert runtime.status()["analysis_cycles"] == 0
        assert "RPC gas price unavailable" in output
    else:
        assert "CONFIGURED_GAS_UNITS + LIVE_RPC_GAS_PRICE" in output
        assert "BUY 109478 gas units, SELL 109478 gas units, validation limit 300000" in output
        assert "RPC price 1630000000000 weibars per gas" in output
        assert "Estimated cost 0.17844914 HBAR, maximum budget 0.35689828 HBAR" in output
        ordered = ("Arbitrage Bot Starting", "Foreground logger initialized", "SIMULATION ONLY",
                   "Applied simulation configuration", "SQLite initialization and integrity validation",
                   "Prior virtual executions and outstanding holds", "DEX WHBAR · Total",
                   "verified chain ID 295", "SaucerSwap WHBAR-USDC token identity",
                   "WebSocket depth", "REST depth", "DEX BUY gas", "DEX SELL gas",
                   "All required readiness checks passed", "RUNNING", "CHECK #001")
        offsets = [output.index(message) for message in ordered]
        assert offsets == sorted(offsets)


def test_clm_t02_ten_cycles_and_shutdown(startup, monkeypatch):
    runtime, stream, _ = capture(startup, monkeypatch, limit=10)
    output = stream.getvalue()
    evidence("ten_cycles_graceful_shutdown", stream)
    assert output.count("Applied simulation configuration") == 1
    assert output.count("Analysis started") == output.count("Analysis completed") == 10
    assert output.count("DEX → CEX · Indicative") == output.count("CEX → DEX · Indicative") == 10
    assert output.index(" RUNNING") < output.index("Analysis started")
    assert "Foreground shutdown requested" in output and "10 completed cycles, 0 admissions" in output
    assert runtime.status()["analysis_cycles"] == 10


def test_clm_t03_persisted_values_and_unknown(startup, monkeypatch):
    def change(runtime, status):
        if status["analysis_cycles"] == 1:
            startup[0].invalid_price = True

    runtime, stream, _ = capture(startup, monkeypatch, limit=2, change=change)
    with runtime.storage.transaction() as tx:
        rows = tx._db.execute("SELECT id FROM analysis_cycles ORDER BY rowid").fetchall()
        good = tx.analysis(rows[0][0])
        bad = tx.analysis(rows[1][0])
    output = stream.getvalue()
    good_text, bad_text = output.split("CHECK #002 · Analysis started")
    for item in good.evaluations:
        assert f"Indicative {item.indicative_profit_usdt:f} USDT" in good_text
        assert f"net {item.net_profit_usdt:f} USDT" in good_text
        assert f"stressed {item.stressed_profit_usdt:f} USDT" in good_text
        assert f"/ {item.gas_cost_usdt:f} USDT." in good_text
    for item in bad.evaluations:
        assert f"gross quote {item.requested_dex.quote_units / 10**6:.6f} USDC" in bad_text
        assert f"fee-inclusive quote {item.requested_cex.quote_units / 10**6:.6f} USDT" in bad_text
    assert bad_text.count("net UNKNOWN") == 2
    assert "Gas data invalid or stale" in bad_text and "NO_OPPORTUNITY" not in bad_text


@pytest.mark.parametrize("fault", ["duplicate_snapshot", "stale_or_invalid_clock", "insufficient_depth", "ws_rest"])
def test_clm_t04_source_and_cycle_faults(startup, monkeypatch, fault):
    if fault == "ws_rest":
        from arbitrage.kucoin import KuCoinDepthStream

        async def listen(stream, recovering):
            book = await stream.market._rest_snapshot()
            stream._book = replace(book, source="websocket", generation=stream.generation)
            stream.health = "healthy"
            stream.initial_attempt.set()
            await asyncio.Future()

        monkeypatch.setattr(KuCoinDepthStream, "_listen", listen)
    if fault == "duplicate_snapshot":
        # Preserve the first accepted snapshot identity while returning fresh time.
        from arbitrage.coordinator import OpportunityCoordinator
        monkeypatch.setattr(OpportunityCoordinator, "_snapshot_ids",
                            staticmethod(lambda market: ("same-dex", "same-cex", "same-time")))
    def change(runtime, status):
        if status["analysis_cycles"] == 1:
            if fault == "insufficient_depth":
                startup[0].depth["asks"] = [["0.05280", "2"]]
            if fault == "stale_or_invalid_clock":
                from arbitrage.market_data import StaleSnapshot
                async def stale_read(adapter):
                    raise StaleSnapshot("offline timing fixture")
                monkeypatch.setattr(KuCoin, "snapshot", stale_read)
    _, stream, _ = capture(startup, monkeypatch, limit=2, change=change)
    output = stream.getvalue()
    if fault == "ws_rest":
        evidence("websocket_to_rest", stream)
        transitions = [line for line in output.splitlines() if "Market data source changed" in line]
        assert any("changed to WebSocket" in line for line in transitions)
        assert any("changed to REST" in line for line in transitions[1:])
    else:
        reason = {"stale_or_invalid_clock": "Stale market data or invalid clock",
                  "duplicate_snapshot": "Duplicate snapshot",
                  "insufficient_depth": "Insufficient depth"}[fault]
        assert reason in output


@pytest.mark.parametrize("case", ["success", "dex_failure", "cex_partial", "unknown"])
def test_clm_t05_committed_execution_and_balances(startup, monkeypatch, case):
    replay = startup[0]
    replay.depth.update(asks=[["0.05510", "2000"]], bids=[["0.05500", "2000"]])
    scenarios = {
        "success": Scenario(),
        "dex_failure": Scenario(dex=(Attempt(effect=Effect.REVERT),) * 3),
        "cex_partial": Scenario(cex=(Attempt(effect=Effect.PARTIAL, partial_units=50_000_000_000),)),
        "unknown": Scenario(cex=(Attempt(effect=Effect.UNKNOWN),)),
    }
    runtime, stream, _ = capture(startup, monkeypatch, scenario=scenarios[case], blocked=case == "unknown")
    output = stream.getvalue()
    evidence(case, stream)
    assert "Admitted SIMULATED execution" in output and "Admission admitted" in output
    expected = {"success": "filled", "dex_failure": "failed", "cex_partial": "incomplete", "unknown": "manual_review"}
    with runtime.storage.transaction() as tx:
        trade_id, status = tx._db.execute("SELECT id, status FROM simulated_trades").fetchone()
        assert status == expected[case]
        assert all(entry.id.startswith(f"sim:{trade_id}:") for entry in tx.ledger())
    outcome = expected[case].replace("_", " ").upper()
    assert f"finished · {outcome}" in output
    assert "Previous " in output and "Reconciliation PASS" in output
    assert "Committed stablecoin changes" in output
    dex_index = output.index("SIMULATED DEX execution")
    if case == "dex_failure":
        assert "SIMULATED KuCoin execution" not in output and "Retry attempt 3" in output
    else:
        assert dex_index < output.index("SIMULATED KuCoin execution")
    if case == "cex_partial":
        assert "Filled 500.00000000 HBAR" in output
    if case == "unknown":
        assert "Fee UNKNOWN" in output and "Retained holds: KuCoin HBAR 1000.00000000" in output


def test_clm_t06_crash_after_commit_no_duplicate(startup, monkeypatch):
    import multiprocessing
    from datetime import datetime

    replay, _, directory = startup
    replay.depth.update(asks=[["0.05510", "2000"]], bids=[["0.05500", "2000"]])

    def child():
        with monkeypatch.context() as isolated:
            # Kill after the actual DEX settlement transaction commits and before the
            # observer can present it as completed.
            def reflect(runtime, method, *args):
                if method == "journal":
                    with runtime.storage.transaction() as tx:
                        if any(entry.holding == Holding.DEX_HBAR for entry in tx.ledger()):
                            os._exit(77)
                return observe(runtime, method, *args)

            observe = Runtime._observe
            isolated.setattr(Runtime, "_observe", reflect)
            capture(startup, isolated, scenario=Scenario())

    proc = multiprocessing.get_context("fork").Process(target=child)
    proc.start()
    proc.join(7)
    if proc.is_alive():
        proc.kill()
        proc.join(5)
    assert proc.exitcode == 77
    prior = Runtime(directory)
    with prior.storage.transaction() as tx:
        pending = tx.unresolved_simulation(NETWORK)
        assert pending["status"] == "active"
        ledger = tx.ledger()
        assert sum(entry.holding == Holding.DEX_HBAR for entry in ledger) == 1
        started = prior.latest(tx)
        replay.source = datetime.fromisoformat(started["high_water"])
    replay.depth.update(asks=[["0.05280", "2000"]], bids=[["0.05270", "2000"]])
    runtime, stream, _ = capture(startup, monkeypatch, action="recover", limit=2)
    evidence("crash_recovery", stream)
    output = stream.getvalue()
    assert f"Recovering SIMULATED execution {pending['id']}" in output
    assert "SIMULATED DEX execution" not in output and "SIMULATED KuCoin execution" in output
    with runtime.storage.transaction() as tx:
        assert tx.simulation_status(pending["id"]) == "filled"
        assert len([entry for entry in tx.ledger() if entry.holding == Holding.DEX_HBAR]) == 1
        assert tx._db.execute("SELECT count(*) FROM simulated_trades").fetchone()[0] == 1


def test_clm_t07_slow_sink_does_not_affect_finances(startup, monkeypatch):
    class Slow(io.StringIO):
        def __init__(self):
            super().__init__()
            self.release = ThreadEvent()

        def write(self, value):
            self.release.wait(3)
            return super().write(value)

    replay = startup[0]
    replay.depth.update(asks=[["0.05510", "2000"]], bids=[["0.05500", "2000"]])
    stream = Slow()
    runtime, _, sink = capture(startup, monkeypatch, stream=stream, capacity=2)
    try:
        assert sink.dropped > 0
        with runtime.storage.transaction() as tx:
            assert tx._db.execute("SELECT status FROM simulated_trades").fetchone()[0] == "filled"
            assert tx._db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 0
    finally:
        stream.release.set()
        sink._thread.join(2)
    evidence("slow_sink", stream)
    assert "Console sink resumed." in stream.getvalue()


def test_clm_t07_failed_sink_recovers():
    class Flaky(io.StringIO):
        broken = True

        def write(self, value):
            if self.broken:
                self.broken = False
                raise OSError("private injected exception")
            return super().write(value)

    stream = Flaky()
    sink = ConsoleSink(stream, capacity=8)
    log = OperationalLog()
    log.sink = sink
    log.emit(Event.STARTUP)
    log.emit(Event.STARTUP)
    sink.close()
    assert sink.failed == 1
    assert "Console sink resumed. 1 event(s) lost" in stream.getvalue()
    assert "private injected exception" not in stream.getvalue()
    evidence("failed_sink_recovered", stream)


def test_clm_t08_secret_injection_and_cli_compatibility(tmp_path, capsys):
    stream = io.StringIO()
    log = OperationalLog()
    sink = ConsoleSink(stream)
    log.sink = sink
    secret = "Bearer secret\x1b[31m\nhttps://private.invalid/key"
    log.emit(Event.SIMULATED_EXECUTION, worker=secret, session=secret, execution=secret,
             venue=secret, phase=secret, result=secret, filled_hbar=secret, payload=secret)
    log.emit(Event.READINESS_CHECK, check=secret, diagnostic=secret, hbar_tinybars=secret)
    sink.close()
    content = stream.getvalue()
    assert "SIMULATED" in content and "\x1b" not in content and "private.invalid" not in content
    assert "Bearer" not in log.take()
    from test_coordinator import CONFIG
    args = ["--data-dir", str(tmp_path / "private")]
    assert cli.main([*args, "init", "--config", str(CONFIG)]) == 0
    capsys.readouterr()
    assert cli.main([*args, "status"]) == 0
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout)["mode"] == "simulation" and not stderr
    assert cli.main([*args, "report"]) == 0
    assert capsys.readouterr().out == "Private report.xlsx exported.\n"


@pytest.mark.parametrize("level", [Level.INFO, Level.DEBUG])
def test_clm_t09_level_utc_correlation_and_summary(startup, monkeypatch, level):
    runtime, stream, _ = capture(startup, monkeypatch, level=level)
    lines = stream.getvalue().splitlines()
    assert all(re.match(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\] (INFO|DEBUG|WARNING|ERROR|CRITICAL) +\[[A-Z0-9-]+\] ", line)
               and line[24] == "[" for line in lines)
    assert (" DEBUG " in stream.getvalue()) == (level == Level.DEBUG)
    assert "(2026-11-26 UTC)" in lines[0]
    assert "1 completed cycles, 0 admissions" in stream.getvalue()
    if level == Level.DEBUG:
        structured = [json.loads(line.split("[LOGGER] ", 1)[1])
                      for line in lines if "[LOGGER] {" in line]
        session = runtime.status()["session"]["id"]
        start = next(event for event in structured if event["event"] == "analysis_start")
        evaluations = [event for event in structured if event["event"] == "evaluation"]
        assert len(evaluations) == 2
        assert all(event["cycle"] == start["cycle"] and event["session"] == session for event in evaluations)
    else:
        assert all("cycle=" not in line and "session=" not in line for line in lines)


@pytest.mark.parametrize("level", list(Level))
def test_console_alignment_and_utc(level):
    [line] = render({"at": "2026-11-26T01:02:03.456789+01:00", "level": level,
                     "event": "storage_status"}).splitlines()
    assert line == f"[00:02:03.456] {level.upper():<8} [STORAGE] Could not read or project the operational status."


def test_unknown_dex_charge_and_retained_hold():
    line = render({"at": POOL_TIME.isoformat(), "level": "warning", "event": "simulated_execution",
                   "phase": "result", "venue": "dex", "attempt": 1, "result": "unknown",
                   "dex.HBAR": "0.35689828"})
    assert "Gas charged UNKNOWN" in line
    assert "Retained holds: DEX HBAR gas 0.35689828" in line


def test_clm_t10_offline_no_unexpected_network(startup, monkeypatch):
    runtime, stream, _ = capture(startup, monkeypatch)
    replay = startup[0]
    assert replay.gas_price_reads == 2
    assert "Real trading, wallet signing and order submission are disabled" in stream.getvalue()
    with runtime.storage.transaction() as tx:
        assert tx._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert tx._db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert tx.ledger() == ()
