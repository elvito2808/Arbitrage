# Arbitrage

## Official owner-approved baseline — v1.0.0

**Canonical branch: `main` (default).** Start future maintenance from `main`. Historical release, QA and fix branches are not authoritative.

Owner-approved executable revision: [`5b3866318cbfaf32fa1674369be1af3ff906e03a`](https://github.com/elvito2808/Arbitrage/commit/5b3866318cbfaf32fa1674369be1af3ff906e03a). Accepted on 2026-11-28 following successful Ubuntu 24.04 VPS installation, startup readiness, Hedera RPC/Mirror and SaucerSwap verification, KuCoin WebSocket depth, live gas-price calculation and four completed analysis cycles. The owner explicitly accepted the observed behavior. A completed simulated fill and full 24-hour session were not observed in the acceptance run.

Official target: Ubuntu 24.04 VPS, systemd, simulation only. WSL is optional/historical, not the acceptance target. No real orders, signing or transfers. Preserve runtime code and financial defaults unless explicitly requested. Keep private server details and runtime state out of Git.

## Install and run

On a fresh Ubuntu 24.04 VPS as root with Git installed:

```bash
git clone https://github.com/elvito2808/Arbitrage.git
cd Arbitrage
bash scripts/install-server.sh --start
```

[Server installation and upgrades](docs/INSTALL_SERVER.md) · [Management commands](docs/COMMANDS.md) · [Startup diagnostics](docs/STARTUP_MARKET_DIAGNOSTICS.md) · [Optional WSL guide](docs/INSTALL_WSL.md)
