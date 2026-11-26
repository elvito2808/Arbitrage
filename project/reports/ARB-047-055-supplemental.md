# P07 supplemental QA — ARB-050 and ARB-051

**Recommendation.** Both supplemental coverage gates passed for the pinned implementation and scenarios below. P07 remains **BLOCKED** pending Agent 0's final review.

The [Issue #16 dispatch](https://github.com/elvito2808/Arbitrage/issues/16#issuecomment-7182526679) and subsequent Agent 0 continuation authorized simulation-only testing. Verification used the reviewed main baseline `0b06c49eee68ff9cc6e25cec99bce5ef0baf6bea`, unchanged QA from PR #15 `fcc0e53ab777edcdf290bedfc7916413a11b48ef`, PR #19 report and evidence `22474bcbe6f7aeb6f6b4ad711a0c496c43871486`, and corrective heads PR #17 `f7fab71523a02fc84387649fab25528372ee5c84` and PR #18 `f99f885e7c3a0c37ba38ec35cd59824a383d4d62`. Framework A remained read-only at `cb23c4b08d2103aead243b59d6b66f325361a4bc`.

| Task | Result | Observed coverage |
| --- | --- | --- |
| ARB-050 | PASS | SQLite emitted `SQLITE_FULL` under a temporary database page cap and `SQLITE_AUTH` when a test connection denied the balance write. The attempted reservation and new ledger entry did not persist. A separate reader saw only the prior committed history. After reopening, integrity and foreign-key checks passed. A successful retry recorded the intended hold and exactly one new ledger entry. The ending balance reconciled to 100 − 5 + 3 = 98, with 90 units available. |
| ARB-051 | PASS | Three event-controlled interleavings held an in-flight quote and a real SQLite workbook read snapshot across a configuration edit and stop/restart, session deadline, or quote expiry. The active engine retained its 10-second TTL and the next session selected 5 seconds. A competing owner was refused until release. The old workbook retained its pre-transition session and config, and the new workbook recorded the terminal state, new session and reason-coded rejection. Both reports reconciled. No snapshot was consumed, no execution or hold was recorded, and balances were unchanged. |

The initial attempt stopped after the first race case failed at a test-only comparison between `sqlite3.Row` and a tuple. Agent 0 authorized continuation. A tuple-returning cursor scoped to the new scalar count checks preserved the expected zero mutation counts and one running-worker count. All three race cases passed on the second attempt. Earlier tests and assertions were preserved.

The independent production-shaped replay still classified both sides as `market_candidate / gas_unknown` with zero eligible admissions, reservations, executions, ledger postings or balance changes. Agent 0's dispatch records P07-03 as resolved and allows the direct aiohttp read-only JSON-RPC implementation under the approved safeguards.

The SQLite test uses a real page-allocation failure and controlled SQL authorization denial. Physical disk exhaustion, device I/O errors and power loss were outside coverage. The concurrency matrix covers the specified interleavings with deterministic barriers and bounded waits. It does not establish exhaustive scheduling coverage or sustained production behavior. The three optional public network probes were skipped.

[Exact commands and sanitized results](../evidence/ARB-047-055-supplemental/verification.txt)
