import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal

import aiohttp

from arbitrage.kucoin import KuCoin, priced_units
from arbitrage.market_data import MAX_SKEW, MIRROR, RPC, TRADE_UNITS, MarketDataError, PublicConnections
from arbitrage.models import Side
from arbitrage.saucerswap import (
    EXACT_INPUT_SELECTOR, EXACT_OUTPUT_SELECTOR, HISTORICAL_GAS, SaucerSwap, gas_cost_tinybars,
)

ROUTES = {"0x" + EXACT_OUTPUT_SELECTOR: "buy", "0x" + EXACT_INPUT_SELECTOR: "sell"}
REVERTS = (
    "INSUFFICIENT_TOKEN_BALANCE", "SPENDER_DOES_NOT_HAVE_ALLOWANCE",
    "TOKEN_NOT_ASSOCIATED_TO_ACCOUNT", "INSUFFICIENT_ALLOWANCE",
    "TRANSFER_FROM_FAILED", "CONTRACT_REVERT_EXECUTED",
)


def emit(stage, **fields):
    print(json.dumps({"utc": datetime.now(UTC).isoformat(), "stage": stage, **fields}), flush=True)


class ObservableConnections(PublicConnections):
    async def _json(self, method, url, payload=None):
        rpc = payload.get("method") if url == RPC and payload else None
        side = None
        if rpc == "eth_estimateGas":
            side = ROUTES[payload["params"][0]["data"][:10]]
            emit("estimate_request", side=side, route_selector=payload["params"][0]["data"][:10])
        try:
            data = await super()._json(method, url, payload)
        except MarketDataError as exc:
            if rpc:
                emit("rpc_failed", method=rpc, side=side, error=str(exc), error_type=type(exc).__name__)
            raise
        if rpc == "eth_estimateGas":
            err = data.get("error", {})
            message = str(err.get("message", "")).upper()
            category = next((category for category in REVERTS if category in message), "unclassified") if err else None
            emit("estimate_response", side=side, gas_hex=data.get("result"), error_code=err.get("code"),
                 error_category=category)
        elif rpc == "eth_getBlockByNumber":
            block = data.get("result") or {}
            emit("rpc_head", number=block.get("number"), source_timestamp_hex=block.get("timestamp"))
        elif rpc in {"eth_chainId", "eth_gasPrice"}:
            emit("rpc_scalar", method=rpc, result=data.get("result"))
        return data


async def mirror_fee(public):
    try:
        data = await public._json("GET", MIRROR + "/network/fees")
        fee, = (fee["gas"] for fee in data["fees"] if fee["transaction_type"] == "EthereumTransaction")
        emit("mirror_fee", tinybars_per_gas=fee)
        return fee
    except (MarketDataError, ValueError, KeyError):
        emit("mirror_fee", status="unverified")
        return None


async def main():
    sender = os.environ["ARB_QA_ESTIMATE_SENDER"]
    proxied = os.environ.get("ARB_QA_EGRESS") == "configured_proxy"
    emit("start", mode="configured_proxy" if proxied else "runtime_direct",
         calibration_tinybars=HISTORICAL_GAS.calculated_gas_tinybars)
    async with aiohttp.ClientSession(trust_env=proxied, timeout=aiohttp.ClientTimeout(total=5)) as session:
        api = ObservableConnections(session)
        dex = SaucerSwap(api, estimate_sender=sender)
        cex = KuCoin(api)
        async with asyncio.timeout(18):
            results = await asyncio.gather(dex.snapshot(), mirror_fee(api), cex.snapshot(), return_exceptions=True)
        snapshot, fee, book = results
        if isinstance(snapshot, BaseException):
            emit("snapshot_failed", error_type=type(snapshot).__name__)
            return 1
        emit("snapshot", block=snapshot.block, router_verified=snapshot.router_verified,
             source_at=snapshot.source_at.isoformat(), received_at=snapshot.received_at.isoformat(),
             gross_micro_usdc={quote.side.value: quote.quote_units for quote in snapshot.quotes})
        valuation = None
        if not isinstance(book, BaseException):
            coherent = (
                abs(book.source_at - snapshot.source_at) <= MAX_SKEW
                and abs(book.received_at - snapshot.received_at) <= MAX_SKEW
            )
            emit("cex_snapshot", source=book.source, source_at=book.source_at.isoformat(),
                 received_at=book.received_at.isoformat(), coherent=coherent)
            if coherent:
                try:
                    from fractions import Fraction
                    valuation = priced_units(book.asks, Side.BUY, Fraction(0))
                except MarketDataError:
                    pass
        valid = 0
        for gas in snapshot.gas:
            if not gas.cost_valid or not dex.verifies_route_gas(gas, snapshot):
                emit("estimate_admission", side=gas.side.value, verified=False,
                     reason=gas.estimation_error or "unverified")
                continue
            rpc = gas.route_evidence.rpc
            price = rpc.gas_price_weibars
            units = rpc.estimated_gas
            tinybars = gas_cost_tinybars(units, price)
            fee_matches = type(fee) is int and price == fee * 10**10
            estimated_micro_usdc = (tinybars * valuation + TRADE_UNITS - 1) // TRADE_UNITS if valuation else None
            valid += 1
            emit("estimate_admission", side=gas.side.value, verified=True, provider=rpc.provider,
                 chain=rpc.chain_id, gas=units, price_weibars_per_gas=price,
                 fee_matches_mirror=fee_matches, historical_calibration_substituted=False,
                 estimated_at=rpc.estimated_at.isoformat(), cost_tinybars=tinybars,
                 cost_hbar=str(Decimal(tinybars).scaleb(-8)), cost_micro_usdc=estimated_micro_usdc)
        emit("finish", verified_count=valid, coherent_cex=valuation is not None)
        return 0 if valid == 2 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
