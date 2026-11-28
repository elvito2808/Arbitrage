"""Read-only SaucerSwap V1 reserve and router pricing."""

import asyncio
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from .market_data import (
    MAX_AGE, RPC, TRADE_UNITS, MarketDataError, MarketIdentityError, PublicConnections, ProviderRateLimited, ProviderTimeout,
    BlockIdentityMismatch, BlockNotFound, MarketFailure, StaleSnapshot, Unavailable, market_errors, market_failure, require_fresh,
)
from .config import DEFAULT_GAS_LIMIT, DEFAULT_GAS_UNITS, Simulation
from .readiness import Diagnostic
from .models import (
    Asset, ConfiguredGasProvenance, HederaGasProvenance, Identifier, MarketQuote, PositiveUnits, Record, Side, Venue,
)

PAIR = "0xdb34c1ef944883f0e5a2fc18b6c1978b088bd31d"
USDC = "0x000000000000000000000000000000000006f89a"
WHBAR = "0x0000000000000000000000000000000000163b5a"
FACTORY = "0x0000000000000000000000000000000000103780"
ROUTER = "0x00000000000000000000000000000000002e7a5d"
CHAIN_ID = 295
WEIBARS_PER_TINYBAR = 10**10
FEE_PROVIDER = urlsplit(RPC).netloc


class HederaIdentityError(MarketIdentityError):
    """Hedera or SaucerSwap returned an unexpected public identity."""


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
    return (gas * price_weibars + WEIBARS_PER_TINYBAR - 1) // WEIBARS_PER_TINYBAR


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
    """Gas inputs associated with a direction and market snapshot."""

    id: Identifier
    snapshot_id: Identifier
    side: Side
    execution_gas: PositiveUnits
    rpc: HederaGasProvenance | None = None
    configured: ConfiguredGasProvenance | None = None


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
        settings: Simulation | None = None,
    ):
        if not timedelta(0) < cache_age <= MAX_AGE:
            raise ValueError("cache age must be positive and at most 10 seconds")
        self.public = public
        self.cache_hits = 0
        self.cache_age = cache_age
        self.gas_units = (
            settings.buy_gas_units if settings else DEFAULT_GAS_UNITS,
            settings.sell_gas_units if settings else DEFAULT_GAS_UNITS,
        )
        self.gas_limit = settings.gas_limit if settings else DEFAULT_GAS_LIMIT
        self.rpc_verified = False
        self.mirror_verified = False
        self.failures: tuple[MarketFailure, ...] = ()
        self.router_failure: MarketFailure | None = None
        self._cached: PoolSnapshot | None = None
        self._lock = asyncio.Lock()

    async def _call(self, address: str, data: str, block: str, length: int) -> tuple[int, ...]:
        result = await self.public.rpc("eth_call", {"to": address, "data": "0x" + data}, block)
        try:
            return words(result, length)
        except MarketDataError as error:
            error.failure = MarketFailure(
                "hedera_rpc", "eth_call", Diagnostic.INVALID_PROVIDER_RESPONSE,
                http_status=200, expected_block=int(block, 16), field="result",
            )
            raise

    async def _gas_price(self) -> tuple[datetime | None, int | None, str | None]:
        try:
            async with asyncio.timeout(2):
                raw = await self.public.rpc("eth_gasPrice")
            price = quantity(raw, 1, 10**18)
        except ProviderRateLimited:
            return None, None, "provider_rate_limited"
        except (ProviderTimeout, TimeoutError):
            return None, None, "provider_timeout"
        except Unavailable:
            return None, None, "gas_price_unavailable"
        except MarketDataError:
            return None, None, "gas_price_invalid"
        return self.public.clock(), price, None

    def _configured_gas(
        self, snapshot_id: str, sampled: tuple[datetime | None, int | None, str | None],
    ) -> tuple[GasEstimate, GasEstimate]:
        observed, price, error = sampled
        if error is not None:
            return tuple(GasEstimate(
                side, observed, None, estimation_error=error, invalid=error == "gas_price_invalid",
            ) for side in Side)
        assert observed is not None and price is not None
        estimates = []
        for side, units in zip(Side, self.gas_units, strict=True):
            provenance = ConfiguredGasProvenance(
                provider=FEE_PROVIDER, chain_id=CHAIN_ID, configured_gas_units=units,
                gas_limit=self.gas_limit, gas_price_weibars=price, price_observed_at=observed,
            )
            evidence = RouteGasEvidence(
                id="hedera-configured-gas:" + sha256(
                    f"{snapshot_id}:{side}:{provenance.model_dump_json()}".encode()
                ).hexdigest(),
                snapshot_id=snapshot_id, side=side, execution_gas=units, configured=provenance,
            )
            estimates.append(GasEstimate(side, observed, (price, price * 2), units, evidence))
        return tuple(estimates)

    def verifies_route_gas(self, gas: GasEstimate, pool: PoolSnapshot) -> bool:
        return (
            pool is self._cached
            and len(pool.gas) == 2
            and gas is pool.gas[0 if gas.side == Side.BUY else 1]
            and gas.route_evidence is not None
            and gas.route_evidence.configured is not None
            and gas.route_evidence.rpc is None
        )

    async def _router(self, block: str, buy: int, sell: int) -> bool:
        async def check(
            selector: str, path: tuple[str, str], expected: tuple[int, ...]
        ) -> bool:
            data = selector + word(TRADE_UNITS) + word(64) + word(2)
            data += "".join(address_word(address) for address in path)
            try:
                quoted = await self._call(ROUTER, data, block, 4)
            except Unavailable as error:
                if self.router_failure is None:
                    self.router_failure = market_failure(error, "saucerswap", "router_quote")
                return False
            if quoted != expected:
                raise MarketDataError("SaucerSwap router and integer quote disagree", MarketFailure(
                    "saucerswap", "router_quote", Diagnostic.ROUTER_QUOTE_MISMATCH,
                    expected_block=int(block, 16),
                ))
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

    async def _rpc_header(self) -> tuple[int, str, datetime]:
        raw_chain = await self.public.rpc("eth_chainId")
        try:
            chain = quantity(raw_chain, 0, 2**64 - 1)
        except MarketDataError:
            raise MarketDataError("invalid Hedera RPC chain ID", MarketFailure(
                "hedera_rpc", "eth_chainId", Diagnostic.INVALID_PROVIDER_RESPONSE, http_status=200, field="result",
            )) from None
        if chain != CHAIN_ID:
            raise HederaIdentityError("expected Hedera chain ID 295", MarketFailure(
                "hedera_rpc", "eth_chainId", Diagnostic.MARKET_IDENTITY_MISMATCH, http_status=200,
            ))
        block = await self.public.rpc("eth_getBlockByNumber", "latest", False)
        values = {}
        for field in ("number", "hash", "timestamp") if isinstance(block, dict) else ("response",):
            raw = block.get(field) if isinstance(block, dict) else None
            try:
                if field == "hash" and isinstance(raw, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", raw):
                    values[field] = raw.lower()
                elif field == "number":
                    values[field] = quantity(raw, 1, 2**63 - 1)
                elif field == "timestamp":
                    values[field] = datetime.fromtimestamp(quantity(raw, 1, 253402300799), UTC)
                else:
                    raise ValueError
            except (ValueError, OverflowError, OSError):
                raise MarketDataError("invalid Hedera RPC block", MarketFailure(
                    "hedera_rpc", "eth_getBlockByNumber", Diagnostic.INVALID_PROVIDER_RESPONSE,
                    http_status=200, field=field, hash_class="invalid_rpc_32_byte_hash" if field == "hash" else None,
                )) from None
        self.rpc_verified = True
        return values["number"], values["hash"], values["timestamp"]

    async def _mirror_block(self, number: int, header_at: datetime, header_received: datetime) -> dict:
        try:
            return await self.public.mirror_block(number)
        except BlockNotFound:
            pass
        try:
            head = await self.public.mirror_head()
        except MarketDataError as error:
            error.failure = replace(market_failure(error, "hedera_mirror", "latest_indexed_block"),
                                    expected_block=number, lookup_http_status=404)
            raise
        blocks = head.get("blocks") if isinstance(head, dict) else None
        if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict):
            raise MarketDataError("invalid Mirror latest block list", MarketFailure(
                "hedera_mirror", "latest_indexed_block", Diagnostic.INVALID_PROVIDER_RESPONSE,
                http_status=200, lookup_http_status=404, field="blocks", expected_block=number,
            ))
        indexed = blocks[0].get("number")
        if type(indexed) is not int or not 0 <= indexed < 2**63:
            raise MarketDataError("invalid Mirror latest block number", MarketFailure(
                "hedera_mirror", "latest_indexed_block", Diagnostic.INVALID_PROVIDER_RESPONSE,
                http_status=200, lookup_http_status=404, field="number", expected_block=number,
            ))
        now = self.public.clock()
        details = dict(http_status=404, expected_block=number, observed_block=indexed,
                       source_age_ms=(now - header_at) // timedelta(milliseconds=1),
                       receipt_age_ms=(now - header_received) // timedelta(milliseconds=1))
        if indexed >= number:
            raise MarketDataError("requested Mirror block absent although the index reached it", MarketFailure(
                "hedera_mirror", "block_by_number", Diagnostic.BLOCK_NOT_FOUND, **details,
            ))
        lag = Unavailable("Mirror index is behind the requested block", MarketFailure(
            "hedera_mirror", "block_by_number", Diagnostic.MIRROR_INDEXING_DELAY, **details,
        ))
        # Keep the same RPC block. Leave clock/freshness, transport cooldown,
        # and the enclosing deadlines in control of this bounded wait.
        for delay in (0.25, 0.5):
            now = self.public.clock()
            if now - min(header_at, header_received) + timedelta(seconds=delay) >= self.cache_age:
                break
            await self.public.sleep(delay)
            try:
                require_fresh(header_at, header_received, self.public.clock(), self.cache_age)
            except StaleSnapshot:
                break
            self.public.retries += 1
            try:
                return await self.public.mirror_block(number)
            except BlockNotFound:
                pass
        raise lag

    async def _mirror_source(self, number: int, block_hash: str, header_at: datetime,
                             header_received: datetime) -> datetime:
        try:
            block = await self._mirror_block(number, header_at, header_received)
        except MarketDataError as error:
            error.failure = replace(market_failure(error, "hedera_mirror", "block_by_number"),
                                    expected_block=number)
            raise

        def invalid(field, **details):
            return MarketDataError("invalid Hedera mirror block", MarketFailure(
                "hedera_mirror", "block_match", Diagnostic.INVALID_PROVIDER_RESPONSE,
                http_status=200, field=field, expected_block=number, **details,
            ))

        if not isinstance(block, dict):
            raise invalid("response")
        observed = block.get("number")
        if type(observed) is not int or not 0 <= observed < 2**63:
            raise invalid("number")
        hash_text = block.get("hash")
        if not isinstance(hash_text, str) or not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{96}", hash_text):
            raise invalid("hash", observed_block=observed, hash_class="invalid_mirror_48_byte_hash")
        mirror_hash = hash_text[-96:].lower()
        timestamp = block.get("timestamp")
        if not isinstance(timestamp, dict):
            raise invalid("timestamp", observed_block=observed)
        end = timestamp.get("to")
        if not isinstance(end, str) or not re.fullmatch(r"[0-9]{1,12}\.[0-9]{1,9}", end):
            raise invalid("timestamp.to", observed_block=observed)
        seconds, fraction = end.split(".")
        try:
            source = datetime.fromtimestamp(int(seconds), UTC).replace(microsecond=int(fraction[:6].ljust(6, "0")))
        except (ValueError, OverflowError, OSError):
            raise invalid("timestamp.to", observed_block=observed) from None
        delta_ms = (source - header_at) // timedelta(milliseconds=1)
        if observed != number:
            category, field, hash_class = Diagnostic.BLOCK_NUMBER_MISMATCH, "number", None
        elif mirror_hash[:64] != block_hash[2:]:
            category, field, hash_class = Diagnostic.BLOCK_HASH_MISMATCH, "hash", "prefix_mismatch"
        elif abs(source.replace(microsecond=0) - header_at) > timedelta(seconds=2):
            category, field, hash_class = Diagnostic.BLOCK_TIMESTAMP_MISMATCH, "timestamp.to", None
        else:
            self._block_freshness(number, source, header_received, self.public.clock())
            self.mirror_verified = True
            return source
        raise BlockIdentityMismatch("Hedera block provenance mismatch", MarketFailure(
            "hedera_mirror", "block_match", category, http_status=200, expected_block=number,
            observed_block=observed, field=field, hash_class=hash_class, timestamp_delta_ms=delta_ms,
        ))

    def _block_freshness(self, number: int, source: datetime, received: datetime, now: datetime) -> None:
        try:
            require_fresh(source, received, now, self.cache_age)
        except StaleSnapshot:
            raise StaleSnapshot("Hedera block is stale or outside clock skew tolerance", MarketFailure(
                "hedera_mirror", "block_freshness", Diagnostic.STALE_SNAPSHOT, expected_block=number,
                observed_block=number, source_age_ms=(now - source) // timedelta(milliseconds=1),
                receipt_age_ms=(now - received) // timedelta(milliseconds=1),
            )) from None

    async def _pool_reserves(self, number: int) -> tuple[int, ...]:
        tag = hex(number)
        requests = (
            (FACTORY, "e6a43905" + address_word(USDC) + address_word(WHBAR), 1),
            (PAIR, "0dfe1681", 1), (PAIR, "d21220a7", 1), (USDC, "313ce567", 1),
            (WHBAR, "313ce567", 1), (PAIR, "0902f1ac", 3),
        )
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._call(address, call, tag, length))
                     for address, call, length in requests]
        expected = ((int(PAIR, 16),), (int(USDC, 16),), (int(WHBAR, 16),), (6,), (8,))
        if tuple(task.result() for task in tasks[:-1]) != expected:
            raise HederaIdentityError("SaucerSwap factory, token ordering or decimals mismatch", MarketFailure(
                "saucerswap", "pool_identity", Diagnostic.MARKET_IDENTITY_MISMATCH, expected_block=number,
            ))
        return tasks[-1].result()

    async def _read(self) -> PoolSnapshot:
        number, block_hash, header_at = await self._rpc_header()
        header_received = self.public.clock()
        source, reserves = await asyncio.gather(
            self._mirror_source(number, block_hash, header_at, header_received),
            self._pool_reserves(number), return_exceptions=True,
        )
        for result in (source, reserves):
            if isinstance(result, asyncio.CancelledError):
                raise result
        errors = [error for error in (source, reserves) if isinstance(error, Exception)]
        if errors:
            raise ExceptionGroup("Hedera verification failed", errors)
        received = self.public.clock()
        self._block_freshness(number, source, header_received, received)
        tag = hex(number)
        usdc, whbar, update = reserves
        if not 0 <= update < 2**32:
            raise MarketDataError("invalid pair reserve timestamp", MarketFailure(
                "saucerswap", "pool_reserves", Diagnostic.INVALID_PROVIDER_RESPONSE,
                expected_block=number, field="timestamp",
            ))
        try:
            buy, sell = amounts(usdc, whbar)
        except MarketDataError as error:
            error.failure = MarketFailure(
                "saucerswap", "pool_reserves", Diagnostic.INVALID_PROVIDER_RESPONSE,
                expected_block=number, field="reserves",
            )
            raise
        async with asyncio.TaskGroup() as group:
            router = group.create_task(self._router(tag, buy, sell))
            gas_price = group.create_task(self._gas_price())
        completed = self.public.clock()
        self._block_freshness(number, source, received, completed)
        snapshot_id = f"hedera:295:{number}:{block_hash}"
        return PoolSnapshot(
            number, block_hash, source, received, usdc, whbar, update, router.result(),
            tuple(MarketQuote(
                venue=Venue.DEX, side=side, base_asset=Asset.WHBAR, quote_asset=Asset.USDC,
                base_units=TRADE_UNITS, quote_units=total, snapshot_id=snapshot_id,
                observed_at=received, source_at=source,
            ) for side, total in ((Side.BUY, buy), (Side.SELL, sell))),
            self._configured_gas(snapshot_id, gas_price.result()),
        )

    async def snapshot(self) -> PoolSnapshot:
        async with self._lock:
            self.rpc_verified = self.mirror_verified = False
            self.failures = ()
            self.router_failure = None
            try:
                try:
                    async with asyncio.timeout(9):
                        snapshot = await self._read()
                except TimeoutError:
                    raise ProviderTimeout("pool snapshot deadline exceeded", MarketFailure(
                        "saucerswap", "pool_snapshot", Diagnostic.PROVIDER_TIMEOUT,
                    )) from None
            except (MarketDataError, ExceptionGroup) as exc:
                errors = market_errors(exc)
                self.failures = tuple(dict.fromkeys(
                    market_failure(error, "saucerswap", "pool_snapshot") for error in errors
                    if isinstance(error, MarketDataError)
                ))
                if all(isinstance(error, Unavailable) for error in errors) and self._cached:
                    try:
                        self._block_freshness(
                            self._cached.block, self._cached.source_at, self._cached.received_at, self.public.clock(),
                        )
                    except MarketDataError as stale:
                        self._cached = None
                        if any(isinstance(error, MarketDataError) and error.failure for error in errors):
                            raise ExceptionGroup("Hedera refresh failed and cache is stale", [stale, *errors]) from None
                        raise
                    self.cache_hits += 1
                    return self._cached
                self._cached = None
                if isinstance(exc, ExceptionGroup) and len(errors) == 1:
                    raise errors[0] from None
                raise
            self._cached = snapshot
            if self.router_failure is not None:
                self.failures = (self.router_failure,)
            return snapshot

    async def quote(self, side: Side, whbar_units: int) -> MarketQuote:
        if whbar_units != TRADE_UNITS:
            raise MarketDataError("expected exactly 1,000 WHBAR")
        return (await self.snapshot()).quotes[0 if side == Side.BUY else 1]
