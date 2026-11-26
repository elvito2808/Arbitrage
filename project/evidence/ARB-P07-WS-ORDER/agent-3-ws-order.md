# Agent 3 independent verification of ARB-P07-WS-ORDER

**PASS** on product commit `662d562ccffec80b98e9b7a6eee8d2190264edb4`, reviewed against assignment base `e85593ad023fed0df293ab5b351c73b535815f33`. This check covers persistent WebSocket depth and ordered market selection. The actual public outcomes below distinguish quote availability from eligibility for virtual execution.

## Implementation review

- Runtime owns one persistent public depth task. The coordinator waits for the DEX snapshot before invoking KuCoin's snapshot selection under the existing shared ten-second deadline. The WebSocket can collect frames independently while DEX I/O runs. No cycle code reconnects the socket.
- KuCoin retains one immutable version of valid depth. Pricing, recorded bid/ask metadata, and the canonical snapshot ID all use the selected object. The deterministic overlap test delivered a new frame while DEX was blocked, verified selection occurred after DEX, and verified a later book did not alter the first selected book. Two simulated cycles used one socket and zero REST depth calls.
- WebSocket symbol and topic, positive bid and ask depth, quote validity, freshness and transport generation checks remain active. Malformed image data can leave a still-fresh previously valid book available. The malformed data changes neither book, receipt telemetry nor the monotonic valid-depth silence deadline. If no fresh valid book exists, the coordinator uses valid REST fallback or rejects. Reconnection remains bounded and switchback requires valid advancing images. The focused regression at `662d562` verifies the deadline directly.
- Both source and receipt skew retain the accepted ten-second limit. Per-venue freshness and two-second source-ahead safeguards remain separate. Risk math, gas guards and virtual ledger rules are unchanged.
- DEX completion UTC and selected KuCoin version, transport, symbol, receipt/source timestamps and exact top-of-book prices and quantities are recorded. Runtime health exposes the last validated WS receipt, separate from the selected book's receipt stored on the cycle. A legacy row missing the new optional fields loaded through the SQLite-backed transaction interface. A new observation round-tripped its exact rational prices and quantities with SQLite integrity `ok` and zero foreign-key errors.

## Reproduction and results

A separate Git archive was installed with Python 3.12.14 using `python -m venv <qa-venv>`, `<qa-venv>/bin/python -m pip -q install '.[test]'`, and `<qa-venv>/bin/python -m pip check`. These commands exited 0. `<qa-venv>/bin/python -m pytest -q -rs -p no:cacheprovider` exited 0 with **258 passed, 3 skipped in 7.18 seconds**. The three skips are the opt-in public smoke module. `git diff --check e85593a 662d562` exited 0.

An earlier review of `c5de7f0` identified that an advancing but ignored malformed frame could extend the background silence timer even though it did not refresh the book. The final reviewed commit corrected it. An initial ad hoc compatibility command imported the previous wheel and failed because that wheel did not contain the new model. The corrected check used the final installed wheel and passed.

From the archive root, the committed bounded probe ran as `PYTHONPATH=src WSS_PROXY="$HTTPS_PROXY" ARB_PUBLIC_ESTIMATE_SENDER=<public-EVM-address> <venv>/bin/python project/evidence/ARB-P07-WS-ORDER/public_cycles.py`, exit 0. This used the current archive code and the environment's public proxy. The evidence contains neither a token nor proxy parameters.

| Cycle start UTC on 2026-11-26 | Selected book version | Source / receipt skew | Gas admission |
| --- | --- | --- | --- |
| 12:14:02.780112 | `kucoin:HBAR-USDT:ws:1795695247755` | 9.582652s / 2.549097s | Both directions rejected changed-head gas |
| 12:14:23.064706 | `kucoin:HBAR-USDT:ws:1795695264160` | 6.185224s / 0.614398s | Both directions rejected changed-head gas |
| 12:14:41.243223 | `kucoin:HBAR-USDT:ws:1795695283076` | 7.353457s / 1.268935s | Both directions rejected changed-head gas |

All three selected WebSocket books were received during DEX acquisition, and selection began after DEX completion. All three stored observations matched the selected book ID and last valid WebSocket receipt and recorded positive uncrossed bid and ask levels with quantities. The single socket showed one open and zero closes across all cycle boundaries and one close at teardown. All cycles reached evaluation with both public snapshots. Unknown or invalid gas produced no qualifying virtual opportunity. The probe emitted read-only analysis and did not submit any trade or run a sustained experiment.

Raw sanitized public evidence is in `agent-3-public.jsonl`. The independent structural assertions are reproducible with `python ws-order-summary.py agent-3-public.jsonl`.

A separate repeat using the final installed wheel also exited 0. It opened one socket and closed it only on teardown. Cycle 1 rejected a stale DEX snapshot and never invoked KuCoin selection. Cycles 2 and 3 selected fresh WebSocket books after DEX completion and rejected changed-head gas. See `ws-order-final-public-repeat.jsonl`.
