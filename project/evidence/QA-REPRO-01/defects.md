# Defect register

All five reports reproduced against RC1. For each command below, run `bash project/evidence/QA-REPRO-01/reproduce.sh independent -k` followed by the named selector.

| Defect and severity | Expected | Actual and impact |
| --- | --- | --- |
| QA-R4-01 · CRITICAL | Direct public-engine calls without readiness create no cycle, admission or reservations. | A DEX outage persisted one analysis cycle. A profitable synthetic market persisted one cycle, one virtual admission and three reservations. The foreground control blocked and left all counts zero. The documented direct entry point bypasses the readiness fence for simulated work. |
| QA-R4-02 · HIGH | Operational worker status stays non-running while readiness blocks analysis. | The worker was `running` in all three retry observations and after `STARTUP_BLOCKED`, alongside the persisted `running` intent. Analysis count stayed zero. Status conflates readiness with intent. |
| QA-R4-03 · HIGH | Preserve four verified full-volume quotes, valid BUY gas and calculable indicative profit while invalid SELL gas blocks admission. | Both persisted evaluations have `gas_invalid` with missing quotes, gas and indicative profit. Both structured events and console lines show those values as unknown. The market observer confirmed four full-volume quotes and trusted BUY gas. Zero trades and reservations confirms the admission fence held. |
| QA-R4-04 · HIGH | An unresolved CEX fee is unknown in both reports, with the submitted input hold retained and no CEX ledger posting. | Workbook reported `unknown`, while the console reported `fee_usdt=0.000000`. The virtual journal remained in manual review and retained 1,000 CEX HBAR. All booked deltas were DEX-side. Console conflates an unknown fee with a known zero. |
| QA-R4-05 · MEDIUM | Chain ID mismatch records a timestamped Hedera FAIL and blocks startup. | Only the snapshots check recorded a timestamped failure. Hedera stayed `UNKNOWN` with null reason and timestamp. Startup blocked with zero work, but component health did not identify the failed check. |

| Defect | RC1 source locations | Evidence |
| --- | --- | --- |
| QA-R4-01 | `docs/development.md:136`, `runtime.py:771`, `coordinator.py:385`, `simulation.py:159` | [Outage](captures/r4-01-direct-outage.json), [profitable](captures/r4-01-direct-profitable.json), [foreground control](captures/r4-01-foreground-outage.json) and [console](captures/r4-01-foreground-outage.txt) |
| QA-R4-02 | `runtime.py:135,158,266` | [Status observations](captures/r4-02-worker-status.json) and [console](captures/r4-02-worker-status.txt) |
| QA-R4-03 | `runtime.py:408`, `coordinator.py:356–364`, `monitor.py:76–89` | [SQLite projection, verified input and structured events](captures/r4-03-route-data.json) and [console](captures/r4-03-route-data.txt) |
| QA-R4-04 | `simulation.py:298`, `monitor.py:144`, `reporting.py:133–144` | [Ledger, reservation and workbook comparison](captures/r4-04-unknown-fee.json) and [console](captures/r4-04-unknown-fee.txt) |
| QA-R4-05 | `runtime.py:525–551` | [Persisted component status](captures/r4-05-chain-mismatch.json) and [console](captures/r4-05-chain-mismatch.txt) |
