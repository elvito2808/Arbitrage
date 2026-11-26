"""Opt-in, bounded, public read-only network checks."""

import asyncio
import os

import aiohttp
import pytest

from arbitrage.kucoin import KuCoin
from arbitrage.market_data import MarketDataError, PublicConnections
from arbitrage.models import Side
from arbitrage.saucerswap import SaucerSwap

pytestmark = pytest.mark.skipif(
    os.environ.get("ARB_PUBLIC_SMOKE") != "1", reason="public network smoke is opt-in",
)


@pytest.mark.parametrize("venue", ["hedera", "kucoin_rest", "kucoin_ws"])
def test_public_market_smoke(venue):
    async def run():
        async with asyncio.timeout(20), aiohttp.ClientSession(trust_env=True) as session:
            public = PublicConnections(session)
            if venue == "hedera":
                snapshot = await SaucerSwap(public).snapshot()
                print(f"chain=295 block={snapshot.block} source={snapshot.source_at.isoformat()} "
                      f"received={snapshot.received_at.isoformat()} router={snapshot.router_verified} "
                      f"execution_gas_valid={[g.cost_valid for g in snapshot.gas]}")
                return
            cex = KuCoin(public)
            if venue == "kucoin_rest":
                book = await cex.snapshot()
                for side in Side:
                    try:
                        covered = cex.price(book, side).base_units == 100_000_000_000
                    except MarketDataError as exc:
                        if "cannot cover" not in str(exc):
                            raise
                        covered = False
                    print(f"{side.value} source={book.source_at.isoformat()} "
                          f"received={book.received_at.isoformat()} depth_covers_1000={covered}")
                return
            stream = asyncio.create_task(cex.stream.run())
            receive = asyncio.create_task(cex.stream.queue.get())
            try:
                done, _ = await asyncio.wait((stream, receive), return_when=asyncio.FIRST_COMPLETED)
                if stream in done:
                    stream.result()
                ticker = receive.result()
                print(f"WS ticker sequence={ticker.sequence} trade={ticker.trade_at.isoformat()} "
                      f"received={ticker.received_at.isoformat()}")
            finally:
                for task in (stream, receive):
                    task.cancel()
                await asyncio.gather(stream, receive, return_exceptions=True)

    try:
        asyncio.run(run())
    except (MarketDataError, TimeoutError, ExceptionGroup) as exc:
        errors = exc.exceptions if isinstance(exc, ExceptionGroup) else (exc,)
        detail = ", ".join(str(error) if isinstance(error, MarketDataError)
                           else type(error).__name__ for error in errors)
        pytest.fail(f"{venue} public smoke blocked: {detail}", pytrace=False)
