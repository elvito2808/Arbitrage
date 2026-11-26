# P02 development

Arbitrage is a Python 3.12 modular monolith for simulation. Install and run the P02 tests from the checkout.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install '.[test]'
python -m pip check
python -m pytest
```

Load `config/simulation.toml` with `arbitrage.config.load_config`. Version 1 fixes the trade size at 1,000 HBAR, the session length at 24 hours, and worker capacity at three with one running at a time. Fee and risk settings are represented as decimal values. The USDC/USDT parity setting is a simulation assumption.

Initialize a SQLite file with `Storage(path).initialize()`. Use a path in private runtime storage. Use `Storage.transaction()` for a complete unit of work. An exception escaping the context rolls back the transaction. Initialize opening balances with `tx.seed_balances(config.balances.as_balances())`. Seeding preserves existing holdings. Holdings form a shared simulated portfolio. HBAR and WHBAR use eight decimal places. USDC uses six, and simulated USDT uses six as an accounting convention.

Persist worker, opportunity and execution records with the corresponding transaction methods. Opportunity records preserve requested quotes while the execution record holds separate fills. Reservation and ledger writes update balances inside the same SQLite transaction. Keep the transaction open only for database work. The experiment start and deadline are immutable per ID, so reopening the file preserves the deadline. The schema version uses SQLite `user_version`. Version 0 initializes the current schema and unsupported versions fail explicitly.

Use `OperationalLog.emit()` with its event and level enums. Caller metadata is discarded. The bounded producer returns false for rejected or dropped events. A consumer can retrieve JSON lines with `take()`. Financial ledger entries remain in SQLite. This phase provides the queue and event format, so a later lifecycle phase can attach the output sink.

`arbitrage.interfaces` defines the typed contracts for later phases. The adapters below implement its DEX and CEX quote interfaces.

## Public market adapters

Use a caller-owned `aiohttp.ClientSession` and pass it to
`arbitrage.market_data.PublicConnections`. Reuse the session for Hedera JSON-RPC,
the Hedera mirror node, and KuCoin REST and WebSocket. Enable `trust_env=True` when
the runner provides an outbound proxy. The caller owns the optional
`KuCoin.stream.run()` task and cancels and awaits it before closing the session.

- Use `await SaucerSwap(public).snapshot()` for both directions from the same
  block. The result includes both requested quotes, token reserves in smallest
  units, the pair's last update epoch, and the router agreement flag. Block
  freshness uses the mirror consensus range end. The relay reports the first
  32 bytes of the mirror block hash.
- Use one `await cex.snapshot()` and then `cex.price(book, Side.BUY)` and
  `cex.price(book, Side.SELL)` for both directions from the same book. Pass
  `fee=config.simulation.cex_fee_fraction` and set each adapter's
  `cache_age` from the corresponding configuration in seconds.
  The symbol's base and price increments validate depth levels. The fixed
  1,000 HBAR is the requested quantity. The internal USDT quantum is 10⁻⁶
  USDT, with buy costs rounded up and sell proceeds rounded down after the
  assumed quote-currency fee. The venue's quote increment describes its
  order interface. These totals are for simulation requests.
- `await cex.indicative_ticker()` and the optional WS stream expose best bid,
  ask and sizes with trade and receive times. Pricing always uses timestamped
  depth-20. The ticker clock describes the last trade and can lag behind book
  changes. The WS queue keeps one latest event and is cleared on reconnect.
  Reconnect obtains a new public bootstrap token, subscribes again and
  invalidates earlier depth snapshots. Keep WS URLs and bootstrap data out of
  logs.

HTTP uses at most three attempts per read, 2.5 seconds per attempt and eight
seconds overall, including backoff. Retry uses exponential jitter and observes
HTTP `Retry-After` and KuCoin `gw-ratelimit-reset`. A delay beyond the budget
fails closed. Pool and depth reads each have a nine-second overall deadline.
Connection failures can reuse a prior snapshot only within its original
freshness deadline. Both source and receipt age are capped at ten seconds, with
two seconds of allowed source-ahead skew. An invalid response drops the cache.
The WS has five-second connection and subscription deadlines, provider-supplied
heartbeat limits, and three connection attempts per run. The caller can start a
new run after a surfaced failure.

Gas comes separately for each direction in weibars per gas. The observed price
and its 2× cap define the scenario range. The P01 single 244,829-gas historical
swap is an illustrative proxy for each direction. Execution gas remains unknown
and the adapter marks cost validity false. P01's 34,133-gas view estimate only
measures the quote read. Divide wei-denominated HBAR exposure by 10¹⁸, then
multiply by an independently fresh HBAR/USDT price to model the gas exposure.
Any USDC conversion uses the approved 1:1 simulation parity assumption.
The adapter does not combine prices across venues.

Run `python -m pytest` for offline verification. The separately opted-in
`ARB_PUBLIC_SMOKE=1 python -m pytest -s tests/test_market_smoke.py` bounds each
public probe to 20 seconds. Service failures in the opt-in run are reported
individually. The historical 2026-11-26 prices are offline fixtures.

## Opportunity analysis

Initialize `Storage` before creating an `OpportunityCoordinator`. Schema version
3 extends versions 1 and 2 in one transaction and retains existing balances,
reservations, workers and records. Older application versions reject the upgraded
database. Seed virtual balances using the existing
P02 API. Create a coordinator with `OpportunityCoordinator.from_public(storage,
config.simulation, public)` and share it across workers. Obtain a worker with
`coordinator.worker("hedera-main")`, call `worker.start()`, await
`worker.analyze()` for one cycle, and call `worker.stop()` on shutdown.
Worker state persists. The caller owns the HTTP session, and worker state
transitions reuse the existing connections.

Each cycle reads one SaucerSwap pool and one KuCoin REST depth snapshot
concurrently with a ten-second read deadline. Both paths must be complete and
within the configured age limit of at most ten seconds. Both source and
receipt skew must be at most two seconds. The DB records both directions and
their observations when available. The coordinator recomputes totals from
reserves and visible depth to verify provenance and the configured CEX fee,
then uses the already fee-adjusted quote totals for profit. BUY DEX maps
WHBAR acquired on Hedera to HBAR sold on KuCoin. SELL DEX reverses the legs.
USDC/USDT use 1:1 simulation parity.

Without route-specific gas evidence, records with usable quotes are
indicative market candidates or balance misses. Verified gas input would
require evidence bound to the pool snapshot, swap direction and gas units.
The risk calculation uses the capped gas-price scenario and rounds the HBAR
budget up to tinybars. Gas is valued at the fee-free 1,000 HBAR ask average
from the same fresh book, rounded up to a simulated USDT micro-unit. The
coordinator requires quoted net profit strictly above the floor and stresses
both legs adversely at the configured slippage limit for the maximum-loss
test. It checks available virtual balances including the stressed buy amount
and gas budget. Configuration can tighten the approved 0.10 USDT profit
floor, 0.30 USDT loss limit, 0.5% slippage and 2× gas-price ceiling.

Call `Storage.transaction()` only for short synchronous operations. The cycle
claims a network slot and commits before any market await. The slot limits
starts to one per 15 seconds per network on the shared SQLite database,
including across worker handoffs. This is a local request budget. The
underlying adapters still apply provider rate limits and bounded retries.
The slot expires after 20 seconds to recover from interrupted processes.
A separate transaction rechecks the worker, lease and snapshot clocks, then
commits both evaluations and individual source IDs together. Duplicates
produce a reason-coded record without consuming the other source ID.
Worker states, analysis records and deduplication are scoped to the shared
database file. An eligible analysis is a point-in-time result. Balance
reservations and execution use the P05 engine below.

## Persistent simulated execution

Create `SimulationEngine.from_public(storage, config.simulation, public)` for
normal operation. Initialize the database and seed virtual holdings as above.
Call `await engine.recover()` before a worker begins analysis after restart.
For an already-running worker, `await worker.analyze()` runs analysis and can
admit a simulation within the same SQLite transaction. Call
`await engine.recover()` to advance the admitted simulation. Worker lifecycle
and scheduling remain the caller's responsibility. Recovery leaves persisted
worker state alone.

The approved public adapters do not independently verify route execution gas.
The engine records reason-coded analysis with zero execution admissions and
zero reservations in that condition. A route-evidence object alone conveys no
execution authority. The positive execution tests subclass the engine in
`tests/test_simulation.py`, authenticate fixture-specific provenance there,
and use offline adapters and disposable databases. Normal configuration has
no option to authorize fixture gas or recover a fixture execution.

Admission rechecks the P04 evaluation against its persisted cycle and source
IDs, lease, worker, freshness, balances and risk limits. It reserves the
venue inputs, including the stressed buy amount and up to three capped DEX
gas budgets. The request stores the evaluated quotes, approved CEX depth, gas
basis and execution scenario. SQLite enforces one unresolved trade per network.

Recovery commits each attempt's start before waiting asynchronously for its
persisted deadline. At that deadline it records the result, ledger entries and
reservation changes atomically. DEX success is committed before the CEX leg
starts. CEX simulation walks the stored initial book and uses the stored fee.
The result records modeled fills separately from the request. The DEX quote
includes the AMM fee. Confirmed DEX fills and reverts debit modeled HBAR gas
at the observed gas price. A scripted timeout represents proven nonexecution.
An ambiguous timeout must be represented by an unknown outcome.

Each leg has up to three total attempts. Only a reconciled no-fill result
permits another attempt. A partial CEX fill ends the trade as incomplete.
Known exhaustion or partial fills release unspent funds. Unknown results end
automatic execution in `manual_review`. Recovery retains the possibly spent
input and gas, releases only proven-unused holds and blocks new executions for
that network. On a journal or settlement discrepancy it retains the existing
holds. No automatic resolution or reverse compensation is applied. There is
no cumulative realized-loss stop.

For audit, `simulated_trades` stores the immutable request and current status.
The same ID keys the persisted analysis and the append-only
`simulation_events` journal. Each event records its timestamp and outstanding
holds, together with any fill, fee, gas and corresponding ledger identifiers.
`simulation_misses` records reasons when the analysis passed but admission did
not. Other rejection reasons remain in `analysis_cycles`. Query operational
SQLite files only in private runtime storage.
