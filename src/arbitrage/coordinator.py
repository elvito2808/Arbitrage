"""Read-only market analysis and durable recording for the approved simulator."""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from uuid import uuid4

from .config import Simulation
from .kucoin import SYMBOL, Book, InsufficientDepth, KuCoin, priced_units, validate_book
from .market_data import (
    MAX_AGE, MAX_SKEW, TRADE_UNITS, MarketDataError, PublicConnections, StaleSnapshot, Unavailable,
    require_fresh, utc_now,
)
from .models import (
    MAX_UNITS, AnalysisCycle, Asset, Direction, Evaluation, EvaluationStatus,
    Holding, MarketQuote, Reason, Side, SnapshotObservation, Venue, Worker, WorkerState,
    normalize_time,
)
from .saucerswap import GAS_PROXY, GasEstimate, PoolSnapshot, RouteGasEvidence, SaucerSwap, amounts
from .storage import Storage, Transaction

NETWORK = "hedera:295"
READ_DEADLINE = 10
LEASE = timedelta(seconds=20)
MIN_INTERVAL = timedelta(seconds=15)
MICRO = 10**Asset.USDT.decimals
WEIBARS = 10**18
WEIBARS_PER_TINYBAR = 10**10


def ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def usdt(units: int) -> Decimal:
    # Construct the decimal exactly, independent of the ambient decimal context.
    return Decimal(f"{units}e-6")


class InvalidCycle(ValueError):
    def __init__(self, reason: Reason):
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class Market:
    pool: PoolSnapshot
    book: Book
    dex_snapshot: SnapshotObservation
    cex_snapshot: SnapshotObservation
    cex_quotes: tuple[MarketQuote, MarketQuote]
    gross_cex_buy_units: int


class OpportunityCoordinator:
    def __init__(
        self, storage: Storage, settings: Simulation, dex: SaucerSwap, cex: KuCoin,
        *, clock: Callable[[], datetime] = utc_now,
        gate: Callable[[Transaction, str, datetime], Reason | None] | None = None,
    ):
        self.storage = storage
        self.dex = dex
        self.cex = cex
        self.clock = clock
        self.gate = gate
        self.age = min(MAX_AGE, timedelta(seconds=settings.cache_ttl_seconds))
        self.fee = Fraction(settings.cex_fee_fraction)
        self.profit_floor = max(Fraction("0.10"), Fraction(settings.minimum_profit_usdt))
        self.loss_limit = min(Fraction("0.30"), Fraction(settings.maximum_loss_usdt))
        self.slippage = min(Fraction("0.005"), Fraction(settings.slippage_fraction))
        self.gas_cap = min(Fraction(2), Fraction(settings.gas_price_cap_multiple))

    @classmethod
    def from_public(
        cls, storage: Storage, settings: Simulation, public: PublicConnections,
    ) -> "OpportunityCoordinator":
        age = timedelta(seconds=settings.cache_ttl_seconds)
        return cls(
            storage, settings, SaucerSwap(public, age),
            KuCoin(public, fee=settings.cex_fee_fraction, cache_age=age), clock=public.clock,
        )

    def worker(self, worker_id: str) -> "AnalysisWorker":
        worker = Worker(id=worker_id, state=WorkerState.STOPPED)
        with self.storage.transaction() as tx:
            if tx.worker(worker.id) is None:
                tx.save_worker(worker)
        return AnalysisWorker(self, worker.id)

    def _now(self) -> datetime:
        return normalize_time(self.clock())

    def _timing(self, pool: PoolSnapshot, book: Book, now: datetime) -> None:
        try:
            for snap in (pool, book):
                require_fresh(snap.source_at, snap.received_at, now, self.age)
        except (MarketDataError, TypeError):
            raise InvalidCycle(Reason.STALE_OR_INVALID_CLOCK) from None
        if (
            abs(pool.source_at - book.source_at) > MAX_SKEW
            or abs(pool.received_at - book.received_at) > MAX_SKEW
        ):
            raise InvalidCycle(Reason.CROSS_VENUE_SKEW)

    @staticmethod
    def _observations(
        pool: PoolSnapshot, book: Book,
    ) -> tuple[SnapshotObservation, SnapshotObservation]:
        if (
            type(pool.block) is not int or pool.block <= 0
            or not isinstance(pool.block_hash, str)
            or not re.fullmatch(r"0x[0-9a-f]{64}", pool.block_hash)
            or type(book.sequence) is not int or book.sequence < 0
        ):
            raise InvalidCycle(Reason.PROVENANCE_MISMATCH)
        try:
            return (
                SnapshotObservation(id=f"{NETWORK}:{pool.block}:{pool.block_hash}",
                                    source_at=pool.source_at, received_at=pool.received_at),
                SnapshotObservation(id=f"kucoin:{SYMBOL}:{book.sequence}",
                                    source_at=book.source_at, received_at=book.received_at),
            )
        except ValueError:
            raise InvalidCycle(Reason.STALE_OR_INVALID_CLOCK) from None

    def _market(
        self, pool: PoolSnapshot, book: Book,
        observations: tuple[SnapshotObservation, SnapshotObservation], now: datetime,
    ) -> Market:
        self._timing(pool, book, now)
        if (type(pool.usdc_reserve) is not int or type(pool.whbar_reserve) is not int
                or len(pool.quotes) != 2):
            raise InvalidCycle(Reason.PROVENANCE_MISMATCH)
        dex, cex = observations
        expected_dex = amounts(pool.usdc_reserve, pool.whbar_reserve)
        validate_book(book)
        quotes = (self.cex.price(book, Side.BUY), self.cex.price(book, Side.SELL))
        expected_cex = (
            priced_units(book.asks, Side.BUY, self.fee),
            priced_units(book.bids, Side.SELL, self.fee),
        )
        for venue, snap, legs, snapshot_id, totals, assets in (
            (Venue.DEX, pool, pool.quotes, dex.id, expected_dex, (Asset.WHBAR, Asset.USDC)),
            (Venue.CEX, book, quotes, cex.id, expected_cex, (Asset.HBAR, Asset.USDT)),
        ):
            for side, quote, total in zip(Side, legs, totals, strict=True):
                if not isinstance(quote, MarketQuote) or (
                    quote.venue != venue or quote.side != side
                    or (quote.base_asset, quote.quote_asset) != assets
                    or quote.base_units != TRADE_UNITS or quote.quote_units != total
                    or quote.snapshot_id != snapshot_id or quote.source_at != snap.source_at
                    or quote.observed_at != snap.received_at
                ):
                    raise InvalidCycle(Reason.PROVENANCE_MISMATCH)
        gross = priced_units(book.asks, Side.BUY, Fraction(0))
        return Market(pool, book, dex, cex, quotes, gross)

    async def _read(self) -> tuple[PoolSnapshot, Book]:
        async with asyncio.timeout(READ_DEADLINE), asyncio.TaskGroup() as group:
            pool = group.create_task(self.dex.snapshot())
            book = group.create_task(self.cex.snapshot())
        return pool.result(), book.result()

    @staticmethod
    def _failure(error: Exception) -> Reason:
        errors = error.exceptions if isinstance(error, ExceptionGroup) else (error,)
        if any(not isinstance(item, (InvalidCycle, MarketDataError, TimeoutError)) for item in errors):
            raise error
        for item in errors:
            if isinstance(item, InvalidCycle):
                return item.reason
            if isinstance(item, StaleSnapshot):
                return Reason.STALE_OR_INVALID_CLOCK
            if isinstance(item, InsufficientDepth):
                return Reason.INSUFFICIENT_DEPTH
            if not isinstance(item, (Unavailable, TimeoutError)):
                return Reason.INVALID_MARKET
        return Reason.MARKET_UNAVAILABLE

    def _gas(
        self, gas: GasEstimate, side: Side, market: Market, now: datetime,
    ) -> tuple[int, int, str]:
        if gas.execution_gas is None:
            raise InvalidCycle(Reason.GAS_UNKNOWN)
        evidence = gas.route_evidence
        prices = gas.price_range_weibars
        if (
            not isinstance(evidence, RouteGasEvidence) or evidence.snapshot_id != market.dex_snapshot.id
            or evidence.side != side or gas.side != side
            or evidence.execution_gas != gas.execution_gas
            or type(gas.execution_gas) is not int or gas.execution_gas <= 0
            or gas.execution_gas in (34_133, GAS_PROXY)
            or gas.observed_at is None or not isinstance(prices, tuple) or len(prices) != 2
            or any(type(price) is not int for price in prices)
            or not 0 < prices[0] <= prices[1] <= 2 * prices[0]
        ):
            raise InvalidCycle(Reason.GAS_INVALID)
        try:
            observed = normalize_time(gas.observed_at)
            require_fresh(observed, observed, now, self.age)
        except (ValueError, TypeError, AttributeError):
            raise InvalidCycle(Reason.GAS_INVALID) from None
        if observed < market.pool.received_at:
            raise InvalidCycle(Reason.GAS_INVALID)
        capped_price = min(prices[1], int(prices[0] * self.gas_cap))
        wei = gas.execution_gas * capped_price
        budget = ceil(Fraction(wei, WEIBARS_PER_TINYBAR))
        if budget > MAX_UNITS:
            raise InvalidCycle(Reason.GAS_INVALID)
        # Use the fee-free full-size CEX ask as a conservative HBAR valuation.
        cost = ceil(Fraction(budget * WEIBARS_PER_TINYBAR * market.gross_cex_buy_units, WEIBARS * 1000))
        return budget, cost, evidence.id

    @staticmethod
    def _available(tx: Transaction, requirements: dict[Holding, int]) -> bool:
        try:
            return all(tx.balance(holding).available_units >= units
                       for holding, units in requirements.items())
        except KeyError:
            return False

    def _evaluate(
        self, tx: Transaction, direction: Direction, market: Market, now: datetime,
    ) -> Evaluation:
        buy_dex = direction == Direction.BUY_DEX
        dex_index = 0 if buy_dex else 1
        dex = market.pool.quotes[dex_index]
        cex = market.cex_quotes[1 - dex_index]
        buy, sell = (dex, cex) if buy_dex else (cex, dex)
        gross = sell.quote_units - buy.quote_units
        fields = dict(
            direction=direction, requested_dex=dex, requested_cex=cex,
            indicative_profit_usdt=usdt(gross),
        )
        stressed_buy = ceil(buy.quote_units * (1 + self.slippage))
        stressed_sell = int(sell.quote_units * (1 - self.slippage))
        requirements = {
            Holding.DEX_USDC if buy_dex else Holding.CEX_USDT: stressed_buy,
            Holding.CEX_HBAR if buy_dex else Holding.DEX_WHBAR: TRADE_UNITS,
        }
        try:
            if len(market.pool.gas) != 2 or not isinstance(market.pool.gas[dex_index], GasEstimate):
                raise InvalidCycle(Reason.GAS_INVALID)
            budget, cost, evidence_id = self._gas(
                market.pool.gas[dex_index], dex.side, market, now,
            )
        except InvalidCycle as error:
            if not self._available(tx, requirements):
                return Evaluation(**fields, status=EvaluationStatus.REJECTED,
                                  reason=Reason.INSUFFICIENT_BALANCE)
            status = (EvaluationStatus.MARKET_CANDIDATE if error.reason == Reason.GAS_UNKNOWN
                      else EvaluationStatus.INSUFFICIENT_DATA)
            return Evaluation(**fields, status=status, reason=error.reason)
        net = gross - cost
        stressed = stressed_sell - stressed_buy - cost
        fields.update(
            gas_cost_usdt=usdt(cost), net_profit_usdt=usdt(net),
            stressed_profit_usdt=usdt(stressed), gas_evidence_id=evidence_id,
            gas_budget_tinybars=budget,
        )
        requirements[Holding.DEX_HBAR] = budget
        reason = None
        if not self._available(tx, requirements):
            reason = Reason.INSUFFICIENT_BALANCE
        elif stressed < -self.loss_limit * MICRO:
            reason = Reason.MAXIMUM_LOSS
        elif net <= self.profit_floor * MICRO:
            reason = Reason.PROFIT_THRESHOLD
        return Evaluation(
            **fields, status=EvaluationStatus.REJECTED if reason else EvaluationStatus.ELIGIBLE,
            reason=reason,
        )

    @staticmethod
    def _miss(
        cycle_id: str, worker_id: str, now: datetime, reason: Reason,
        observations: tuple[SnapshotObservation | None, SnapshotObservation | None] = (None, None),
    ) -> AnalysisCycle:
        status = (EvaluationStatus.REJECTED if reason in {
            Reason.DUPLICATE_SNAPSHOT, Reason.CYCLE_BUSY, Reason.NETWORK_QUOTA, Reason.WORKER_INACTIVE,
        } else EvaluationStatus.INSUFFICIENT_DATA)
        return AnalysisCycle(
            id=cycle_id, worker_id=worker_id, observed_at=now,
            dex_snapshot=observations[0], cex_snapshot=observations[1],
            evaluations=tuple(Evaluation(direction=direction, status=status, reason=reason)
                              for direction in Direction),
        )

    def _record(
        self, tx: Transaction, cycle_id: str, worker_id: str,
        market: Market | None, reason: Reason | None,
        observations: tuple[SnapshotObservation | None, SnapshotObservation | None],
    ) -> AnalysisCycle:
        now = self._now()
        worker = tx.worker(worker_id)
        if not tx.owns_analysis(NETWORK, cycle_id, now):
            reason = Reason.CYCLE_EXPIRED
        elif worker.state != WorkerState.RUNNING:
            reason = Reason.WORKER_INACTIVE
        if self.gate:
            reason = self.gate(tx, worker_id, now) or reason
        if reason is None and market is None:
            raise ValueError("analysis requires a market or a reason")
        if reason is None:
            try:
                self._timing(market.pool, market.book, now)
            except InvalidCycle as error:
                reason = error.reason
        if reason is None:
            ids = (market.dex_snapshot.id, market.cex_snapshot.id)
            if any(tx.seen_snapshot(NETWORK, key) for key in ids):
                reason = Reason.DUPLICATE_SNAPSHOT
        if reason is not None:
            result = self._miss(cycle_id, worker_id, now, reason, observations)
            tx.add_analysis(NETWORK, result)
        else:
            result = AnalysisCycle(
                id=cycle_id, worker_id=worker_id, observed_at=now,
                dex_snapshot=observations[0], cex_snapshot=observations[1],
                evaluations=tuple(self._evaluate(tx, direction, market, now) for direction in Direction),
            )
            tx.add_analysis(NETWORK, result, ids)
            self._after_analysis(tx, result, market, now)
        tx.release_analysis(NETWORK, cycle_id, now)
        return result

    def _after_analysis(
        self, tx: Transaction, cycle: AnalysisCycle, market: Market, now: datetime,
    ) -> None:
        """Optional in-transaction consumer of a freshly evaluated market."""

    async def _cycle(self, worker_id: str) -> AnalysisCycle:
        cycle_id = uuid4().hex
        with self.storage.transaction() as tx:
            now = self._now()
            denied = self.gate(tx, worker_id, now) if self.gate else None
            if denied is None:
                denied = tx.claim_analysis(NETWORK, cycle_id, worker_id, now, LEASE, MIN_INTERVAL)
            if denied:
                result = self._miss(cycle_id, worker_id, now, denied)
                tx.add_analysis(NETWORK, result)
                return result
        recorded = False
        try:
            market, reason = None, None
            observations = (None, None)
            try:
                pool, book = await self._read()
                observations = self._observations(pool, book)
                market = self._market(pool, book, observations, self._now())
            except (InvalidCycle, MarketDataError, TimeoutError, ExceptionGroup) as error:
                reason = self._failure(error)
            with self.storage.transaction() as tx:
                result = self._record(tx, cycle_id, worker_id, market, reason, observations)
            recorded = True
            return result
        except asyncio.CancelledError:
            if self.gate:
                with self.storage.transaction() as tx:
                    now = self._now()
                    reason = self.gate(tx, worker_id, now) or Reason.CYCLE_INTERRUPTED
                    tx.add_analysis(NETWORK, self._miss(cycle_id, worker_id, now, reason))
            raise
        finally:
            if not recorded:
                with self.storage.transaction() as tx:
                    tx.release_analysis(NETWORK, cycle_id, self._now())


@dataclass(frozen=True)
class AnalysisWorker:
    coordinator: OpportunityCoordinator
    id: str

    def start(self) -> None:
        coordinator = self.coordinator
        with coordinator.storage.transaction() as tx:
            if tx.worker(self.id).state == WorkerState.RUNNING:
                return
            if tx.worker_has_analysis(NETWORK, self.id, coordinator._now()):
                raise ValueError("prior worker cycle is still in progress")
            tx.save_worker(Worker(id=self.id, state=WorkerState.RUNNING))

    def stop(self) -> None:
        with self.coordinator.storage.transaction() as tx:
            tx.save_worker(Worker(id=self.id, state=WorkerState.STOPPED))

    async def analyze(self) -> AnalysisCycle:
        return await self.coordinator._cycle(self.id)
