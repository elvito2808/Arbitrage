# Task 01 implementation report

The requested FIX-01 commit was unavailable locally and from GitHub. The implementation uses the published QA reproduction commit `92beb16f4cf7417a324b5a604225825dca48fc4e` as its parent. The readiness corrections fence standalone public simulation analysis before network reads or persistence, report operational worker status from readiness, retain usable direction data when gas blocks admission, show unknown execution fees as unknown, and record Hedera identity failures against the Hedera check.

## Gas configuration and calculation

| Setting | Value |
| --- | --- |
| Method | `CONFIGURED_GAS_UNITS + LIVE_RPC_GAS_PRICE` |
| BUY units | 109,478 |
| SELL units | 109,478 |
| Validation limit | 300,000 |
| Maximum budget multiplier | 2× |
| Price source | Public Hedera JSON-RPC `eth_gasPrice` |

The reference price of 1,540 gWei is 1,540,000,000,000 weibars per gas unit. The estimate rounds the price-times-units product up to whole tinybars. Each direction yields 16,859,612 tinybars (0.16859612 HBAR) at the reference price. The maximum budget is 33,719,224 tinybars (0.33719224 HBAR). Runtime pricing uses the fresh RPC response.

Both values use the same fresh fee-free KuCoin ask average for 1,000 HBAR and approved USDC/USDT parity, with upward rounding to micro-USDT. The risk evaluation deducts the capped value once from gross quotes. The virtual journal books modeled gas at the sampled price according to the existing execution rules. Previously accepted journal records retain their recovery path.

Readiness and new admissions require a valid gas price and fresh market snapshots. The gas estimate requires no wallet or real account state. Provenance records the price observation and the configured quantities. Startup, cycle logs, and the workbook expose the method and cost values.

## Verification

Python 3.12 imports and compilation passed. The focused offline test run passed 170 cases covering configuration, reference arithmetic and rounding, readiness and price failures, logs and reporting, simulation admission, and restart recovery. Synthetic transports exercised public read-only RPC and rejected swap estimation, signing, and submission. No live provider requests, real transactions, account changes, signing, or deployment were performed.
