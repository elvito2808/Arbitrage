"""SRG-T01–T10 through the foreground runtime with deterministic public reads."""

import asyncio
import socket
from datetime import timedelta

import pytest

from arbitrage.config import load_config
from arbitrage.coordinator import NETWORK
from arbitrage.market_data import ProviderRateLimited, PublicConnections, Unavailable
from arbitrage.models import Holding
from arbitrage.readiness import StartupBlocked
from arbitrage.runtime import Runtime
from arbitrage.simulation import SimulationEngine
from test_coordinator import CONFIG
from test_market_data import Exchange, Hedera, POOL_TIME

PUBLIC_RPC = PublicConnections.rpc


@pytest.fixture
def startup(tmp_path, monkeypatch):
    class Replay(Hedera, Exchange):
        def __init__(self):
            Hedera.__init__(self)
            Exchange.__init__(self)
            self.depth.update(asks=[["0.05280", "2000"]], bids=[["0.05270", "2000"]])
            self.chain_calls = 0
            self.invalid_price = False
            self.rest_down = False
            self.timeout_attempts = set()
            self.rate_limit = False
            self.gas_price_reads = 0
            self.reads = []

        async def rpc(self, method, *params):
            if method == "eth_chainId":
                self.chain_calls += 1
                self.reads.append("dex")
                if self.chain_calls in self.timeout_attempts:
                    raise TimeoutError
                if self.rate_limit:
                    raise ProviderRateLimited("fixture 429")
            if method == "eth_gasPrice":
                self.gas_price_reads += 1
                if self.invalid_price:
                    return "0x0"
            return await super().rpc(method, *params)

        async def kucoin(self, resource):
            if resource == "bootstrap":
                raise Unavailable("fixture WS unavailable")
            if resource == "depth":
                self.reads.append("cex")
                if self.rest_down:
                    raise Unavailable("fixture REST unavailable")
                self.depth["time"] = int(self.source.timestamp() * 1000)
                self.depth["sequence"] = str(int(self.source.timestamp()))
            return await Exchange.kucoin(self, resource)

    config = load_config(CONFIG)
    replay = Replay()
    def forbid_network(*args, **kwargs):
        pytest.fail("unexpected socket connection")
    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    monkeypatch.setattr(PublicConnections, "rpc", lambda _, *args: replay.rpc(*args))
    monkeypatch.setattr(PublicConnections, "kucoin", lambda _, *args: replay.kucoin(*args))
    monkeypatch.setattr(PublicConnections, "mirror_block", lambda _, *args: replay.mirror_block(*args))
    return replay, config, tmp_path


def drive(startup, *, on_wait=None, blocked=None, retries=None, action="start", hook=None):
    replay, config, directory = startup
    timestamp = [POOL_TIME + timedelta(seconds=1)]
    waits = []

    async def run():
        never = asyncio.Event()
        shutdown = asyncio.Event()

        async def sleep(seconds):
            if seconds == 0.25:
                await never.wait()
                return
            status = runtime.status()
            waits.append((seconds, status["readiness"]["state"], status["analysis_cycles"]))
            if on_wait:
                on_wait(runtime, status)
            elif status["analysis_cycles"]:
                runtime.control("stopped")
            timestamp[0] += timedelta(seconds=seconds)
            replay.source = timestamp[0] - timedelta(seconds=1)
            await asyncio.sleep(0)
            if runtime.status()["session"]["state"] != "running":
                never.set()

        runtime = Runtime(directory, clock=lambda: timestamp[0], timer=lambda: 0,
                          sleep=sleep, jitter=lambda *_: 0)
        runtime.initialize(config)
        if hook:
            hook(runtime)
        with runtime.owner():
            activation = runtime.activate(action, "worker-1", config)
            if blocked:
                with pytest.raises(StartupBlocked):
                    await asyncio.wait_for(runtime.run(*activation, shutdown), 2)
                assert runtime.status()["readiness"]["reason"] == blocked
            else:
                await asyncio.wait_for(runtime.run(*activation, shutdown), 2)
        return runtime

    runtime = asyncio.run(run())
    if retries is not None:
        assert sum(state == "WAITING_RETRY" for _, state, _ in waits) == retries
    return runtime, waits


def db_count(runtime, table):
    with runtime.storage.transaction() as tx:
        return tx._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_srg_t01_ready_precedes_first_cycle(startup):
    runtime, waits = drive(startup)
    health = runtime.status()["health"]
    sequence = [event.get("state") for event in health["recent_events"] if event["event"] == "readiness_state"]
    assert sequence == ["STARTING", "PREFLIGHT_CHECKING", "READY", "RUNNING", "CANCELLED"]
    assert waits[0] == (15, "READY", 0)
    assert db_count(runtime, "analysis_cycles") == 1
    assert db_count(runtime, "analyzed_snapshots") == 3
    assert startup[0].reads == ["dex", "cex", "dex", "cex"]


def test_srg_t02_timeout_then_success(startup):
    startup[0].timeout_attempts = {1}
    runtime, waits = drive(startup, retries=1)
    assert waits[0] == (15, "WAITING_RETRY", 0)
    assert any(event.get("diagnostic") == "provider_timeout"
               for event in runtime.status()["health"]["recent_events"])
    assert db_count(runtime, "simulated_trades") == 0


@pytest.mark.parametrize("failure", ["chain", "ledger"])
def test_srg_t03_critical_without_retry(startup, failure):
    replay, config, directory = startup
    hook = None
    if failure == "chain":
        replay.chain = "0x128"
        expected = "market_identity_mismatch"
    else:
        expected = "storage_inconsistent"
        def corrupt(runtime):
            with runtime.storage.transaction() as tx:
                tx._db.execute("UPDATE balances SET total_units=total_units+1 WHERE holding=?",
                               (Holding.DEX_HBAR,))
        hook = corrupt
    runtime, _ = drive((replay, config, directory), blocked=expected, retries=0, hook=hook)
    assert db_count(runtime, "analysis_cycles") == db_count(runtime, "simulated_trades") == 0
    if failure == "chain":
        check = runtime.status()["readiness"]["checks"]["hedera"]
        assert (check["outcome"], check["reason"], bool(check["at"])) == ("FAIL", expected, True)
    with runtime.owner(), pytest.raises(StartupBlocked):
        runtime.activate("recover", None)


@pytest.mark.parametrize("rest_available", [True, False])
def test_srg_t04_websocket_fallback(startup, rest_available):
    startup[0].rest_down = not rest_available
    runtime, _ = drive(startup, blocked=None if rest_available else "market_unavailable",
                       retries=0 if rest_available else 3)
    readiness = runtime.status()["readiness"]
    assert readiness["checks"]["kucoin_websocket"]["outcome"] == ("DEGRADED" if rest_available else "UNKNOWN")
    assert readiness["checks"]["kucoin_rest"]["outcome"] == ("PASS" if rest_available else "UNKNOWN")
    assert db_count(runtime, "analysis_cycles") == int(rest_available)


@pytest.mark.parametrize(("field", "diagnostic"), [
    ("invalid_price", "gas_price_invalid"), ("gas_down", "gas_price_unavailable"),
])
def test_srg_t05_bad_gas_price_blocks_both_directions(startup, field, diagnostic):
    setattr(startup[0], field, True)
    def check_wait(runtime, status):
        assert status["workers"]["worker-1"] != "running"
    runtime, _ = drive(startup, blocked=diagnostic, retries=3, on_wait=check_wait)
    status = runtime.status()
    assert status["workers"]["worker-1"] != "running"
    assert db_count(runtime, "analysis_cycles") == db_count(runtime, "reservations") == 0
    for key in ("buy_gas", "sell_gas"):
        assert status["readiness"]["checks"][key]["reason"] == diagnostic


@pytest.mark.parametrize("deadline", [False, True])
def test_srg_t06_retry_after_and_deadline(startup, monkeypatch, deadline):
    startup[0].rate_limit = True
    monkeypatch.setattr(PublicConnections, "cooldown_seconds", lambda _: 90_000 if deadline else 35)

    def on_wait(runtime, status):
        assert status["analysis_cycles"] == 0
        assert (status["readiness"]["next_attempt_at"] is None) == deadline

    runtime, waits = drive(startup, on_wait=on_wait, blocked=None if deadline else "provider_rate_limited",
                           retries=1 if deadline else 3)
    assert [delay for delay, _, _ in waits] == ([86_400] if deadline else [35, 35, 35])
    assert runtime.status()["session"]["state"] == ("expired" if deadline else "running")
    assert db_count(runtime, "analysis_cycles") == 0


@pytest.mark.parametrize("control", ["paused", "stopped", "emergency", "crash"])
def test_srg_t07_cancellation_and_crash(startup, control):
    startup[0].timeout_attempts = {1}
    def stop(runtime, status):
        assert status["readiness"]["state"] == "WAITING_RETRY"
        assert status["analysis_cycles"] == 0
        if control == "crash":
            raise asyncio.CancelledError
        runtime.control(control)

    if control == "crash":
        with pytest.raises(asyncio.CancelledError):
            drive(startup, on_wait=stop)
        replay, config, directory = startup
        runtime = Runtime(directory)
        assert db_count(runtime, "analysis_cycles") == 0
        assert db_count(runtime, "reservations") == 0
    else:
        runtime, _ = drive(startup, on_wait=stop, retries=1)
        assert runtime.status()["session"]["state"] == control
        assert runtime.status()["readiness"]["state"] == "CANCELLED"
        assert db_count(runtime, "analysis_cycles") == db_count(runtime, "analyzed_snapshots") == 0


def test_srg_t07_process_death_during_retry(startup):
    import multiprocessing
    import os
    import signal

    replay, config, directory = startup
    replay.timeout_attempts = {1}
    waiting = multiprocessing.get_context("fork").Event()

    def child():
        async def run():
            async def sleep(seconds):
                if seconds > 0.25:
                    waiting.set()
                await asyncio.Future()

            runtime = Runtime(directory, clock=lambda: POOL_TIME + timedelta(seconds=1), sleep=sleep)
            runtime.initialize(config)
            with runtime.owner():
                activation = runtime.activate("start", "worker-1", config)
                await runtime.run(*activation, asyncio.Event())
        asyncio.run(run())

    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    runtime = Runtime(directory, clock=lambda: POOL_TIME + timedelta(seconds=1))
    try:
        assert waiting.wait(5)
        assert runtime.status()["readiness"]["state"] == "WAITING_RETRY"
        os.kill(process.pid, signal.SIGKILL)
        process.join(5)
        assert process.exitcode == -signal.SIGKILL
        assert runtime.status()["readiness"]["state"] == "CANCELLED"
        assert db_count(runtime, "analysis_cycles") == db_count(runtime, "analyzed_snapshots") == 0
        with runtime.owner():
            assert runtime.activate("recover", None)
        assert runtime.status()["readiness"] is None
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)


def test_srg_t08_restart_recovery_before_reads(startup, monkeypatch):
    from arbitrage.models import LedgerEntry

    calls = []
    original = SimulationEngine.recover
    async def recover(engine):
        calls.append(startup[0].chain_calls)
        return await original(engine)

    monkeypatch.setattr(SimulationEngine, "recover", recover)

    def prior(runtime):
        with runtime.storage.transaction() as tx:
            tx.post(LedgerEntry(id="opening-adjustment", holding=Holding.DEX_HBAR, delta_units=-1,
                                recorded_at=runtime.now()))
        with runtime.owner():
            runtime.activate("start", "worker-1", startup[1])

    runtime, _ = drive(startup, hook=prior, action="recover")
    assert calls[0] == 0
    assert db_count(runtime, "ledger") == 1
    with runtime.storage.transaction() as tx:
        opening, = tx._db.execute("SELECT units FROM runtime_openings WHERE holding=?", (Holding.DEX_HBAR,)).fetchone()
        assert tx.balance(Holding.DEX_HBAR).total_units == opening - 1


def test_srg_t08_manual_review_retains_accepted_reservations(startup):
    replay, config, _ = startup
    replay.depth.update(asks=[["0.05510", "2000"]], bids=[["0.05500", "2000"]])

    def prior(runtime):
        with runtime.owner():
            runtime.activate("start", "worker-1", config)
        engine = SimulationEngine.from_public(runtime.storage, config.simulation,
                                              PublicConnections(None, clock=runtime.now))
        engine.gate = lambda *_: None
        # Use the same adapters with a deterministic public-read fixture.
        async def admit():
            return await engine.worker("worker-1").analyze()
        # Hook is invoked in the test event loop, so insert the accepted trade in a
        # separate thread with an independent event loop.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            cycle = pool.submit(lambda: asyncio.run(admit())).result()
        with runtime.storage.transaction() as tx:
            assert tx.unresolved_simulation(NETWORK)["id"] == cycle.id
            tx.finish_simulation(cycle.id, "manual_review")

    runtime, _ = drive(startup, hook=prior, action="recover", blocked="recovery_unresolved", retries=0)
    assert db_count(runtime, "analysis_cycles") == 1
    assert db_count(runtime, "reservations") == 3
    assert db_count(runtime, "ledger") == 0


@pytest.mark.parametrize("running_failure", ["timeout", "route"])
def test_srg_t09_valid_unprofitable_market_and_running_outage(startup, running_failure):
    def control(runtime, status):
        if status["analysis_cycles"] == 1:
            if running_failure == "timeout":
                startup[0].timeout_attempts = {3}
            else:
                startup[0].invalid_price = True
        elif status["analysis_cycles"] == 2:
            runtime.control("stopped")
    runtime, _ = drive(startup, on_wait=control)
    assert db_count(runtime, "analysis_cycles") == 2
    assert db_count(runtime, "simulated_trades") == 0
    assert db_count(runtime, "analyzed_snapshots") == 3


def test_srg_t10_public_read_allowlist(startup):
    runtime, _ = drive(startup)
    assert startup[0].gas_price_reads == 2
    assert startup[1].simulation.hedera_estimate_sender is None
    assert db_count(runtime, "simulated_trades") == db_count(runtime, "ledger") == 0
    public = PublicConnections(None)
    for method in ("eth_sendRawTransaction", "eth_sendTransaction", "eth_sign", "personal_sign"):
        with pytest.raises(ValueError, match="outside the public read scope"):
            asyncio.run(PUBLIC_RPC(public, method))
