import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import aiohttp

from arbitrage.kucoin import KuCoin, priced_units
from arbitrage.market_data import MAX_AGE, MAX_SKEW, TRADE_UNITS, MarketDataError
from arbitrage.models import Side
from arbitrage.saucerswap import PAIR, SaucerSwap, amounts, quantity
from check_hedera import ObservableConnections, emit, mirror_fee


def identity(head):
    return head["number"], head["hash"]


async def bracket(api, dex, cex, price, fee, attempt):
    head = await api.rpc("eth_getBlockByNumber", "latest", False)
    before_at = api.clock()
    block_num, block_hash = identity(head)
    usdc, whbar, _ = await dex._call(PAIR, "0902f1ac", block_num, 3)
    gross_buy, gross_sell = amounts(usdc, whbar)
    if identity(await api.rpc("eth_getBlockByNumber", "latest", False)) != identity(head):
        emit("bracket_skipped", attempt=attempt, reason="head_changed_before_estimates")
        return False
    deadline = api.clock() + MAX_AGE
    calls = {
        side: dex._estimate_call(side, dex.estimate_sender, gross_buy, gross_sell, deadline)[0]
        for side in Side
    }
    results = await asyncio.gather(
        *(api.rpc("eth_estimateGas", call, "latest") for call in calls.values()),
        cex.snapshot(), return_exceptions=True,
    )
    after = await api.rpc("eth_getBlockByNumber", "latest", False)
    checked_at = api.clock()
    age = (checked_at - datetime.fromtimestamp(int(head["timestamp"], 16), UTC)).total_seconds()
    stable = identity(after) == identity(head)
    emit("bracket", attempt=attempt, head=block_num, head_hash=block_hash,
         after=after["number"], after_hash=after["hash"], stable=stable,
         age_seconds=age, duration_seconds=(checked_at - before_at).total_seconds(),
         gross_buy_micro_usdc=gross_buy, gross_sell_micro_usdc=gross_sell)
    if not stable or any(isinstance(result, Exception) for result in results[:2]):
        return False
    book = results[2]
    if isinstance(book, Exception):
        emit("cex_snapshot", verified=False, error_type=type(book).__name__)
        return False
    ask_micro_usdt = priced_units(book.asks, Side.BUY, Fraction(0))
    assert price == fee * 10**10
    dex_source = datetime.fromtimestamp(int(head["timestamp"], 16), UTC)
    source_skew = abs(book.source_at - dex_source)
    emit("valuation_book", source=book.source, source_at=book.source_at.isoformat(),
         received_at=book.received_at.isoformat(), gross_1000_hbar_ask_micro_usdt=ask_micro_usdt,
         dex_cex_source_skew_seconds=source_skew.total_seconds(), within_runtime_skew=source_skew <= MAX_SKEW)
    for side, raw in zip(Side, results[:2], strict=True):
        units = quantity(raw, 21_000, 15_000_000)
        tinybars = (units * price + 10**10 - 1) // 10**10
        usdc = (tinybars * ask_micro_usdt + TRADE_UNITS - 1) // TRADE_UNITS
        emit("verified_raw_estimate", side=side.value, chain=295, gas=units, price_weibars=price,
             tinybars=tinybars, hbar=str(Decimal(tinybars).scaleb(-8)), micro_usdc=usdc)
    return True


async def main():
    sender = os.environ["ARB_QA_ESTIMATE_SENDER"]
    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=5)) as session:
        api = ObservableConnections(session)
        dex = SaucerSwap(api, estimate_sender=sender)
        cex = KuCoin(api)
        chain, price, fee = await asyncio.gather(
            api.rpc("eth_chainId"), api.rpc("eth_gasPrice"), mirror_fee(api),
        )
        assert chain == "0x127"
        price = int(price, 16)
        async with asyncio.timeout(24):
            for attempt in range(1, 5):
                try:
                    verified = await bracket(api, dex, cex, price, fee, attempt)
                except MarketDataError as exc:
                    emit("attempt_failed", attempt=attempt, error_type=type(exc).__name__)
                else:
                    if verified:
                        emit("finish", stable_pair_verified=True)
                        return 0
                await asyncio.sleep(0.25)
    emit("finish", stable_pair_verified=False)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
