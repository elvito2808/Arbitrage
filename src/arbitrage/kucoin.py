"""Public KuCoin HBAR-USDT data and simulation request pricing."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp

from .market_data import MAX_AGE, TRADE_UNITS, MarketDataError, MarketIdentityError, PublicConnections, Unavailable, market_failure, require_fresh
from .models import Asset, MarketQuote, Side, Venue

BASE_CURRENCY = "HBAR"
QUOTE_CURRENCY = "USDT"
SYMBOL = f"{BASE_CURRENCY}-{QUOTE_CURRENCY}"
TOPIC = f"/market/ticker:{SYMBOL}"
DEPTH_TOPIC = f"/spotMarket/level2Depth5:{SYMBOL}"
WS_STARTUP_GRACE = 0.25
WS_STABLE_SECONDS = 15
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def exact(value: object) -> Fraction:
    if not isinstance(value, str) or len(value) > 40:
        raise MarketDataError("expected a decimal exchange value")
    try:
        decimal = Decimal(value)
        if not decimal.is_finite() or abs(decimal.as_tuple().exponent) > 18 or decimal < 0:
            raise MarketDataError("exchange decimal out of range")
        return Fraction(decimal)
    except InvalidOperation:
        raise MarketDataError("invalid exchange decimal") from None


def millis(value: object) -> datetime:
    if type(value) is not int:
        raise MarketDataError("expected an integer exchange timestamp")
    try:
        return EPOCH + timedelta(milliseconds=value)
    except OverflowError:
        raise MarketDataError("exchange timestamp out of range") from None


@dataclass(frozen=True)
class Level:
    price: Fraction
    quantity: Fraction


@dataclass(frozen=True)
class Book:
    sequence: int
    source_at: datetime
    received_at: datetime
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    generation: int = 0
    source: str = "rest"

    @property
    def snapshot_id(self) -> str:
        version = f"ws:{self.sequence}" if self.source == "websocket" else self.sequence
        return f"kucoin:{SYMBOL}:{version}"

    @property
    def timestamp_id(self) -> str:
        timestamp = (self.source_at - EPOCH) // timedelta(milliseconds=1)
        return f"kucoin:{SYMBOL}:time:{timestamp}"


@dataclass(frozen=True)
class Ticker:
    sequence: int
    trade_at: datetime
    received_at: datetime
    bid: Level
    ask: Level


def level(price: object, quantity: object) -> Level:
    result = Level(exact(price), exact(quantity))
    if min(result.price, result.quantity) <= 0:
        raise MarketDataError("exchange level must have positive price and quantity")
    return result


def crossed(bid: Level, ask: Level) -> None:
    if bid.price >= ask.price:
        raise MarketDataError("locked or crossed KuCoin book")


def sequence(value: object) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 24:
        raise MarketDataError("invalid KuCoin sequence")
    return int(value)


def ticker_from(data: dict, received: datetime) -> Ticker:
    ticker = Ticker(
        sequence(data.get("sequence")), millis(data.get("time")), received,
        level(data.get("bestBid"), data.get("bestBidSize")),
        level(data.get("bestAsk"), data.get("bestAskSize")),
    )
    crossed(ticker.bid, ticker.ask)
    if ticker.trade_at - received > timedelta(seconds=2):
        raise MarketDataError("KuCoin ticker trade time is ahead of the receiver")
    return ticker


def book_from(data: dict, received: datetime, base_step: Fraction, price_step: Fraction) -> Book:
    if not isinstance(data, dict):
        raise MarketDataError("invalid KuCoin depth payload")
    sides = []
    for key in ("bids", "asks"):
        rows = data.get(key)
        if not isinstance(rows, list) or not 0 < len(rows) <= 20:
            raise MarketDataError("invalid KuCoin depth side")
        side = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise MarketDataError("invalid KuCoin depth row")
            entry = level(*row)
            if entry.price % price_step or entry.quantity % base_step:
                raise MarketDataError("KuCoin order book disagrees with market increments")
            side.append(entry)
        sides.append(tuple(side))
    bids, asks = sides
    book = Book(sequence(data.get("sequence")), millis(data.get("time")), received, bids, asks)
    validate_book(book)
    return book


def validate_book(book: Book) -> None:
    for side, descending in ((book.bids, True), (book.asks, False)):
        if not 0 < len(side) <= 20 or any(
            type(entry.price) is not Fraction or type(entry.quantity) is not Fraction
            or min(entry.price, entry.quantity) <= 0 for entry in side
        ):
            raise MarketDataError("invalid KuCoin depth side")
        prices = [entry.price for entry in side]
        if prices != sorted(set(prices), reverse=descending):
            raise MarketDataError("KuCoin depth order or duplicate level")
    crossed(book.bids[0], book.asks[0])


class InsufficientDepth(MarketDataError):
    """Visible depth cannot cover the requested size."""


def depth_value(levels: tuple[Level, ...], quantity: Fraction) -> Fraction:
    remaining = quantity
    gross = Fraction(0)
    for entry in levels:
        quantity = min(remaining, entry.quantity)
        gross += quantity * entry.price
        remaining -= quantity
        if not remaining:
            break
    if remaining:
        raise InsufficientDepth("visible KuCoin depth cannot cover the requested quantity")
    return gross


def priced_units(levels: tuple[Level, ...], side: Side, fee: Fraction) -> int:
    gross = depth_value(levels, Fraction(1000))
    total = gross * (1 + fee if side == Side.BUY else 1 - fee) * 10**6
    units = -(-total.numerator // total.denominator) if side == Side.BUY else int(total)
    if units <= 0:
        raise MarketDataError("KuCoin total rounds to zero")
    return units


class KuCoin:
    parity_assumption = "USDC/USDT 1:1 for simulation only"

    def __init__(
        self, public: PublicConnections, fee: Decimal = Decimal("0.001"),
        cache_age: timedelta = MAX_AGE,
    ):
        if not isinstance(fee, Decimal) or not fee.is_finite() or not 0 <= fee < 1:
            raise ValueError("CEX fee must be an exact Decimal in [0, 1)")
        if not timedelta(0) < cache_age <= MAX_AGE:
            raise ValueError("cache age must be positive and at most 10 seconds")
        self.public = public
        self.cache_hits = 0
        self.fee = Fraction(fee)
        self.cache_age = cache_age
        self._increments: tuple[Fraction, Fraction] | None = None
        self._book: Book | None = None
        self._generation = 0
        self._lock = asyncio.Lock()
        self.stream = KuCoinTickerStream(public, self._invalidate)
        self.source = "unavailable"
        self.rest_verified = False
        self.rest_failure = None
        self.on_source_change: Callable[[str, str], None] | None = None
        self.depth_stream = KuCoinDepthStream(self)

    def _invalidate(self) -> None:
        self._generation += 1
        self._increments = None
        self._book = None

    async def indicative_ticker(self) -> Ticker:
        data = await self.public.kucoin("ticker")
        return ticker_from(data, self.public.clock())

    async def _market_increments(self) -> tuple[Fraction, Fraction]:
        data = await self.public.kucoin("symbol")
        if not isinstance(data, dict):
            raise MarketDataError("invalid KuCoin symbol metadata")
        if (data.get("symbol"), data.get("baseCurrency"), data.get("quoteCurrency")) != (
            SYMBOL, BASE_CURRENCY, QUOTE_CURRENCY,
        ):
            raise MarketIdentityError("KuCoin symbol identity mismatch")
        base = exact(data.get("baseIncrement"))
        price = exact(data.get("priceIncrement"))
        quote = exact(data.get("quoteIncrement"))
        if min(base, price, quote) <= 0 or 1000 % base:
            raise MarketDataError("invalid KuCoin increments for fixed size")
        return base, price

    async def _rest_snapshot(self) -> Book:
        async with self._lock:
            generation = self._generation
            from_cache = False
            try:
                async with asyncio.timeout(9):
                    if self._increments is None:
                        self._increments = await self._market_increments()
                    data = await self.public.kucoin("depth")
                    received = self.public.clock()
                    book = replace(book_from(data, received, *self._increments), generation=generation)
                    if self._book and (
                        book.sequence < self._book.sequence
                        or book.source_at < self._book.source_at
                        or (
                            book.sequence == self._book.sequence
                            and (book.bids, book.asks) != (self._book.bids, self._book.asks)
                        )
                    ):
                        raise MarketDataError("KuCoin depth sequence or timestamp regressed")
            except (Unavailable, TimeoutError) as error:
                self.rest_failure = market_failure(error, "kucoin_rest", "depth_snapshot")
                if self._book is None:
                    error_type = type(error) if isinstance(error, Unavailable) else Unavailable
                    raise error_type("KuCoin depth unavailable", self.rest_failure) from None
                book = self._book
                from_cache = True
            except MarketDataError as error:
                self.rest_failure = market_failure(error, "kucoin_rest", "depth_snapshot")
                self._invalidate()
                raise
            if generation != self._generation:
                raise Unavailable("KuCoin connection changed during depth read")
            try:
                require_fresh(book.source_at, book.received_at, self.public.clock(), self.cache_age)
            except MarketDataError:
                self._invalidate()
                raise
            self._book = book
            self.rest_verified = not from_cache
            self.cache_hits += int(from_cache)
            return book

    def _set_source(self, source: str, reason: str) -> None:
        if source != self.source:
            self.source = source
            if self.on_source_change:
                self.on_source_change(source, reason)

    async def snapshot(self) -> Book:
        self.rest_verified = False
        self.rest_failure = None
        if (
            self.depth_stream.health in ("connecting", "awaiting_depth")
            and not self.depth_stream.initial_attempt.is_set()
        ):
            try:
                async with asyncio.timeout(WS_STARTUP_GRACE):
                    await self.depth_stream.initial_attempt.wait()
            except TimeoutError:
                self.depth_stream.initial_attempt.set()
        book = self.depth_stream.current()
        if book is not None:
            self._set_source("websocket", "fresh_depth")
            return book
        try:
            rest = await self._rest_snapshot()
        except MarketDataError:
            book = self.depth_stream.current()
            if book is None:
                self._set_source("unavailable", "no_fresh_depth")
                raise
        else:
            book = self.depth_stream.current()
            if book is None:
                self._set_source("rest", "websocket_unavailable")
                return rest
        self._set_source("websocket", "fresh_depth")
        return book

    def price(self, book: Book, side: Side) -> MarketQuote:
        generation = self.depth_stream.generation if book.source == "websocket" else self._generation
        if book.generation != generation:
            raise MarketDataError("KuCoin book invalidated by connection reset")
        require_fresh(book.source_at, book.received_at, self.public.clock(), self.cache_age)
        return MarketQuote(
            venue=Venue.CEX, side=side, base_asset=Asset.HBAR, quote_asset=Asset.USDT,
            base_units=TRADE_UNITS,
            quote_units=priced_units(book.asks if side == Side.BUY else book.bids, side, self.fee),
            snapshot_id=book.snapshot_id,
            observed_at=book.received_at, source_at=book.source_at,
        )

    async def quote(self, side: Side, hbar_units: int) -> MarketQuote:
        if hbar_units != TRADE_UNITS:
            raise MarketDataError("expected exactly 1,000 HBAR")
        return self.price(await self.snapshot(), side)


class KuCoinTickerStream:
    """Indicative ticker retained for diagnostics and public transport probes."""

    def __init__(self, public: PublicConnections, invalidate: Callable[[], None]):
        self.public = public
        self.invalidate = invalidate
        self.queue: asyncio.Queue[Ticker] = asyncio.Queue(maxsize=1)
        self.health = "idle"
        self._running = False

    async def _listen(self) -> None:
        self.health = "connecting"
        last_sequence = -1
        async with kucoin_subscription(self.public, TOPIC) as (socket, interval, timeout, connection):
            self.health = "healthy"
            async for message in kucoin_messages(socket, TOPIC, interval, timeout, connection):
                quote = ticker_from(message["data"], self.public.clock())
                if quote.sequence < last_sequence:
                    raise MarketDataError("KuCoin ticker sequence regressed")
                last_sequence = quote.sequence
                if self.queue.full():
                    self.queue.get_nowait()
                    self.queue.task_done()
                self.queue.put_nowait(quote)

    def _reset(self) -> None:
        if not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.invalidate()

    async def run(self) -> None:
        """Caller owns the task and cancels it when finished."""
        if self._running:
            raise MarketDataError("KuCoin WS is already running")
        self._running = True
        try:
            for attempt in range(3):
                try:
                    async with asyncio.timeout(23 * 3600):
                        await self._listen()
                except (aiohttp.ClientError, TimeoutError, Unavailable, aiohttp.WSMessageTypeError) as exc:
                    self.health = "degraded"
                    self._reset()
                    if attempt == 2:
                        raise Unavailable(
                            f"KuCoin WS reconnect limit reached ({type(exc).__name__})"
                        ) from None
                    await self.public.sleep(self.public.jitter(0.25, 0.5) * 2**attempt)
                except (ValueError, TypeError, AttributeError, KeyError):
                    self.health = "invalid"
                    self._reset()
                    raise MarketDataError("invalid KuCoin WS response") from None
        except asyncio.CancelledError:
            self.health = "idle"
            self._reset()
            raise
        finally:
            self._running = False


@asynccontextmanager
async def kucoin_subscription(public: PublicConnections, topic: str):
    """Open one acknowledged public KuCoin market subscription."""
    async with asyncio.timeout(8):
        data = await public.kucoin("bootstrap")
        try:
            server = data["instanceServers"][0]
            endpoint = urlsplit(server["endpoint"])
            token = data["token"]
            interval = server["pingInterval"] / 1000
            timeout = server["pingTimeout"] / 1000
            if (
                endpoint.scheme != "wss" or endpoint.hostname != "ws-api-spot.kucoin.com"
                or endpoint.username or endpoint.password or endpoint.port not in (None, 443)
                or endpoint.query or endpoint.fragment
                or not isinstance(token, str) or not token
                or not 0 < timeout < interval <= 60
            ):
                raise MarketDataError("invalid public WS bootstrap")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise MarketDataError("invalid public WS bootstrap") from None
    connect_id = uuid4().hex
    async with public.kucoin_socket(endpoint.geturl(), token, connect_id) as ws:
        async with asyncio.timeout(5):
            welcome = await ws.receive_json()
            if welcome.get("type") != "welcome":
                raise MarketDataError("missing KuCoin WS welcome")
            await ws.send_json({
                "id": connect_id, "type": "subscribe", "topic": topic,
                "privateChannel": False, "response": True,
            })
            ack = await ws.receive_json()
            if ack.get("type") != "ack" or ack.get("id") != connect_id:
                raise MarketDataError("missing KuCoin WS subscription acknowledgment")
        yield ws, interval, timeout, connect_id


async def kucoin_messages(
    ws: aiohttp.ClientWebSocketResponse, topic: str, interval: float, timeout: float,
    connect_id: str, max_silence: float | None = None,
    validated_source: Callable[[], datetime | None] | None = None,
) -> AsyncIterator[dict]:
    """Read public data with heartbeat and an optional advancing depth deadline."""
    loop = asyncio.get_running_loop()
    ping_at = loop.time() + interval - timeout
    pong_due = None
    last_timestamp = None
    data_due = loop.time() + max_silence if max_silence is not None else None
    while True:
        deadline = pong_due if pong_due is not None else ping_at
        if data_due is not None:
            deadline = min(deadline, data_due)
        try:
            if loop.time() >= deadline:
                raise TimeoutError
            async with asyncio.timeout_at(deadline):
                message = await ws.receive_json()
        except TimeoutError:
            if data_due is not None and loop.time() >= data_due:
                raise Unavailable("KuCoin WS depth timed out") from None
            if pong_due is not None:
                raise Unavailable("KuCoin WS heartbeat timed out") from None
            async with asyncio.timeout(timeout):
                await ws.send_json({"id": connect_id, "type": "ping"})
            pong_due = loop.time() + timeout
            continue
        if not isinstance(message, dict):
            raise MarketDataError("invalid KuCoin WS message")
        if message.get("type") == "pong" and message.get("id") == connect_id:
            pong_due = None
            ping_at = loop.time() + interval - timeout
        if message.get("type") == "error":
            raise MarketDataError("KuCoin WS channel error")
        if message.get("type") != "message":
            continue
        if message.get("topic") != topic:
            raise MarketDataError("unexpected KuCoin WS market")
        yield message
        timestamp = validated_source() if validated_source is not None else None
        if max_silence is not None and timestamp is not None and (
            last_timestamp is None or timestamp > last_timestamp
        ):
            data_due = loop.time() + max_silence
            last_timestamp = timestamp


class KuCoinDepthStream:
    """Retain one validated depth image for the runtime's normal pricing path."""

    def __init__(self, market: KuCoin):
        self.market = market
        self.public = market.public
        self.health = "idle"
        self.generation = 0
        self.initial_attempt = asyncio.Event()
        self._book: Book | None = None
        self._last_timestamp: datetime | None = None
        self.last_valid_received_at: datetime | None = None

    def current(self) -> Book | None:
        if self._book is None:
            return None
        try:
            require_fresh(self._book.source_at, self._book.received_at,
                          self.public.clock(), self.market.cache_age)
        except MarketDataError:
            self.health = "stale"
            self._reset("stale_depth")
            return None
        return self._book

    def _reset(self, reason: str) -> None:
        if self._book is not None:
            self._book = None
            self.generation += 1
        if self.market.source == "websocket":
            self.market._set_source("unavailable", reason)

    async def _listen(self, recovering: bool) -> None:
        steps = await self.market._market_increments()
        previous: Book | None = None
        validated_timestamp: datetime | None = None
        silence = self.market.cache_age.total_seconds()
        async with kucoin_subscription(self.public, DEPTH_TOPIC) as (socket, interval, timeout, connection):
            self.health = "awaiting_depth"
            try:
                async for message in kucoin_messages(
                    socket, DEPTH_TOPIC, interval, timeout, connection, silence,
                    lambda: validated_timestamp,
                ):
                    data = message.get("data")
                    try:
                        if message.get("subject") != "level2" or not isinstance(data, dict):
                            raise MarketDataError("invalid KuCoin WS depth payload")
                        if any(not isinstance(data.get(side), list) or len(data[side]) > 5
                               for side in ("bids", "asks")):
                            raise MarketDataError("invalid KuCoin WS depth side")
                        received = self.public.clock()
                        timestamp = data.get("timestamp")
                        book = book_from({**data, "time": timestamp, "sequence": str(timestamp)}, received, *steps)
                    except MarketDataError:
                        # A malformed depth image contributes no data or freshness.
                        if self.current() is None:
                            raise
                        continue
                    book = replace(book, generation=self.generation, source="websocket")
                    require_fresh(book.source_at, book.received_at, received, self.market.cache_age)
                    if previous is not None and book.generation != previous.generation:
                        recovering, previous = True, None
                    if previous is not None and book.source_at <= previous.source_at:
                        if book.source_at == previous.source_at and (
                            book.bids, book.asks
                        ) == (previous.bids, previous.asks):
                            continue
                        raise MarketDataError("KuCoin WS depth timestamp regressed or conflicted")
                    if self._last_timestamp is not None and book.source_at <= self._last_timestamp:
                        if book.source_at < self._last_timestamp:
                            raise MarketDataError("KuCoin WS depth predates the last accepted image")
                        continue
                    # A recovery requires two consecutive fresh, advancing images.
                    if not recovering or previous is not None:
                        self._book = book
                        self._last_timestamp = book.source_at
                        self.last_valid_received_at = book.received_at
                        self.health = "healthy"
                        self.initial_attempt.set()
                    previous = book
                    validated_timestamp = book.source_at
            except (Unavailable, TimeoutError, aiohttp.ClientError, aiohttp.WSMessageTypeError):
                self._reset("websocket_unavailable")
                raise
            except (MarketDataError, ValueError, TypeError, AttributeError, KeyError):
                self._reset("invalid_depth")
                raise

    async def run(self) -> None:
        recovering = False
        delay = 1.0
        loop = asyncio.get_running_loop()
        try:
            while True:
                self.health = "connecting"
                started_at = loop.time()
                try:
                    await self._listen(recovering)
                except (Unavailable, TimeoutError, aiohttp.ClientError, aiohttp.WSMessageTypeError):
                    reason = "websocket_unavailable"
                except (MarketDataError, ValueError, TypeError, AttributeError, KeyError):
                    reason = "invalid_depth"
                self.health = "reconnecting"
                self._reset(reason)
                self.initial_attempt.set()
                recovering = True
                if loop.time() - started_at >= WS_STABLE_SECONDS:
                    delay = 1.0
                await self.public.sleep(delay + self.public.jitter(0.0, 0.5))
                delay = min(delay * 2, WS_STABLE_SECONDS)
        finally:
            self.health = "idle"
            self._reset("stream_stopped")
            self.initial_attempt.set()
