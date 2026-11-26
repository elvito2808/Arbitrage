"""P02 module contracts for later simulation stages."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from .models import (
    Balance as BalanceRecord,
    Direction,
    Execution as ExecutionRecord,
    Experiment,
    Holding,
    MarketQuote,
    Opportunity as OpportunityRecord,
    Reservation,
    Side,
    Worker,
    WorkerState,
)
from .operational_log import Event, Level


class Connections(Protocol):
    async def get_public_json(self, url: str) -> Mapping[str, object]: ...


class DEX(Protocol):
    async def quote(self, side: Side, whbar_units: int) -> MarketQuote: ...


class CEX(Protocol):
    async def quote(self, side: Side, hbar_units: int) -> MarketQuote: ...


class Opportunity(Protocol):
    def evaluate(
        self, worker_id: str, direction: Direction, dex: MarketQuote, cex: MarketQuote
    ) -> OpportunityRecord: ...


class Execution(Protocol):
    async def simulate(self, opportunity: OpportunityRecord) -> ExecutionRecord: ...


class Balance(Protocol):
    def balance(self, holding: Holding) -> BalanceRecord: ...

    def reserve(self, reservation: Reservation) -> None: ...

    def release(self, reservation_id: str) -> bool: ...


class Lifecycle(Protocol):
    async def set_state(self, worker_id: str, state: WorkerState) -> Worker: ...


class Monitoring(Protocol):
    def emit(self, event: Event, level: Level = Level.INFO) -> bool: ...


class Reporting(Protocol):
    def export(
        self,
        destination: Path,
        experiment: Experiment,
        opportunities: Sequence[OpportunityRecord],
        executions: Sequence[ExecutionRecord],
    ) -> None: ...
