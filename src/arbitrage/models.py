"""Immutable records shared by the simulator's persistence and interfaces."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator


MAX_UNITS = 2**63 - 1
EXPERIMENT_DURATION = timedelta(hours=24)
WORKER_CAPACITY = 3
TRADE_HBAR = 1_000

Units = Annotated[int, Field(strict=True, ge=0, le=MAX_UNITS)]
PositiveUnits = Annotated[int, Field(strict=True, gt=0, le=MAX_UNITS)]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128)]


def reject_inexact(value: object) -> object:
    if type(value) not in (Decimal, str, int):
        raise ValueError("use a decimal string, Decimal, or integer")
    return value


def normalize_time(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value.astimezone(UTC)


Exact = Annotated[Decimal, BeforeValidator(reject_inexact)]
Timestamp = Annotated[datetime, AfterValidator(normalize_time)]


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class Venue(StrEnum):
    DEX = "dex"
    CEX = "cex"


class Asset(StrEnum):
    HBAR = "HBAR"
    WHBAR = "WHBAR"
    USDC = "USDC"
    USDT = "USDT"

    @property
    def decimals(self) -> int:
        return 8 if self in (Asset.HBAR, Asset.WHBAR) else 6


class Holding(StrEnum):
    DEX_WHBAR = "dex.WHBAR"
    DEX_USDC = "dex.USDC"
    DEX_HBAR = "dex.HBAR"
    CEX_HBAR = "cex.HBAR"
    CEX_USDT = "cex.USDT"

    @property
    def asset(self) -> Asset:
        return Asset(self.value.split(".")[1])


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Direction(StrEnum):
    BUY_DEX = "buy_dex"
    SELL_DEX = "sell_dex"


class Outcome(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"


class WorkerState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class MarketQuote(Record):
    venue: Venue
    side: Side
    base_asset: Asset
    quote_asset: Asset
    base_units: PositiveUnits
    quote_units: PositiveUnits
    snapshot_id: Identifier
    observed_at: Timestamp
    source_at: Timestamp | None = None

    @model_validator(mode="after")
    def validate_market(self) -> "MarketQuote":
        expected = (Asset.WHBAR, Asset.USDC) if self.venue == Venue.DEX else (Asset.HBAR, Asset.USDT)
        if (self.base_asset, self.quote_asset) != expected:
            raise ValueError("assets must match the configured venue")
        return self


class Opportunity(Record):
    id: Identifier
    worker_id: Identifier
    direction: Direction
    requested_dex: MarketQuote
    requested_cex: MarketQuote
    estimated_profit_usdt: Exact
    observed_at: Timestamp
    rejection_reason: Identifier | None = None

    @model_validator(mode="after")
    def validate_legs(self) -> "Opportunity":
        dex_side = Side.BUY if self.direction == Direction.BUY_DEX else Side.SELL
        cex_side = Side.SELL if dex_side == Side.BUY else Side.BUY
        if (self.requested_dex.venue, self.requested_dex.side) != (Venue.DEX, dex_side):
            raise ValueError("DEX quote does not match direction")
        if (self.requested_cex.venue, self.requested_cex.side) != (Venue.CEX, cex_side):
            raise ValueError("CEX quote does not match direction")
        if any(quote.base_units != TRADE_HBAR * 10**Asset.HBAR.decimals
               for quote in (self.requested_dex, self.requested_cex)):
            raise ValueError("both requested quotes must cover exactly 1,000 HBAR")
        return self


class Execution(Record):
    id: Identifier
    opportunity_id: Identifier
    outcome: Outcome
    observed_at: Timestamp
    dex_fill: MarketQuote | None = None
    cex_fill: MarketQuote | None = None

    @model_validator(mode="after")
    def validate_fill_venues(self) -> "Execution":
        if self.dex_fill and self.dex_fill.venue != Venue.DEX:
            raise ValueError("DEX fill must belong to the DEX venue")
        if self.cex_fill and self.cex_fill.venue != Venue.CEX:
            raise ValueError("CEX fill must belong to the CEX venue")
        return self


class Balance(Record):
    holding: Holding
    total_units: Units
    reserved_units: Units = 0

    @model_validator(mode="after")
    def validate_reservation(self) -> "Balance":
        if self.reserved_units > self.total_units:
            raise ValueError("reserved units exceed total units")
        return self

    @property
    def available_units(self) -> int:
        return self.total_units - self.reserved_units


class Reservation(Record):
    id: Identifier
    holding: Holding
    units: PositiveUnits


class LedgerEntry(Record):
    id: Identifier
    holding: Holding
    delta_units: Annotated[int, Field(strict=True, ge=-MAX_UNITS, le=MAX_UNITS)]
    recorded_at: Timestamp


class Worker(Record):
    id: Identifier
    state: WorkerState


class Experiment(Record):
    id: Identifier
    started_at: Timestamp
    deadline_at: Timestamp

    @model_validator(mode="after")
    def validate_duration(self) -> "Experiment":
        if self.deadline_at - self.started_at != EXPERIMENT_DURATION:
            raise ValueError("deadline must be 24 hours after the start")
        return self


class EvaluationStatus(StrEnum):
    MARKET_CANDIDATE = "market_candidate"
    INSUFFICIENT_DATA = "insufficient_data"
    REJECTED = "rejected"
    ELIGIBLE = "eligible_for_simulation"


class Reason(StrEnum):
    WORKER_INACTIVE = "worker_inactive"
    CYCLE_BUSY = "cycle_busy"
    SESSION_DEADLINE = "session_deadline"
    CYCLE_INTERRUPTED = "cycle_interrupted"
    CYCLE_EXPIRED = "cycle_expired"
    NETWORK_QUOTA = "network_quota"
    MARKET_UNAVAILABLE = "market_unavailable"
    INVALID_MARKET = "invalid_market"
    MARKET_IDENTITY_MISMATCH = "market_identity_mismatch"
    READINESS_BLOCKED = "readiness_blocked"
    STALE_OR_INVALID_CLOCK = "stale_or_invalid_clock"
    CROSS_VENUE_SKEW = "cross_venue_skew"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    DUPLICATE_SNAPSHOT = "duplicate_snapshot"
    GAS_UNKNOWN = "gas_unknown"
    GAS_INVALID = "gas_invalid"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    MAXIMUM_LOSS = "maximum_loss"
    PROFIT_THRESHOLD = "profit_threshold"


class HederaGasProvenance(Record):
    provider: Identifier
    chain_id: Literal[295]
    block: PositiveUnits
    block_hash: Annotated[str, Field(pattern=r"^0x[0-9a-f]{64}$")]
    side: Side
    sender: Annotated[str, Field(pattern=r"^0x[0-9a-f]{40}$")]
    router: Annotated[str, Field(pattern=r"^0x[0-9a-f]{40}$")]
    path: tuple[str, str]
    calldata: Annotated[str, Field(pattern=r"^0x[0-9a-f]{520}$")]
    value_weibars: Literal[0]
    requested_deadline: Timestamp
    price_observed_at: Timestamp
    gas_price_weibars: PositiveUnits
    estimated_gas: PositiveUnits
    estimated_at: Timestamp


GAS_SOURCE = "CONFIGURED_GAS_UNITS + LIVE_RPC_GAS_PRICE"


class ConfiguredGasProvenance(Record):
    source: Literal[GAS_SOURCE] = GAS_SOURCE
    provider: Identifier
    chain_id: Literal[295]
    configured_gas_units: PositiveUnits
    gas_limit: PositiveUnits
    gas_price_weibars: PositiveUnits
    price_observed_at: Timestamp

    @model_validator(mode="after")
    def validate_limit(self) -> "ConfiguredGasProvenance":
        if self.configured_gas_units > self.gas_limit:
            raise ValueError("configured gas units must fit the gas limit")
        return self


class Evaluation(Record):
    direction: Direction
    status: EvaluationStatus
    reason: Reason | None = None
    requested_dex: MarketQuote | None = None
    requested_cex: MarketQuote | None = None
    indicative_profit_usdt: Exact | None = None
    gas_cost_usdt: Exact | None = None
    net_profit_usdt: Exact | None = None
    stressed_profit_usdt: Exact | None = None
    gas_evidence_id: Identifier | None = None
    gas_budget_tinybars: PositiveUnits | None = None
    gas_rpc: HederaGasProvenance | None = None
    configured_gas: ConfiguredGasProvenance | None = None
    gas_estimation_error: Identifier | None = None
    dex_net_quote_usdc: Exact | None = None
    estimated_gas_hbar: Exact | None = None
    estimated_gas_usdc: Exact | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "Evaluation":
        if self.status == EvaluationStatus.ELIGIBLE:
            if self.reason is not None or any(value is None for value in (
                self.requested_dex, self.requested_cex, self.indicative_profit_usdt,
                self.gas_cost_usdt, self.net_profit_usdt, self.stressed_profit_usdt,
                self.gas_evidence_id, self.gas_budget_tinybars,
            )):
                raise ValueError("eligibility requires complete evaluated costs")
        elif self.reason is None:
            raise ValueError("noneligible evaluation requires a reason")
        return self


class KuCoinBookObservation(Record):
    symbol: Literal["HBAR-USDT"]
    transport: Literal["websocket", "rest"]
    bid_price: Fraction
    bid_quantity: Fraction
    ask_price: Fraction
    ask_quantity: Fraction


class SnapshotObservation(Record):
    id: Identifier
    source_at: Timestamp
    received_at: Timestamp
    completed_at: Timestamp | None = None
    kucoin_book: KuCoinBookObservation | None = None


class AnalysisCycle(Record):
    id: Identifier
    session_id: Identifier | None = None
    worker_id: Identifier
    observed_at: Timestamp
    dex_snapshot: SnapshotObservation | None = None
    cex_snapshot: SnapshotObservation | None = None

    @property
    def dex_snapshot_id(self) -> str | None:
        return self.dex_snapshot.id if self.dex_snapshot else None

    @property
    def cex_snapshot_id(self) -> str | None:
        return self.cex_snapshot.id if self.cex_snapshot else None
    evaluations: tuple[Evaluation, Evaluation]

    @model_validator(mode="after")
    def validate_directions(self) -> "AnalysisCycle":
        if tuple(item.direction for item in self.evaluations) != tuple(Direction):
            raise ValueError("a cycle must record both directions in order")
        return self
