# Agent 3 independent Round 2 provider verification

Independent checks used the published integration head `5d9eb59985fd71d81e474f1e9c72eaba313d5e6e`, verified with `git ls-remote --heads origin agent-0/p07-integration`. Product code was loaded from a separate Git archive. Checks used public read-only requests, the designated public simulation address, and the environment's outbound proxy. No keys, signing, allowances, balance changes, orders, or transaction submission were used. The local WebSocket was closed once to check fallback and recovery.

## K1 — public WebSocket, pricing, fallback and recovery

**PASS through the selected test egress.** Explicitly treating the existing HTTPS proxy as the WSS proxy enabled the production depth adapter to reach the public endpoint. The current runtime's direct egress remains a separate environment check.

- Public symbol metadata and bootstrap passed. The first socket received welcome and acknowledgment for the HBAR-USDT depth-5 topic at 08:49:48 UTC.
- At exchange time 08:49:48.215 UTC, normal WebSocket pricing consumed bid 0.05421 with 5,325.5715 HBAR, and ask 0.05423 with 223.3491 HBAR. The full book yielded a 1,000 HBAR buy for 54.299779 USDT and sell for 54.155790 USDT after the configured fee. The buy required executable levels beyond the best ask.
- The service ping and pong were observed at 08:49:56.200740 and 08:49:56.333632 UTC. After closing only the test socket at 08:49:56.482085 UTC, the adapter selected valid REST depth at 08:49:56.851956 UTC. It reconnected, received a new acknowledgment and selected fresh advancing WebSocket data at 08:49:59.725013 UTC.
- Agent 0's evidence shows a separate bootstrap, WebSocket upgrade, acknowledgment and priced WebSocket book. These observations are consistent with the independent reproduction.

Reproduction from the archived root: `WSS_PROXY="$HTTPS_PROXY" PYTHONPATH=src ARB_QA_EGRESS=configured_proxy <venv>/bin/python agent-3-kucoin.py`, exit 0. The command sets the WebSocket proxy from the configured HTTPS proxy. The depth phase has a 24-second bound. See [KuCoin log](agent-3-kucoin.jsonl). The procedure wraps transport methods to record event types and uses the unmodified product subscription, transition, validation and pricing methods. It records neither bootstrap token nor proxy settings.

## H1 — independent public BUY/SELL estimates and fee conversion

**PASS for same-head public RPC estimates and gas-unit conversion.** Together with Agent 0's separate same-head observation, the independent probe reproduces the requested route capability. Application admission remains open in this environment.

- Read-only estimates used the public mainnet context for account `0.0.1686527`, exact product calldata with 1,000 HBAR, known SaucerSwap router, zero value, and independent BUY and SELL selectors. The designated account's association, balance and allowance evidence was checked separately by Agent 0.
- Before and after the independently bracketed sample, the public latest head matched in both number and hash. Head `0x620883a` was observed from 08:51:16.828416 through 08:51:17.873116 UTC. BUY `0x1df10` and SELL `0x1df23` reproduced the same independent quantities as Agent 0 on a different stable head. Mainnet chain ID was 295. `eth_gasPrice` returned 1,610,000,000,000 weibars per gas, consistent with 161 tinybars per gas from the public mirror fee schedule.
- BUY 122,640 gas costs 19,745,040 tinybars or 0.19745040 HBAR. SELL 122,659 gas costs 19,748,099 tinybars or 0.19748099 HBAR. At a contemporaneous 1,000 HBAR gross KuCoin ask of 54.080000 USDT, estimated costs rounded upward to 0.010679 and 0.010680 USDC under the configured parity assumption. The historical calibration reproduced 22,762,599 tinybars. Its 143,161 measured gas was never substituted for either estimate.
- The bracketed diagnostic sampled an independently fresh CEX book. The DEX block time and CEX exchange time differed by 7.716 seconds, beyond the 2-second configured cross-venue limit. The diagnostic conversion is evidence of arithmetic and provider capabilities. It does not establish an admissible opportunity.
- In a separate invocation of the normal product snapshot at 08:50:24 UTC, the same RPC estimates both returned successfully. The adapter rejected both because the latest head changed from `0x6208820` to `0x6208822`. Subsequent normal snapshots showed a stale snapshot or public deadline. These outcomes verify the guard's fail-closed behavior.

Reproduction from the archived root: `PYTHONPATH=src ARB_QA_EGRESS=configured_proxy ARB_QA_ESTIMATE_SENDER=<designated-public-EVM-address> <venv>/bin/python check_hedera_bracket.py`, exit 0. It permits at most four attempts and uses a 24-second bracket deadline. See [Hedera bracket log](agent-3-hedera-bracket.jsonl). For the independent full-path attempt, `PYTHONPATH=src ARB_QA_EGRESS=configured_proxy ARB_QA_ESTIMATE_SENDER=<designated-public-EVM-address> <venv>/bin/python check_hedera.py`, exit 1. See [Hedera adapter log](agent-3-hedera-production.jsonl). The probes emit sanitized method names, selectors, head IDs, typed failure categories and public price outputs.

## Disposition

Independent reproducibility is established for K1's live protocol, actual pricing and transport recovery, and H1's independently successful public route gas estimates and unit conversion. Live full-path opportunity admission remains unverified because the measured DEX/CEX snapshots exceeded their skew gate and the normal route checks observed a moving head. Failure remained closed. Final phase disposition belongs to Agent 0.
