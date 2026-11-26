# Independent QA baseline

Agent 3 verified an archive of commit `813d9f840c6283f62836a7162d23ed1ee9b08fe8` in a fresh Python 3.12.14 virtual environment.

| Command | Exit code | Result |
| --- | --- | --- |
| `python -m pip -q install '.[test]'` | 0 | Clean package and test dependency install |
| `python -m pip check` | 0 | No broken requirements |
| `python -m pytest -q -rs -p no:cacheprovider` | 0 | 209 passed, 3 skipped in 6.56 seconds. The three public smoke tests are opt-in. |
| `ARB_PUBLIC_SMOKE=1 python -m pytest -q -p no:cacheprovider tests/test_market_smoke.py` | 1 | KuCoin public REST passed. Hedera rejected a stale or skewed snapshot. KuCoin public WebSocket reached the reconnect limit after timeouts. 1 passed, 2 failed in 18.50 seconds. |

The opt-in run used bounded read-only public market probes. Provider availability and trusted buy/sell gas estimates remain open at this baseline.

The historical transaction arithmetic reproduces the owner's PDF: 143,161 gas × 1,590 gWei/gas = 227,625,990,000,000,000 weibars = 22,762,599 tinybars = 0.22762599 HBAR. This measurement alone establishes no fee prediction for either current direction.
