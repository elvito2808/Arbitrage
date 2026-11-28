# Agent 0 · Execution Task 01/10

## Complete gas calculation and consolidate implementation

The repository is [elvito2808/Arbitrage](https://github.com/elvito2808/Arbitrage). The project owner authorized repository access, implementation, commits, and publication to `fix/srg-clm-rc2`.

1. Use `fix/srg-clm-rc2` as the single working branch. Preserve FIX-01 commit `57d35ae19fa2f66b004ace8bbbad0b068f0d5fe5` if available. Otherwise start from the published QA reproduction branch and implement the readiness correction directly. Preserve startup readiness, English console logging, financial calculations, and recovery.
2. Configure and validate 109,478 gas units for BUY and SELL and a gas limit of 300,000 in `config/simulation.toml`. Use the limit for validation and reference. The approved pre-EIP-1559 transaction used and consumed 109,478 gas units at 1,540 gWei for 0.16859612 HBAR. Sample the current price with the existing public Hedera adapter and `eth_gasPrice`. Multiply the configured units by that price, then apply the existing 2× multiplier for the maximum budget. Use integer arithmetic and conservative Hedera unit rounding. Use the approved KuCoin conversion to USDT and deduct gas once.
3. Use the source `CONFIGURED_GAS_UNITS + LIVE_RPC_GAS_PRICE` consistently in readiness, provenance, and admission. Remove dependencies on wallets, token associations, allowances, real balances, swap estimation calls, private keys, and signing. A legacy optional public sender may remain. Preserve price and market freshness checks and fail closed when price is unavailable or invalid. Represent the gas units as configured inputs without any live swap measurement or block-pinned swap estimate.
4. Log the method, both configured unit counts, limit, current price, estimated HBAR cost, and maximum HBAR budget at startup. Integrate the estimate and budget for both directions in cycle logs and reports.
5. Use only offline synthetic inputs to verify configuration, Python imports and syntax, arithmetic, readiness, wallet independence, and simulation-only restrictions. The reference estimate is 0.16859612 HBAR per direction and the cap is 0.33719224 HBAR. Keep verification focused.
6. Commit and push to `fix/srg-clm-rc2` using a regular push. Leave `main` untouched and verify the published commit. Keep this assignment and the concise implementation report under `project/agent-tasks/`.
7. Report status, branch and SHA, changed files, final configuration, reference arithmetic, wallet independence, verification, and any remaining blocker.

**Simulation only.** No real transactions, account changes, signing, live Hedera or KuCoin tests, server deployment, or additional handoff missions.
