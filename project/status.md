# Arbitrage project status — 2026-11-26 UTC

Framework A read-only approved pin `cb23c4b08d2103aead243b59d6b66f325361a4bc`, v1.0.0. B `elvito2808/Arbitrage`, canonical `main`; roadmap ARB-RM-001 v1.0.0 owner approved in project/decisions.md. Original B owner-approved baseline `1baff43168e7d17060d44a49d10225857b87a0af`.

**Verified milestones bounded by evidence:** P01 public feasibility at merge `b4ed121aac1249352dccf4a5d4353765e66dfee5`; P02 core infrastructure at merge `633ec6573e650bf7846621aea797f2223b3ccae2`; P03 REST public market adapters at merge `bb10fae5a76669d2fdfb6ddb8933256c066c9997`. See separate Agent 0 reviews `project/evidence/ARB-005-011/agent-0-review.md`, `project/evidence/ARB-012-017/agent-0-review.md`, `project/evidence/ARB-018-025/agent-0-review.md`. P03 source and specialist's report/log evidence inspected; **53 passed, 3 opt-in skipped specialist-run tests**, not independently rerun by Agent 0. Public smoke second pass 2 PASS, 1 FAIL (KuCoin WS); mandatory offline suite PASS; no CI checks.

| Stage | Task | Execution state | Verification / approval | Next |
|---|---|---|---|---|
| P00 | ARB-001–005 | ended | Governance initialized; temporary Git bundle restore inspected; owner-controlled durable backup deferred | Retain explicit recovery risk |
| P01 | ARB-006–011 | ended | ACCEPTED bounded public-data feasibility | Input for P03 |
| P02 | ARB-012–017 | ended | ACCEPTED foundational simulator infrastructure | Input for P03–P05 |
| P03 | ARB-018–025 | ended | ACCEPTED bounded REST/DEX public price discovery; live WS, gas path and 24h operations UNVERIFIED | Do not claim executable cross-market profit |
| P04 | ARB-026–031, task revision 1 | planned, owner-supervised Codex handoff pending | No report | Agent 2 implements coordinator/evaluation; eligible-for-simulation must fail closed on missing gas |
| P05–P09 | future tasks | planned | No report | Do not dispatch before relevant gates |

**Hard safety gates:** Current P03 GasEstimate.execution_gas is None and cost_valid=False, so P04 cannot approve execution based on the historical proxy or treat 34,133 view-call gas as swap gas. Require 10s freshness, max 2s cross-venue skew, full 1,000 HBAR liquidity, strict profit > 0.10 USDT, coherent snapshots, and independent simulated risk checks; record missed/rejected opportunities. WS live endpoint was BLOCKED twice in Codex runner; REST book may provide bounded read-only source; never mark WS as validated. No owner-side local execution required. Independent durable backup explicitly deferred by owner, not completed. No wallet/private API, live trading, exchange orders, real swaps, paid services or deployment approved.

**Recovery:** CP-001, CP-002, CP-003, CP-004, project/backups.md, latest GitHub PR and exact accepted merges. No active trading software, no in-flight orders or transactions. Codex assignments require manual owner relay and exact dispatch SHA.
