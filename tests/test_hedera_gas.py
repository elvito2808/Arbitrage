import asyncio
from dataclasses import replace
from fractions import Fraction

import pytest

from arbitrage.config import load_config, parse_config
from arbitrage.market_data import RPC, MarketDataError, PublicConnections, Unavailable
from arbitrage.models import GAS_SOURCE, Side
from arbitrage.saucerswap import FEE_PROVIDER, HISTORICAL_GAS, SaucerSwap, gas_cost_tinybars

from test_coordinator import CONFIG
from test_market_data import Hedera

REFERENCE_WEIBARS = 1_540_000_000_000


class GasPriceReplay(Hedera):
    def __init__(self, result=hex(REFERENCE_WEIBARS)):
        super().__init__()
        self.result = result
        self.reads = []

    async def rpc(self, method, *params):
        self.reads.append(method)
        assert method in {"eth_chainId", "eth_getBlockByNumber", "eth_call", "eth_gasPrice"}
        if method == "eth_gasPrice":
            assert not params
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        return await super().rpc(method, *params)


def test_configured_directions_share_only_the_live_price():
    provider = GasPriceReplay()
    settings = parse_config(CONFIG.read_bytes().replace(b"sell_gas_units = 109478", b"sell_gas_units = 110000"))
    dex = SaucerSwap(provider, settings=settings.simulation)
    snap = asyncio.run(dex.snapshot())
    assert provider.reads.count("eth_gasPrice") == provider.reads.count("eth_getBlockByNumber") == 1
    assert snap.gas[0].route_evidence.id != snap.gas[1].route_evidence.id
    for side, gas, units in zip(Side, snap.gas, (109_478, 110_000), strict=True):
        evidence = gas.route_evidence
        basis = evidence.configured
        assert (evidence.side, evidence.execution_gas, evidence.rpc) == (side, units, None)
        assert (basis.source, basis.provider, basis.chain_id) == (GAS_SOURCE, FEE_PROVIDER, 295)
        assert (basis.configured_gas_units, basis.gas_limit) == (units, 300_000)
        assert (basis.gas_price_weibars, basis.price_observed_at) == (REFERENCE_WEIBARS, provider.now)
        assert dex.verifies_route_gas(gas, snap)
        assert not dex.verifies_route_gas(replace(gas), snap)
        assert not dex.verifies_route_gas(gas, replace(snap))


@pytest.mark.parametrize(("result", "error"), [
    (Unavailable("fixture outage"), "gas_price_unavailable"),
    ("0x0", "gas_price_invalid"),
    ("0x012345", "gas_price_invalid"),
    ("not a quantity", "gas_price_invalid"),
    (hex(10**18 + 1), "gas_price_invalid"),
])
def test_missing_and_invalid_prices_fail_closed_in_both_directions(result, error):
    pool = asyncio.run(SaucerSwap(GasPriceReplay(result)).snapshot())
    assert len(pool.quotes) == 2
    for gas in pool.gas:
        assert gas.estimation_error == error
        assert gas.invalid == (error == "gas_price_invalid")
        assert gas.price_range_weibars is None and gas.route_evidence is None and not gas.cost_valid


def test_reference_arithmetic_and_tinybar_ceiling():
    assert HISTORICAL_GAS.calculated_gas_tinybars == HISTORICAL_GAS.charged_gas_tinybars
    estimated = gas_cost_tinybars(109_478, REFERENCE_WEIBARS)
    assert Fraction(estimated, 10**8) == Fraction("0.16859612")
    assert Fraction(estimated * 2, 10**8) == Fraction("0.33719224")
    assert gas_cost_tinybars(21_001, 15_000_000_001) == 31_502


@pytest.mark.parametrize(("key", "old", "invalid"), [
    ("buy_gas_units", "109478", "0"),
    ("sell_gas_units", "109478", '"109478"'),
    ("sell_gas_units", "109478", "300001"),
    ("gas_limit", "300000", "109477"),
    ("gas_limit", "300000", "true"),
])
def test_gas_configuration_validation(key, old, invalid):
    config = CONFIG.read_bytes().replace(f"{key} = {old}".encode(), f"{key} = {invalid}".encode())
    with pytest.raises(ValueError, match="invalid simulation configuration"):
        parse_config(config)


def test_default_configuration_has_no_sender():
    settings = load_config(CONFIG).simulation
    assert settings.hedera_estimate_sender is None
    assert (settings.buy_gas_units, settings.sell_gas_units, settings.gas_limit) == (109_478, 109_478, 300_000)
    assert settings.gas_price_cap_multiple == 2


def test_rpc_gas_price_allowlist_does_not_allow_estimation_or_submission(monkeypatch):
    api = PublicConnections(None)
    calls = []

    async def fake_json(method, url, payload):
        calls.append((method, url, payload["method"]))
        return {"jsonrpc": "2.0", "id": payload["id"], "result": hex(REFERENCE_WEIBARS)}

    monkeypatch.setattr(api, "_json", fake_json)
    assert asyncio.run(api.rpc("eth_gasPrice")) == hex(REFERENCE_WEIBARS)
    for method in ("eth_estimateGas", "eth_sendTransaction", "eth_sendRawTransaction", "personal_sign"):
        with pytest.raises(MarketDataError, match="outside the public read scope"):
            asyncio.run(api.rpc(method, {}))
    assert calls == [("POST", RPC, "eth_gasPrice")]
