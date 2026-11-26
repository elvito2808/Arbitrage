# P01–P07 Round 2 public-provider evidence

**Disposition.** K1 and H1 public checks passed via configured public egress and were independently reproduced by Agent 3. Full foreground public admission remains unverified. The direct KuCoin path from this runner was unavailable, and the complete Hedera snapshot runs did not retain one stable head through quote verification and estimation. PRs remain in Draft for Agent 0 review.

The checks ran on 2026-11-26 UTC. The tested integration revision was `5d9eb59985fd71d81e474f1e9c72eaba313d5e6e` in PR #26. Its product code was pinned in Round 1 at `8ac742cba1bd904ce2df9684a5250f1c25a7987c`. The KuCoin workstream was `3c6d588a1ee2bbe818cdefceb97ead5565904665` and Hedera was `ff02afb090a94eed0d7d4ce4c800e3b09b99b6d3`. Remote `main` was `813d9f840c6283f62836a7162d23ed1ee9b08fe8`. Python 3.12.14.

## K1 – KuCoin executable public WebSocket

The unmodified production KuCoin adapter used a proxy-capable HTTP session supplied by the probe. Public HBAR-USDT symbol metadata and bootstrap succeeded. The WebSocket upgrade returned 101, welcome arrived at **08:49:14.019122**, and the server acknowledged `/spotMarket/level2Depth5:HBAR-USDT` at **08:49:14.159801**. The adapter switched from REST to WebSocket at **08:49:15.347605** and priced both directions from that book. The exchange timestamp was **08:49:15.153000** and receipt was **08:49:15.231049**. All times in this report are UTC.

| Observation | Value |
| --- | --- |
| Best bid and displayed size | 0.05422 USDT × 5,339.7065 HBAR |
| Best ask and displayed size | 0.05423 USDT × 223.3491 HBAR |
| Executable BUY for 1,000 HBAR | 54.303224 USDT including modeled fee, across four ask levels |
| Executable SELL for 1,000 HBAR | 54.165780 USDT after modeled fee |
| Heartbeat | Ping 08:49:22.160999, pong 08:49:22.302175 |

Agent 3 reproduced executable pricing and heartbeat, then closed only its own socket at **08:49:56.482085**. REST supplied a fresh book at **08:49:56.851956**. The replacement subscription acknowledged at **08:49:58.705242** and the adapter returned to WebSocket at **08:49:59.725013**, after valid advancing books. The direct network trial began at **08:50:21.281795** and ended at **08:50:53.283786** with repeated symbol metadata unavailability and visible reconnect state. The successful checks used the environment's public egress proxy with WebSocket proxy support explicitly selected. The foreground runtime retains its existing direct transport configuration.

[Public attempt and depth](evidence/P01-P07-round2/kucoin.jsonl) · [Direct attempt](evidence/P01-P07-round2/kucoin-direct.jsonl) · [Agent 3 heartbeat, fallback and reconnect](evidence/P01-P07-round2/agent-3-kucoin.jsonl)

## H1 – Hedera route-specific estimates

Public Mirror Node confirmed both token associations, spendable token status, sufficient balances and existing router allowances for **0.0.1686527**. The route needed at least **54,232,171** USDC base units for BUY and **100,000,000,000** WHBAR base units for SELL. The observed token balances were **99,865,978** and **19,418,317,852,886** raw respectively. The first candidate, 0.0.9976411, had sufficient balances and allowances, but Hashio reported `INVALID_ALIAS_KEY` when the long-zero address was used. That case confirms public balances and allowances alone can be insufficient to qualify an address form.

In the successful pair, unsigned exact-output BUY and exact-input SELL used separate `eth_estimateGas` requests with the corresponding token order, exact 1,000 HBAR size, route-specific amount bound and zero native value. The chain was 295. At **08:49:55.069813**, the post-estimate check matched both number and hash of head **0x6208814**. An earlier successful-looking pair was discarded when the head advanced. Hashio reported **1,610,000,000,000 weibars per gas**, matching Mirror Node's **161 tinybars per gas**. Each cost is the independently reported gas quantity multiplied by the sampled price, rounded up to tinybars and divided by 100 million for HBAR.

| Direction | Selector | Hashio result | Gas units | Tinybars | HBAR |
| --- | --- | --- | ---: | ---: | ---: |
| BUY | `0x8803dbee` | `0x1df10` | 122,640 | 19,745,040 | 0.19745040 |
| SELL | `0x38ed1739` | `0x1df23` | 122,659 | 19,748,099 | 0.19748099 |

Agent 3 reproduced both quantities and unit conversion, bracketing the calls with an identical head number and hash at **0x620883a** between **08:51:16.828416** and **08:51:17.873116**. Its public HBAR-USDT valuation sample was 54.080000 USDT for 1,000 HBAR before fees, yielding 0.010679 and 0.010680 USDC after upward micro-unit rounding under the simulation parity assumption. The two venue timestamps differed by **7.716 seconds**, exceeding the runtime's 2-second limit, so this valuation is evidence of arithmetic only.

In a separate run, the complete production SaucerSwap snapshot received both estimates from Hashio and rejected them on a changed or unconfirmed final head. Another snapshot was stale. Agent 3 independently reproduced the moving-head rejection. No route admission based on those snapshots was verified. The owner's historical gas measurement was not substituted for a current estimate, and no state override, signing, submission, or account mutation was used.

[Raw requests and sanitized responses](evidence/P01-P07-round2/hedera-backup-retry.jsonl) · [Initial sender and alias errors](evidence/P01-P07-round2/hedera-detail.jsonl) · [Production adapter observations](evidence/P01-P07-round2/hedera-production.jsonl) · [Agent 3 stable-head evidence](evidence/P01-P07-round2/agent-3-hedera-bracket.jsonl) · [Agent 3 production snapshot](evidence/P01-P07-round2/agent-3-hedera-production.jsonl)

[Agent 3 independent report](evidence/P01-P07-round2/agent-3-round2.md)

## Commands and disposition

The public probes are stored under `project/evidence/P01-P07-round2/`. Run them with the installed package at the tested SHA. Proxy addresses and transient public bootstrap tokens are omitted from evidence.

| Command | Exit | Observation |
| --- | ---: | --- |
| `WSS_PROXY="$HTTPS_PROXY" python probe.py K1 environment` | 0 | Priceable WebSocket depth, subscription ACK and heartbeat |
| `timeout 34 python probe.py K1 direct` | 1 | Public symbol metadata unavailable, reconnect observed |
| `WSS_PROXY="$HTTPS_PROXY" ARB_QA_EGRESS=configured_proxy python agent-3-kucoin.py` | 0 | Independent depth, heartbeat, fallback and validated WS recovery |
| `python probe.py H1 environment --account 0.0.9976411` | 1 | Both route calls reverted, including across stable heads |
| `python probe.py H1 environment --account 0.0.1686527` | 1 then 0 | Public request unavailable on initial trial. Stable-head pair on retry |
| `python product.py` | 1 | Complete production snapshot rejected changing/unconfirmed heads and stale data |
| `ARB_QA_EGRESS=configured_proxy ARB_QA_ESTIMATE_SENDER=0x000000000000000000000000000000000019bbff python check_hedera_bracket.py` | 0 | Independent stable-head pair, fee and valuation arithmetic |
| `python -m pip check` | 0 | No broken requirements |
| `python -m pytest -q -rs -p no:cacheprovider` | 0 | 239 passed, 3 optional network smoke tests skipped in 6.61 s |

The standard smoke tests were left opt-in. The bounded public Round 2 checks above supplied separate provider evidence. The product and its fail-closed behavior were unchanged. All PRs remain unmerged and in Draft. No phase was closed and P08/P09 were not started.
