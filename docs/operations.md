# Public market simulation operations

Install the package in Python 3.12. The `arbitrage` command runs on Linux with
a private, local filesystem supporting SQLite WAL and advisory file locks.
Use an absolute data directory outside Git repositories. Its parent must exist.
Run the commands as the unprivileged user who owns the runtime directory.

```sh
arbitrage --data-dir /path/to/private-data init --config config/simulation.toml
arbitrage --data-dir /path/to/private-data status
arbitrage --data-dir /path/to/private-data start --worker worker-1
```

Start owns the foreground. From a separate terminal, use the same
`--data-dir` with `pause`, `stop` or `emergency-stop`. Each records intent
in SQLite before the foreground process cancels its tasks and closes its
transport. Wait for the foreground process to exit before invoking `resume`
or starting a new session. Ctrl+C and SIGTERM record a clean stop. There are
three named workers and one shared foreground owner. An unexpected process
exit leaves running intent, and `recover` may explicitly reattach to that
session. Paused, stopped, expired and emergency sessions never auto-resume
market analysis. An explicit `recover` can settle accepted virtual work in an
expired session when the emergency latch is clear.
An emergency stop latches for this runtime directory and preserves journal
records and holds. Review unsettled outcomes with the owner before any
separately approved recovery action. The production runtime never settles
historical fixture trades.

Edit the private `simulation.toml` for the next clean start or pause and
resume. Running settings are frozen. Unexpected-exit recovery uses the last
applied configuration. Editing opening balances cannot replenish a ledger.
The immutable session deadline applies across pauses and restarts. The process
uses both UTC and monotonic time, and expires conservatively if the wall
clock falls behind the last persisted clock observation. At the deadline it
cancels in-flight analysis, blocks new admissions and drains accepted virtual
work. Pricing prefers valid public KuCoin WebSocket depth and automatically
uses REST during outages. Status and the workbook expose the selected source,
quote freshness, and execution status.

Gas readiness uses the configured BUY and SELL gas units and the current
Hedera RPC gas price. The configured gas limit validates both unit counts.
The legacy `simulation.hedera_estimate_sender` field is optional and unused.
A valid price and fresh market data are required for admission. The simulator
requires no wallet state and records execution in the virtual SQLite portfolio.

Use `status` for durable intent, observations, counters, freshness at the
last cycle, and a capped operational event history. Operational events use a
bounded producer queue and retain up to 256 structured events in SQLite.
Historical observations and the financial journal are retained for completeness
and grow with use. The runtime schedules at most one cycle per 15 seconds.
Use `report` to atomically replace the private workbook using a consistent
database snapshot. The workbook shows both directions per recorded cycle,
reason codes, historical fixture outcomes, exact integer or decimal data,
and an explicit USDC/USDT parity assumption. Missing data is shown as unknown.

## KuCoin market-data sources

The worker uses KuCoin's timestamped public spot level-5 snapshots. Each
snapshot includes the best bid and ask, their available quantities, and up
to five levels per side. Quotes walk the visible depth and reject sizes the
snapshot cannot fill, as with REST. The worker validates the subscription,
market metadata, increments, book ordering, timestamps and heartbeat.

The existing public REST depth endpoint takes over if WebSocket is unavailable
or its data is invalid or expires. REST honors provider rate-limit headers
and may reuse a still-fresh snapshot during a transient outage. WebSocket
reconnect attempts use exponential backoff from 1 to 15 seconds plus bounded
jitter. After failure, two consecutive valid snapshots with advancing
timestamps are required before WebSocket can take over again. An idle market
can remain on REST if WebSocket has no changed depth to publish.

The `kucoin_source` health field identifies the source selected by pricing.
The `ws` field records socket readiness separately. Source transitions
appear in the bounded operational event history and the private workbook.
Both transports use the same freshness bounds and timestamp-based duplicate
guard. When both sources fail validation the cycle cannot use CEX depth.

[KuCoin public spot level-5 protocol](https://www.kucoin.com/docs-new/3470069w0)

## Linux systemd template

**NOT DEPLOYED. Separate owner approval required.** The example in
`deploy/templates/arbitrage.service.example` is inert. A future operator
would verify the dedicated unprivileged account, installed executable path,
private state directory, and logging retention with the owner. The template
reattaches only to persisted running intent and has bounded failure restarts.
It exits normally when there is no running session. Its working directory and
writable state are outside the repository. It routes console messages to
journald, whose size and retention are managed by the future host operator.
Structured health history remains bounded in SQLite. No install or service
activation was performed for P06.

### SQLite schema 4 upgrade

With the foreground worker stopped, take a private, consistent SQLite backup
before updating the simulator. Use SQLite's backup API or a cold backup taken
with all connections closed. Keep the copied simulation configuration with the
backup. Run `arbitrage --data-dir /absolute/private/path init --config /path/to/approved.toml`
with the new version before using the other commands. Initialization takes the
runtime owner lock and adds the ledger guards in a single SQLite transaction.
Existing balances, ledger rows, reservations and simulation journals are
retained. The upgrade does not repair pre-existing inconsistencies.

If migration fails, SQLite rolls back the schema transaction. Keep the original
database and backup for diagnosis. For an application rollback, restore the
matching pre-upgrade backup while the worker is stopped. Do not edit the schema
version or remove the ledger guards to force a downgrade. After a successful
upgrade, use `status` and `report` to check balances and reconciliation before
resuming an authorized simulation.
