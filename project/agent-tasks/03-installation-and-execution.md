# Agent 0 — Execution Task 03/10
## Prepare One-Command Installation and Simulation Execution

Repository:
https://github.com/elvito2808/Arbitrage

Working branch:
`fix/srg-clm-rc2`

Expected starting commit:
`39eea1d8d98e61911f04d890c1c22fcd2795c85b`

The project owner has authorized repository implementation, commits and publication to this branch.

The owner will personally install, run and evaluate the simulator.

## 1. Objective

Deliver a complete installation and execution package for two environments:

A. Ubuntu 24.04 personal server with root access.

B. Windows WSL 2 running Ubuntu 24.04, using a normal Linux user with sudo available for dependency installation.

Both environments must use the same application implementation and simulation configuration.

Do not modify the completed Gas or Logging implementations except for strictly necessary packaging integration.

## 2. Preserve approved simulation settings

- SIMULATION ONLY.
- Hedera Mainnet public market data.
- SaucerSwap V1 WHBAR-USDC.
- KuCoin HBAR-USDT.
- Trade quantity: 1000 HBAR.
- Duration: 24 hours.
- Worker capacity: 3.
- Maximum active workers: 1.
- Minimum cycle interval: 15 seconds.
- Minimum profit: 0.10 USDT.
- Maximum loss: 0.30 USDT.
- Slippage: 0.5%.
- KuCoin fee: 0.1%.
- BUY gas units: 109478.
- SELL gas units: 109478.
- Gas validation limit: 300000.
- Live RPC gas price.
- Gas budget multiplier: 2.
- Existing approved virtual opening balances.
- English console logging.
- Excel reporting.

Do not introduce wallet requirements, credentials, private APIs or real trading.

## 3. Ubuntu Server installer

Create:

`scripts/install-server.sh`

Support:

`bash scripts/install-server.sh --start`

The installer must:

- Validate Ubuntu 24.04 and root privileges.
- Install required OS packages, Python 3.12 and venv support.
- Create a dedicated unprivileged arbitrage service account.
- Install the application under `/opt/arbitrage/`.
- Create an isolated Python virtual environment.
- Install dependencies from the project package definition.
- Initialize `/var/lib/arbitrage/` with mode 0700 and correct ownership.
- Initialize SQLite and simulation configuration using the existing application CLI.
- Install a production-appropriate systemd unit for simulation.
- Install `/usr/local/bin/arbitrage-ctl`.
- Start the simulation when explicitly invoked with `--start`.
- Print installation results and management commands.
- Never run the application as root.

The installer must be safe to rerun. Never silently overwrite an existing private configuration, reset virtual balances, delete SQLite data or replace an active installation.

Use a safe explicit upgrade procedure when an installation already exists.

## 4. Systemd lifecycle

Adapt the existing service template correctly.

The existing template uses `recover`, which cannot create a new session on a fresh installation.

Ensure:

- Explicit start creates a new authorized simulation session.
- Resume continues a paused session.
- Recover reattaches to an interrupted session.
- Existing sessions are never silently replaced.
- Unexpected exits use bounded recovery where appropriate.
- No automatic restart of completed, paused, stopped, emergency-latched or startup-blocked sessions.
- The 24-hour deadline survives process restart.
- Only one foreground runtime owns the private database.
- Service control preserves the application's lifecycle semantics.
- The service continues after SSH disconnect.
- stdout and stderr are captured by journald.
- Graceful shutdown preserves accepted virtual work and recovery records.

Do not enable unattended new simulation sessions on boot.

Use the application's actual CLI behavior rather than assuming that systemctl start alone creates a session.

## 5. WSL installer

Create:

`scripts/install-wsl.sh`

Support:

`bash scripts/install-wsl.sh --start`

The installer must:

- Validate a supported WSL 2 Ubuntu environment.
- Verify Python 3.12 availability.
- Install required packages using sudo only where necessary.
- Install the application under `$HOME/.local/share/arbitrage/app/`.
- Create an isolated virtual environment.
- Initialize `$HOME/.local/share/arbitrage/data/` with mode 0700.
- Store runtime data outside Git repositories and Windows-mounted filesystems.
- Create `$HOME/.local/share/arbitrage/logs/`.
- Create a convenient `./arbitrage-ctl` launcher in the installation source checkout.
- Start the simulation in the foreground when invoked with `--start`.
- Display live console logs and persist them to private log files.
- Preserve the application's exit code and signal handling.

Do not require systemd in WSL.

Never silently reset existing runtime data.

Document that Windows shutdown, sleep or WSL termination may interrupt the experiment.

## 6. Common management interface

Provide these commands in both environments:

- start
- logs
- status
- pause
- resume
- stop
- emergency-stop
- recover
- report
- help

Server interface:

`arbitrage-ctl COMMAND`

WSL interface:

`./arbitrage-ctl COMMAND`

Support INFO and DEBUG logging.

Ensure commands operate on the correct private runtime directory and respect the existing owner lock.

The server wrapper must execute runtime operations under the dedicated service account.

The WSL wrapper must execute under the normal Linux user.

Never bypass an emergency latch, readiness failure or unresolved execution.

## 7. Logging and reports

Preserve the Task 02 human-readable console output.

Server:

- Use journald for persistent console logs.
- Provide live log viewing.
- Document how to export the complete experiment log.
- Document host journal retention and disk limits without silently changing global host policies.

WSL:

- Save console logs to private timestamped files.
- Provide live log viewing.
- Prevent unbounded accumulation through a documented local retention policy.
- Never delete financial journals or experiment reports as part of log cleanup.

Both environments:

- Provide explicit Excel report generation.
- Print the actual report location.
- Generate a final report automatically after normal session completion when safe.
- Preserve the original runtime exit status if report generation fails.
- Do not claim report success unless the workbook exists.

## 8. Owner documentation

Create:

`docs/INSTALL_SERVER.md`

`docs/INSTALL_WSL.md`

`docs/COMMANDS.md`

Provide exact copy-and-paste commands and explanations for:

- Fresh installation.
- One-command installation and startup.
- Repository authentication if required.
- SSH server access.
- WSL installation and launch from Windows PowerShell.
- Live logs.
- INFO and DEBUG.
- Status.
- Pause and resume.
- Stop and emergency stop.
- Recovery after interruption.
- Excel report generation.
- Copying the report and logs to the owner's computer.
- Safe configuration changes.
- Safe updates without deleting virtual balances or history.
- Common installation errors.

Update README with prominent links to both installation guides.

Use English for code, scripts and console messages.

## 9. Minimal structural verification

Do not run live Hedera, SaucerSwap or KuCoin tests.

Do not activate a real service or start a live market simulation in the Codex environment.

Perform only:

- Bash syntax validation.
- Python imports and compilation.
- Package installation verification.
- CLI help and argument validation.
- Configuration loading.
- Private directory and ownership logic checks.
- Offline SQLite initialization.
- Offline management-wrapper checks using temporary directories and stubbed process/service commands.
- Static systemd unit verification where available.

Do not report a successful Ubuntu or WSL installation unless it was actually performed in that environment.

No independent QA campaign or additional evidence handoff.

## 10. Publication

Save this assignment:

`project/agent-tasks/03-installation-and-execution.md`

Save a concise completion report:

`project/agent-tasks/03-implementation-report.md`

Commit and push the completed implementation to:

`fix/srg-clm-rc2`

Verify the published remote commit.

Do not force-push or modify main.

All scripts, documentation and reports must be accessible directly from GitHub.

## Completion report

Return only:

- Status.
- Published commit SHA.
- Changed files.
- Exact Ubuntu Server installation and startup command.
- Exact WSL installation and startup command.
- Management commands for both environments.
- Actual log and report locations.
- GitHub links to both installation guides.
- Minimal structural verification results.
- Any actual remaining implementation blocker or unverified platform-specific behavior.

SIMULATION ONLY.

No live provider tests, real transactions, wallet operations, remote server access or actual deployment.

Complete both installation paths within this assignment.