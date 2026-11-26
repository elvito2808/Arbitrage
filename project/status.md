# Arbitrage project status — 2026-11-26 UTC

Framework A v1.0.0 pinned read-only at `cb23c4b08d2103aead243b59d6b66f325361a4bc`. B `elvito2808/Arbitrage`, canonical main. Owner-approved ARB-RM-001 v1.0.0 and original B authorized baseline `1baff43168e7d17060d44a49d10225857b87a0af`, approvals in project/decisions.md.

**Accepted milestones (bounded):** P01 bounded public-data feasibility PR #1 main `b4ed121aac1249352dccf4a5d4353765e66dfee5`; P02 foundational Python infrastructure PR #4 main `633ec6573e650bf7846621aea797f2223b3ccae2`; P03 public REST/DEX price discovery PR #6 main `bb10fae5a76669d2fdfb6ddb8933256c066c9997`; P04 read-only opportunity analysis, reason-coded rejection, persisted dedup and worker fencing PR #8 main `3ea6cd1a62b3f471e614f4eb008eb7aff70a2cfb`. Agent 0 reviews in project/evidence/ARB-005-011/, ARB-012-017/, ARB-018-025/, ARB-026-031/. Specialist P04 final installed Python3.12 suite reported 87 pass, 3 optional network skips, reviewed statically by Agent 0, not independently rerun; no CI status checks.

| Stage | Assignment | Execution | Verification disposition | Next action |
|---|---|---|---|---|
| P00 ARB-001–005 | Agent 0 / Agent 1 | ended | Governance initialized, temporary restore evidence inspected; independent durable backup owner-deferred | Retain recovery risk |
| P01 ARB-006–011 | Agent 1 | ended | ACCEPTED bounded public-data feasibility; 24h unknown | Used by P03 |
| P02 ARB-012–017 | Agent 1 rev2 | ended | ACCEPTED simulator foundations | Used by P04–05 |
| P03 ARB-018–025 | Agent 2 rev1 | ended | ACCEPTED public REST/DEX quote computation; live KuCoin WS, execution gas remain UNVERIFIED | No executable claims on live gas |
| P04 ARB-026–031 | Agent 2 rev1 PR #8 | ended | ACCEPTED read-only analysis and rejection; current P03 gas unknown produces zero eligible; positive tests synthetic | Used by P05 with strict provenance safeguards |
| P05 ARB-032–039 | Agent 2 rev1 task project/tasks/ARB-032-039.md | planned, owner-supervised dispatch pending | No report | Build test-only simulated execution, fail closed for live-data gas unknown |
| P06–P09 | Not assigned | planned | No report | Wait for dependencies; deployment needs separate owner authorization |

**Hard gates:** No real trades, swaps, signing, keys, wallets, authenticated order APIs, broadcasting, paid services or server deployment. P05 may test simulated fills with expressly test-only injection and deterministic trusted fixtures; **normal public-data runtime must never accept fake gas evidence or generate simulated fills without independently verified route cost**. All real-market quotes with unknown gas remain candidates/rejected only. Preserve DEX-first, no CEX price recheck in same cycle, max three retries per side, no automatic compensation, persistent incomplete outcomes, no double spend, exact 1000 HBAR, fees counted once, 24h experiment later.

**Outstanding:** real route-specific gas & trusted estimation, sustained public provider quota/uptime and actual 24h continuity, KuCoin live WS from cloud runner, live coherent two-venue opportunity, crash/power-loss validation, independent A+B durable backup deferred by owner (not VERIFIED). Owner explicitly asked cloud-only development with no owner local machine execution.

**Checkpoint/recovery:** CP-001 through CP-005, project/backups.md, PR #8 and its review. No active supervised software or in-flight real trade. Pending manual Codex P05 dispatch after verified main SHA of this management PR.
