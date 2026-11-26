# Arbitrage project status — 2026-11-26 UTC

**Project:** ARB. **Roadmap:** ARB-RM-001 rev 1.0.0 approved in project/decisions.md. Framework A pinned to `cb23c4b08d2103aead243b59d6b66f325361a4bc` (read-only). B original owner-authorized baseline `1baff43168e7d17060d44a49d10225857b87a0af`; P01 evidence merged to main `b4ed121aac1249352dccf4a5d4353765e66dfee5`. This status amendment is submitted through PR from `agent-0/arb-p01-acceptance`.

**Latest verified milestone:** P01 bounded public-data feasibility. Agent 1 head `09f43c2e48a7db3413a7565766cad0150982e1c5`, reviewed/merged PR #1. Evidence: project/reports/ARB-005-011.md, project/evidence/ARB-005-011/, and agent-0-review.md. External API endurance, account-dependent execution and accurate per-route gas are NOT verified.

**Open prerequisite:** Durable independent owner-controlled Git bundle copies and isolated restore of BOTH repositories (A+B) at accepted revisions remain pending. A temporary bundle/restore was evidenced, but does not meet durable backup policy. See project/backups.md and project/backup-instructions.md. No P02 implementation modifications until backup gate accepted.

| Stage/tasks | Assignment | Execution | Verification | Dependencies | Evidence | Next |
|---|---|---|---|---|---|---|
| P00 ARB-001–004 | Agent 0 initialization | ended | partially VERIFIED; original governance records committed | Roadmap v1.0.0 | context/requirements/roadmap/roles/decisions | Maintain records and checkpoint |
| P00 ARB-005 | Agent 1 ARB-005-011 rev 1 | ended | temporary restore VERIFIED from inspected evidence; durable backup PENDING | Original approved baseline | PR #1, backup-restore.txt | Owner persistent backup + isolated verification |
| P01 ARB-006–011 | Agent 1 ARB-005-011 rev 1 | ended | VERIFIED for bounded public-data feasibility only | Owner-approved scope | PR #1, agent-0-review.md | Preserve unresolved operating risks |
| P02 ARB-012–017 | Agent 1 task rev 1 | planned, BACKUP-GATED | No report | P01 accepted + durable backup | project/tasks/ARB-012-017.md | Dispatch after backup gate verified |
| P03–P09 | Not assigned | planned | No report | Earlier milestones | project/roadmap.md | Not released |

**Current blocked / unverified:** Owner-controlled independent backup, actual 24h service continuity, representative swap gas both directions, cost/latency under sustained load, any real-market profitability. No LIVE, credentials, server deployment or additional costs authorized. No autonomous Codex dispatch; owner relays English bounded assignment.

**Recovery:** project/checkpoints/CP-001.md original initialization, new CP-002 approval handoff, and project/backups.md. Do not conflate GitHub source repository with independent offline backup. No active trading or background task.


## Owner decision — 2026-11-26
Owner explicitly requests proceeding without local execution or personal-system backup at this stage. Durable independent backup is deferred, not VERIFIED. This supersedes the P02 backup gate in prior records; retain recovery risk and temporary restore evidence. No change to A, no live trading, no deployment or added cost. P02 may proceed in Codex Cloud. Owner instruction: «من فعلا نیازی به نسخه پشتیبانی روی سیستم خودم ندارم. پروژه بدون اینکه لازم باشد نسخه ای را روی سیستم خودم اجرا کنم، جلو ببر.»
