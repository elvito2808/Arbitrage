import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiohttp

from arbitrage.kucoin import KuCoin
from arbitrage.market_data import PublicConnections, RPC, TRADE_UNITS
from arbitrage.models import Side
from arbitrage.saucerswap import PAIR, ROUTER, SaucerSwap, amounts, gas_cost_tinybars

MIRROR = "https://mainnet-public.mirrornode.hedera.com/api/v1"
COINS = {"0.0.456858", "0.0.1456986"}


def log(event, **fields):
    print(json.dumps({"utc": datetime.now(UTC).isoformat(), "event": event, **fields}), flush=True)


class ObservedSocket:
    def __init__(self, socket):
        self.socket = socket

    async def send_json(self, message):
        log("ws_send", kind=message["type"], topic=message.get("topic"))
        return await self.socket.send_json(message)

    async def receive_json(self):
        message = await self.socket.receive_json()
        if message.get("type") != "message":
            log("ws_receive", kind=message.get("type"), code=message.get("code"))
        return message


class ObservedKuCoin(PublicConnections):
    async def kucoin(self, resource):
        try:
            data = await super().kucoin(resource)
        except Exception as error:
            log("kucoin_public_error", resource=resource, category=type(error).__name__)
            raise
        if resource == "bootstrap":
            log("public_bootstrap", servers=data.get("instanceServers"))
        return data

    @asynccontextmanager
    async def kucoin_socket(self, *args):
        try:
            async with super().kucoin_socket(*args) as socket:
                log("ws_upgrade", status=101)
                yield ObservedSocket(socket)
        except Exception as error:
            log("ws_error", category=type(error).__name__, http_status=getattr(error, "status", None))
            raise


async def kucoin(session):
    api = ObservedKuCoin(session)
    market = KuCoin(api)
    market.on_source_change = lambda source, reason: log("source_transition", source=source, reason=reason)
    stream = asyncio.create_task(market.depth_stream.run())
    last_health = None
    seen = set()
    checks = False
    try:
        for _ in range(23):
            if market.depth_stream.health != last_health:
                last_health = market.depth_stream.health
                log("stream_health", state=last_health)
            if stream.done():
                stream.result()
            if market.source == "unavailable" or market.depth_stream.current() is not None:
                try:
                    book = await market.snapshot()
                    if book.source not in seen:
                        seen.add(book.source)
                        quotes = {side.value: market.price(book, side).quote_units for side in Side}
                        def depth(levels):
                            return [[str(Decimal(level.price.numerator) / level.price.denominator),
                                     str(Decimal(level.quantity.numerator) / level.quantity.denominator)]
                                    for level in levels]
                        log("priced_book", source=book.source, symbol="HBAR-USDT",
                            exchange_utc=book.source_at.isoformat(), received_utc=book.received_at.isoformat(),
                            bids=depth(book.bids), asks=depth(book.asks), quotes_micro_usdt=quotes)
                        checks |= book.source == "websocket"
                except Exception as error:
                    log("pricing_error", category=type(error).__name__)
            await asyncio.sleep(1)
    finally:
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
    return checks


async def hedera(session, account):
    api = PublicConnections(session)
    sender = "0x" + format(int(account.split(".")[-1]), "040x")
    dex = SaucerSwap(api, estimate_sender=sender)
    endpoints = [f"{MIRROR}/accounts/{account}/tokens?limit=100",
                 f"{MIRROR}/accounts/{account}/allowances/tokens?spender.id=0.0.{int(ROUTER, 16)}&limit=100",
                 f"{MIRROR}/network/fees"]
    results = await asyncio.gather(*(api._json("GET", endpoint) for endpoint in endpoints))
    assets = {v["token_id"]: v for v in results[0]["tokens"] if v["token_id"] in COINS}
    approvals = {v["token_id"]: v["amount"] for v in results[1]["allowances"] if v["token_id"] in COINS}
    fees = next(v["gas"] for v in results[2]["fees"] if v["transaction_type"] == "EthereumTransaction")
    done = False
    for attempt in range(1, 6):
        chain, book_head, price_hex = await asyncio.gather(
            api.rpc("eth_chainId"), api.rpc("eth_getBlockByNumber", "latest", False), api.rpc("eth_gasPrice"))
        price = int(price_hex, 16)
        reserves = await dex._call(PAIR, "0902f1ac", book_head["number"], 3)
        buy, sell = amounts(*reserves[:2])
        due = datetime.now(UTC) + timedelta(seconds=10)
        calls = {side: dex._estimate_call(side, sender, buy, sell, due)[0] for side in Side}
        max_buy = int(calls[Side.BUY]["data"][74:138], 16)
        required = {"0.0.456858": max_buy, "0.0.1456986": TRADE_UNITS}
        suitable = all(assets.get(token, {}).get("balance", 0) >= amount
                       and approvals.get(token, 0) >= amount
                       and assets[token].get("freeze_status") != "FROZEN"
                       and assets[token].get("kyc_status") != "REVOKED"
                       for token, amount in required.items())
        log("sender_check", account=account, balance_and_allowance_sufficient=suitable,
            required_raw=required, associated_tokens=sorted(assets), chain=chain, attempt=attempt,
            rpc_weibars_per_gas=price, rpc_raw_gas_price=price_hex, mirror_tinybars_per_gas=fees)
        if not suitable or chain != "0x127":
            return False
        start = await api.rpc("eth_getBlockByNumber", "latest", False)
        if (start["hash"], start["number"]) != (book_head["hash"], book_head["number"]):
            log("head_before_estimate_changed", initial=book_head["number"], current=start["number"])
            continue

        async def estimate(side, call):
            request = {"jsonrpc": "2.0", "id": 2 if side == Side.BUY else 3,
                       "method": "eth_estimateGas", "params": [call, "latest"]}
            log("rpc_request", side=side.value, request=request)
            async with session.post(RPC, json=request, allow_redirects=False) as response:
                raw = await response.json()
                status = response.status
            error = raw.get("error", {})
            categories = ("INSUFFICIENT_TOKEN_BALANCE", "INSUFFICIENT_ALLOWANCE",
                          "SPENDER_DOES_NOT_HAVE_ALLOWANCE", "TOKEN_NOT_ASSOCIATED_TO_ACCOUNT",
                          "CONTRACT_REVERT_EXECUTED", "execution reverted")
            category = next((value for value in categories if value in str(error.get("message"))), None)
            log("rpc_response", side=side.value, http_status=status, id=raw.get("id"),
                jsonrpc=raw.get("jsonrpc"), result=raw.get("result"), error_code=error.get("code"),
                error_category=category, error_message=str(error.get("message", ""))[:240],
                error_data=error.get("data") if isinstance(error.get("data"), str) and error["data"].startswith("0x") else None)
            value = raw.get("result")
            if status == 200 and raw.get("id") == request["id"] and raw.get("jsonrpc") == "2.0" and not error:
                try:
                    gas = int(value, 16)
                except (TypeError, ValueError):
                    return None
                if 21_000 <= gas <= 15_000_000:
                    return gas
            return None

        estimates = await asyncio.gather(*(estimate(side, call) for side, call in calls.items()))
        end = await api.rpc("eth_getBlockByNumber", "latest", False)
        stable = (start["number"], start["hash"]) == (end["number"], end["hash"])
        log("head_bracket", stable=stable, before=start["number"], before_hash=start["hash"],
            after=end["number"], after_hash=end["hash"])
        if stable and all(value is not None for value in estimates):
            for side, gas in zip(calls, estimates, strict=True):
                tinybars = gas_cost_tinybars(gas, price)
                log("gas_conversion", side=side.value, gas=gas, tinybars=tinybars,
                    hbar=str(Decimal(tinybars) / 10**8))
            done = True
            break
        await asyncio.sleep(.25)
    if done:
        try:
            pool = await dex.snapshot()
            log("production_snapshot", block=pool.block,
                valid=[dex.verifies_route_gas(gas, pool) for gas in pool.gas],
                errors=[gas.estimation_error for gas in pool.gas])
        except Exception as error:
            log("production_snapshot_error", category=type(error).__name__, message=str(error))
    return done


async def main(args):
    log("probe_start", check=args.check, mode=args.transport)
    success = False
    try:
        async with asyncio.timeout(32):
            async with aiohttp.ClientSession(trust_env=(args.transport == "environment"),
                                             timeout=aiohttp.ClientTimeout(total=5)) as session:
                success = await (kucoin(session) if args.check == "K1" else hedera(session, args.account))
    except (TimeoutError, aiohttp.ClientError, ValueError) as error:
        log("probe_error", category=type(error).__name__)
    log("probe_end", success=success)
    return 0 if success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("check", choices=("K1", "H1"))
    parser.add_argument("transport", choices=("direct", "environment"))
    parser.add_argument("--account", default="0.0.9976411")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
