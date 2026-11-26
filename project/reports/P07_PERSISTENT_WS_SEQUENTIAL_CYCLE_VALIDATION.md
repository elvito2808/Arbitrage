# P07 persistent WebSocket and sequential cycle validation

## Architecture and scope

The owner-approved Option A keeps one KuCoin depth stream per foreground runtime session. The stream can subscribe, receive, validate and buffer market images while the coordinator awaits SaucerSwap. The coordinator completes the DEX snapshot and records its completion time before invoking KuCoin's snapshot selector. The two awaits still share a single 10-second deadline. The selection is followed by timestamp, quote, gas, balance and risk validation. DEX failure or cancellation prevents KuCoin selection for that cycle.

The KuCoin adapter returns an immutable, versioned book with frozen levels and tuple-backed sides. A valid replacement becomes the latest buffered book for the next selection. The persisted observation includes snapshot ID, transport, symbol, exact best prices and quantities, source time and receipt time. DEX observations now include completion time. Existing serialized observations remain readable. Runtime health includes the most recent accepted WebSocket book receipt timestamp.

A malformed depth image on the subscribed market cannot replace the valid book, change its receipt time or postpone the valid-depth silence deadline. A previous valid book can be selected only while individually fresh. Invalid stream identity, stale depth, disconnection or expired data follow the existing invalidation and fallback paths. Recovery still requires fresh, advancing depth before WS takes preference. REST and WS continue to share existing freshness and duplicate protections.

| Revision | Scope |
| --- | --- |
| `e85593ad023fed0df293ab5b351c73b535815f33` | Owner assignment base on PR #27 |
| `c5de7f0d4545e8be0c4333901f8c5c51ec3065bb` | Persist selected book evidence, record WS update time, and cover controlled persistent cycle ordering and snapshot isolation |
| `662d562ccffec80b98e9b7a6eee8d2190264edb4` | Final product revision. Reset valid-depth silence only after a validated advancing image. Regression asserts the actual monotonic deadline remains unchanged after malformed data |

## Deterministic verification

- Controlled asynchronous events hold DEX while the same socket ingests a newer book. The test observes no KuCoin selection or evaluation before release. It then verifies DEX completion, KuCoin selection, and market evaluation in order. A later image is selected by the next cycle while the first selected book and its persisted observation retain their original quantities. The socket connects once, with no REST read and no close between the cycles.
- Existing cases verify failed startup, disconnect, silence, stale or wrong-market depth, REST fallback, rate limiting, inability to price when both sources are unavailable, and WS recovery after two advancing images. Invalid same-market depth is covered by a focused positive retention test and an expiry-to-REST assertion.
- Existing tests preserve rejection after DEX failure, a single shared deadline, cancellation, 10-second cross-venue acceptance for source and receipt timestamps, and rejection above 10 seconds. Individual freshness, future receipt rejection and the separate source-ahead tolerance remain effective.
- Gas validation, stable head protection, financial and reservation rules, ledger immutability, lifecycle and recovery were exercised by the full regression suite.

Verification used Python 3.12.14. `python -m pip check` passed. The focused command and full run are in the [test log](../evidence/ARB-P07-WS-ORDER/tests.txt). The focused run passed 59 tests. The full run passed 258 tests with 3 skipped in 7.29 seconds, exit 0. The three omitted smoke cases are opt-in network tests. The bounded public verification below is separate.

## Public provider verification

The probe uses one coordinator, one worker, one temporary database and one persistent socket task for three cycles separated by the normal 15-second minimum scheduling interval. An overall timeout bounds execution. Public egress used the configured environment proxy. The public sender was used solely for read-only gas estimation.

```sh
WSS_PROXY="$HTTPS_PROXY" ARB_PUBLIC_ESTIMATE_SENDER=0x000000000000000000000000000000000019bbff \
  python project/evidence/ARB-P07-WS-ORDER/public_cycles.py
```

Public JSONL records cycle IDs, individual timestamps, selected versions and top-of-book data, source transport, socket counts, last valid WS message, Hedera block, gas evidence, skew, and directional dispositions.

Both final-revision runs completed on 2026-11-26 UTC with process exit 0. Each run opened one socket, kept it open across all three cycles, and closed it once at probe shutdown. All six selections used executable WebSocket depth. In each row below, DEX completion preceded KuCoin selection.

| Run and cycle | DEX completion UTC | KuCoin selection UTC | Source skew | Receipt skew | Both directions |
| --- | --- | --- | ---: | ---: | --- |
| Agent 1, 1 | 12:14:11.969204 | 12:14:11.969214 | 9.321668 s | 3.341636 s | gas invalid |
| Agent 1, 2 | 12:14:30.348370 | 12:14:30.348377 | 7.728253 s | 0.701772 s | gas invalid |
| Agent 1, 3 | 12:14:46.960447 | 12:14:46.960455 | 5.634818 s | 0.311253 s | gas invalid |
| Agent 3, 1 | 12:14:08.046369 | 12:14:08.046378 | 9.582652 s | 2.549097 s | gas invalid |
| Agent 3, 2 | 12:14:26.237015 | 12:14:26.237098 | 6.185224 s | 0.614398 s | gas invalid |
| Agent 3, 3 | 12:14:43.281437 | 12:14:43.281444 | 7.353457 s | 1.268935 s | gas invalid |

In all six cycles the final Hedera head check invalidated both estimates with `route_estimate_head_changed`. No opportunity was eligible. A separate pre-fix probe at `c5de7f0` captured one stable-head pair at 122,640 and 122,659 gas and both directions were rejected on the unchanged maximum-loss rule. Gas and risk logic were untouched by the final silence fix. The independent repeat also retained one socket. One DEX snapshot failed individual freshness and the coordinator skipped KuCoin selection for that cycle. The next two cycles used WS and rejected changed-head gas. The short public runs do not establish provider uptime. Live reconnect was not induced in these runs. Existing Round 2 public evidence observed fallback and recovery, and the current deterministic suite verified both paths.

[Pre-fix public JSONL](../evidence/ARB-P07-WS-ORDER/pre-fix-public.jsonl) · [Agent 1 public JSONL](../evidence/ARB-P07-WS-ORDER/public.jsonl) · [Agent 3 public JSONL](../evidence/ARB-P07-WS-ORDER/agent-3-public.jsonl) · [Agent 3 repeat JSONL](../evidence/ARB-P07-WS-ORDER/agent-3-public-repeat.jsonl) · [Round 2 provider report](../P01-P07_ROUND_2_PROVIDER_EVIDENCE.md)

## Independent QA and disposition

Agent 3 independently returned **PASS** at `662d562` from a separate Git archive and clean install. The full run passed 258 with three opt-in smoke skips in 7.18 seconds. Independent checks covered ordering, buffering, immutable book selection, valid-depth silence, fallback, recovery, timestamp limits and safe public rejections. SQLite checks confirmed that old observations deserialize and exact new metadata round-trips. [Agent 3 report and reproduction commands](../evidence/ARB-P07-WS-ORDER/agent-3-ws-order.md)

**Final recommendation: PASS for ARB-P07-WS-ORDER.** Owner-approved persistent transport and DEX-first cycle selection were verified. Finite public runs observed no eligible virtual opportunity. Agent 0 retains P07 acceptance. PR #27 remains Draft, unmerged, and simulation-only.
