# Agent 3 independent QA for P01–P07 Round 1

**Disposition.** PASS for deterministic implementation and integrated virtual execution. PARTIAL for public provider acceptance. This review pins integrated implementation `8ac742cba1bd904ce2df9684a5250f1c25a7987c` and the independent replay tests in `tests/qa/test_round1_independent.py`. Successful current public gas estimates and validated public KuCoin WebSocket depth remain unverified. Agent 0 retains phase acceptance.

## Verification record

Each implementation was tested from a separate Git archive on Python 3.12.14. Setup used `python -m venv <qa-venv>`, `<qa-venv>/bin/python -m pip -q install '.[test]'`, and `<qa-venv>/bin/python -m pip check`. All setup commands exited 0. The final A test-only commit reused the clean wheel from its unchanged product tree.

| Revision | Verification command | Exit | Result |
| --- | --- | --- | --- |
| Baseline `813d9f840c6283f62836a7162d23ed1ee9b08fe8` | `python -m pytest -q -rs -p no:cacheprovider` | 0 | 209 passed, 3 skipped |
| A `3c6d588a1ee2bbe818cdefceb97ead5565904665` | `python -m pytest -q -rs -p no:cacheprovider` | 0 | 218 passed, 3 skipped in 7.07s |
| B `ff02afb090a94eed0d7d4ce4c800e3b09b99b6d3` | `python -m pytest -q -rs -p no:cacheprovider` | 0 | 223 passed, 3 skipped in 6.39s |
| Integrated `8ac742cba1bd904ce2df9684a5250f1c25a7987c` with independent tests | `python -m pytest -q -rs -p no:cacheprovider tests/qa/test_round1_independent.py` | 0 | 3 passed in 0.36s |
| Same integration and independent tests | `python -m pytest -q -rs -p no:cacheprovider` | 0 | 239 passed, 3 skipped in 7.58s |
| Same integration | `git diff --check 813d9f8 8ac742c` | 0 | Passed whitespace check |

The three skipped cases in each full run are opt-in network smoke checks in `tests/test_market_smoke.py`. A separate baseline opt-in run enabled all three. Public KuCoin REST passed. The Hedera snapshot exceeded the clock-skew/freshness limit and the indicative KuCoin WebSocket exhausted its timeout/reconnect budget. On A’s identical product tree `169a462`, a separate `python probe_depth.py` check used the production depth adapter with a 20-second outer deadline and proxy-capable public HTTP transport. It exited 1 with the adapter reconnecting and no validated depth. The gas workstream documented insufficient token balance for both live read-only estimates using the PDF sender. These public observations neither required credentials nor initiated a continuity experiment.

A source-only preview of gas tests initially failed 10 crash cases because subprocesses imported the baseline wheel despite a parent `PYTHONPATH` override. A clean install of the same source resolved all 10. A preliminary integrated independent test used an inaccurate status string in its test-only assertion after passing financial reconciliation. The corrected assertion uses `EvaluationStatus.ELIGIBLE`, and the repeat passed on both integration heads.

## Independent observations

- **A1–A3, ARB-019, ARB-024, ARB-025.** Production-shaped WebSocket frames drove the actual normal price path. The profitable SELL order consumed 300 HBAR at 0.05500 and 700 at 0.05490. A 0.1% fee yielded exactly 54.875070 USDT, with zero REST depth requests in that cycle. The branch suite verified startup fallback, stale and malformed market data, wrong market, socket silence, HTTP 429, capped reconnection, timestamp fencing, and return only after fresh depth. The independent race captured a valid WebSocket book, invalidated it with a wrong-market frame, then delivered it to the coordinator. Both opportunities rejected with `invalid_market`, no snapshot was consumed, and no financial state changed.
- **B1–B4, ARB-023, ARB-029.** The replay decoded both independent route calls, checking router, sender, token order, exact size, slippage bounds, deadline and zero value. Distinct mock gas results reached the corresponding directions. The PDF reproduces 143,161 gas × 1,590 gWei/gas = 22,762,599 tinybars = 0.22762599 HBAR. The integrated case used a distinct 156,000-gas estimate at 163 tinybars per gas. It reserved the 2× gas price cap for each of three attempts and settled one gas debit of 25,428,000 tinybars. When both RPC estimates reverted, both directions stayed in gas rejection states. Balances, executions, reservations, simulated trades and ledger stayed unchanged.
- **R03, R04, R06, ARB-050–054.** The foreground replay cancelled with three durable reservations and zero posted entries. Restart settled one virtual trade, released every reservation, and produced five postings. Final holdings equaled opening balances plus postings for every asset. SQLite integrity and foreign-key checks passed before and after restart. The complete suite also covered deadline settlement, emergency fencing, SIGKILL, reservation and export races, disk-full and denied-write recovery, and append-only ledger behavior. Existing independent P07 assertions were preserved. Its prior HTTP fixture gained a public bootstrap failure response so its original fallback assertions remain applicable.
- **Scope.** The independent replays forbade outbound sockets and accepted only expected public-read HTTP/WS shapes. Source inspection found the RPC addition limited to read-only estimation. The scoped pattern scan found zero private-key headers, GitHub token patterns or AWS access-key IDs in audited source and project records.

The public provider limitations keep live market-data and gas requirements open. Offline results support review of the implementation, including safety and financial correctness. Final project acceptance remains with Agent 0.
