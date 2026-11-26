# Public analysis operations

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
session. Paused, stopped, expired and emergency sessions never auto-resume.
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
cancels in-flight analysis and blocks new request attempts. Public analysis uses
REST. WS status explicitly reports that the stream is disabled. Route gas
remains unverified, so the production engine admits zero simulated trades.

Use `status` for durable intent, observations, counters, freshness at the
last cycle, and a capped operational event history. Operational events use a
bounded producer queue and retain up to 256 structured events in SQLite.
Historical observations and the financial journal are retained for completeness
and grow with use. The runtime schedules at most one cycle per 15 seconds.
Use `report` to atomically replace the private workbook using a consistent
database snapshot. The workbook shows both directions per recorded cycle,
reason codes, historical fixture outcomes, exact integer or decimal data,
and an explicit USDC/USDT parity assumption. Missing data is shown as unknown.

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
