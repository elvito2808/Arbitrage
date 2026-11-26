import asyncio
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from arbitrage.config import load_config, parse_config
from arbitrage.market_data import RPC, MarketDataError, PublicConnections, Unavailable
from arbitrage.models import Side
from arbitrage.saucerswap import (
    EXACT_INPUT_SELECTOR, EXACT_OUTPUT_SELECTOR, FEE_PROVIDER, HISTORICAL_GAS, ROUTER, TRADE_UNITS, USDC,
    WHBAR, SaucerSwap, gas_cost_tinybars, words,
)

from test_coordinator import CONFIG
from test_market_data import BLOCK, HASH, Hedera, POOL_TIME

SENDER = "0x" + "12" * 20


class Estimator(Hedera):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.outcomes = {Side.BUY: hex(145_000), Side.SELL: hex(165_000)}
        self.head_changed = False
        self.advance_time = False

    async def rpc(self, method, *params):
        if method == "eth_getBlockByNumber" and self.head_changed and self.calls:
            return {"number": hex(BLOCK + 1), "hash": HASH}
        if method != "eth_estimateGas":
            return await super().rpc(method, *params)
        call, block = params
        assert block == "latest"
        side = Side.BUY if call["data"][2:10] == EXACT_OUTPUT_SELECTOR else Side.SELL
        self.calls.append((side, call))
        if self.advance_time and side == Side.SELL:
            self.now = POOL_TIME + timedelta(seconds=10.1)
        result = self.outcomes[side]
        if isinstance(result, Exception):
            raise result
        return result


def test_bidirectional_estimate_requests_and_adapter_bound_evidence():
    provider = Estimator()
    dex = SaucerSwap(provider, estimate_sender=SENDER)
    snap = asyncio.run(dex.snapshot())
    buy, sell = snap.gas
    assert len(provider.calls) == 2
    assert {side for side, _ in provider.calls} == {Side.BUY, Side.SELL}
    assert (buy.execution_gas, sell.execution_gas) == (145_000, 165_000)
    assert buy.route_evidence.id != sell.route_evidence.id
    for side, call in provider.calls:
        is_buy = side == Side.BUY
        gas = buy if is_buy else sell
        expected_path = (USDC, WHBAR) if is_buy else (WHBAR, USDC)
        provenance = gas.route_evidence.rpc
        assert call == {"from": SENDER, "to": ROUTER, "data": provenance.calldata, "value": "0x0"}
        assert call["data"][2:10] == (EXACT_OUTPUT_SELECTOR if is_buy else EXACT_INPUT_SELECTOR)
        assert words("0x" + call["data"][10:], 8) == (
            TRADE_UNITS, 53_025_571 if is_buy else 52_155_926, 160, int(SENDER, 16),
            int(POOL_TIME.timestamp()) + 10, 2, *(int(address, 16) for address in expected_path),
        )
        assert provenance.path == expected_path and provenance.value_weibars == 0
        assert (provenance.provider, provenance.chain_id) == (FEE_PROVIDER, 295)
        assert (provenance.block, provenance.block_hash) == (BLOCK, HASH)
        assert provenance.estimated_gas == gas.execution_gas
        assert provenance.gas_price_weibars == 1_630_000_000_000
        assert provenance.estimated_at == gas.observed_at == provider.now
        assert provenance.price_observed_at == provider.now
        assert dex.verifies_route_gas(gas, snap)
        assert not dex.verifies_route_gas(replace(gas), snap)
        assert not dex.verifies_route_gas(gas, replace(snap))


@pytest.mark.parametrize(("sell_result", "error", "invalid"), [
    (Unavailable("fixture outage"), "route_estimate_unavailable", False),
    (MarketDataError("fixture revert"), "route_estimate_unavailable", False),
    ("0x0", "route_estimate_invalid", True),
    ("0x012345", "route_estimate_invalid", True),
    ("0xnot-gas", "route_estimate_invalid", True),
    (hex(15_000_001), "route_estimate_invalid", True),
])
def test_invalid_sell_never_inherits_buy_gas(sell_result, error, invalid):
    provider = Estimator()
    provider.outcomes[Side.SELL] = sell_result
    pool = asyncio.run(SaucerSwap(provider, estimate_sender=SENDER).snapshot())
    buy, sell = pool.gas
    assert buy.cost_valid
    assert not sell.cost_valid and sell.execution_gas is None and sell.route_evidence is None
    assert sell.estimation_error == error and sell.invalid == invalid
    assert len(pool.quotes) == 2


@pytest.mark.parametrize(("field", "error"), [
    ("head_changed", "route_estimate_head_changed"),
    ("router_down", "route_context_unverified"),
    ("gas_down", "gas_price_unavailable"),
])
def test_unverified_shared_context_preserves_gross_quotes(field, error):
    provider = Estimator()
    setattr(provider, field, True)
    pool = asyncio.run(SaucerSwap(provider, estimate_sender=SENDER).snapshot())
    assert all(gas.estimation_error == error and not gas.cost_valid for gas in pool.gas)
    assert len(pool.quotes) == 2
    assert len(provider.calls) == (2 if field == "head_changed" else 0)


def test_route_deadline_rejects_estimate_before_quote_ttl():
    provider = Estimator()
    provider.advance_time = True
    pool = asyncio.run(SaucerSwap(provider, estimate_sender=SENDER).snapshot())
    buy, sell = pool.gas
    assert buy.cost_valid
    assert not sell.cost_valid and sell.invalid
    assert sell.estimation_error == "route_estimate_invalid"


def test_historical_calibration_and_tinybar_ceiling():
    assert HISTORICAL_GAS.gas_limit == 1_120_000 and HISTORICAL_GAS.gas_used == 143_161
    assert HISTORICAL_GAS.output_usdc_units == 53_048_295
    assert HISTORICAL_GAS.calculated_gas_tinybars == HISTORICAL_GAS.charged_gas_tinybars
    assert Fraction(HISTORICAL_GAS.charged_gas_tinybars, 10**8) == Fraction("0.22762599")
    assert gas_cost_tinybars(21_001, 15_000_000_001) == 31_502


def test_only_public_sender_is_configurable():
    assert load_config(CONFIG).simulation.hedera_estimate_sender is None
    config = CONFIG.read_bytes()
    insertion = b"[simulation]\n"
    configured = config.replace(insertion, insertion + f'hedera_estimate_sender="{SENDER}"\n'.encode())
    assert parse_config(configured).simulation.hedera_estimate_sender == SENDER
    for rejected in ("0x" + "0" * 40, "0x" + "a" * 64, "not-an-address"):
        invalid = config.replace(insertion, insertion + f'hedera_estimate_sender="{rejected}"\n'.encode())
        with pytest.raises(ValueError, match="invalid simulation configuration"):
            parse_config(invalid)


def test_rpc_estimation_allowlist_does_not_allow_submission(monkeypatch):
    api = PublicConnections(None)
    calls = []

    async def fake_json(method, url, payload):
        calls.append((method, url, payload["method"]))
        return {"jsonrpc": "2.0", "id": payload["id"], "result": hex(150_000)}

    monkeypatch.setattr(api, "_json", fake_json)
    assert asyncio.run(api.rpc("eth_estimateGas", {"to": ROUTER, "value": "0x0"}, "latest")) == hex(150_000)
    for method in ("eth_sendTransaction", "eth_sendRawTransaction", "personal_sign"):
        with pytest.raises(MarketDataError, match="outside the public read scope"):
            asyncio.run(api.rpc(method, {}))
    assert calls == [("POST", RPC, "eth_estimateGas")]
