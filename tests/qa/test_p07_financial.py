"""Independent exact-arithmetic test vectors for public quotes."""

from decimal import Decimal
from fractions import Fraction

import pytest

from arbitrage.kucoin import InsufficientDepth, exact, level, priced_units
from arbitrage.market_data import MarketDataError
from arbitrage.models import Side
from arbitrage.saucerswap import amounts


@pytest.mark.parametrize("stable,inventory", [(10**9, 2500), (997, 2000), (2**112 - 1, 2000)])
def test_amm_quotes_preserve_the_fee_adjusted_invariant(stable, inventory):
    tinybars = 100_000_000
    buy, sell = amounts(stable, inventory * tinybars)
    invariant = stable * inventory
    # The buy path uses the V1 router's strict rounding rule (floor + one).
    assert (stable + Fraction(997, 1000) * (buy - 1)) * (inventory - 1000) <= invariant
    assert (stable + Fraction(997, 1000) * buy) * (inventory - 1000) > invariant
    adjusted_base = inventory + 997
    assert adjusted_base * (stable - sell) >= invariant
    assert adjusted_base * (stable - sell - 1) < invariant


@pytest.mark.parametrize("stable,base", [(0, 10**15), (-1, 10**15), (1, 10**11), (2**112, 10**15)])
def test_amm_rejects_out_of_range_reserves(stable, base):
    with pytest.raises(MarketDataError):
        amounts(stable, base)


@pytest.mark.parametrize("side,expected", [(Side.BUY, 51_451_404), (Side.SELL, 48_571_380)])
def test_fee_once_and_partial_final_depth_level(side, expected):
    if side == Side.BUY:
        # 300 * 0.05000001 + 700 * 0.052, then 0.1% fee and round up.
        depth = (level("0.05000001", "300"), level("0.052", "800"))
    else:
        # 300 * 0.051 + 700 * 0.0476, less 0.1% fee.
        depth = (level("0.051", "300"), level("0.0476", "800"))
    assert priced_units(depth, side, Fraction(Decimal("0.001"))) == expected
    with pytest.raises(InsufficientDepth):
        priced_units(depth[:1], side, Fraction(Decimal("0.001")))


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "-0.01", "1e-19", "9" * 41, 0.05])
def test_invalid_exchange_precision_is_rejected(invalid):
    with pytest.raises(MarketDataError):
        exact(invalid)


@pytest.mark.parametrize("boundary,offset", [
    ("profit", -1), ("profit", 0), ("profit", 1),
    ("loss", -1), ("loss", 0), ("loss", 1),
])
def test_risk_thresholds_to_the_micro_usdt(tmp_path, boundary, offset):
    from datetime import UTC, datetime

    from arbitrage.config import load_config
    from arbitrage.coordinator import OpportunityCoordinator
    from arbitrage.models import EvaluationStatus, Reason
    from arbitrage.storage import Storage
    from test_coordinator import CONFIG, MarketFixture, run

    settings = load_config(CONFIG)
    store = Storage(tmp_path / "threshold.sqlite")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(settings.balances.as_balances())
    market = MarketFixture(datetime(2026, 11, 26, tzinfo=UTC), settings.simulation.cex_fee_fraction)
    market.estimate = 250_000
    market.ask = Fraction("0.054")

    # 250,000 x 2e12 weibars is 0.5 HBAR, or 0.027 USDT at the fee-free ask.
    # The independently recorded DEX buy is 52.761762 USDC.
    config = settings.simulation
    if boundary == "profit":
        config = config.model_copy(update={"slippage_fraction": Decimal(0)})
        net_sell = 52_888_762 + offset
    else:
        # ceil(52,761,762 * 1.005) is 53,025,571. A 0.30 loss requires
        # a stressed sell of 52,752,571 before subtracting 27,000 gas.
        required_stressed_sell = 52_752_571 + offset
        net_sell = -(-required_stressed_sell * 1000 // 995)
    market.bid = Fraction(net_sell, 999_000_000)

    first, _ = run(OpportunityCoordinator(
        store, config, market, market.cex, clock=market.clock,
    )).evaluations
    assert first.gas_budget_tinybars == 50_000_000
    assert first.gas_cost_usdt == Decimal("0.027")
    if boundary == "profit":
        assert first.net_profit_usdt == Decimal(100_000 + offset).scaleb(-6)
        reason = Reason.PROFIT_THRESHOLD if offset <= 0 else None
    else:
        assert first.stressed_profit_usdt == Decimal(-300_000 + offset).scaleb(-6)
        reason = Reason.MAXIMUM_LOSS if offset < 0 else None
    assert first.reason == reason
    assert first.status == (EvaluationStatus.REJECTED if reason else EvaluationStatus.ELIGIBLE)
