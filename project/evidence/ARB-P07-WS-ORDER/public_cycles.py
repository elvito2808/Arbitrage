"""Bounded read-only pricing probe with a single persistent public stream."""

import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

from arbitrage.config import load_config
from arbitrage.coordinator import MIN_INTERVAL, OpportunityCoordinator
from arbitrage.market_data import PublicConnections
from arbitrage.storage import Storage


class PublicProbe(PublicConnections):
    socket_opens = 0
    socket_closes = 0

    @asynccontextmanager
    async def kucoin_socket(self, *args):
        async with super().kucoin_socket(*args) as socket:
            self.socket_opens += 1
            try:
                yield socket
            finally:
                self.socket_closes += 1


async def run(directory):
    config = load_config(Path("config/simulation.toml"))
    settings = config.simulation.model_copy(update={
        "hedera_estimate_sender": os.environ["ARB_PUBLIC_ESTIMATE_SENDER"],
    })
    store = Storage(directory / "analysis.sqlite")
    store.initialize()
    with store.transaction() as tx:
        tx.seed_balances(config.balances.as_balances())
    async with aiohttp.ClientSession(trust_env=True) as session:
        api = PublicProbe(session)
        coordinator = OpportunityCoordinator.from_public(store, settings, api)
        worker = coordinator.worker("public-verification")
        worker.start()
        dex, kucoin = coordinator.dex.snapshot, coordinator.cex.snapshot
        stream = coordinator.cex.depth_stream
        task = asyncio.create_task(stream.run())
        row = {}

        async def read_dex():
            row["dex_start"] = datetime.now(UTC).isoformat()
            try:
                pool = await dex()
            except Exception as error:
                row["dex_error"] = type(error).__name__
                raise
            row.update(dex_source=pool.source_at.isoformat(), dex_receipt=pool.received_at.isoformat(),
                       block=pool.block, block_hash=pool.block_hash,
                       gas=[{"side": g.side, "units": g.execution_gas, "price_weibars": g.price_range_weibars,
                             "error": g.estimation_error} for g in pool.gas])
            row["dex_done"] = datetime.now(UTC).isoformat()
            return pool

        async def select_kucoin():
            row["kucoin_selection_start"] = datetime.now(UTC).isoformat()
            row["ws_state"] = stream.health
            latest = stream.last_valid_received_at
            row["ws_last_valid_message"] = latest.isoformat() if latest else None
            book = await kucoin()
            row.update(book_version=book.snapshot_id, transport=book.source,
                       kucoin_source=book.source_at.isoformat(), kucoin_receipt=book.received_at.isoformat())
            for kind in ("source", "receipt"):
                a = datetime.fromisoformat(row[f"dex_{kind}"])
                b = datetime.fromisoformat(row[f"kucoin_{kind}"])
                row[f"{kind}_skew_seconds"] = abs((a - b).total_seconds())
            return book

        coordinator.dex.snapshot, coordinator.cex.snapshot = read_dex, select_kucoin
        try:
            async with asyncio.timeout(4):
                await stream.initial_attempt.wait()
        except TimeoutError:
            pass
        try:
            for cycle in range(3):
                if cycle:
                    await asyncio.sleep(MIN_INTERVAL.total_seconds())
                row = {"cycle": cycle + 1, "socket_opens_before": api.socket_opens,
                       "socket_closes_before": api.socket_closes}
                result = await worker.analyze()
                if "kucoin_selection_start" in row:
                    assert row["dex_done"] <= row["kucoin_selection_start"]
                row.update(cycle_id=result.id, socket_opens_after=api.socket_opens,
                           socket_closes_after=api.socket_closes,
                           observations=[item.model_dump(mode="json", exclude_none=True) if item else None
                                         for item in (result.dex_snapshot, result.cex_snapshot)],
                           evaluations=[item.model_dump(mode="json", exclude_none=True)
                                        for item in result.evaluations])
                print(json.dumps(row), flush=True)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            print(json.dumps({"socket_opens": api.socket_opens, "socket_closes": api.socket_closes}), flush=True)


async def main():
    async with asyncio.timeout(65):
        with tempfile.TemporaryDirectory() as directory:
            await run(Path(directory))


if __name__ == "__main__":
    asyncio.run(main())
