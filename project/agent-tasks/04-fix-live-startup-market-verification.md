# Agent 0 — Execution Task 04/10

## Fix Live Startup Market Verification Failure

Repository: https://github.com/elvito2808/Arbitrage
Branch: `fix/srg-clm-rc2`
Expected base commit: `c0b0481aa74bc2bdc383bc49858c13e7bc99e027`

The owner successfully installed and launched the simulator on Ubuntu WSL. Two startup attempts, including DEBUG mode, failed after the startup retry sequence. Hedera RPC and KuCoin REST showed healthy transport, Hedera Mirror showed invalid transport, and KuCoin WebSocket recorded a valid receipt timestamp. Snapshots were UNKNOWN with `market_unverified`. Readiness was STARTUP_BLOCKED with zero completed cycles, zero trades, and zero console drops. DEBUG repeated the generic diagnostic.

## Required corrections

1. Trace the entire Hedera Mirror request, response validation, and exception propagation path.
2. Investigate RPC-to-Mirror block number, hash representation, timestamp parsing, schema, and Mirror indexing lag in `saucerswap.py`.
3. Correct demonstrable defects. Establish a live root cause only if the available evidence supports it.
4. Preserve exact sanitized categories through provider, market-data, coordinator, readiness, and logging layers.
5. Show the relevant provider, operation, and actionable reason in INFO, WARNING, or ERROR text.
6. Include safe details in DEBUG, including HTTP status, expected and observed block numbers, ages, hash classification, and invalid field names where applicable.
7. Persist diagnostics in JSON status.
8. Report independent provider outcomes, including completed successful checks.
9. Distinguish indexing lag, malformed responses, identity mismatch, stale snapshots, and network failure.
10. Handle transient indexing lag within existing freshness, retry, and rate limits.
11. Distinguish persisted session intent from actual stopped runtime state.
12. Document safe upgrades for existing WSL and server installations while preserving configuration and all financial state and history.

Simulation only. No live provider testing, wallet operations, private credentials, fabricated prices, runtime mock inputs, state resets, or extensive QA. Keep identity, freshness, and consistency checks and the approved 10-second freshness limit. Preserve gas calculations and financial logic. Use minimal offline fixtures and structural checks.

If the live cause remains unknown, say so and deliver diagnostic information for the owner's next attempt. Save the assignment and implementation report under `project/agent-tasks/`, publish to `fix/srg-clm-rc2`, and verify the remote commit. Report cause or uncertainty, corrections, commit, changed files, exact upgrade and restart commands for WSL, exact upgrade commands for server, offline results, and blockers. Claim a successful live startup only with owner runtime evidence.
