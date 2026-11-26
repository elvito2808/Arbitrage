import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from fractions import Fraction

import pytest

from arbitrage.kucoin import DEPTH_TOPIC, KuCoin
from arbitrage.market_data import MarketDataError, Unavailable
from arbitrage.models import Reason, Side
from test_coordinator import setup
from test_market_data import Exchange


class DepthSocket:
    def __init__(self):
        self.id = None
        self.messages = asyncio.Queue()
        self.reads = asyncio.Condition()
        self.read_count = 0
        self.closed = False

    async def send_json(self, message):
        assert message == {
            "id": self.id, "type": "subscribe", "topic": DEPTH_TOPIC,
            "privateChannel": False, "response": True,
        }

    async def receive_json(self):
        async with self.reads:
            self.read_count += 1
            self.reads.notify_all()
        if self.read_count == 1:
            return {"type": "welcome"}
        if self.read_count == 2:
            return {"type": "ack", "id": self.id}
        item = await self.messages.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def reading(self, count):
        async with self.reads:
            await self.reads.wait_for(lambda: self.read_count >= count)


class DepthExchange(Exchange):
    def __init__(self, *sockets):
        super().__init__()
        self.sockets = iter(sockets)
        self.connects = 0
        self.rest_reads = 0
        self.backoffs = asyncio.Queue()
        self.retry = asyncio.Queue()

    async def kucoin(self, resource):
        if resource == "bootstrap":
            return {"token": "test-public-token", "instanceServers": [{
                "endpoint": "wss://ws-api-spot.kucoin.com/",
                "pingInterval": 18000, "pingTimeout": 10000,
            }]}
        if resource == "depth":
            self.rest_reads += 1
        return await super().kucoin(resource)

    @asynccontextmanager
    async def kucoin_socket(self, endpoint, token, connect_id):
        self.connects += 1
        socket = next(self.sockets)
        if isinstance(socket, Exception):
            raise socket
        socket.id = connect_id
        try:
            yield socket
        finally:
            socket.closed = True

    async def sleep(self, delay):
        await self.backoffs.put(delay)
        await self.retry.get()

    def jitter(self, low, high):
        return low


def frame(api):
    return {
        "type": "message", "topic": DEPTH_TOPIC, "subject": "level2",
        "data": {
            "timestamp": api.depth["time"],
            "asks": [["0.054", "200"], ["0.055", "800"]],
            "bids": [["0.053", "300"], ["0.052", "700"]],
        },
    }


@asynccontextmanager
async def running(cex):
    task = asyncio.create_task(cex.depth_stream.run())
    try:
        async with asyncio.timeout(2):
            yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_websocket_prices_orders_and_switch_does_not_reuse_snapshot(setup, monkeypatch):
    monkeypatch.setattr("arbitrage.coordinator.MIN_INTERVAL", timedelta(seconds=1))
    _, market, coordinator = setup

    async def run():
        socket = DepthSocket()
        api = DepthExchange(socket)
        # Synchronize the two venue clocks while leaving different REST and WS prices.
        api.now = market.now
        api.depth["time"] = int((market.now - market.dex_age).timestamp() * 1000)
        coordinator.cex = cex = KuCoin(api)
        worker = coordinator.worker("hb-main")
        worker.start()
        socket.messages.put_nowait(frame(api))
        async with running(cex):
            await socket.reading(4)
            buy, sell = await asyncio.gather(cex.snapshot(), cex.snapshot())
            assert buy is sell
            assert buy.source == "websocket"
            assert buy.bids[0].quantity == Fraction(300)
            assert buy.asks[0].quantity == Fraction(200)
            cycle = await worker.analyze()
            assert cycle.cex_snapshot_id == buy.snapshot_id
            assert tuple(item.requested_cex.quote_units for item in cycle.evaluations) == (
                52_247_700, 54_854_800,
            )
            assert api.rest_reads == 0
            socket.messages.put_nowait(Unavailable("fixture disconnected"))
            assert await api.backoffs.get() == 1
            with pytest.raises(MarketDataError, match="invalidated"):
                cex.price(buy, Side.BUY)
            market.now += timedelta(seconds=1)
            api.now = market.now
            market.block += 1
            rest = await cex.snapshot()
            assert rest.timestamp_id == buy.timestamp_id and rest.snapshot_id != buy.snapshot_id
            replay = await worker.analyze()
            assert all(item.reason == Reason.DUPLICATE_SNAPSHOT for item in replay.evaluations)
        assert socket.closed

    asyncio.run(run())


def test_initial_failure_rest_during_reconnect_and_two_fresh_frames_to_recover():
    async def run():
        socket = DepthSocket()
        api = DepthExchange(Unavailable("fixture connect failure"), socket)
        cex = KuCoin(api)
        events = []
        cex.on_source_change = lambda source, reason: events.append((source, reason))
        async with running(cex):
            assert await api.backoffs.get() == 1
            assert (await cex.snapshot()).source == "rest"
            api.retry.put_nowait(None)
            await socket.reading(3)
            assert cex.depth_stream.health == "awaiting_depth"
            assert (await cex.snapshot()).source == "rest"
            data = frame(api)
            socket.messages.put_nowait(data)
            await socket.reading(4)
            assert (await cex.snapshot()).source == "rest"
            socket.messages.put_nowait(data)
            await socket.reading(5)
            assert (await cex.snapshot()).source == "rest"
            second = frame(api)
            second["data"]["timestamp"] += 1
            socket.messages.put_nowait(second)
            await socket.reading(6)
            assert (await cex.snapshot()).source == "websocket"
            assert api.rest_reads == 4
            assert events == [("rest", "websocket_unavailable"), ("websocket", "fresh_depth")]
        assert socket.closed

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["wrong_market", "stale", "heartbeat_silence"])
def test_unusable_websocket_does_not_enter_pricing(failure):
    async def run():
        socket = DepthSocket()
        api = DepthExchange(socket)
        cex = KuCoin(api, cache_age=timedelta(milliseconds=100))
        initial = frame(api)
        socket.messages.put_nowait(initial)
        async with running(cex):
            await socket.reading(4)
            assert (await cex.snapshot()).source == "websocket"
            bad = frame(api)
            if failure == "wrong_market":
                bad["topic"] = DEPTH_TOPIC.replace("HBAR", "BTC")
            elif failure == "stale":
                bad["data"]["timestamp"] -= 1000
            if failure != "heartbeat_silence":
                socket.messages.put_nowait(bad)
            assert await api.backoffs.get() == 1
            assert cex.source == "unavailable"
            assert (await cex.snapshot()).source == "rest"
            api.down = True
            api.now += timedelta(seconds=1)
            with pytest.raises(MarketDataError, match="stale"):
                await cex.snapshot()
            assert cex.source == "unavailable"

    asyncio.run(run())


def test_reconnect_backoff_stays_bounded_during_dual_source_outage():
    async def run():
        api = DepthExchange(*(Unavailable("fixture outage") for _ in range(7)))
        api.down = True
        cex = KuCoin(api)
        async with running(cex):
            for attempt, expected in enumerate((1, 2, 4, 8, 15, 15, 15), 1):
                assert await api.backoffs.get() == expected
                with pytest.raises(Unavailable, match="depth unavailable"):
                    await cex.snapshot()
                assert cex.source == "unavailable"
                assert api.connects == attempt
                if attempt < 7:
                    api.retry.put_nowait(None)

    asyncio.run(run())


def test_rest_fallback_observes_the_public_provider_rate_limit():
    from arbitrage.market_data import PublicConnections
    from test_market_data import BOOK_TIME, Http

    class FallbackHttp(Http):
        async def json(self):
            if self.url.endswith("/symbols/HBAR-USDT"):
                return {"code": "200000", "data": Exchange.metadata}
            return await super().json()

        @asynccontextmanager
        async def request(self, method, url, **kwargs):
            self.url = url
            if url.endswith("/symbols/HBAR-USDT"):
                assert method == "GET"
                self.status = 200
                yield self
            else:
                async with super().request(method, url, **kwargs) as response:
                    yield response

    async def run():
        slept = []

        async def sleep(delay):
            slept.append(delay)

        session = FallbackHttp([429, 200], {"Retry-After": "2"})
        cex = KuCoin(PublicConnections(session, clock=lambda: BOOK_TIME, sleep=sleep,
                                      timer=lambda: sum(slept)))
        assert (await cex.snapshot()).source == "rest"
        assert session.calls == 2 and slept == [2]

    asyncio.run(run())


def test_websocket_recovery_ignores_the_last_accepted_image():
    async def run():
        first, next_socket = DepthSocket(), DepthSocket()
        api = DepthExchange(first, next_socket)
        cex = KuCoin(api)
        first.messages.put_nowait(frame(api))
        async with running(cex):
            await first.reading(4)
            assert (await cex.snapshot()).source == "websocket"
            first.messages.put_nowait(Unavailable("fixture disconnect"))
            assert await api.backoffs.get() == 1
            api.retry.put_nowait(None)
            next_socket.messages.put_nowait(frame(api))
            await next_socket.reading(4)
            assert (await cex.snapshot()).source == "rest"
            api.depth["time"] += 1
            next_socket.messages.put_nowait(frame(api))
            await next_socket.reading(5)
            assert (await cex.snapshot()).source == "rest"
            api.depth["time"] += 1
            next_socket.messages.put_nowait(frame(api))
            await next_socket.reading(6)
            assert (await cex.snapshot()).source == "websocket"

    asyncio.run(run())


def test_persistent_stream_buffers_during_dex_and_preserves_selected_book(setup, monkeypatch):
    _, market, coordinator = setup
    monkeypatch.setattr("arbitrage.coordinator.MIN_INTERVAL", timedelta(seconds=1))

    async def exercise():
        socket = DepthSocket()
        api = DepthExchange(socket)
        api.now = market.now
        api.depth["time"] = int((market.now - market.dex_age).timestamp() * 1000)
        coordinator.cex = cex = KuCoin(api)
        worker = coordinator.worker("persistent-depth")
        worker.start()
        started = asyncio.Event()
        release = asyncio.Event()
        order = []
        selected = []
        original_dex, original_cex, original_market = market.snapshot, cex.snapshot, coordinator._market

        async def dex():
            order.append("dex:start")
            started.set()
            await release.wait()
            result = await original_dex()
            order.append("dex:done")
            return result

        async def kucoin():
            order.append("kucoin:select")
            book = await original_cex()
            selected.append(book)
            return book

        def evaluate(pool, book, observations, now):
            order.append("market:evaluate")
            assert book is selected[-1]
            return original_market(pool, book, observations, now)

        monkeypatch.setattr(market, "snapshot", dex)
        monkeypatch.setattr(cex, "snapshot", kucoin)
        monkeypatch.setattr(coordinator, "_market", evaluate)

        async with running(cex):
            socket.messages.put_nowait(frame(api))
            await socket.reading(4)
            first = asyncio.create_task(worker.analyze())
            try:
                await started.wait()
                api.depth["time"] += 1
                update = frame(api)
                update["data"]["asks"][0][1] = "250"
                socket.messages.put_nowait(update)
                await socket.reading(5)
                assert order == ["dex:start"]
                release.set()
                first_cycle = await first
            finally:
                if not first.done():
                    first.cancel()
                    await asyncio.gather(first, return_exceptions=True)

            assert first_cycle.dex_snapshot.completed_at == market.now
            assert first_cycle.cex_snapshot.id == selected[0].snapshot_id
            assert first_cycle.cex_snapshot.kucoin_book.ask_quantity == 250
            assert first_cycle.cex_snapshot.kucoin_book.symbol == "HBAR-USDT"
            assert first_cycle.cex_snapshot.kucoin_book.transport == "websocket"

            market.now += timedelta(seconds=1)
            api.now = market.now
            market.block += 1
            api.depth["time"] += 1000
            update = frame(api)
            update["data"]["asks"][0][1] = "300"
            socket.messages.put_nowait(update)
            await socket.reading(6)
            second_cycle = await worker.analyze()
            assert second_cycle.cex_snapshot.id != first_cycle.cex_snapshot.id
            assert second_cycle.cex_snapshot.kucoin_book.ask_quantity == 300
            assert selected[0].asks[0].quantity == 250
            assert selected[0].received_at != selected[1].received_at
            assert cex.depth_stream.last_valid_received_at == selected[1].received_at
            assert order == ["dex:start", "dex:done", "kucoin:select", "market:evaluate"] * 2
            assert api.connects == 1 and api.rest_reads == 0 and not socket.closed
        assert socket.closed

    asyncio.run(exercise())


def test_malformed_depth_does_not_refresh_or_mutate_last_valid_book():
    async def exercise():
        socket = DepthSocket()
        api = DepthExchange(socket)
        cex = KuCoin(api)
        socket.messages.put_nowait(frame(api))
        async with running(cex):
            await socket.reading(4)
            original = await cex.snapshot()
            api.now += timedelta(seconds=1)
            api.depth["time"] += 1000
            malformed = frame(api)
            malformed["data"]["bids"][0][1] = "0"
            socket.messages.put_nowait(malformed)
            await socket.reading(5)
            assert await cex.snapshot() is original
            assert cex.depth_stream.last_valid_received_at == original.received_at
            assert api.connects == 1 and api.rest_reads == 0

            api.now += timedelta(seconds=10)
            api.depth["time"] += 10_000
            assert (await cex.snapshot()).source == "rest"
            assert cex.depth_stream.last_valid_received_at == original.received_at

    asyncio.run(exercise())
