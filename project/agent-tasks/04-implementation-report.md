# Task 04 implementation report

Base commit `c0b0481aa74bc2bdc383bc49858c13e7bc99e027` on `fix/srg-clm-rc2`.

## Live cause

Unresolved. The two owner runs showed invalid Mirror transport and the generic `market_unverified` diagnostic. That evidence does not identify the HTTP response or block verification stage that failed. The agent made no live provider requests.

## Request trace and corrections

Startup reserves the existing network slot and asks the coordinator for sequential DEX and KuCoin reads under the existing deadline. SaucerSwap verifies the Hedera RPC chain and header, then reads the exact Mirror block alongside the pinned pool reads. It validates the Mirror number, full 48-byte hexadecimal hash representation, 32-byte RPC hash prefix, decimal consensus end timestamp, timestamp correspondence, and original freshness bounds. It then verifies pool and router quotes and uses the existing gas calculation. The coordinator still requires both valid, fresh snapshots for readiness or admission.

The old transport classified every nonretryable HTTP response generically, including exact-block 404. On 404 the new adapter reads the indexed head, and classifies indexing delay only when that response shows a lower height. It makes up to two brief retries for the same RPC block while the head remains within the freshness budget. When the Mirror has already reached the requested height, an absent exact block is reported separately. Transport cooldowns and the existing read deadlines continue to govern every request.

The old hash comparison inspected only a string prefix, accepted an invalid suffix, and depended on identical hex casing and prefixes. The timestamp parser also allowed invalid shapes to collapse into a generic error or escape as an attribute failure. Malformed fields, mismatch types, stale snapshots, and transport failures now carry closed, sanitized diagnostics. Preflight records an independently verified RPC header, Mirror block, and KuCoin source or REST outcome even when another stage fails. Console warnings and errors name the provider and operation and include the reason. DEBUG and JSON status include available HTTP status, expected and observed heights, ages or timestamp offsets, invalid field names, and hash classifications. Historical transport health remains available separately.

JSON status now exposes stored `session_intent` and observed `runtime_state`. A blocked foreground that has exited shows a stopped runtime, even if the persisted session intent still says running. The upgrade guide uses explicit stop and the existing versioned installers. It preserves private TOML, SQLite and sidecars, balances, and experiment history.

## Verification

Python 3.12 imports and compilation passed. The 167 focused offline tests passed, including synthetic 404 with lag, exact-block polling, missing blocks with a current Mirror index, throttling, malformed envelopes and fields, block mismatches, stale ages, persisted diagnostics, and independent readiness outcomes. The existing static service test was aligned with the previously published Task 03 recovery-only unit. No live startup or deployment was performed. The precise cause of the owner's earlier failure remains unverified pending the new diagnostic output.
