from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from arbitrage.models import (
    Asset, Balance, Direction, Execution, Experiment, Holding, MarketQuote, Opportunity, Outcome,
)


def test_opportunity_round_trip(opportunity):
    assert opportunity.estimated_profit_usdt == Decimal("0.155268")
    assert Opportunity.model_validate_json(opportunity.model_dump_json()) == opportunity
    with pytest.raises(ValidationError, match="frozen"):
        opportunity.id = "different"


@pytest.mark.parametrize("bad_profit", [0.155268, True, "Infinity"])
def test_profit_rejects_inexact_or_nonfinite_values(opportunity, bad_profit):
    with pytest.raises(ValidationError):
        Opportunity.model_validate(opportunity.model_dump() | {"estimated_profit_usdt": bad_profit})


def test_quote_constraints(opportunity):
    data = opportunity.requested_dex.model_dump()
    for invalid in ({"base_asset": Asset.HBAR}, {"base_units": 1.0},
                    {"observed_at": data["observed_at"].replace(tzinfo=None)}):
        with pytest.raises(ValidationError):
            MarketQuote.model_validate(data | invalid)
    with pytest.raises(ValidationError, match="direction"):
        Opportunity.model_validate(opportunity.model_dump() | {"direction": Direction.SELL_DEX})


def test_balance_and_deadline(now):
    balance = Balance(holding=Holding.DEX_HBAR, total_units=7, reserved_units=5)
    assert balance.available_units == 2
    with pytest.raises(ValidationError):
        Balance(holding=Holding.DEX_HBAR, total_units=3, reserved_units=4)
    with pytest.raises(ValidationError, match="24 hours"):
        Experiment(id="experiment", started_at=now, deadline_at=now + timedelta(hours=23))


def test_execution_fill_must_match_venue(opportunity, now):
    with pytest.raises(ValidationError, match="DEX fill"):
        Execution(
            id="execution", opportunity_id=opportunity.id, observed_at=now,
            outcome=Outcome.PARTIAL, dex_fill=opportunity.requested_cex,
        )
