import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal

import aiohttp

from arbitrage.kucoin import KuCoin
from arbitrage.market_data import MarketDataError, PublicConnections
from arbitrage.models import Side


def emit(stage, **fields):
    print(json.dumps({"utc": datetime.now(UTC).isoformat(), "stage": stage, **fields}), flush=True)


class ObservableConnections(PublicConnections):
    async def kucoin(self, resource):
        try:
            result = await super().kucoin(resource)
        except MarketDataError as exc:
            emit("kucoin_http", resource=resource, error=str(exc), error_type=type(exc).__name__)
            raise
        emit("kucoin_http", resource=resource, status="ok")
        return result


class ObservableSession:
    def __init__(self, session):
        self._session = session
        self.socket = None
        self.heartbeat = asyncio.Event()

    def request(self, *args, **kwargs):
        return self._session.request(*args, **kwargs)

    async def ws_connect(self, *args, **kwargs):
        try:
            websocket = await self._session.ws_connect(*args, **kwargs)
        except (aiohttp.ClientError, TimeoutError) as exc:
            emit("ws_handshake", status="failed", error_type=type(exc).__name__,
                 http_status=getattr(exc, "status", None))
            raise
        emit("ws_handshake", status="ok")
        self.socket = websocket
        return ObservableSocket(websocket, self.heartbeat)


class ObservableSocket:
    def __init__(self, socket, heartbeat):
        self._socket = socket
        self._heartbeat = heartbeat

    async def receive_json(self):
        data = await self._socket.receive_json()
        if isinstance(data, dict) and data.get("type") in {"welcome", "ack", "pong"}:
            emit("ws_control", message_type=data["type"])
            if data["type"] == "pong":
                self._heartbeat.set()
        return data

    async def send_json(self, data):
        if data.get("type") in {"subscribe", "ping"}:
            emit("ws_control", message_type=data["type"], topic=data.get("topic"))
        await self._socket.send_json(data)

    async def close(self):
        await self._socket.close()


def exact(value):
    return str(Decimal(value.numerator) / Decimal(value.denominator))


async def main():
    emit("start", mode=os.environ.get("ARB_QA_EGRESS", "runtime_direct"))
    timeout = aiohttp.ClientTimeout(total=5, connect=4)
    proxied = os.environ.get("ARB_QA_EGRESS") == "configured_proxy"
    if proxied and os.environ.get("HTTPS_PROXY"):
        os.environ.setdefault("WSS_PROXY", os.environ["HTTPS_PROXY"])
    async with aiohttp.ClientSession(trust_env=proxied, timeout=timeout) as session:
        observable = ObservableSession(session)
        public = ObservableConnections(observable)
        market = KuCoin(public)
        stream = asyncio.create_task(market.depth_stream.run())
        market.on_source_change = lambda source, reason: emit("source_transition", source=source, reason=reason)
        observed = []
        fallback_verified = False
        recovered = False
        try:
            async with asyncio.timeout(24):
                while len(observed) < 2 or not observable.heartbeat.is_set():
                    if stream.done():
                        stream.result()
                    book = market.depth_stream.current()
                    if book is not None and (not observed or book.source_at > observed[-1]):
                        selected = await market.snapshot()
                        if selected.source == "websocket":
                            book = selected
                            quote = {}
                            for side in Side:
                                try:
                                    quote[side.value] = market.price(book, side).quote_units
                                except MarketDataError as exc:
                                    quote[side.value] = type(exc).__name__
                            observed.append(book.source_at)
                            emit("validated_depth", health=market.depth_stream.health,
                                 source=book.source, source_at=book.source_at.isoformat(),
                                 received_at=book.received_at.isoformat(),
                                 bid=exact(book.bids[0].price), bid_quantity=exact(book.bids[0].quantity),
                                 ask=exact(book.asks[0].price), ask_quantity=exact(book.asks[0].quantity),
                                 quote_micro_usdt=quote)
                    await asyncio.sleep(0.15)
                emit("interrupt_own_ws")
                await asyncio.wait_for(observable.socket.close(), 2)
                while market.depth_stream.current() is not None:
                    await asyncio.sleep(0.02)
                replacement = await market.snapshot()
                fallback_verified = replacement.source == "rest"
                emit("fallback_after_own_close", source=replacement.source,
                     source_at=replacement.source_at.isoformat(), verified=fallback_verified)
                while not recovered:
                    if market.depth_stream.current() is not None:
                        recovery = await market.snapshot()
                        recovered = recovery.source == "websocket"
                        if recovered:
                            emit("websocket_recovered", source_at=recovery.source_at.isoformat())
                    await asyncio.sleep(0.15)
        except (TimeoutError, MarketDataError) as exc:
            emit("incomplete", valid_depth_count=len(observed), health=market.depth_stream.health, error_type=type(exc).__name__)
        finally:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        if len(observed) < 2:
            try:
                async with asyncio.timeout(9):
                    fallback = await market.snapshot()
                emit("fallback", source=fallback.source, source_at=fallback.source_at.isoformat(),
                     received_at=fallback.received_at.isoformat(), status="validated")
            except (MarketDataError, TimeoutError) as exc:
                emit("fallback", status="failed", error_type=type(exc).__name__)
        verified = len(observed) >= 2 and observable.heartbeat.is_set() and fallback_verified and recovered
        emit("finish", verified=verified, heartbeat=observable.heartbeat.is_set(),
             fallback_verified=fallback_verified, recovered=recovered)
        return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
