"""Run isolated read-only analyses with the normal public market adapters."""

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

from arbitrage.config import load_config
from arbitrage.coordinator import OpportunityCoordinator
from arbitrage.market_data import PublicConnections
from arbitrage.storage import Storage


async def cycle(session, run, config, directory):
    storage = Storage(directory / f"cycle-{run}.sqlite")
    storage.initialize()
    with storage.transaction() as transaction:
        transaction.seed_balances(config.balances.as_balances())
    engine = OpportunityCoordinator.from_public(storage, config.simulation, PublicConnections(session))
    dex_snapshot, cex_snapshot = engine.dex.snapshot, engine.cex.snapshot
    row = {"cycle": run}
    stream = None

    def stamp(key):
        row[key] = datetime.now(UTC).isoformat()

    async def dex():
        stamp("dex_start")
        try:
            pool = await dex_snapshot()
            row.update(block=pool.block, hash=pool.block_hash, dex_source=pool.source_at.isoformat(),
                       dex_receipt=pool.received_at.isoformat(),
                       gas=[{"side": gas.side, "units": gas.execution_gas,
                             "prices_weibars": gas.price_range_weibars, "error": gas.estimation_error,
                             "evidence": gas.route_evidence.model_dump(mode="json") if gas.route_evidence else None}
                            for gas in pool.gas])
            return pool
        except Exception as error:
            row["dex_error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            stamp("dex_done")

    async def cex():
        nonlocal stream
        stamp("kucoin_start")
        stream = asyncio.create_task(engine.cex.depth_stream.run())
        await asyncio.sleep(0)
        try:
            book = await cex_snapshot()
            row.update(kucoin_source=book.source_at.isoformat(), kucoin_receipt=book.received_at.isoformat(),
                       kucoin_transport=book.source)
            return book
        except Exception as error:
            row["kucoin_error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            stamp("kucoin_done")

    engine.dex.snapshot, engine.cex.snapshot = dex, cex
    worker = engine.worker("public-verification")
    worker.start()
    stamp("request_start")
    try:
        result = await worker.analyze()
        row["evaluations"] = [item.model_dump(mode="json", exclude_none=True) for item in result.evaluations]
        if "kucoin_start" in row:
            assert row["dex_done"] <= row["kucoin_start"]
        if "kucoin_source" in row:
            for field in ("source", "receipt"):
                dex_time = datetime.fromisoformat(row[f"dex_{field}"])
                cex_time = datetime.fromisoformat(row[f"kucoin_{field}"])
                row[f"{field}_skew_seconds"] = abs((dex_time - cex_time).total_seconds())
    finally:
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        stamp("finished")
        print(json.dumps(row), flush=True)


async def main():
    config = load_config(Path("config/simulation.toml"))
    sender = os.environ.get("ARB_PUBLIC_ESTIMATE_SENDER")
    if sender:
        config = config.model_copy(update={"simulation": config.simulation.model_copy(
            update={"hedera_estimate_sender": sender})})
    async with asyncio.timeout(46), aiohttp.ClientSession(trust_env=True) as session:
        with tempfile.TemporaryDirectory() as temporary:
            for run in range(1, 5):
                await cycle(session, run, config, Path(temporary))


if __name__ == "__main__":
    asyncio.run(main())
