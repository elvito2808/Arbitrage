"""Persistent, virtual-only execution of coordinator-approved opportunities."""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Annotated

from pydantic import Field, ValidationError

from .config import Simulation
from .coordinator import (
    NETWORK, WEIBARS_PER_TINYBAR, InvalidCycle, Market, OpportunityCoordinator, ceil,
)
from .kucoin import Level, depth_value
from .market_data import TRADE_UNITS, PublicConnections
from .models import (
    MAX_UNITS, AnalysisCycle, Evaluation, EvaluationStatus, Holding, LedgerEntry,
    MarketQuote, PositiveUnits, Reason, Record, Reservation, Side, Timestamp, Units, Venue, WorkerState,
)
from .saucerswap import GasEstimate, RouteGasEvidence
from .storage import Storage, Transaction

MAX_ATTEMPTS = 3
PUBLIC_SIMULATION_SCOPE = "public-market-simulation-v1"
FixtureVerifier = Callable[[GasEstimate, Side, Market, datetime], bool]


class Effect(StrEnum):
    FILL = "fill"
    REVERT = "revert"
    TIMEOUT = "timeout"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Attempt(Record):
    effect: Effect = Effect.FILL
    delay_ms: Annotated[Units, Field(le=60_000)] = 0
    adverse_bps: Annotated[Units, Field(le=50)] = 0
    partial_units: Annotated[Units, Field(lt=TRADE_UNITS)] = 0


class Scenario(Record):
    """Deterministic attempt scripts. Missing attempts use the venue default."""

    dex: Annotated[tuple[Attempt, ...], Field(max_length=MAX_ATTEMPTS)] = ()
    cex: Annotated[tuple[Attempt, ...], Field(max_length=MAX_ATTEMPTS)] = ()

    def attempt(self, venue: Venue, number: int) -> Attempt:
        script = self.dex if venue == Venue.DEX else self.cex
        if number <= len(script):
            return script[number - 1]
        return Attempt(delay_ms=1_000 if venue == Venue.DEX else 0)

    def model_post_init(self, context: object) -> None:
        if any(item.effect == Effect.PARTIAL for item in self.dex):
            raise ValueError("DEX executions are exact size or fail")
        if any(item.effect == Effect.REVERT for item in self.cex):
            raise ValueError("only DEX executions can revert")
        for item in (*self.dex, *self.cex):
            if bool(item.partial_units) != (item.effect == Effect.PARTIAL):
                raise ValueError("partial executions require a positive, smaller quantity")


class GasBasis(Record):
    observed_at: Timestamp
    route_gas: PositiveUnits
    price_weibars: tuple[int, int]
    debit_tinybars: PositiveUnits
    evidence: RouteGasEvidence | None = None


class Request(Record):
    evaluation: Evaluation
    fixture_scope: str
    accepted_at: Timestamp
    cex_depth: tuple[tuple[str, str], ...]
    fee: str
    slippage: str
    gas: GasBasis
    funding: tuple[Reservation, ...]
    scenario: Scenario


class Event(Record):
    id: str
    venue: Venue | None = None
    attempt: Annotated[Units, Field(le=MAX_ATTEMPTS)] = 0
    at: Timestamp
    due_at: Timestamp | None = None
    outcome: Effect | None = None
    fill: MarketQuote | None = None
    fee_units: Units = 0
    gas_units: Units = 0
    ledger: tuple[LedgerEntry, ...] = ()
    remaining: tuple[Reservation, ...] | None
    note: str | None = None


def _assets(quote: MarketQuote) -> tuple[Holding, Holding]:
    if quote.venue == Venue.DEX:
        return Holding.DEX_WHBAR, Holding.DEX_USDC
    return Holding.CEX_HBAR, Holding.CEX_USDT


def _input(quote: MarketQuote) -> Holding:
    base, counter = _assets(quote)
    return counter if quote.side == Side.BUY else base


def _reservation_id(trade_id: str, holding: Holding) -> str:
    return f"sim:{trade_id}:{holding}"


def _round(value: Fraction, side: Side) -> int:
    return ceil(value) if side == Side.BUY else int(value)


class SimulationEngine(OpportunityCoordinator):
    """A coordinator with atomic simulation admission and explicit async recovery."""

    _fixture_scope: str | None = None
    _verify_fixture: FixtureVerifier | None = None
    _scenario = Scenario()
    _sleep: Callable[[float], Awaitable[None]] = staticmethod(asyncio.sleep)
    _public_simulation = False

    @classmethod
    def from_public(
        cls, storage: Storage, settings: Simulation, public: PublicConnections,
    ) -> "SimulationEngine":
        engine = super().from_public(storage, settings, public)
        engine._public_simulation = True
        return engine

    @property
    def _journal_scope(self) -> str | None:
        return PUBLIC_SIMULATION_SCOPE if self._public_simulation else self._fixture_scope

    def _gas(
        self, gas: GasEstimate, side: Side, market: Market, now: datetime,
    ) -> tuple[int, int, str]:
        budget, cost, evidence_id = super()._gas(gas, side, market, now)
        if self._public_simulation:
            trusted = self.dex.verifies_route_gas(gas, market.pool)
        else:
            trusted = (self._fixture_scope is not None and self._verify_fixture is not None
                       and self._verify_fixture(gas, side, market, now))
        if not trusted:
            raise InvalidCycle(Reason.GAS_INVALID)
        return budget, cost, evidence_id

    def _after_analysis(
        self, tx: Transaction, cycle: AnalysisCycle, market: Market, now: datetime,
    ) -> None:
        eligible = next((item for item in cycle.evaluations
                         if item.status == EvaluationStatus.ELIGIBLE), None)
        scope = self._journal_scope
        if eligible is None or scope is None:
            return
        if tx.unresolved_simulation(NETWORK):
            tx.simulation_miss(cycle.id, "unresolved_network_execution")
            return

        # Recheck admission, using the same approved snapshots and the live lease
        # in this transaction. There is no caller-supplied Evaluation entry point.
        now = self._now()
        worker = tx.worker(cycle.worker_id)
        if (not tx.owns_analysis(NETWORK, cycle.id, now)
                or worker is None or worker.state != WorkerState.RUNNING
                or tx.analysis(cycle.id) != cycle
                or not tx.owns_snapshots(NETWORK, cycle.id, (market.dex_snapshot.id, market.cex_snapshot.id))):
            tx.simulation_miss(cycle.id, "cycle_fence_lost")
            return
        try:
            self._timing(market.pool, market.book, now)
        except InvalidCycle as error:
            tx.simulation_miss(cycle.id, error.reason)
            return
        if self._evaluate(tx, eligible.direction, market, now) != eligible:
            tx.simulation_miss(cycle.id, "risk_revalidation_failed")
            return

        dex, cex = eligible.requested_dex, eligible.requested_cex
        budget = eligible.gas_budget_tinybars
        for quote in (dex, cex):
            base, counter = _assets(quote)
            credit, units = ((base, quote.base_units) if quote.side == Side.BUY
                             else (counter, quote.quote_units))
            try:
                headroom = MAX_UNITS - tx.balance(credit).total_units
            except KeyError:
                headroom = -1
            if headroom < units:
                tx.simulation_miss(cycle.id, "insufficient_credit_headroom")
                return
        requirements = {
            _input(quote): (ceil(quote.quote_units * (1 + self.slippage))
                            if quote.side == Side.BUY else quote.base_units)
            for quote in (dex, cex)
        }
        requirements[Holding.DEX_HBAR] = MAX_ATTEMPTS * budget
        if not self._available(tx, requirements):
            tx.simulation_miss(cycle.id, Reason.INSUFFICIENT_BALANCE)
            return
        funding = tuple(Reservation(id=_reservation_id(cycle.id, holding), holding=holding, units=units)
                        for holding, units in requirements.items())
        gas = market.pool.gas[0 if dex.side == Side.BUY else 1]
        request = Request(
            evaluation=eligible, fixture_scope=scope, accepted_at=now,
            cex_depth=tuple((str(level.price), str(level.quantity))
                            for level in (market.book.asks if cex.side == Side.BUY else market.book.bids)),
            fee=str(self.fee), slippage=str(self.slippage),
            gas=GasBasis(
                observed_at=gas.observed_at, route_gas=gas.execution_gas,
                price_weibars=gas.price_range_weibars,
                debit_tinybars=ceil(Fraction(
                    gas.execution_gas * gas.price_range_weibars[0], WEIBARS_PER_TINYBAR,
                )),
                evidence=gas.route_evidence if self._public_simulation else None,
            ),
            funding=funding, scenario=self._scenario,
        )
        for reservation in funding:
            tx.reserve(reservation)
        tx.add_simulation(cycle.id, NETWORK, request.model_dump_json())
        self._append(tx, cycle.id, Event(id=f"sim:{cycle.id}:accepted", at=now, remaining=funding))

    @staticmethod
    def _append(tx: Transaction, trade_id: str, event: Event) -> None:
        tx.add_simulation_event(event.id, trade_id, event.model_dump_json())

    @staticmethod
    def _remaining(tx: Transaction, request: Request) -> tuple[Reservation, ...]:
        return tuple(row for item in request.funding if (row := tx.reservation(item.id)) is not None)

    @staticmethod
    def _release(tx: Transaction, request: Request, *holdings: Holding) -> None:
        for item in request.funding:
            if item.holding in holdings:
                tx.release(item.id)

    @staticmethod
    def _reduce_gas(tx: Transaction, trade_id: str, retained: int) -> None:
        key = _reservation_id(trade_id, Holding.DEX_HBAR)
        current = tx.reservation(key)
        if current is None or not 0 <= retained <= current.units:
            raise ValueError("gas reservation disagrees with journal")
        tx.release(key)
        if retained:
            tx.reserve(Reservation(id=key, holding=Holding.DEX_HBAR, units=retained))

    @staticmethod
    def _fill(
        request: Request, quote: MarketQuote, attempt: Attempt, at: datetime,
    ) -> tuple[MarketQuote | None, int]:
        base, _ = _assets(quote)
        units = attempt.partial_units or quote.base_units
        rate = min(Fraction(attempt.adverse_bps, 10_000), Fraction(request.slippage))
        buy = quote.side == Side.BUY
        adverse = 1 + rate if buy else 1 - rate
        fee = 0
        if quote.venue == Venue.DEX:
            total = _round(quote.quote_units * adverse, quote.side)
        else:
            levels = tuple(Level(Fraction(price), Fraction(size)) for price, size in request.cex_depth)
            gross = depth_value(levels, Fraction(units, 10**base.asset.decimals)) * adverse * 10**6
            rate = Fraction(request.fee)
            total = _round(gross * (1 + rate if buy else 1 - rate), quote.side)
            before_fee = _round(gross, quote.side)
            fee = abs(total - before_fee)
        if total <= 0:
            return None, 0
        fill = MarketQuote.model_validate({
            **quote.model_dump(), "base_units": units, "quote_units": total, "observed_at": at,
        })
        return fill, fee

    def _settle(
        self, tx: Transaction, trade_id: str, request: Request, pending: Event, now: datetime,
    ) -> str:
        venue, number = pending.venue, pending.attempt
        dex, cex = request.evaluation.requested_dex, request.evaluation.requested_cex
        quote = dex if venue == Venue.DEX else cex
        attempt = request.scenario.attempt(venue, number)
        effect = attempt.effect
        status = "active"
        gas, fee = 0, 0
        fill = None
        deltas = {}
        if effect == Effect.UNKNOWN:
            # Only the submitted leg can have spent funds. Hold that leg's input
            # and at most one attempt's gas for manual review.
            if venue == Venue.DEX:
                self._release(tx, request, _input(cex))
                self._reduce_gas(tx, trade_id, request.evaluation.gas_budget_tinybars)
            status = "manual_review"
        else:
            if effect in (Effect.FILL, Effect.PARTIAL):
                fill, fee = self._fill(request, quote, attempt, now)
                if fill is None:
                    effect = Effect.FAILURE
            if venue == Venue.DEX:
                if fill or effect == Effect.REVERT:
                    gas = request.gas.debit_tinybars
                    deltas[Holding.DEX_HBAR] = -gas
                retrying = fill is None and number < MAX_ATTEMPTS
                retained = (MAX_ATTEMPTS - number) * request.evaluation.gas_budget_tinybars if retrying else 0
                self._reduce_gas(tx, trade_id, retained)
            if fill:
                base, counter = _assets(quote)
                sign = 1 if quote.side == Side.BUY else -1
                deltas.update({base: sign * fill.base_units, counter: -sign * fill.quote_units})
                self._release(tx, request, _input(quote))
                if venue == Venue.CEX:
                    status = "filled" if effect == Effect.FILL else "incomplete"
            elif number == MAX_ATTEMPTS:
                status = "failed" if venue == Venue.DEX else "incomplete"
                self._release(tx, request, _input(dex), _input(cex))

        entries = tuple(LedgerEntry(id=f"{pending.id}:{holding}", holding=holding,
                                    delta_units=delta, recorded_at=now)
                        for holding, delta in deltas.items() if delta)
        for entry in entries:
            tx.post(entry)
        self._append(tx, trade_id, Event(
            id=f"{pending.id}:result", venue=venue, attempt=number, at=now, outcome=effect,
            fill=fill, gas_units=gas, fee_units=fee, ledger=entries, remaining=self._remaining(tx, request),
        ))
        if status != "active":
            tx.finish_simulation(trade_id, status)
        return status

    def _advance(self, trade_id: str) -> tuple[str | None, datetime | None]:
        with self.storage.transaction() as tx:
            row = tx.unresolved_simulation(NETWORK)
            if row is None or row["id"] != trade_id or row["status"] == "manual_review":
                return tx.simulation_status(trade_id), None
            request = Request.model_validate_json(row["request"])
            if self._journal_scope is None or request.fixture_scope != self._journal_scope:
                return "manual_review", None
            now = self._now()
            if self._public_simulation:
                evidence = request.gas.evidence
                dex = request.evaluation.requested_dex
                if (evidence is None or dex is None or evidence.side != dex.side
                        or evidence.snapshot_id != dex.snapshot_id
                        or evidence.execution_gas != request.gas.route_gas
                        or evidence.id != request.evaluation.gas_evidence_id):
                    self._append(tx, trade_id, Event(
                        id=f"sim:{trade_id}:review", at=now,
                        remaining=self._remaining(tx, request), note="gas provenance mismatch",
                    ))
                    tx.finish_simulation(trade_id, "manual_review")
                    return "manual_review", None
            try:
                events = tuple(Event.model_validate_json(data) for data in tx.simulation_events(trade_id))
            except ValidationError:
                events = ()
            remaining = self._remaining(tx, request)
            expected_ledger = tuple(entry for event in events for entry in event.ledger)
            if (not events or events[0].id != f"sim:{trade_id}:accepted"
                    or events[-1].remaining != remaining or tx.simulation_ledger(trade_id) != expected_ledger):
                self._append(tx, trade_id, Event(id=f"sim:{trade_id}:review", at=now,
                                                 remaining=remaining, note="journal mismatch"))
                tx.finish_simulation(trade_id, "manual_review")
                return "manual_review", None
            last = events[-1]
            if last.due_at is not None:
                if now < last.due_at:
                    return "active", last.due_at
                return self._settle(tx, trade_id, request, last, now), None
            if last.venue is None:
                venue, number = Venue.DEX, 1
            elif last.venue == Venue.DEX and last.outcome == Effect.FILL:
                venue, number = Venue.CEX, 1
            else:
                # A retry follows a durable, reconciled result proving the prior
                # attempt did not fill. Unknown and partial results are terminal.
                venue, number = last.venue, last.attempt + 1
            attempt = request.scenario.attempt(venue, number)
            now = max(now, last.at)
            due = now + timedelta(milliseconds=attempt.delay_ms)
            self._append(tx, trade_id, Event(
                id=f"sim:{trade_id}:{venue}:{number}", venue=venue, attempt=number,
                at=now, due_at=due, remaining=remaining,
            ))
            return "active", due

    def _settlement_review(self, trade_id: str) -> str | None:
        with self.storage.transaction() as tx:
            row = tx.unresolved_simulation(NETWORK)
            if row is None or row["id"] != trade_id or row["status"] != "active":
                return tx.simulation_status(trade_id)
            try:
                request = Request.model_validate_json(row["request"])
                remaining = self._remaining(tx, request)
            except ValidationError:
                # Keep all holds when the request itself cannot be reconciled.
                remaining = None
            self._append(tx, trade_id, Event(
                id=f"sim:{trade_id}:review", at=self._now(), remaining=remaining,
                note="settlement rejected; existing holds retained",
            ))
            tx.finish_simulation(trade_id, "manual_review")
            return "manual_review"

    async def recover(self) -> str | None:
        """Resolve the existing simulation, if any, without starting a new trade."""
        with self.storage.transaction() as tx:
            row = tx.unresolved_simulation(NETWORK)
        if row is None:
            return None
        status = "active"
        while status == "active":
            try:
                status, due = self._advance(row["id"])
            except (ValueError, sqlite3.IntegrityError):
                if self._journal_scope is None:
                    return "manual_review"
                return self._settlement_review(row["id"])
            if due is not None:
                await self._sleep(max(0, (due - self._now()).total_seconds()))
        return status
