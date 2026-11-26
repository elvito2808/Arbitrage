"""Public KuCoin HBAR-USDT data and simulation request pricing."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp

from .market_data import MAX_AGE, TRADE_UNITS, MarketDataError, PublicConnections, Unavailable, require_fresh
from .models import Asset, MarketQuote, Side, Venue

SYMBOL = "HBAR-USDT"
TOPIC = f"/market/ticker:{SYMBOL}"
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
        prices = [entry.price for entry in side]
        if prices != sorted(set(prices), reverse=key == "bids"):
            raise MarketDataError("KuCoin depth order or duplicate level")
        sides.append(tuple(side))
    bids, asks = sides
    crossed(bids[0], asks[0])
    return Book(sequence(data.get("sequence")), millis(data.get("time")), received, bids, asks)


def priced_units(levels: tuple[Level, ...], side: Side, fee: Fraction) -> int:
    remaining = Fraction(1000)
    gross = Fraction(0)
    for entry in levels:
        quantity = min(remaining, entry.quantity)
        gross += quantity * entry.price
        remaining -= quantity
        if not remaining:
            break
    if remaining:
        raise MarketDataError("visible KuCoin depth cannot cover 1,000 HBAR")
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
        self.fee = Fraction(fee)
        self.cache_age = cache_age
        self._increments: tuple[Fraction, Fraction] | None = None
        self._book: Book | None = None
        self._generation = 0
        self._lock = asyncio.Lock()
        self.stream = KuCoinTickerStream(public, self._invalidate)

    def _invalidate(self) -> None:
        self._generation += 1
        self._book = None

    async def indicative_ticker(self) -> Ticker:
        data = await self.public.kucoin("ticker")
        return ticker_from(data, self.public.clock())

    async def snapshot(self) -> Book:
        async with self._lock:
            generation = self._generation
            try:
                async with asyncio.timeout(9):
                    if self._increments is None:
                        data = await self.public.kucoin("symbol")
                        base = exact(data.get("baseIncrement"))
                        price = exact(data.get("priceIncrement"))
                        quote = exact(data.get("quoteIncrement"))
                        if min(base, price, quote) <= 0 or 1000 % base:
                            raise MarketDataError("invalid KuCoin increments for fixed size")
                        self._increments = base, price
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
            except (Unavailable, TimeoutError):
                if self._book is None:
                    raise Unavailable("KuCoin depth unavailable") from None
                book = self._book
            except MarketDataError:
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
            return book

    def price(self, book: Book, side: Side) -> MarketQuote:
        if book.generation != self._generation:
            raise MarketDataError("KuCoin book invalidated by connection reset")
        require_fresh(book.source_at, book.received_at, self.public.clock(), self.cache_age)
        return MarketQuote(
            venue=Venue.CEX, side=side, base_asset=Asset.HBAR, quote_asset=Asset.USDT,
            base_units=TRADE_UNITS,
            quote_units=priced_units(book.asks if side == Side.BUY else book.bids, side, self.fee),
            snapshot_id=f"kucoin:{SYMBOL}:{book.sequence}",
            observed_at=book.received_at, source_at=book.source_at,
        )

    async def quote(self, side: Side, hbar_units: int) -> MarketQuote:
        if hbar_units != TRADE_UNITS:
            raise MarketDataError("expected exactly 1,000 HBAR")
        return self.price(await self.snapshot(), side)


class KuCoinTickerStream:
    """Indicative top levels. Only timestamped REST depth is eligible for pricing."""

    def __init__(self, public: PublicConnections, invalidate: Callable[[], None]):
        self.public = public
        self.invalidate = invalidate
        self.queue: asyncio.Queue[Ticker] = asyncio.Queue(maxsize=1)
        self.health = "idle"
        self._running = False

    async def _listen(self) -> None:
        self.health = "connecting"
        async with asyncio.timeout(8):
            data = await self.public.kucoin("bootstrap")
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
        async with self.public.kucoin_socket(endpoint.geturl(), token, connect_id) as ws:
            async with asyncio.timeout(5):
                welcome = await ws.receive_json()
                if welcome.get("type") != "welcome":
                    raise MarketDataError("missing KuCoin WS welcome")
                await ws.send_json({
                    "id": connect_id, "type": "subscribe", "topic": TOPIC,
                    "privateChannel": False, "response": True,
                })
                ack = await ws.receive_json()
                if ack.get("type") != "ack" or ack.get("id") != connect_id:
                    raise MarketDataError("missing KuCoin WS subscription acknowledgment")
            self.health = "healthy"
            loop = asyncio.get_running_loop()
            ping_at = loop.time() + interval - timeout
            pong_due = None
            last_sequence = -1
            while True:
                deadline = pong_due if pong_due is not None else ping_at
                try:
                    if loop.time() >= deadline:
                        raise TimeoutError
                    async with asyncio.timeout_at(deadline):
                        message = await ws.receive_json()
                except TimeoutError:
                    if pong_due is not None:
                        raise Unavailable("KuCoin WS heartbeat timed out") from None
                    async with asyncio.timeout(timeout):
                        await ws.send_json({"id": connect_id, "type": "ping"})
                    pong_due = loop.time() + timeout
                    continue
                if message.get("type") == "pong" and message.get("id") == connect_id:
                    pong_due = None
                    ping_at = loop.time() + interval - timeout
                if message.get("type") != "message" or message.get("topic") != TOPIC:
                    continue
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
