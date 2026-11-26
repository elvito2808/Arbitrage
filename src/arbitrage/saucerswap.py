"""Read-only SaucerSwap V1 reserve and router pricing."""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from hashlib import sha256
from math import ceil
from urllib.parse import urlsplit

from .market_data import (
    MAX_AGE, RPC, TRADE_UNITS, MarketDataError, PublicConnections, Unavailable, require_fresh,
)
from .models import Asset, HederaGasProvenance, Identifier, MarketQuote, PositiveUnits, Record, Side, Venue

PAIR = "0xdb34c1ef944883f0e5a2fc18b6c1978b088bd31d"
USDC = "0x000000000000000000000000000000000006f89a"
WHBAR = "0x0000000000000000000000000000000000163b5a"
FACTORY = "0x0000000000000000000000000000000000103780"
ROUTER = "0x00000000000000000000000000000000002e7a5d"
CHAIN_ID = 295
WEIBARS_PER_TINYBAR = 10**10
MAX_TRANSACTION_GAS = 15_000_000
MIN_CONTRACT_GAS = 21_000
FEE_PROVIDER = urlsplit(RPC).netloc
EXACT_OUTPUT_SELECTOR = "8803dbee"
EXACT_INPUT_SELECTOR = "38ed1739"


@dataclass(frozen=True)
class HistoricalGasCalibration:
    transaction_id: str = "0.0.1268672@1795674832.261067947"
    router: str = ROUTER
    selector: str = "7ff36ab5"
    path: tuple[str, str] = (WHBAR, USDC)
    input_whbar_units: int = 100_000_000_000
    output_usdc_units: int = 53_048_295
    gas_limit: int = 1_120_000
    gas_used: int = 143_161
    gas_price_weibars: int = 1_590_000_000_000
    charged_gas_tinybars: int = 22_762_599

    @property
    def calculated_gas_tinybars(self) -> int:
        return gas_cost_tinybars(self.gas_used, self.gas_price_weibars)


HISTORICAL_GAS = HistoricalGasCalibration()


def gas_cost_tinybars(gas: int, price_weibars: int) -> int:
    return ceil(Fraction(gas * price_weibars, WEIBARS_PER_TINYBAR))


def quantity(result: object, lower: int, upper: int) -> int:
    if (
        not isinstance(result, str) or len(result) > len(hex(upper))
        or not re.fullmatch(r"0x(?:0|[1-9a-f][0-9a-f]*)", result)
    ):
        raise MarketDataError("invalid Hedera JSON-RPC quantity")
    if not lower <= (value := int(result, 16)) <= upper:
        raise MarketDataError("Hedera JSON-RPC quantity outside supported range")
    return value


def word(value: int) -> str:
    return f"{value:064x}"


def address_word(address: str) -> str:
    return word(int(address, 16))


def words(result: object, length: int) -> tuple[int, ...]:
    if not isinstance(result, str) or not result.startswith("0x") or len(result) != 2 + 64 * length:
        raise MarketDataError("invalid ABI response length")
    try:
        return tuple(int(result[i:i + 64], 16) for i in range(2, len(result), 64))
    except ValueError:
        raise MarketDataError("invalid ABI response") from None


def amounts(usdc: int, whbar: int) -> tuple[int, int]:
    if not 0 < usdc < 2**112 or not TRADE_UNITS < whbar < 2**112:
        raise MarketDataError("invalid or insufficient target pool reserves")
    buy = usdc * TRADE_UNITS * 1000 // ((whbar - TRADE_UNITS) * 997) + 1
    sell = TRADE_UNITS * 997 * usdc // (whbar * 1000 + TRADE_UNITS * 997)
    if sell == 0:
        raise MarketDataError("pool sell rounds to zero")
    return buy, sell


class RouteGasEvidence(Record):
    """Reference from a trusted route-specific estimation provider."""

    id: Identifier
    snapshot_id: Identifier
    side: Side
    execution_gas: PositiveUnits
    rpc: HederaGasProvenance | None = None


@dataclass(frozen=True)
class GasEstimate:
    side: Side
    observed_at: datetime | None
    price_range_weibars: tuple[int, int] | None
    execution_gas: int | None = None
    route_evidence: RouteGasEvidence | None = None
    estimation_error: str | None = None
    invalid: bool = False

    @property
    def cost_valid(self) -> bool:
        return self.execution_gas is not None and self.price_range_weibars is not None


@dataclass(frozen=True)
class PoolSnapshot:
    block: int
    block_hash: str
    source_at: datetime
    received_at: datetime
    usdc_reserve: int
    whbar_reserve: int
    reserve_update_epoch: int
    router_verified: bool
    quotes: tuple[MarketQuote, MarketQuote]
    gas: tuple[GasEstimate, GasEstimate]


class SaucerSwap:
    def __init__(
        self, public: PublicConnections, cache_age: timedelta = MAX_AGE, *,
        estimate_sender: str | None = None, slippage: Fraction = Fraction("0.005"),
    ):
        if not timedelta(0) < cache_age <= MAX_AGE:
            raise ValueError("cache age must be positive and at most 10 seconds")
        self.public = public
        self.cache_hits = 0
        self.cache_age = cache_age
        if estimate_sender is not None and (
            not re.fullmatch(r"0x[0-9a-f]{40}", estimate_sender) or int(estimate_sender, 16) == 0
        ):
            raise ValueError("expected a nonzero public lowercase EVM address")
        self.estimate_sender = estimate_sender
        self.slippage = slippage
        self._cached: PoolSnapshot | None = None
        self._lock = asyncio.Lock()

    async def _call(self, address: str, data: str, block: str, length: int) -> tuple[int, ...]:
        result = await self.public.rpc("eth_call", {"to": address, "data": "0x" + data}, block)
        return words(result, length)

    def _estimate_call(
        self, side: Side, sender: str, buy: int, sell: int, deadline: datetime,
    ) -> tuple[dict[str, str], tuple[str, str]]:
        if side == Side.BUY:
            selector, path = EXACT_OUTPUT_SELECTOR, (USDC, WHBAR)
            amount, boundary = TRADE_UNITS, ceil(buy * (1 + self.slippage))
        else:
            selector, path = EXACT_INPUT_SELECTOR, (WHBAR, USDC)
            amount, boundary = TRADE_UNITS, int(sell * (1 - self.slippage))
        if boundary <= 0:
            raise MarketDataError("invalid SaucerSwap gas-estimate amount bound")
        data = selector + "".join(map(word, (
            amount, boundary, 160, int(sender, 16), int(deadline.timestamp()),
            2, int(path[0], 16), int(path[1], 16),
        )))
        return {"from": sender, "to": ROUTER, "data": "0x" + data, "value": "0x0"}, path

    async def _gas_price(self) -> tuple[datetime | None, int | None, str | None]:
        try:
            async with asyncio.timeout(2):
                raw = await self.public.rpc("eth_gasPrice")
            price = quantity(raw, WEIBARS_PER_TINYBAR, 10**18)
        except (Unavailable, TimeoutError):
            return None, None, "gas_price_unavailable"
        except MarketDataError:
            return None, None, "gas_price_invalid"
        return self.public.clock(), price, None

    async def _estimate(
        self, side: Side, sender: str, snapshot_id: str, block: int, block_hash: str,
        deadline: datetime, buy: int, sell: int, price_observed: datetime, price: int,
    ) -> GasEstimate:
        prices = (price, price * 2)
        try:
            call, path = self._estimate_call(side, sender, buy, sell, deadline)
            async with asyncio.timeout(2):
                raw = await self.public.rpc("eth_estimateGas", call, "latest")
        except (MarketDataError, TimeoutError):
            return GasEstimate(side, price_observed, prices, estimation_error="route_estimate_unavailable")
        try:
            estimate = quantity(raw, MIN_CONTRACT_GAS, MAX_TRANSACTION_GAS)
            observed = self.public.clock()
            require_fresh(price_observed, price_observed, observed, self.cache_age)
            if observed >= deadline:
                raise MarketDataError("route deadline elapsed")
            provenance = HederaGasProvenance(
                provider=FEE_PROVIDER, chain_id=CHAIN_ID, block=block, block_hash=block_hash,
                side=side, sender=sender, router=ROUTER, path=path, calldata=call["data"],
                value_weibars=0, requested_deadline=deadline, price_observed_at=price_observed,
                gas_price_weibars=price, estimated_gas=estimate, estimated_at=observed,
            )
        except ValueError:
            return GasEstimate(side, price_observed, prices, estimation_error="route_estimate_invalid", invalid=True)
        evidence = RouteGasEvidence(
            id="hedera-gas:" + sha256(provenance.model_dump_json().encode()).hexdigest(),
            snapshot_id=snapshot_id, side=side, execution_gas=estimate, rpc=provenance,
        )
        return GasEstimate(side, observed, prices, estimate, evidence)

    async def _route_gas(
        self, block: int, block_hash: str, source: datetime, buy: int, sell: int,
        router_verified: bool, sampled: tuple[datetime | None, int | None, str | None],
    ) -> tuple[GasEstimate, GasEstimate]:
        observed, price, price_error = sampled
        prices = (price, price * 2) if price is not None else None
        deadline = (source + self.cache_age).replace(microsecond=0)
        sender = self.estimate_sender
        error = price_error
        if error is None and sender is None:
            error = "estimate_sender_unconfigured"
        if error is None and (not router_verified or self.public.clock() >= deadline):
            error = "route_context_unverified"
        if error is not None:
            return tuple(GasEstimate(
                side, observed, prices, estimation_error=error,
                invalid=error in {"gas_price_invalid", "route_context_unverified"},
            ) for side in (Side.BUY, Side.SELL))
        assert sender is not None and observed is not None and price is not None
        snapshot_id = f"hedera:{CHAIN_ID}:{block}:{block_hash}"
        estimates = await asyncio.gather(*(
            self._estimate(side, sender, snapshot_id, block, block_hash, deadline, buy, sell, observed, price)
            for side in (Side.BUY, Side.SELL)
        ))
        if any(gas.cost_valid for gas in estimates):
            try:
                # Hashio's relay estimates latest state even if a historical block
                # is requested. Reject across a head change rather than claiming
                # a historical estimate from the ignored block parameter.
                async with asyncio.timeout(2):
                    head = await self.public.rpc("eth_getBlockByNumber", "latest", False)
                stable = isinstance(head, dict) and (head.get("number"), head.get("hash")) == (hex(block), block_hash)
            except (MarketDataError, TimeoutError):
                stable = False
            if not stable:
                return tuple(
                    GasEstimate(
                        gas.side, observed, prices, estimation_error="route_estimate_head_changed", invalid=True,
                    ) if gas.cost_valid else gas for gas in estimates
                )
        return tuple(estimates)

    def verifies_route_gas(self, gas: GasEstimate, pool: PoolSnapshot) -> bool:
        return (
            pool is self._cached
            and len(pool.gas) == 2
            and gas is pool.gas[0 if gas.side == Side.BUY else 1]
            and gas.route_evidence is not None
            and gas.route_evidence.rpc is not None
        )

    async def _router(self, block: str, buy: int, sell: int) -> bool:
        async def check(
            selector: str, path: tuple[str, str], expected: tuple[int, ...]
        ) -> bool:
            data = selector + word(TRADE_UNITS) + word(64) + word(2)
            data += "".join(address_word(address) for address in path)
            try:
                quoted = await self._call(ROUTER, data, block, 4)
            except Unavailable:
                return False
            if quoted != expected:
                raise MarketDataError("SaucerSwap router and integer quote disagree")
            return True

        results = await asyncio.gather(
            check("1f00ca74", (USDC, WHBAR), (32, 2, buy, TRADE_UNITS)),
            check("d06ca61f", (WHBAR, USDC), (32, 2, TRADE_UNITS, sell)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return all(results)

    async def _read(self) -> PoolSnapshot:
        if await self.public.rpc("eth_chainId") != hex(CHAIN_ID):
            raise MarketDataError("expected Hedera chain ID 295")
        block = await self.public.rpc("eth_getBlockByNumber", "latest", False)
        header_received = self.public.clock()
        try:
            number = int(block["number"], 16)
            block_hash = block["hash"]
            block_timestamp = int(block["timestamp"], 16)
            if not isinstance(block_hash, str) or not re.fullmatch(r"0x[0-9a-f]{64}", block_hash):
                raise ValueError("invalid block hash")
        except (KeyError, TypeError, ValueError):
            raise MarketDataError("invalid Hedera RPC block") from None
        tag = hex(number)
        async with asyncio.TaskGroup() as group:
            mirror = group.create_task(self.public.mirror_block(number))
            factory_pair = group.create_task(self._call(
                FACTORY, "e6a43905" + address_word(USDC) + address_word(WHBAR), tag, 1
            ))
            token0 = group.create_task(self._call(PAIR, "0dfe1681", tag, 1))
            token1 = group.create_task(self._call(PAIR, "d21220a7", tag, 1))
            decimals0 = group.create_task(self._call(USDC, "313ce567", tag, 1))
            decimals1 = group.create_task(self._call(WHBAR, "313ce567", tag, 1))
            reserves = group.create_task(self._call(PAIR, "0902f1ac", tag, 3))
        received = self.public.clock()
        expected = (
            (int(PAIR, 16),), (int(USDC, 16),), (int(WHBAR, 16),), (6,), (8,),
        )
        if tuple(task.result() for task in (factory_pair, token0, token1, decimals0, decimals1)) != expected:
            raise MarketDataError("SaucerSwap factory, token ordering or decimals mismatch")
        try:
            mirror_data = mirror.result()
            seconds, _, fraction = mirror_data["timestamp"]["to"].partition(".")
            source = datetime.fromtimestamp(int(seconds), UTC).replace(
                microsecond=int(fraction[:6].ljust(6, "0"))
            )
            if (
                mirror_data["number"] != number
                or mirror_data["hash"][:66] != block_hash
                or abs(block_timestamp - int(seconds)) > 2
            ):
                raise MarketDataError("Hedera block provenance mismatch")
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError("invalid Hedera mirror block") from None
        require_fresh(source, header_received, received, self.cache_age)
        usdc, whbar, update = reserves.result()
        if not 0 <= update < 2**32:
            raise MarketDataError("invalid pair reserve timestamp")
        buy, sell = amounts(usdc, whbar)
        async with asyncio.TaskGroup() as group:
            router = group.create_task(self._router(tag, buy, sell))
            gas_price = group.create_task(self._gas_price())
        gas = await self._route_gas(number, block_hash, source, buy, sell, router.result(), gas_price.result())
        completed = self.public.clock()
        require_fresh(source, received, completed, self.cache_age)
        snapshot_id = f"hedera:295:{number}:{block_hash}"
        return PoolSnapshot(
            number, block_hash, source, received, usdc, whbar, update, router.result(),
            tuple(MarketQuote(
                venue=Venue.DEX, side=side, base_asset=Asset.WHBAR, quote_asset=Asset.USDC,
                base_units=TRADE_UNITS, quote_units=total, snapshot_id=snapshot_id,
                observed_at=received, source_at=source,
            ) for side, total in ((Side.BUY, buy), (Side.SELL, sell))),
            gas,
        )

    async def snapshot(self) -> PoolSnapshot:
        async with self._lock:
            try:
                try:
                    async with asyncio.timeout(9):
                        snapshot = await self._read()
                except TimeoutError:
                    raise Unavailable("pool snapshot deadline exceeded") from None
            except (MarketDataError, ExceptionGroup) as exc:
                errors = exc.exceptions if isinstance(exc, ExceptionGroup) else (exc,)
                if all(isinstance(error, Unavailable) for error in errors) and self._cached:
                    try:
                        require_fresh(
                            self._cached.source_at, self._cached.received_at,
                            self.public.clock(), self.cache_age,
                        )
                    except MarketDataError:
                        self._cached = None
                        raise
                    self.cache_hits += 1
                    return self._cached
                self._cached = None
                if isinstance(exc, ExceptionGroup):
                    invalid = next((error for error in errors if not isinstance(error, Unavailable)), None)
                    if invalid is not None:
                        raise invalid from None
                    raise errors[0] from None
                raise
            self._cached = snapshot
            return snapshot

    async def quote(self, side: Side, whbar_units: int) -> MarketQuote:
        if whbar_units != TRADE_UNITS:
            raise MarketDataError("expected exactly 1,000 WHBAR")
        return (await self.snapshot()).quotes[0 if side == Side.BUY else 1]
