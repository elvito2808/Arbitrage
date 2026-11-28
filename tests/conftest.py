from datetime import UTC, datetime

import pytest

from arbitrage.models import Asset, Direction, MarketQuote, Opportunity, Side, Venue


@pytest.fixture
def now():
    return datetime(2026, 11, 26, tzinfo=UTC)


@pytest.fixture
def opportunity(now):
    dex = MarketQuote(
        venue=Venue.DEX, side=Side.BUY, base_asset=Asset.WHBAR, quote_asset=Asset.USDC,
        base_units=100_000_000_000, quote_units=52_761_762, snapshot_id="dex-1", observed_at=now,
    )
    cex = MarketQuote(
        venue=Venue.CEX, side=Side.SELL, base_asset=Asset.HBAR, quote_asset=Asset.USDT,
        base_units=100_000_000_000, quote_units=52_970_000, snapshot_id="cex-1", observed_at=now,
    )
    return Opportunity(
        id="opportunity-1", worker_id="worker-1", direction=Direction.BUY_DEX,
        requested_dex=dex, requested_cex=cex, estimated_profit_usdt="0.155268", observed_at=now,
    )
