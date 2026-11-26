# Arbitrage project status — 2026-11-26 UTC

**Governance:** approved ARB-RM-001 v1.0.0; owner SIMULATION ONLY, zero added cost, no owner-local execution, independently durable backup deferred (NOT VERIFIED). Framework A `elvito2808/Agent-Management-Framework` approved read-only v1.0.0 pin `cb23c4b08d2103aead243b59d6b66f325361a4bc`. B `elvito2808/Arbitrage`, canonical `main`; original authorized baseline `1baff43168e7d17060d44a49d10225857b87a0af`.

**Accepted bounded milestones:** P01 PR #1 merge `b4ed121aac1249352dccf4a5d4353765e66dfee5` (public feasibility); P02 PR #4 merge `633ec6573e650bf7846621aea797f2223b3ccae2` (Python/SQLite base); P03 PR #6 merge `bb10fae5a76669d2fdfb6ddb8933256c066c9997` (REST/DEX prices); P04 PR #8 merge `3ea6cd1a62b3f471e614f4eb008eb7aff70a2cfb` (read-only opportunities); P05 PR #10 specialist head `cb748805eaacb33fafe0ce57a75b70e67da14cde` merged `9fb2a938fecac35fb2a44c536c4ef6a6860f5109` (deterministic **fixture-only** virtual execution and recovery). Agent 0 reviews at project/evidence/ARB-005-011/, ARB-012-017/, ARB-018-025/, ARB-026-031/, ARB-032-039/. P05 evidence: **116 specialist-run Python3.12 tests PASS, 3 opt-in network skips**; code/tests inspected by Agent 0, not independently run by Agent 0; no GitHub CI statuses.

| Phase | Task status | Verification status | Next |
|---|---|---|---|
| P00 ARB-001–005 | Governance complete, backup owner-deferred | Temporary restore evidence inspected, independent durable backup UNVERIFIED | Retain accepted risk |
| P01 ARB-006–011 | Done | ACCEPTED bounded feasibility, no 24h endurance | Dependency passed |
| P02 ARB-012–017 | Done | ACCEPTED foundational Python/SQLite | Dependency passed |
| P03 ARB-018–025 | Done | ACCEPTED read-only public market prices; live WS/gas UNKNOWN | Carry fail-closed gate |
| P04 ARB-026–031 | Done | ACCEPTED analysis/rejections, zero eligible with real P03 unknown gas | Carry fail-closed gate |
| P05 ARB-032–039 | Done | ACCEPTED deterministic fixture-only virtual ledger/trade/recovery, **public-data simulation BLOCKED** | P06 allowed for offline tooling only |
| P06 ARB-040–046 | Task rev1 staged at `project/tasks/ARB-040-046.md` | NOT STARTED | Owner-supervised Agent 1 dispatch after management PR merge |
| P07–P09 | Not assigned | Not verified | Future gates and separate server authorization |

**Outstanding:** independently validated route-specific execution gas and trust provenance for both DEX directions, live KuCoin public WS from Codex runner, continuous free API behavior over 24h, physical power loss, real-data trading effectiveness, independent A+B durable backup. No server deployment, real orders, actual tokens, keys, signed transactions, paid tools or always-on execution approved.

**P06 hard rule:** May create safe CLI, read-only runtime/monitoring, stop/pause/restart, private Excel reports, 24h boundary logic and inert systemd templates without executing them on user systems. Must NOT enable real-public-data simulated fills while gas trust is unavailable. Synthetic HMAC fixture is a test-only mechanism and must never be surfaced as production configuration. No 24h live experiment started.

**Recovery:** CP-001 through CP-006, PR #10 and review, exact accepted merge SHAs, project/backups.md. No running bot or real trade. Owner manually relays Codex assignment on exact verified main after management PR merge.
