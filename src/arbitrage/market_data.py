"""Bounded public market transport and snapshot freshness checks."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from itertools import count
from random import uniform
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .models import Asset, TRADE_HBAR
from .readiness import Diagnostic

RPC = "https://mainnet.hashio.io/api"
MIRROR = "https://mainnet-public.mirrornode.hedera.com/api/v1"
KUCOIN = "https://api.kucoin.com"
MAX_SKEW = timedelta(seconds=10)
MAX_SOURCE_AHEAD = timedelta(seconds=2)
MAX_AGE = timedelta(seconds=10)
TRADE_UNITS = TRADE_HBAR * 10**Asset.HBAR.decimals


@dataclass(frozen=True)
class MarketFailure:
    """Closed diagnostic projection. Provider bodies and exception strings are excluded."""

    provider: str
    operation: str
    category: Diagnostic
    http_status: int | None = None
    lookup_http_status: int | None = None
    expected_block: int | None = None
    observed_block: int | None = None
    source_age_ms: int | None = None
    receipt_age_ms: int | None = None
    timestamp_delta_ms: int | None = None
    field: str | None = None
    hash_class: str | None = None

    def as_dict(self) -> dict:
        details = {"provider": self.provider, "operation": self.operation, "category": self.category.value}
        if self.provider not in {"hedera_rpc", "hedera_mirror", "saucerswap", "kucoin_rest", "market"}:
            raise ValueError("unknown diagnostic provider")
        if self.operation not in {
            "eth_chainId", "eth_getBlockByNumber", "eth_call", "eth_gasPrice",
            "block_by_number", "latest_indexed_block", "block_match", "block_freshness",
            "pool_identity", "pool_reserves", "router_quote", "pool_snapshot", "depth_snapshot",
            "ticker", "depth", "symbol", "bootstrap", "snapshot_read", "snapshot_pair",
        }:
            raise ValueError("unknown diagnostic operation")
        for key in ("http_status", "lookup_http_status", "expected_block", "observed_block", "source_age_ms",
                    "receipt_age_ms", "timestamp_delta_ms"):
            value = getattr(self, key)
            if type(value) is int and abs(value) < 10**20:
                details[key] = value
        if self.field in {"response", "result", "id", "jsonrpc", "blocks", "number", "hash",
                          "timestamp", "timestamp.to", "reserves", "data", "code"}:
            details["field"] = self.field
        if self.hash_class in {"invalid_rpc_32_byte_hash", "invalid_mirror_48_byte_hash", "prefix_mismatch"}:
            details["hash_class"] = self.hash_class
        return details


class MarketDataError(ValueError):
    """Invalid, inconsistent or stale public data."""

    def __init__(self, message: str, failure: MarketFailure | None = None):
        super().__init__(message)
        self.failure = failure


class MarketIdentityError(MarketDataError):
    """Provider state contradicts a required network or market identity."""


class StaleSnapshot(MarketDataError):
    """Snapshot time exceeds the freshness or clock skew limit."""


class Unavailable(MarketDataError):
    """Transient public provider failure."""


class ProviderTimeout(Unavailable):
    """Confirmed public request deadline."""


class ProviderRateLimited(Unavailable):
    """Confirmed provider throttle."""


class BlockNotFound(Unavailable):
    """The Mirror endpoint has no result for the requested block."""


class BlockIdentityMismatch(MarketIdentityError):
    """RPC and Mirror data disagree about the requested block."""


def market_errors(error: Exception) -> tuple[Exception, ...]:
    if isinstance(error, ExceptionGroup):
        return tuple(child for item in error.exceptions for child in market_errors(item))
    return (error,)


def market_failure(error: Exception, provider="market", operation="snapshot_pair") -> MarketFailure:
    failure = getattr(error, "failure", None)
    if isinstance(failure, MarketFailure):
        return failure
    if isinstance(error, ProviderRateLimited):
        category = Diagnostic.PROVIDER_RATE_LIMITED
    elif isinstance(error, (ProviderTimeout, TimeoutError)):
        category = Diagnostic.PROVIDER_TIMEOUT
    elif isinstance(error, Unavailable):
        category = Diagnostic.MARKET_UNAVAILABLE
    elif isinstance(error, MarketIdentityError):
        category = Diagnostic.MARKET_IDENTITY_MISMATCH
    elif isinstance(error, StaleSnapshot):
        category = Diagnostic.STALE_SNAPSHOT
    else:
        category = Diagnostic.MARKET_UNVERIFIED
    return MarketFailure(provider, operation, category)


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_fresh(
    source_at: datetime, received_at: datetime, now: datetime, age: timedelta = MAX_AGE
) -> None:
    if (
        source_at - received_at > MAX_SOURCE_AHEAD
        or received_at > now
        or now - received_at > age
        or now - source_at > age
    ):
        raise StaleSnapshot("market snapshot is stale or outside clock skew tolerance")


def retry_delay(headers: Any, now: datetime) -> float:
    delays = [0.0]
    for key, divisor in (("Retry-After", 1), ("gw-ratelimit-reset", 1000)):
        value = headers.get(key)
        if value:
            try:
                delays.append(int(value) / divisor)
            except ValueError:
                if key == "Retry-After":
                    try:
                        delays.append((parsedate_to_datetime(value) - now).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        pass
    return max(delays)


class PublicConnections:
    """Reuse one async session. Errors omit URLs, response bodies and WS tokens."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = uniform,
        timer: Callable[[], float] = monotonic,
    ):
        self.session = session
        self.clock = clock
        self.sleep = sleep
        self.jitter = jitter
        self.timer = timer
        self.health: dict[str, str] = {}
        self.before_request: Callable[[], None] | None = None
        self.retries = 0
        self._ids = count(1)
        self._rate_limit_until: dict[str, float] = {}

    def cooldown_seconds(self) -> float:
        return max(0.0, max(self._rate_limit_until.values(), default=0.0) - self.timer())

    async def _json(self, method: str, url: str, payload: dict | None = None) -> dict:
        source = urlsplit(url).hostname
        if url == RPC:
            provider, operation = "hedera_rpc", payload["method"]
        elif url.startswith(MIRROR + "/blocks"):
            provider = "hedera_mirror"
            operation = "latest_indexed_block" if "?" in url else "block_by_number"
        else:
            provider = "kucoin_rest"
            operation = next((name for marker, name in (
                ("level2_20", "depth"), ("level1", "ticker"), ("/symbols/", "symbol"),
                ("bullet-public", "bootstrap"),
            ) if marker in url), "snapshot_read")
        status = None

        def detail(category, **fields):
            return MarketFailure(provider, operation, category, http_status=status, **fields)
        try:
            async with asyncio.timeout(8):
                for attempt in range(3):
                    wait = self._rate_limit_until.get(source, 0) - self.timer()
                    if wait > 0:
                        await self.sleep(wait)
                    if self.before_request:
                        self.before_request()
                    self.retries += int(attempt > 0)
                    delay = self.jitter(0.15, 0.25) * 2**attempt
                    status = None
                    try:
                        async with asyncio.timeout(2.5):
                            async with self.session.request(
                                method, url, json=payload, allow_redirects=False
                            ) as response:
                                status = response.status
                                if response.status == 429:
                                    delay = max(delay, retry_delay(response.headers, self.clock()))
                                    until = self.timer() + delay
                                    self._rate_limit_until[source] = max(
                                        until, self._rate_limit_until.get(source, 0)
                                    )
                                if response.status == 429:
                                    raise ProviderRateLimited("public HTTP 429", detail(Diagnostic.PROVIDER_RATE_LIMITED))
                                if response.status in (408, 500, 502, 503, 504):
                                    raise Unavailable(f"public HTTP {response.status}", detail(Diagnostic.PROVIDER_HTTP_ERROR))
                                if status == 404 and operation == "block_by_number":
                                    self.health[source] = "degraded"
                                    raise BlockNotFound("requested Mirror block not found", detail(Diagnostic.BLOCK_NOT_FOUND))
                                if status != 200:
                                    raise MarketDataError(f"public HTTP {status}", detail(Diagnostic.PROVIDER_HTTP_ERROR))
                                data = await response.json()
                                if not isinstance(data, dict):
                                    raise MarketDataError("expected a JSON object", detail(Diagnostic.INVALID_PROVIDER_RESPONSE, field="response"))
                                if data.get("code") == "429000":
                                    self._rate_limit_until[source] = self.timer() + max(
                                        delay, retry_delay(response.headers, self.clock())
                                    )
                                    raise ProviderRateLimited("public KuCoin rate limit", detail(Diagnostic.PROVIDER_RATE_LIMITED))
                                if response.headers.get("gw-ratelimit-remaining") == "0":
                                    self._rate_limit_until[source] = self.timer() + retry_delay(
                                        response.headers, self.clock()
                                    )
                                self.health[source] = "healthy"
                                return data
                    except BlockNotFound:
                        raise
                    except Unavailable as exc:
                        failure = exc
                    except MarketDataError:
                        self.health[source] = "invalid"
                        raise
                    except (aiohttp.ContentTypeError, ValueError):
                        self.health[source] = "invalid"
                        raise MarketDataError("invalid public JSON response", detail(Diagnostic.INVALID_PROVIDER_RESPONSE, field="response")) from None
                    except TimeoutError:
                        failure = ProviderTimeout("public request timed out", detail(Diagnostic.PROVIDER_TIMEOUT))
                    except aiohttp.ClientError:
                        failure = Unavailable("public connection failed", detail(Diagnostic.PROVIDER_NETWORK_FAILURE))
                    self.health[source] = "degraded"
                    if attempt == 2:
                        raise failure
                    if self._rate_limit_until.get(source, 0) - self.timer() >= 8:
                        raise failure
                    if self._rate_limit_until.get(source, 0) <= self.timer():
                        await self.sleep(delay)
        except TimeoutError:
            self.health[source] = "degraded"
            if self._rate_limit_until.get(source, 0) > self.timer():
                raise ProviderRateLimited("provider cooldown exceeded request budget",
                                          detail(Diagnostic.PROVIDER_RATE_LIMITED)) from None
            raise ProviderTimeout("public request deadline exceeded", detail(Diagnostic.PROVIDER_TIMEOUT)) from None

    @asynccontextmanager
    async def kucoin_socket(self, endpoint: str, token: str, connect_id: str):
        if self.before_request:
            self.before_request()
        async with asyncio.timeout(5):
            socket = await self.session.ws_connect(
                endpoint, params={"token": token, "connectId": connect_id},
                timeout=aiohttp.ClientWSTimeout(ws_close=2), max_msg_size=64 * 1024,
            )
        try:
            yield socket
        finally:
            async with asyncio.timeout(3):
                await socket.close()

    async def rpc(self, method: str, *params: object) -> Any:
        if method not in {"eth_chainId", "eth_getBlockByNumber", "eth_gasPrice", "eth_call"}:
            raise MarketDataError("RPC method is outside the public read scope")
        request_id = next(self._ids)
        data = await self._json("POST", RPC, {
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        })
        field = ("jsonrpc" if data.get("jsonrpc") != "2.0" else
                 "id" if type(data.get("id")) is not int or data["id"] != request_id else
                 "result" if data.get("result") is None or "error" in data else None)
        if field:
            raise MarketDataError("RPC response verification failed", MarketFailure(
                "hedera_rpc", method, Diagnostic.INVALID_PROVIDER_RESPONSE, http_status=200, field=field,
            ))
        return data["result"]

    async def mirror_block(self, number: int) -> dict:
        return await self._json("GET", f"{MIRROR}/blocks/{number}")

    async def mirror_head(self) -> dict:
        return await self._json("GET", f"{MIRROR}/blocks?limit=1&order=desc")

    async def kucoin(self, resource: str) -> dict:
        paths = {
            "ticker": ("GET", "/api/v1/market/orderbook/level1?symbol=HBAR-USDT"),
            "depth": ("GET", "/api/v1/market/orderbook/level2_20?symbol=HBAR-USDT"),
            "symbol": ("GET", "/api/v2/symbols/HBAR-USDT"),
            "bootstrap": ("POST", "/api/v1/bullet-public"),
        }
        method, path = paths[resource]
        data = await self._json(method, KUCOIN + path)
        if data.get("code") != "200000" or not isinstance(data.get("data"), dict):
            raise MarketDataError("KuCoin public read failed", MarketFailure(
                "kucoin_rest", resource, Diagnostic.INVALID_PROVIDER_RESPONSE, http_status=200,
                field="code" if data.get("code") != "200000" else "data",
            ))
        return data["data"]
