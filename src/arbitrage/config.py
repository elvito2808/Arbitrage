"""Version-one TOML configuration for the approved simulation."""

import tomllib
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .models import MAX_UNITS, Balance, Exact, Holding, Record, TRADE_HBAR, WORKER_CAPACITY


class Simulation(Record):
    mode: Literal["simulation"]
    trade_hbar: Literal[TRADE_HBAR]
    duration_hours: Literal[24]
    worker_capacity: Literal[WORKER_CAPACITY]
    max_active_workers: Literal[1]
    cache_ttl_seconds: Annotated[int, Field(strict=True, gt=0, le=10)]
    minimum_profit_usdt: Annotated[Exact, Field(gt=0)]
    maximum_loss_usdt: Annotated[Exact, Field(gt=0)]
    slippage_fraction: Annotated[Exact, Field(ge=0, lt=1)]
    gas_price_cap_multiple: Annotated[Exact, Field(ge=1)]
    cex_fee_fraction: Annotated[Exact, Field(ge=0, lt=1)]
    usdc_usdt_parity: Literal[True]

    @field_validator("trade_hbar", "duration_hours", "worker_capacity", "max_active_workers", mode="before")
    @classmethod
    def require_int(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("expected an integer")
        return value

    @field_validator("usdc_usdt_parity", mode="before")
    @classmethod
    def require_parity(cls, value: object) -> object:
        if value is not True:
            raise ValueError("the simulation assumes USDC/USDT parity")
        return value


class InitialBalances(Record):
    dex_whbar: Exact
    dex_usdc: Exact
    dex_hbar: Exact
    cex_hbar: Exact
    cex_usdt: Exact

    @model_validator(mode="after")
    def validate_units(self) -> "InitialBalances":
        self.as_balances()
        return self

    def as_balances(self) -> tuple[Balance, ...]:
        balances = []
        for holding in Holding:
            amount = getattr(self, holding.name.lower())
            scale = 10**holding.asset.decimals
            if not 0 <= amount <= MAX_UNITS:
                raise ValueError("initial balance out of range")
            if 0 < amount < Decimal(1).scaleb(-holding.asset.decimals):
                raise ValueError("initial balance must fit integer base units")
            with localcontext() as context:
                context.prec = max(28, len(amount.as_tuple().digits) + holding.asset.decimals)
                units = amount * scale
            if units > MAX_UNITS or units != units.to_integral_value():
                raise ValueError("initial balance must fit integer base units")
            balances.append(Balance(holding=holding, total_units=int(units)))
        return tuple(balances)


class Config(Record):
    schema_version: Literal[1]
    simulation: Simulation
    balances: InitialBalances

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_version_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("expected an integer schema version")
        return value


def load_config(path: str | Path) -> Config:
    try:
        with Path(path).open("rb") as stream:
            return Config.model_validate(tomllib.load(stream, parse_float=Decimal))
    except (tomllib.TOMLDecodeError, UnicodeError, ValidationError):
        # Invalid values and unknown keys may contain sensitive input.
        raise ValueError("invalid simulation configuration") from None
