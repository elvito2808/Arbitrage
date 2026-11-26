"""Bounded public market transport and snapshot freshness checks."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from itertools import count
from random import uniform
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .models import Asset, TRADE_HBAR

RPC = "https://mainnet.hashio.io/api"
MIRROR = "https://mainnet-public.mirrornode.hedera.com/api/v1"
KUCOIN = "https://api.kucoin.com"
MAX_SKEW = timedelta(seconds=2)
MAX_AGE = timedelta(seconds=10)
TRADE_UNITS = TRADE_HBAR * 10**Asset.HBAR.decimals


class MarketDataError(ValueError):
    """Invalid, inconsistent or stale public data."""


class StaleSnapshot(MarketDataError):
    """Snapshot time exceeds the freshness or clock skew limit."""


class Unavailable(MarketDataError):
    """Transient public provider failure."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_fresh(
    source_at: datetime, received_at: datetime, now: datetime, age: timedelta = MAX_AGE
) -> None:
    if (
        source_at - received_at > MAX_SKEW
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

    async def _json(self, method: str, url: str, payload: dict | None = None) -> dict:
        source = urlsplit(url).hostname
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
                    try:
                        async with asyncio.timeout(2.5):
                            async with self.session.request(
                                method, url, json=payload, allow_redirects=False
                            ) as response:
                                if response.status == 429:
                                    delay = max(delay, retry_delay(response.headers, self.clock()))
                                    until = self.timer() + delay
                                    self._rate_limit_until[source] = max(
                                        until, self._rate_limit_until.get(source, 0)
                                    )
                                if response.status == 429 or response.status in (408, 500, 502, 503, 504):
                                    raise Unavailable(f"public HTTP {response.status}")
                                if response.status != 200:
                                    raise MarketDataError(f"public HTTP {response.status}")
                                data = await response.json()
                                if not isinstance(data, dict):
                                    raise MarketDataError("expected a JSON object")
                                if data.get("code") == "429000":
                                    self._rate_limit_until[source] = self.timer() + max(
                                        delay, retry_delay(response.headers, self.clock())
                                    )
                                    raise Unavailable("public KuCoin rate limit")
                                if response.headers.get("gw-ratelimit-remaining") == "0":
                                    self._rate_limit_until[source] = self.timer() + retry_delay(
                                        response.headers, self.clock()
                                    )
                                self.health[source] = "healthy"
                                return data
                    except Unavailable as exc:
                        failure = exc
                    except MarketDataError:
                        self.health[source] = "invalid"
                        raise
                    except (aiohttp.ContentTypeError, ValueError):
                        self.health[source] = "invalid"
                        raise MarketDataError("invalid public JSON response") from None
                    except (aiohttp.ClientError, TimeoutError):
                        failure = Unavailable("public connection failed or timed out")
                    self.health[source] = "degraded"
                    if attempt == 2:
                        raise failure
                    if self._rate_limit_until.get(source, 0) - self.timer() >= 8:
                        raise failure
                    if self._rate_limit_until.get(source, 0) <= self.timer():
                        await self.sleep(delay)
        except TimeoutError:
            self.health[source] = "degraded"
            raise Unavailable("public request deadline exceeded") from None

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
        if data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
            raise MarketDataError("RPC response identity mismatch")
        if data.get("result") is None or "error" in data:
            raise MarketDataError("RPC read failed")
        return data["result"]

    async def mirror_block(self, number: int) -> dict:
        return await self._json("GET", f"{MIRROR}/blocks/{number}")

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
            raise MarketDataError("KuCoin public read failed")
        return data["data"]
