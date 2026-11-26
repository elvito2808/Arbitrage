# P07 owner-approved sequential reads validation

## A. Executive summary

**Recommendation: PARTIAL.** The coordinator's DEX-first snapshot calls, 10-second cross-venue tolerance, existing individual timestamp controls, and shared request deadline passed deterministic tests. Four bounded public cycles exercised this sequence. One cycle obtained independently verified gas estimates and executable quotes in both directions. Its existing maximum-loss check rejected both directions. The other three cycles rejected gas evidence when the Hedera head moved.

The foreground runtime still starts its persistent KuCoin WebSocket subscription separately from the coordinator. Thus the snapshot order is verified, but strict wire-level serialization of every KuCoin request in foreground operation is outside the evidence from this change. The public harness starts its socket inside the KuCoin snapshot stage so the captured order includes that startup.

## B. Implementation

| Revision | Effect |
| --- | --- |
| `2fd9c998242c88902385a6e5135a4a47bf88e439` | Verified PR #27 base on the integration branch |
| `3c7b68329724bbcee8c326f1e50ded82a044731a` | Initial PR HEAD with the owner-approved sequence, skew and regression test |
| `6a043cd` | Separate cross-venue skew from individual source-ahead validation in `market_data.py`. Reject a malformed Hedera block hash in `saucerswap.py` before the next venue read. Cover boundaries, read failure and shared deadline. Use the existing pytest/asyncio setup. Update developer guidance. |
| `a5ad4b5ff6b88e4142cd33e348bfd7871c17b4a3` | Correct pre-change test assumptions and cover cancellation during each sequential stage. Preserve the cross-venue simulation no-reservation assertion using valid individual inputs that exceed the new limit. |

The product diff is in `coordinator.py`, `market_data.py` and `saucerswap.py`. Coverage changes are in `test_coordinator.py`, `test_market_data.py`, `test_operations.py` and `test_simulation.py`.

## C. Timestamp validation

| Difference | Source timestamps | Receipt timestamps |
| --- | --- | --- |
| 0, 2, 5, 9 seconds | Accepted | Accepted |
| Exactly 10 seconds | Accepted | Accepted |
| 10 seconds plus 1 microsecond | Rejected as cross-venue skew with individually valid sources | Rejected by the individual freshness gate |

Two independently fresh receipt timestamps cannot differ by more than their maximum age of 10 seconds. The previous two-second allowance for a source timestamp ahead of its own receipt, rejection of future receipts, and 10-second freshness limits remain effective. The initial PR reused the cross-venue constant in the individual source-ahead condition, which would have widened that allowance. The correction preserves it.

## D. Request ordering and shared deadline

The event test suspends DEX before completion and requires `dex:start → dex:done → kucoin:start → kucoin:done`. Timeout testing confirms that both awaits share a single timeout context. Expiration during KuCoin cancels its pending await and runs cleanup. The runtime tests cover emergency and deadline interruption during either venue and require every opened session to close. The coordinator skips the KuCoin snapshot when DEX raises.

## E. Failure handling

- **DEX:** Timeout, HTTP unavailability and invalid response stop the sequential read before KuCoin. Invalid hash and invalid mirror provenance fail in the adapter. Unstable provider head, invalid gas and missing gas cannot produce an admitted opportunity. When gross DEX pricing remains valid the coordinator can still read KuCoin and report the gas rejection.
- **KuCoin:** Existing tests cover startup failure, disconnect, silent WebSocket, stale or malformed depth, automatic REST fallback, HTTP 429, timeout and dual-source unavailability. The pricing and simulation tests reject stale, invalid or insufficient market data. Ordered snapshot acquisition and shared cancellation are tested separately.
- **Risk and reservations:** The tests cover insufficient balances and depth, duplicate snapshots, price and maximum-loss limits, invalid gas, and virtual execution isolation. The adverse-skew case retains the no-reservation assertion with an 11-second source difference.

## F. Financial validation

The deterministic suites verify executable depth, one fee per leg, independent DEX quotes, snapshot-bound BUY/SELL estimates, integer rounding, and net and stressed risk calculations.

Public cycle 3 used the stable Hedera block `102800137`, with gas price **1,580,000,000,000 weibars/gas**. Prices below are totals for 1,000 HBAR. The gas budget used the unchanged 2× cap for risk evaluation.

| Direction | Gross DEX | Fee-adjusted KuCoin | Estimated gas | Estimated gas in USDC | Capped gas in USDT | Net profit USDT | Stressed profit USDT |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| Buy DEX | 53.896909 USDC | 54.025920 USDT received | 122,640, or 0.19377120 HBAR | 0.010484 | 0.020967 | 0.108044 | −0.431571 |
| Sell DEX | 53.545467 USDC | 54.154100 USDT spent | 122,659, or 0.19380122 HBAR | 0.010485 | 0.020970 | −0.629603 | −1.168102 |

Both evaluations recorded `maximum_loss`. In particular, the positive nominal buy-side result exceeded the 0.10 USDT profit floor, but its stressed loss exceeded the unchanged 0.30 USDT limit. No historical gas was substituted.

## G. Test results

Python 3.12.14 clean installation with `python -m pip -q install '.[test]'` and `python -m pip check` succeeded. The numbered logs include exact commands, results and exit codes in execution order.

| Step | Result | Command and output |
| --- | --- | --- |
| 1 Sequential ordering | 1 passed, exit 0 | [Log](../evidence/P07-sequential-reads/1.txt) |
| 2 Time boundaries and freshness | 30 passed, 68 deselected, exit 0 | [Log](../evidence/P07-sequential-reads/2.txt) |
| 3 Transport and failures | 121 passed, exit 0 | [Log](../evidence/P07-sequential-reads/3.txt) |
| 4 Financial and simulation | 121 passed, exit 0 | [Log](../evidence/P07-sequential-reads/4.txt) |
| 5 Full project suite | 260 total, 257 passed, 0 failed, 3 skipped in 6.86 seconds, exit 0 | [Log](../evidence/P07-sequential-reads/5.txt) |

The three skipped cases are opt-in public smoke tests. Public providers were tested separately below. The initial ordering test failed because the pytest async plugin was absent. The first full run after correcting it failed three pre-change expectations that assumed concurrent venue reads or a two-second cross-venue threshold. The final tests preserve cancellation at either venue and rejection at a genuinely out-of-bounds timestamp. [Initial ordering](../evidence/P07-sequential-reads/initial.txt) · [Initial financial suite](../evidence/P07-sequential-reads/first-financial.txt) · [Initial full suite](../evidence/P07-sequential-reads/first-full.txt)

## H. Public verification

Executed on 2026-11-26 UTC with the installed product at the validated revision. Command from the repository root:

```sh
WSS_PROXY="$HTTPS_PROXY" ARB_PUBLIC_ESTIMATE_SENDER=0x000000000000000000000000000000000019bbff \
  python project/evidence/P07-sequential-reads/public_cycles.py
```

The probe used the environment's configured public egress, four isolated coordinator cycles and a total 46-second cap. Each cycle used the standard 10-second coordinator deadline. All four priced from executable KuCoin REST depth. The WebSocket was started after DEX in each isolated probe cycle, but none of the four samples selected it before REST supplied a book. Prior provider evidence demonstrated live WebSocket pricing and recovery on the same adapter.

| Cycle | Request start UTC | DEX done UTC | KuCoin start UTC | KuCoin done UTC | Source / receipt skew | Both directions |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 11:47:14.580029 | 11:47:16.593513 | 11:47:16.593519 | 11:47:17.348933 | 7.112110 / 1.679884 s | `gas_invalid`, head changed |
| 2 | 11:47:17.361894 | 11:47:19.981317 | 11:47:19.981322 | 11:47:20.704491 | 8.343485 / 2.250886 s | `gas_invalid`, head changed |
| 3 | 11:47:20.718565 | 11:47:22.993421 | 11:47:22.993425 | 11:47:23.630709 | 6.905357 / 2.078615 s | `maximum_loss`, both estimates verified |
| 4 | 11:47:23.641903 | 11:47:25.652962 | 11:47:25.652966 | 11:47:26.273850 | 7.827199 / 1.743935 s | `gas_invalid`, head changed |

The JSONL includes both source and receipt times, block numbers and hashes, gas prices, available route provenance, quotes, and final evaluation details. Head changes are provider observations rejected by the existing gate, rather than an implementation failure. No public cycle met eligibility. [Exact cycle evidence](../evidence/P07-sequential-reads/public.jsonl) · [Previous WebSocket verification](../P01-P07_ROUND_2_PROVIDER_EVIDENCE.md)

## I. Independent Agent 3 review

Agent 3 returned **PASS for coordinator sequencing and safety** after independently installing and testing product revision `a5ad4b5ff6b88e4142cd33e348bfd7871c17b4a3`. The full suite returned 257 passed and 3 opt-in smoke skips in 7.07 seconds. Two cold-start and two warmed-WebSocket public cycles reported stable gas and reached the unchanged risk gate. All eight independent public cycles recorded DEX snapshot completion before the KuCoin snapshot call. A second run with a warmed WebSocket selected executable WS depth after the DEX snapshot had completed. Its second cycle verified both gas estimates with source skew of 6.261908 seconds and receipt skew of 1.001135 seconds. Both directions rejected at the maximum-loss gate. Virtual balances were unchanged, no financial rows were posted, and SQLite integrity and foreign-key checks passed. [Independent Agent 3 report](../evidence/P07-sequential-reads/agent-3-final-p07.md) · [Independent cold-start cycles](../evidence/P07-sequential-reads/agent-3-public.jsonl) · [Independent warmed WS cycles](../evidence/P07-sequential-reads/agent-3-public-warm.jsonl)

## J. Remaining acceptance items

- Confirm with Agent 0 whether persistent WebSocket feed maintenance falls within the requested serialization rule. The production coordinator serializes snapshot calls, but the foreground owns a continuously running subscription that can bootstrap or reconnect independently.
- The isolated cold-start public cycles selected REST. Agent 3 separately reproduced sequential coordinator pricing from a warmed public WebSocket, and fallback remains covered by deterministic tests and Round 2 public evidence.
- Obtain Agent 0 final acceptance. PR #27 remains Draft and unmerged. No deployment, live order, phase closure, or P08/P09 work occurred.

## K. Final recommendation

**PARTIAL.** The sequential pricing path and 10-second boundary are verified. Submit the evidence to Agent 0 for disposition of the foreground WebSocket interpretation and final acceptance.
