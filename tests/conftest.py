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


@pytest.fixture
def funded_sender(monkeypatch):
    from arbitrage.market_data import PublicConnections

    def data(address, router):
        account = {"account": "0.0.10969490", "evm_address": address, "deleted": False,
                   "balance": {"balance": 100_000_000}}
        tokens = {"links": {"next": None}, "tokens": [
            {"token_id": "0.0.456858", "balance": 1_000_000_000},
            {"token_id": "0.0.1456986", "balance": 200_000_000_000},
        ]}
        allowances = {"links": {"next": None}, "allowances": [
            {"token_id": token["token_id"], "amount": token["balance"],
             "spender": router, "owner": account["account"]} for token in tokens["tokens"]
        ]}
        return account, tokens, allowances

    async def mirror_sender(self, address, router):
        return data(address, router)

    monkeypatch.setattr(PublicConnections, "mirror_sender", mirror_sender)
    return data
