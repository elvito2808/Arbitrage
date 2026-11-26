# ARB-RM-001 — 1.0.0 APPROVED
Approval: decisions.md. Target MVP v0.1.0. Sequential gates; no dependent implementation until feasibility accepted. 20h planning estimate plus 24h experiment, not guaranteed.
P00 ARB-001–005 Agent 0/1: governance, requirements, roadmap, roles/tasks, checkpoint/backup. Accept durable records and recovery plan.
P01 ARB-006–011 Agent 1: RPC, pool/token identity, ABI, KuCoin REST/WS, gas/latency/rate limits, evidence. Accept verified free public data; stop on blocker.
P02 ARB-012–017 Agent 1: Python structure, config, models, SQLite, logging, interfaces. Accept local install and persistence.
P03 ARB-018–025 Agent 2: connections, retry/cache, DEX adapter, exact-output buy, exact-input sell, gas, CEX adapter and pricing. Accept correct 1000 HBAR quotes including liquidity/costs.
P04 ARB-026–031 Agent 2: coordinator, worker, two-direction profit, validation, risk, deduplication. Accept independent opportunity detection.
P05 ARB-032–039 Agent 2: balances, atomic reservations, DEX simulation, delay, CEX simulation, partial/failure, ledger, recovery. Accept no real orders and consistent persistent balances.
P06 ARB-040–046 Agent 1: lifecycle, worker lifecycle, CLI, systemd, monitoring, timer, Excel. Accept restart and report.
P07 ARB-047–055 Agent 3: independent review, unit/integration/failure/concurrency/crash/isolation/E2E tests, acceptance report. Accept no critical defects.
P08 ARB-056–060 Agent 3/Owner: transfer guide, server check, systemd, smoke test, release checkpoint; separate deployment approval.
P09 ARB-061–065 Owner/Agent 0: start, record 24h, stop new trades, Excel, evaluation. Owner decides economic outcome.
Decisions DEC-001–018: modular monolith; no live; one of three workers; explicit asset mapping; parity assumption; configurable CEX fee; no double-counting; persistence; initial requested price; separate simulated fill; snapshot dedup; backoff; 10s TTL; no compensation; 24h stop; persistent start; private operational data outside public repo; no cumulative-loss auto-stop. See requirements.md. Risks: free limits, quote validity, non-atomic legs, simulated liquidity, stablecoin divergence. All acceptance criteria pending verification.