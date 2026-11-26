import asyncio
import aiohttp
from arbitrage.market_data import PublicConnections
from arbitrage.saucerswap import SaucerSwap
from probe import log

class ObservedPublic(PublicConnections):
    async def rpc(self, method, *params):
        try:
            value = await super().rpc(method, *params)
            if method == 'eth_getBlockByNumber':
                log('production_head', number=value['number'], hash=value['hash'], timestamp=value['timestamp'])
            if method == 'eth_estimateGas':
                log('production_gas', result=value)
            return value
        except Exception as error:
            log('production_rpc_error', method=method, category=type(error).__name__, message=str(error))
            raise

    async def mirror_block(self, number):
        try:
            value = await super().mirror_block(number)
            log('mirror_block', number=number, timestamp=value['timestamp']['to'])
            return value
        except Exception as error:
            log('production_mirror_error', number=number, category=type(error).__name__, message=str(error))
            raise

async def main():
    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=6)) as session:
        dex = SaucerSwap(ObservedPublic(session), estimate_sender='0x000000000000000000000000000000000019bbff')
        for attempt in range(1, 5):
            try:
                pool = await dex.snapshot()
                valid = [dex.verifies_route_gas(gas, pool) for gas in pool.gas]
                log('snapshot', attempt=attempt, block=pool.block, valid=valid,
                    errors=[gas.estimation_error for gas in pool.gas])
                if all(valid):
                    return 0
            except Exception as error:
                log('snapshot_error', attempt=attempt, category=type(error).__name__, message=str(error))
            await asyncio.sleep(.5)
    return 1

if __name__ == '__main__':
    raise SystemExit(asyncio.run(asyncio.wait_for(main(), timeout=32)))
