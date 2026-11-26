# ARB-047–055 — Independent P07 corrective retest

**Recommendation.** The pinned P07-01 and P07-02 corrections pass independent retest. P07 remains **BLOCKED** pending Agent 0 review of this report and explicit Agent 0 or owner resolution of P07-03, the Web3.py requirement discrepancy.

The [Issue #16 dispatch](https://github.com/elvito2808/Arbitrage/issues/16#issuecomment-7182461015) authorized a simulation-only retest. The isolated checkout started at PR #15's QA publication head. The two commits from PR #17 and the single commit from PR #18 applied cleanly. The QA tree object was identical before and after application, and all 46 existing QA tests ran unchanged. The production diffs add metadata validation and ledger immutability. Source review found no live side-effect path in either change.

| Reference | SHA |
| --- | --- |
| Framework A, read-only | `cb23c4b08d2103aead243b59d6b66f325361a4bc` |
| B baseline and observed remote main | `0b06c49eee68ff9cc6e25cec99bce5ef0baf6bea` |
| QA source PR #15 | `fcc0e53ab777edcdf290bedfc7916413a11b48ef` |
| Market fix PR #17 | `f7fab71523a02fc84387649fab25528372ee5c84` |
| Ledger fix PR #18 | `f99f885e7c3a0c37ba38ec35cd59824a383d4d62` |
| Disposable combined test commit | `3000361165cd2ad401f6b4148d4426bcb5ee02de` |
| Combined Git tree | `3c2a753303eac7ba382890681cc533d60269eeda` |
| Unchanged QA subtree | `2e9791bc16fe41fedb1b78d6859c70bbf0611f17` |

## Retest results

| Task | Result | Evidence |
| --- | --- | --- |
| ARB-047 | PASS | Reviewed both production diffs, checked QA provenance, and mapped P07-01 to R01/ARB-024 and P07-02 to R03/ARB-038. R06 remains open. |
| ARB-048 | PASS | The unchanged exact arithmetic, pricing, fees, and risk boundary tests passed. |
| ARB-049 | PASS | Wrong, missing, malformed and contradictory symbol/base/quote metadata rejected. Non-object payloads rejected, cached state discarded, and valid HBAR-USDT pricing recovered. |
| ARB-050 | PARTIAL | Existing deterministic fault cases and the metadata no-stale-fallback test passed. SQLite VFS disk-full and exhaustive fault combinations remain untested. |
| ARB-051 | PARTIAL | The unchanged concurrency and reservation tests passed. Combined lifecycle/deadline/config/export interleavings remain incompletely covered. |
| ARB-052 | PASS | Selected SIGKILL recovery cases and the SQLite migration rollback test passed. Physical power loss remains outside the test scope. |
| ARB-053 | PASS | Simulation isolation tests passed and corrective diffs add no transport or trading capabilities. Public smoke probes were disabled. |
| ARB-054 | PASS | The normal runtime's production-shaped offline replay persisted both directions as `market_candidate / gas_unknown`. Zero eligible admissions, holds, executions, ledger postings, and balance changes. CLI and workbook assertions passed. |
| ARB-055 | PASS | This report and sanitized commands/results document the retest. |

## Defect disposition

- **P07-01 PASS on pinned fixes.** `test_wrong_kucoin_symbol_is_rejected` passed. The corrective matrices verified identity fields and malformed top-level metadata, no depth read on invalid metadata, invalidation of the prior book and increments, no stale fallback on an ensuing outage, and valid pricing after recovery.
- **P07-02 PASS on pinned fixes.** `test_database_rejects_rewriting_posted_ledger` and `test_posted_ledger_is_append_only_for_direct_writers` passed. Direct SQL UPDATE, DELETE, REPLACE and upsert attempts failed on fresh and upgraded databases, including rowid replacement and both recursive-trigger settings. History and foreign keys survived migration. The tested opening balance of 100 plus posted deltas of −5 and +3 reconciled to 98. Migration collision rolled back trigger DDL and retained schema v3.
- **P07-03 BLOCKED.** R06 names Web3.py, while production uses aiohttp and direct read-only JSON-RPC. Agent 0 or the owner must explicitly resolve the requirement.

Python 3.12.14 clean installation and `pip check` passed. The targeted run recorded **37 passed, 47 deselected**, the unchanged QA suite **46 passed**, and the full suite **204 passed, 3 skipped, 0 failures**. All test commands exited 0. The three skips were opt-in public smoke probes. No stop condition was observed. [Sanitized verification record](../evidence/ARB-047-055-retest/verification.txt)
