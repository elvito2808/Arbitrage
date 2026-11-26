"""Read-only SaucerSwap V1 reserve and router pricing."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .market_data import MAX_AGE, TRADE_UNITS, MarketDataError, PublicConnections, Unavailable, require_fresh
from .models import Asset, Identifier, MarketQuote, PositiveUnits, Record, Side, Venue

PAIR = "0xdb34c1ef944883f0e5a2fc18b6c1978b088bd31d"
USDC = "0x000000000000000000000000000000000006f89a"
WHBAR = "0x0000000000000000000000000000000000163b5a"
FACTORY = "0x0000000000000000000000000000000000103780"
ROUTER = "0x00000000000000000000000000000000002e7a5d"
GAS_PROXY = 244_829


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


@dataclass(frozen=True)
class GasEstimate:
    side: Side
    observed_at: datetime | None
    price_range_weibars: tuple[int, int] | None
    execution_gas: int | None = None
    historical_proxy_gas: int = GAS_PROXY
    provenance: str = "P01 single historical swap, route and direction coverage unknown"
    route_evidence: RouteGasEvidence | None = None

    @property
    def cost_valid(self) -> bool:
        return self.execution_gas is not None and self.price_range_weibars is not None

    @property
    def capped_proxy_weibars(self) -> int | None:
        if self.price_range_weibars is not None:
            return self.historical_proxy_gas * self.price_range_weibars[1]
        return None


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
    def __init__(self, public: PublicConnections, cache_age: timedelta = MAX_AGE):
        if not timedelta(0) < cache_age <= MAX_AGE:
            raise ValueError("cache age must be positive and at most 10 seconds")
        self.public = public
        self.cache_hits = 0
        self.cache_age = cache_age
        self._cached: PoolSnapshot | None = None
        self._lock = asyncio.Lock()

    async def _call(self, address: str, data: str, block: str, length: int) -> tuple[int, ...]:
        result = await self.public.rpc("eth_call", {"to": address, "data": "0x" + data}, block)
        return words(result, length)

    async def _gas(self) -> tuple[GasEstimate, GasEstimate]:
        try:
            price = int(await self.public.rpc("eth_gasPrice"), 16)
            if price <= 0:
                raise ValueError
        except (MarketDataError, ValueError, TypeError):
            return GasEstimate(Side.BUY, None, None), GasEstimate(Side.SELL, None, None)
        observed = self.public.clock()
        return (
            GasEstimate(Side.BUY, observed, (price, price * 2)),
            GasEstimate(Side.SELL, observed, (price, price * 2)),
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
        if await self.public.rpc("eth_chainId") != "0x127":
            raise MarketDataError("expected Hedera chain ID 295")
        block = await self.public.rpc("eth_getBlockByNumber", "latest", False)
        header_received = self.public.clock()
        try:
            number = int(block["number"], 16)
            block_hash = block["hash"]
            block_timestamp = int(block["timestamp"], 16)
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
                or len(block_hash) != 66
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
            gas = group.create_task(self._gas())
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
            gas.result(),
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
