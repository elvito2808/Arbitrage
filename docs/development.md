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
the runner provides an outbound proxy. The foreground runtime owns
`KuCoin.depth_stream.run()` and cancels and awaits it before closing the
session. A caller using the adapter outside the runtime owns that task.

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
- The public depth-5 WebSocket is the primary pricing feed. It supplies
  timestamped bids, asks and executable quantities in the same book model as
  the depth-20 REST fallback. Both use validated HBAR-USDT metadata and exact
  increments. An unavailable, silent, stale or invalid stream falls back to
  REST. A recovering connection needs two consecutive fresh, advancing books.
  A book with insufficient visible quantity is rejected by pricing.
  Keep WS URLs and bootstrap data out of logs.

HTTP uses at most three attempts per read, 2.5 seconds per attempt and eight
seconds overall, including backoff. Retry uses exponential jitter and observes
HTTP `Retry-After` and KuCoin `gw-ratelimit-reset`. A delay beyond the budget
fails closed. Pool and depth reads each have a nine-second overall deadline.
Connection failures can reuse a prior snapshot only within its original
freshness deadline. Both source and receipt age are capped at ten seconds, with
two seconds of allowed source-ahead skew. An invalid response drops the cache.
WebSocket connection, subscription, liveness and depth freshness have bounded
timeouts. Reconnection uses bounded backoff while REST remains available.

Gas uses `CONFIGURED_GAS_UNITS + LIVE_RPC_GAS_PRICE`. The simulation
configuration specifies 109,478 gas units for BUY and SELL and a limit of
300,000 for validation. Hashio supplies the current price through
`eth_gasPrice` in weibars per gas unit. Multiply the configured units by
the price and round up to a whole tinybar. Apply the 2× budget multiplier
and round up again if needed. A single fresh price sample serves both
directions. Gas estimation requires no wallet, account state, or swap
simulation. Missing, invalid, or stale gas prices block admission.

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

Each cycle completes one SaucerSwap pool snapshot before requesting one
KuCoin depth snapshot, under a shared ten-second read deadline. Both paths must be complete and
within the configured age limit of at most ten seconds. Both source and
receipt skew must be at most ten seconds. The DB records both directions and
their observations when available. The coordinator recomputes totals from
reserves and visible depth to verify provenance and the configured CEX fee,
then uses the already fee-adjusted quote totals for profit. BUY DEX maps
WHBAR acquired on Hedera to HBAR sold on KuCoin. SELL DEX reverses the legs.
USDC/USDT use 1:1 simulation parity.

Without valid gas inputs, records with usable quotes remain indicative
market candidates or record the applicable data or balance failure. Gas
evidence is associated with the market snapshot and swap direction. The
risk calculation uses the capped gas budget. Both the estimate and budget
are valued at the fee-free 1,000 HBAR ask average from the same fresh book,
rounded up to a simulated USDT micro-unit. The coordinator requires quoted net profit strictly above the floor and stresses
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

The foreground runtime creates the public simulation engine, recovers prior
journal entries, and checks readiness before any worker can analyze a cycle.
Analysis and virtual admission can share one SQLite transaction. After
admission, recovery advances the persisted simulation. A standalone public
engine can recover prior journal entries, subject to journal scope and
provenance checks.

The engine requires gas evidence bound to the exact public adapter snapshot
for the requested route. When evidence is missing, expired or invalid, it
records the reason and creates no admission or reservation. A route-evidence
object alone conveys no execution authority. The positive execution tests subclass the engine in
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

## Hedera gas provenance

Evaluations record the configured units, gas limit, method, public price
provider, chain, gas price, and price observation time. The report shows
gross DEX quotes, DEX quotes adjusted by estimated gas, and the capped gas
budget used in risk evaluation. The simulated journal debits modeled HBAR
gas at the observed price according to the simulated execution outcome.
