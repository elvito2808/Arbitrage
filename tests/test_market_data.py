import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arbitrage.kucoin import TOPIC, KuCoin
from arbitrage.market_data import MarketDataError, PublicConnections, Unavailable
from arbitrage.models import Side
from arbitrage.saucerswap import (
    FACTORY, HISTORICAL_GAS, PAIR, ROUTER, TRADE_UNITS, USDC, WHBAR, GasEstimate, SaucerSwap, amounts, word,
)

POOL_TIME = datetime(2026, 11, 26, 1, 30, 14, tzinfo=UTC)
BOOK_TIME = datetime(2026, 11, 26, 1, 31, 9, 991000, tzinfo=UTC)
BLOCK = 102783290
HASH = "0x" + "ab" * 32


def abi(*values):
    return "0x" + "".join(map(word, values))


class Hedera:
    def __init__(self):
        self.source = POOL_TIME
        self.now = POOL_TIME + timedelta(seconds=1)
        self.chain = "0x127"
        self.reverse = False
        self.down = False
        self.gas_down = False
        self.router_down = False
        self.router_wrong = False
        self.mirror_hash = HASH
        self.rpc_hash = HASH

    def clock(self):
        return self.now

    async def mirror_block(self, number):
        assert number == BLOCK
        return {"number": number, "hash": self.mirror_hash + "cd" * 16,
                "timestamp": {"to": f"{int(self.source.timestamp())}.617000001"}}

    async def rpc(self, method, *params):
        if self.down:
            raise Unavailable("fixture offline")
        if method == "eth_chainId":
            return self.chain
        if method == "eth_getBlockByNumber":
            return {"number": hex(BLOCK), "hash": self.rpc_hash, "timestamp": hex(int(self.source.timestamp()))}
        if method == "eth_gasPrice":
            if self.gas_down:
                raise Unavailable("fixture gas unavailable")
            return hex(1_630_000_000_000)
        if method == "eth_estimateGas":
            raise Unavailable("fixture has no route estimate")
        assert method == "eth_call"
        call, at = params
        assert at == hex(BLOCK)
        to, data = call["to"], call["data"]
        if to == FACTORY:
            assert data == "0xe6a43905" + word(int(USDC, 16)) + word(int(WHBAR, 16))
            return abi(int(PAIR, 16))
        if to in (USDC, WHBAR):
            assert data == "0x313ce567"
            return abi(6 if to == USDC else 8)
        if to == ROUTER:
            if self.router_down:
                raise Unavailable("fixture router unavailable")
            buy = data[:10] == "0x1f00ca74"
            a, b = (USDC, WHBAR) if buy else (WHBAR, USDC)
            selector = "1f00ca74" if buy else "d06ca61f"
            assert data == "0x" + selector + "".join(map(word, (
                TRADE_UNITS, 64, 2, int(a, 16), int(b, 16),
            )))
            return abi(32, 2, 52_761_762 + self.router_wrong, TRADE_UNITS) if buy else abi(
                32, 2, TRADE_UNITS, 52_418_017
            )
        assert to == PAIR
        return {
            "0x0dfe1681": abi(int(WHBAR if self.reverse else USDC, 16)),
            "0xd21220a7": abi(int(USDC if self.reverse else WHBAR, 16)),
            "0x0902f1ac": abi(199162090589, 378710131341665, 1795655018),
        }[data]


def test_historical_dex_integer_router_and_gas():
    api = Hedera()
    snapshot = asyncio.run(SaucerSwap(api).snapshot())
    buy, sell = snapshot.quotes
    assert snapshot.router_verified
    assert (buy.quote_units, sell.quote_units) == (52_761_762, 52_418_017)
    assert buy.snapshot_id == sell.snapshot_id
    assert buy.source_at == snapshot.source_at < buy.observed_at
    assert amounts(997, 2 * TRADE_UNITS) == (1001, 331)
    with pytest.raises(FrozenInstanceError):
        snapshot.block = BLOCK + 1
    for side, gas in zip((Side.BUY, Side.SELL), snapshot.gas, strict=True):
        assert gas.side == side
        assert gas.price_range_weibars == (1_630_000_000_000, 3_260_000_000_000)
        assert gas.route_evidence is None
        assert gas.estimation_error == "estimate_sender_unconfigured"
        assert HISTORICAL_GAS.calculated_gas_tinybars == HISTORICAL_GAS.charged_gas_tinybars == 22_762_599
        assert gas.execution_gas is None and not gas.cost_valid
        assert replace(gas, execution_gas=250_000).cost_valid
        assert not GasEstimate(side, None, None, execution_gas=250_000).cost_valid


@pytest.mark.parametrize(("field", "value", "match"), [
    ("reverse", True, "token ordering"),
    ("chain", "0x128", "chain ID"),
    ("rpc_hash", "0x" + "gg" * 32, "invalid Hedera RPC block"),
    ("mirror_hash", "0x" + "ef" * 32, "provenance"),
    ("router_wrong", True, "router"),
    ("now", POOL_TIME + timedelta(seconds=11), "stale"),
])
def test_dex_rejects_invalid_or_stale_data(field, value, match):
    api = Hedera()
    setattr(api, field, value)
    with pytest.raises(MarketDataError, match=match):
        asyncio.run(SaucerSwap(api).snapshot())


def test_dex_cache_and_optional_reads():
    async def run():
        api = Hedera()
        dex = SaucerSwap(api)
        api.router_down = api.gas_down = True
        original = await dex.snapshot()
        assert not original.router_verified
        assert original.gas[0].price_range_weibars is None
        api.down = True
        assert await dex.snapshot() is original
        api.now += timedelta(seconds=10)
        with pytest.raises(MarketDataError, match="stale"):
            await dex.snapshot()
    asyncio.run(run())


def historical_book():
    return {
        "sequence": "42", "time": (BOOK_TIME - datetime(1970, 1, 1, tzinfo=UTC))
        // timedelta(milliseconds=1),
        "bids": [["0.05297", "8500.6656"]],
        "asks": [["0.05300", "3835.502"]],
    }


class Exchange:
    metadata = {
        "symbol": "HBAR-USDT", "baseCurrency": "HBAR", "quoteCurrency": "USDT",
        "baseIncrement": "0.0001", "priceIncrement": "0.00001", "quoteIncrement": "0.00001",
    }

    def __init__(self):
        self.now = BOOK_TIME + timedelta(milliseconds=50)
        self.depth = historical_book()
        self.down = False

    def clock(self):
        return self.now

    async def kucoin(self, resource):
        if resource == "symbol":
            return self.metadata
        if resource == "ticker":
            return {"sequence": "41", "time": self.depth["time"] - 120_000,
                    "bestBid": "0.05297", "bestBidSize": "8500.6656",
                    "bestAsk": "0.05300", "bestAskSize": "3835.502"}
        assert resource == "depth"
        if self.down:
            raise Unavailable("fixture offline")
        return self.depth


def test_cex_historical_fee_once_and_partial_level():
    async def run():
        api = Exchange()
        cex = KuCoin(api)
        ticker = await cex.indicative_ticker()
        book = await cex.snapshot()
        assert ticker.bid == book.bids[0] and ticker.ask == book.asks[0]
        assert ticker.trade_at < book.source_at
        buy, sell = cex.price(book, Side.BUY), cex.price(book, Side.SELL)
        assert (buy.quote_units, sell.quote_units) == (53_053_000, 52_917_030)
        assert buy.source_at == BOOK_TIME < buy.observed_at
        assert buy.snapshot_id == sell.snapshot_id
        no_fee = KuCoin(api, fee=Decimal("0"))
        assert (await no_fee.quote(Side.BUY, TRADE_UNITS)).quote_units == 53_000_000
        api.depth["sequence"] = "43"
        api.depth["asks"] = [["0.053", "250.0001"], ["0.05301", "800"]]
        api.depth["bids"] = [["0.05297", "200.0001"], ["0.05296", "999"]]
        book = await cex.snapshot()
        assert cex.price(book, Side.BUY).quote_units == 53_060_508
        assert cex.price(book, Side.SELL).quote_units == 52_909_038
        api.depth["sequence"] = "44"
        api.depth["asks"].pop()
        with pytest.raises(MarketDataError, match="cannot cover"):
            await cex.quote(Side.BUY, TRADE_UNITS)
        with pytest.raises(MarketDataError, match="exactly"):
            await cex.quote(Side.BUY, 1)

    asyncio.run(run())


@pytest.mark.parametrize(("key", "value", "match"), [
    ("asks", [["0.05296", "1000"]], "crossed"),
    ("bids", [["0.05297", "1000.00001"]], "increments"),
    ("asks", [["0.05300", "1"], ["0.05300", "2"]], "duplicate"),
    ("time", 0, "stale"),
    ("time", historical_book()["time"] + 3000, "skew"),
])
def test_bad_books(key, value, match):
    api = Exchange()
    api.depth[key] = value
    with pytest.raises(MarketDataError, match=match):
        asyncio.run(KuCoin(api).snapshot())


def test_cex_cache_expiry_and_reconnect_invalidation():
    async def run():
        api = Exchange()
        cex = KuCoin(api)
        book = await cex.snapshot()
        api.down = True
        assert await cex.snapshot() is book
        cex.stream._reset()
        with pytest.raises(MarketDataError, match="invalidated"):
            cex.price(book, Side.BUY)
        with pytest.raises(Unavailable):
            await cex.snapshot()
        api.down = False
        await cex.snapshot()
        api.down = True
        api.now += timedelta(seconds=10)
        with pytest.raises(MarketDataError, match="stale"):
            await cex.snapshot()

    asyncio.run(run())


class Http:
    def __init__(self, statuses, headers):
        self.statuses = deque(statuses)
        self.headers = headers
        self.calls = 0

    @asynccontextmanager
    async def request(self, method, url, **kwargs):
        assert method == "GET" and url.endswith("level2_20?symbol=HBAR-USDT")
        assert kwargs["allow_redirects"] is False
        self.calls += 1
        self.status = self.statuses.popleft()
        yield self

    async def json(self):
        return {"code": "200000", "data": historical_book()}


@pytest.mark.parametrize(("statuses", "succeeds"), [
    ([429, 200], True), ([429, 429, 429], False),
])
def test_http_rate_limit_headers_and_attempt_cap(statuses, succeeds):
    async def run():
        delays = []

        async def sleep(delay):
            delays.append(delay)

        session = Http(statuses, {"Retry-After": "1", "gw-ratelimit-reset": "1200"})
        client = PublicConnections(session, sleep=sleep, timer=lambda: sum(delays))
        if succeeds:
            assert (await client.kucoin("depth"))["sequence"] == "42"
        else:
            with pytest.raises(Unavailable, match="429"):
                await client.kucoin("depth")
        assert session.calls == len(statuses)
        assert delays == [1.2] * (len(statuses) - 1)

    asyncio.run(run())


def test_rate_limit_beyond_budget_does_not_retry():
    async def run():
        session = Http([429], {"Retry-After": "30"})
        client = PublicConnections(session)
        with pytest.raises(Unavailable, match="429"):
            await client.kucoin("depth")
        assert session.calls == 1

    asyncio.run(run())


class StreamExchange(Exchange):
    def __init__(self):
        super().__init__()
        self.bootstraps = 0
        self.connection_ids = []
        self.socket_ready = asyncio.Event()

    async def kucoin(self, resource):
        if resource != "bootstrap":
            return await super().kucoin(resource)
        self.bootstraps += 1
        return {
            "token": f"fixture-{self.bootstraps}",
            "instanceServers": [{"endpoint": "wss://ws-api-spot.kucoin.com/",
                                 "pingInterval": 18000, "pingTimeout": 10000}],
        }

    async def sleep(self, delay):
        assert delay > 0

    def jitter(self, start, end):
        return end

    @asynccontextmanager
    async def kucoin_socket(self, endpoint, token, connect_id):
        assert token == f"fixture-{self.bootstraps}"
        self.connection_ids.append(connect_id)
        if self.bootstraps == 1:
            raise Unavailable("fixture disconnect")
        self.connect_id = connect_id
        self.recv_index = 0
        yield self

    async def send_json(self, data):
        assert data["topic"] == TOPIC and data["privateChannel"] is False

    async def receive_json(self):
        self.recv_index += 1
        if self.recv_index == 1:
            return {"type": "welcome"}
        if self.recv_index == 2:
            return {"type": "ack", "id": self.connect_id}
        if self.recv_index <= 4:
            return {
                "type": "message", "topic": TOPIC,
                "data": {"time": historical_book()["time"] - 120_000,
                         "sequence": str(self.recv_index), "bestBid": "0.05297",
                         "bestAsk": "0.053", "bestBidSize": "300", "bestAskSize": "200"},
            }
        self.socket_ready.set()
        await asyncio.Future()


def test_ws_refreshes_token_resubscribes_and_bounds_queue():
    async def run():
        api = StreamExchange()
        cex = KuCoin(api)
        await cex.snapshot()
        task = asyncio.create_task(cex.stream.run())
        try:
            await asyncio.wait_for(api.socket_ready.wait(), 1)
            assert api.bootstraps == len(set(api.connection_ids)) == 2
            assert cex.stream.health == "healthy"
            assert cex.stream.queue.qsize() == 1
            ticker = cex.stream.queue.get_nowait()
            assert ticker.sequence == 4
            assert ticker.received_at - ticker.trade_at > timedelta(minutes=2)
            api.down = True
            with pytest.raises(Unavailable):
                await cex.snapshot()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert cex.stream.health == "idle"

    asyncio.run(run())


@pytest.mark.parametrize("field,invalid", [
    ("symbol", "BTC-USDT"), ("baseCurrency", "BTC"), ("quoteCurrency", "USDC"),
    ("symbol", None), ("baseCurrency", None), ("quoteCurrency", None),
    ("symbol", ["HBAR-USDT"]), ("baseCurrency", 123), ("quoteCurrency", {"value": "USDT"}),
    ("symbol", "hbar-usdt"), ("baseCurrency", " HBAR"), ("quoteCurrency", ""),
])
def test_kucoin_validates_requested_identity_before_reading_depth(field, invalid):
    class RecordingExchange(Exchange):
        def __init__(self):
            super().__init__()
            self.metadata = self.metadata.copy()
            self.requests = []

        async def kucoin(self, resource):
            self.requests.append(resource)
            return await super().kucoin(resource)

    async def exercise():
        api = RecordingExchange()
        cex = KuCoin(api)
        original = await cex.snapshot()
        metadata = api.metadata.copy()
        cex._invalidate()  # The same invalidation used on WS reconnect.
        if invalid is None:
            del api.metadata[field]
        else:
            api.metadata[field] = invalid
        api.requests.clear()
        with pytest.raises(MarketDataError, match="symbol identity"):
            await cex.snapshot()
        assert api.requests == ["symbol"]
        with pytest.raises(MarketDataError, match="invalidated"):
            cex.price(original, Side.BUY)
        api.down = True
        with pytest.raises(MarketDataError, match="symbol identity"):
            await cex.snapshot()
        api.metadata = metadata
        api.down = False
        assert (await cex.quote(Side.BUY, TRADE_UNITS)).quote_units == 53_053_000

    asyncio.run(exercise())


@pytest.mark.parametrize("metadata", [
    None,
    [],
    ["HBAR-USDT"],
    "HBAR-USDT",
    42,
    True,
    {},
    {**Exchange.metadata, "baseIncrement": []},
    {**Exchange.metadata, "priceIncrement": "NaN"},
    {**Exchange.metadata, "quoteIncrement": "0"},
], ids=[
    "null", "empty-list", "list", "string", "number", "boolean",
    "empty-object", "invalid-base-increment", "invalid-price-increment", "invalid-quote-increment",
])
def test_kucoin_invalid_symbol_metadata_discards_cached_book(metadata):
    class RecordingExchange(Exchange):
        def __init__(self):
            super().__init__()
            self.requests = []

        async def kucoin(self, resource):
            self.requests.append(resource)
            return await super().kucoin(resource)

    async def exercise():
        api = RecordingExchange()
        cex = KuCoin(api)
        original = await cex.snapshot()
        valid_metadata = api.metadata
        # Force a metadata refresh with the prior book still eligible for fallback.
        cex._increments = None
        api.metadata = metadata
        api.requests.clear()
        with pytest.raises(MarketDataError, match="KuCoin|exchange"):
            await cex.snapshot()
        assert api.requests == ["symbol"]
        assert cex._increments is None
        assert cex._book is None
        with pytest.raises(MarketDataError, match="invalidated"):
            cex.price(original, Side.BUY)

        api.metadata = valid_metadata
        api.down = True
        with pytest.raises(Unavailable, match="depth unavailable"):
            await cex.snapshot()
        assert cex.cache_hits == 0

        api.down = False
        assert (await cex.quote(Side.BUY, TRADE_UNITS)).quote_units == 53_053_000

    asyncio.run(exercise())
