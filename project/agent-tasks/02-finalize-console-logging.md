# Agent 0 — Execution Task 02/10
## Finalize Human-Readable Console Logging

Repository:
https://github.com/elvito2808/Arbitrage

Working branch:
`fix/srg-clm-rc2`

Expected starting commit:
`785768b7a2d3754722431afd5308d5b59b090438`

The project owner has authorized implementation, commits and publication to this working branch.

## 1. Starting point

Use the published working branch.

Read:

- `project/requirements/Console_Logging_and_Monitoring_Requirements_v1.0.md`
- `project/agent-tasks/01-implementation-report.md`
- `src/arbitrage/console.py`
- `src/arbitrage/monitor.py`
- `src/arbitrage/operational_log.py`
- Relevant runtime and simulation integration.

The approved configured Gas Units and live RPC Gas Price method supersede the historical wallet-dependent gas requirements.

Reuse the existing logging infrastructure. Do not rebuild the application or redesign its financial logic.

## 2. Required console format

All console messages must be in English.

Use:

`[HH:MM:SS.mmm] INFO  [MODULE] Message`

Use UTC and announce the date and timezone at startup.

Use fixed-width level alignment.

Default level: INFO.

Support DEBUG, WARNING, ERROR and CRITICAL.

Use meaningful module names:

SYSTEM, CONFIG, LOGGER, SECURITY, STORAGE, HEDERA, DEX, KUCOIN, RECOVERY, BALANCE, GAS, HEALTH, ARB, WORKER-01 and SIMULATION.

Replace generic key=value dumps at INFO level with readable, event-specific messages.

Preserve detailed structured events and correlation identifiers. Full technical details may appear at DEBUG level.

## 3. Startup logging

Display the following information in a logical order, based on actual completed operations:

- Arbitrage Bot Starting.
- Applied simulation configuration.
- Logger initialization.
- Simulation-only security restrictions.
- SQLite initialization and validation.
- Hedera RPC and verified chain ID.
- SaucerSwap pool, router and token identity.
- KuCoin REST and WebSocket status.
- Recovery status.
- Current persisted virtual balances.
- Configured gas estimation method.
- BUY and SELL gas units: 109478 by default.
- Gas limit: 300000 by default.
- Current RPC Gas Price.
- Estimated gas cost in HBAR.
- Maximum gas budget in HBAR.
- Worker configuration.
- Trade quantity.
- Profit threshold and maximum loss.
- Slippage, fee and gas multiplier.
- Cycle interval and session deadline.
- Final readiness result.
- Worker RUNNING only after successful readiness.

Report PASS, FAIL, UNKNOWN and DEGRADED truthfully.

Never print successful checks before they complete.

Show retry reason, attempt and effective delay.

If startup is blocked, show the actual reason clearly.

Do not repeat completed static startup checks during ordinary market cycles.

## 4. Normal cycle logging

Each cycle must display:

- Worker and sequential cycle number, for example CHECK #001.
- DEX BUY quote for the full configured quantity.
- DEX SELL quote for the full configured quantity.
- KuCoin BUY quote for the full configured quantity.
- KuCoin SELL quote for the full configured quantity.
- Active KuCoin market data source.
- Estimated gas cost and capped gas budget in HBAR and USDT.
- Indicative profit for both arbitrage directions.
- Net profit for both directions.
- Stressed profit for both directions.
- Actual eligibility and rejection reasons.
- Actual admission result.
- Cycle completion and duration.

Use clear direction names:

DEX → CEX

CEX → DEX

Show quote currency and full traded quantity.

Use actual persisted evaluation values. Do not recalculate profit in the renderer.

Show NO_OPPORTUNITY only when supported by valid evaluations.

Missing or invalid data must appear as UNKNOWN or INSUFFICIENT_DATA, not zero.

Preserve valid market quotes when gas data is unavailable.

## 5. Simulated execution logging

When an opportunity is admitted, display:

- Opportunity direction.
- Estimated and stressed profit.
- Admission result.
- Reserved virtual balances.
- Execution identifier.
- SIMULATED DEX execution.
- DEX attempt and result.
- Simulated gas charged.
- SIMULATED KuCoin execution.
- KuCoin attempt and result.
- Filled quantity and fee.
- Final execution outcome.
- Stablecoin balance change.
- Updated virtual balances.

DEX must remain first.

Display partial fills, failures, retries, unknown outcomes and retained holds truthfully.

Unknown fees or gas charges must not appear as zero.

Do not claim that any real transaction or exchange order was submitted.

## 6. Balance logging

Read balances from committed storage.

Display separately:

- DEX WHBAR.
- DEX USDC.
- DEX HBAR gas balance.
- KuCoin HBAR.
- KuCoin USDT.

After a committed financial change, display previous balance, delta, new balance, reserved amount and available amount.

Do not print successful balance updates before commit.

Do not claim actual realized trading profit.

## 7. Shutdown and summary

Display pause, stop, emergency stop, recovery and session expiry.

At session completion, display:

- Session duration.
- Completed analysis cycles.
- Admitted opportunities.
- Filled simulated trades.
- Failed and incomplete trades.
- Manual-review executions.
- Remaining virtual balances.
- Cycle errors.
- Console event drops.
- Report availability or report generation instructions.

Do not claim an Excel report was generated unless it actually exists.

## 8. Preserve existing functionality

Preserve Task 01 gas implementation:

CONFIGURED_GAS_UNITS + LIVE_RPC_GAS_PRICE.

Do not reintroduce wallet prerequisites.

Preserve financial formulas, virtual balances, readiness enforcement, recovery and simulation-only restrictions.

Preserve JSON status output and Excel reporting.

Keep console output on stderr and compatible with systemd/journald.

Keep the console sink nonblocking and independent of financial transactions.

Do not expose secrets or raw sensitive provider payloads.

Do not implement deployment in this task.

## 9. Minimal offline verification

Do not contact Hedera, SaucerSwap or KuCoin.

Do not create a new QA campaign.

Perform only focused structural and rendering checks:

- Python imports and compilation.
- Correct console format and level alignment.
- Startup ordering.
- Normal cycle formatting.
- Simulated execution formatting.
- UNKNOWN handling.
- Preservation of status JSON output.

Generate two short console examples using synthetic data and the actual renderer:

1. Startup followed by one normal analysis cycle.
2. One simulated execution followed by committed balance updates.

Clearly label both examples as offline synthetic output.

Save them under:

`project/agent-tasks/02-console-startup-example.txt`

`project/agent-tasks/02-console-execution-example.txt`

These are formatting examples, not live provider verification.

## 10. Commit and publish

Save this assignment as:

`project/agent-tasks/02-finalize-console-logging.md`

Save a concise completion report as:

`project/agent-tasks/02-implementation-report.md`

Commit and push all completed changes to:

`fix/srg-clm-rc2`

Verify the published remote commit.

No force-push. No changes to main. No separate handoff mission. No dependence on local workspace paths.

## Completion report

Return only:

- Status.
- Published commit SHA.
- Changed files.
- GitHub links to both generated console examples.
- Minimal structural verification results.
- Any actual remaining implementation blocker.

SIMULATION ONLY.

No live provider tests, real transactions, wallet operations, deployment or 24-hour experiment.

Complete the logging implementation in this assignment.