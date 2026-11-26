# Arbitrage project status — 2026-11-26 UTC

**Governance:** owner-approved ARB-RM-001 v1.0.0, SIMULATION ONLY, zero additional cost, no owner-local execution, no deployment/live trading. Framework A read-only pin `cb23c4b08d2103aead243b59d6b66f325361a4bc`, VERSION 1.0.0. B `elvito2808/Arbitrage`, canonical `main`; original owner-approved B baseline `1baff43168e7d17060d44a49d10225857b87a0af`. Independent durable backup remains owner-deferred and UNVERIFIED.

**Accepted phase merge references:** P01 `b4ed121aac1249352dccf4a5d4353765e66dfee5`; P02 `633ec6573e650bf7846621aea797f2223b3ccae2`; P03 `bb10fae5a76669d2fdfb6ddb8933256c066c9997`; P04 `3ea6cd1a62b3f471e614f4eb008eb7aff70a2cfb`; P05 `9fb2a938fecac35fb2a44c536c4ef6a6860f5109`; P06 `602ea3f54282201a88c629818176d1bb5366ee31`. P06 report states 133 passed / 3 optional network checks skipped on Python 3.12.14; Agent 0 reviewed source/evidence but did not independently rerun tests; no CI status checks.

| Phase | Scope | State | Acceptance / limitations |
|---|---|---|---|
| P00 | ARB-001–005 | Done | Governance and temporary restore evidence; durable independent backup UNVERIFIED |
| P01 | ARB-006–011 | Done | Bounded public feasibility accepted |
| P02 | ARB-012–017 | Done | Core infrastructure accepted |
| P03 | ARB-018–025 | Done | Public REST/DEX price reads accepted; live WS and route gas unverified |
| P04 | ARB-026–031 | Done | Read-only opportunity analysis accepted; no eligible opportunity on unknown live gas |
| P05 | ARB-032–039 | Done | Fixture-only deterministic virtual execution accepted; public-data simulated execution BLOCKED |
| P06 | ARB-040–046 | Done | Lifecycle, CLI, monitoring, deadline and Excel tooling accepted; inert systemd only, no deployment/24h run |
| P07 | ARB-047–055 | Defined, dispatch pending | Independent QA only; production code write-prohibited; Agent 3 |
| P08 | ARB-056–060 | Planned | Owner separate deployment approval required |
| P09 | ARB-061–065 | Planned | Owner-led 24h experiment and economic decision |

**P07 assignment:** `project/tasks/ARB-047-055.md` revision 1, Agent 3 role `project/roles/agent-3.md` revision 1. It requires clean independent QA checkout, traceability and source review, unit/integration/failure/concurrency/crash/isolation/E2E tests, and an acceptance report. Write scope restricted to `tests/qa/**`, `project/reports/ARB-047-055.md`, `project/evidence/ARB-047-055/**`, and `docs/qa/**`. No production code fixes, no live trading, no deployment, no real 24h run, no paid services. Any production defect is reported and handed back to Agent 0 for separate remediation; P07 must not patch production.

**Critical gates still open:** independently trusted route-specific gas for both DEX directions; live KuCoin public WS; provider quota/continuity over 24h; physical power-loss validation; deployed systemd behavior; independent durable backup (owner deferred). Public-data simulated fills remain blocked while route gas is unknown. Test-only HMAC fixture verification is never runtime evidence.

**Recovery:** CP-001 through CP-008, project/backups.md, PRs #1, #4, #6, #8, #10, #12, #13 and exact accepted merge SHAs. No bot process, 24h experiment or in-flight real trade.
