# P07 requirements traceability

The classifications apply to the pinned baseline and the offline scope of P07. Earlier reports and Agent 0 dispositions establish project history. The rerun and QA tests supply independent execution evidence. See the [test record](verification.txt) and [defect register](../../reports/ARB-047-055.md#defect-register).

| Requirement | Result | Observable evidence and limitation |
| --- | --- | --- |
| R01 Market/pair identity | FAIL | [Hedera chain, pool, token order, decimals and router checks](../../../src/arbitrage/saucerswap.py) and historical adapter tests passed. KuCoin symbol metadata can identify the wrong market while the book is attributed to HBAR-USDT. P07-01. Live free access was outside this rerun. |
| R02 Fixed-size bidirectional pricing and risk | PASS | [Financial vectors](../../../tests/qa/test_p07_financial.py), `test_both_directions_exact_balance_and_no_cex_reread` and `test_invalid_gas_fails_closed`. Both directions request exactly 1,000 HBAR. Thresholds, per-leg stress, cap, fee-once and separate requested/fill records were exercised. The parity assumption is explicit in `KuCoin.parity_assumption` and the workbook Guide. |
| R03 Virtual balances, reservations, recovery and immutable history | FAIL | [Reservation and workbook races](../../../tests/qa/test_p07_storage.py), [SIGKILL checks](../../../tests/qa/test_p07_process.py), and existing partial/failure tests passed. SQLite permits rewriting posted ledger rows. P07-02. |
| R04 Public reads, lifecycle and data freshness | PARTIAL | `PublicConnections`, `require_fresh`, `Runtime`, and worker/adapter tests support bounded retries, 10-second TTL, dedup, three definitions and a single active worker, restart, emergency latch and no cumulative-loss stop. Wrong-symbol validation remains open. Live WS and free sustained continuity remain unverified. |
| R05 Deadline, monitoring, CLI and Excel | PASS | Existing operations tests and the new foreground and export tests exercised deadline boundaries, session persistence, CLI, monitored reasons and workbook reconciliation. Systemd/journald coverage is limited to source and template inspection. Physical power loss, deployment and economic acceptance were outside this gate. |
| R06 Simulation boundary and stack | PARTIAL | [Production replay](../../../tests/qa/test_p07_integration.py) and production fixture-isolation tests passed. The approved requirement names Web3.py, while the installed package uses direct aiohttp JSON-RPC and does not declare Web3.py. Deployment remains separately gated. |

| Earlier task | Result | Baseline source, evidence or independently rerun test |
| --- | --- | --- |
| ARB-001 | PASS | `project/context.md` and `project/decisions.md`, verified scope and repository pins. |
| ARB-002 | PASS | `project/requirements.md`, committed R01–R06 baseline for QA. |
| ARB-003 | PASS | `project/roadmap.md`, approved ARB-RM-001 1.0.0 and phase gates. |
| ARB-004 | PASS | `project/roles/`, `project/tasks/` and `CP-008`, dispatch provenance confirmed. |
| ARB-005 | PARTIAL | P01 report and Agent 0 review describe temporary bundle restoration. Durable owner-controlled recovery remains deferred. Restoration was not independently repeated in P07. |
| ARB-006 | PASS | `SaucerSwap._read` and `test_dex_rejects_invalid_or_stale_data`, Hedera chain and block checks. |
| ARB-007 | PASS | `SaucerSwap._read` tests for token mapping and reserve/block provenance. |
| ARB-008 | PASS | `test_historical_dex_integer_router_and_gas`, ABI selectors and historical router quote agreement. |
| ARB-009 | PARTIAL | P01 public probes, replayed REST depth and deterministic WS reconnection tests. Live WS and wrong-symbol rejection remain open. |
| ARB-010 | PARTIAL | Historical gas proxy and sampled gas-price range, production replay demonstrates the unknown-gas gate. Trusted bidirectional route gas and sustained quotas remain unverified. |
| ARB-011 | PASS | P01 report, Agent 0 review and sanitized probes are present and bounded to their sampled evidence. |
| ARB-012 | PASS | Clean installation, wheel source comparison and `pip check`. Web3.py discrepancy tracked under R06. |
| ARB-013 | PASS | Approved TOML, exact numeric parsing, invalid config and fixture-option rejection. |
| ARB-014 | PASS | `test_models.py`, exact typed quotes, balances, deadline and fill constraints. |
| ARB-015 | PASS | `test_storage.py`, migration, writer lock, reservations and restart. SQL-level immutability tracked separately under ARB-038. |
| ARB-016 | PASS | `test_operational_log.py`, bounded queue and redaction with a separate ledger. |
| ARB-017 | PASS | `interfaces.py` source, production adapter/coordinator/CLI composition. |
| ARB-018 | PASS | `PublicConnections`, replay gate, and deterministic public WS bootstrap and reconnect handlers. No live transport session. |
| ARB-019 | PASS | Timeout/reset/429 tests, ten-second boundary, expired cache and reconnect invalidation. |
| ARB-020 | PASS | `SaucerSwap._read`, pinned factory/pair and token decimals/order compared with independent historical responses. |
| ARB-021 | PASS | Fixed-output buy integer invariant and router audit. |
| ARB-022 | PASS | Fixed-input sell integer invariant and router audit. |
| ARB-023 | PARTIAL | Historical proxy cannot qualify a route. Price range and cap exercised. Trusted execution gas unavailable. |
| ARB-024 | FAIL | `KuCoin.snapshot` accepts an explicitly wrong symbol/base identity. P07-01. |
| ARB-025 | PASS | Independent multi-level depth, exact fee-once totals, directional rounding and quote/fill distinction. |
| ARB-026 | PARTIAL | `OpportunityCoordinator._timing`, coherence and reason-coded tests passed for valid identities. P07-01 limits CEX provenance. |
| ARB-027 | PASS | Single running worker, three definitions and shared-cycle tests. |
| ARB-028 | PASS | Both directional profit cases, strict threshold and parity assumption tested. |
| ARB-029 | PARTIAL | Stale/skew/duplicate/depth/gas rejection passed. CEX metadata identity gap remains. |
| ARB-030 | PASS | Micro-USDT stress boundary, per-leg rounding, funds checks and 2× gas cap. |
| ARB-031 | PASS | Persistent per-network snapshot and lease dedup tests, no transaction wrapping market await in reviewed coordinator code. |
| ARB-032 | PASS | Five opening holdings and exact terminal deltas reproduced. |
| ARB-033 | PASS | Concurrent reservation and recovery tests, before/after admission SIGKILL, no double spend. |
| ARB-034 | PASS | DEX commits first on fixture trades, and DEX failure never initiates CEX. |
| ARB-035 | PASS | Delayed confirmation, cancellation and confirmation-wait SIGKILL with durable start event. |
| ARB-036 | PASS | CEX market pricing through retained order-book depth, with no reread after admission. |
| ARB-037 | PASS | DEX revert/timeout, three-attempt bounds, partial CEX fills and terminal CEX failures, no compensating execution. |
| ARB-038 | FAIL | Simulated requests and events are guarded. SQL can alter or delete posted balance deltas. P07-02. |
| ARB-039 | PASS | Selected process-kill points, repeated recovery, exact balances, and existing unknown-outcome/hold tests. Power loss outside scope. |
| ARB-040 | PASS | `Runtime.activate/control/run` and CLI replay of persistent intent and latch. |
| ARB-041 | PASS | Existing cross-process foreground lock and concurrent worker/cycle tests; independent shared DB reservation race. |
| ARB-042 | PASS | Installed CLI commands, path/privacy checks, report and redacted failures. |
| ARB-043 | PASS | Inert template source and template assertions. Actual service operation outside scope. |
| ARB-044 | PASS | Queue bounds, redaction, health and reason-coded monitoring tests. |
| ARB-045 | PASS | UTC/monotonic timing, restart and deadline cancellation with deterministic clocks. |
| ARB-046 | PASS | Workbook columns, privacy, exact text, reconciliation, failed replace, ENOSPC, concurrent export and SIGKILL replacement. P07-02 limits trust in rewritten source records. |

Prior evidence reviewed from [reports](../../reports/), P01–P05 Agent 0 review files under [evidence](../), and the P06 acceptance checkpoint [CP-007](../../checkpoints/CP-007.md).
