import asyncio
import sys

import aiohttp

from arbitrage.kucoin import KuCoin
from arbitrage.market_data import PublicConnections


async def main():
    async with aiohttp.ClientSession(trust_env=True) as session:
        market = KuCoin(PublicConnections(session))
        stream = asyncio.create_task(market.depth_stream.run())
        try:
            async with asyncio.timeout(20):
                while (book := market.depth_stream.current()) is None:
                    if stream.done():
                        stream.result()
                    await asyncio.sleep(0.1)
                print(f"source={book.source} health={market.depth_stream.health} "
                      f"source_time={book.source_at.isoformat()} "
                      f"bid={float(book.bids[0].price)} bid_volume={float(book.bids[0].quantity)} "
                      f"ask={float(book.asks[0].price)} ask_volume={float(book.asks[0].quantity)}")
                return 0
        except TimeoutError:
            print(f"public WS depth timed out in 20s; stream={market.depth_stream.health}")
            return 1
        finally:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)


sys.exit(asyncio.run(main()))
