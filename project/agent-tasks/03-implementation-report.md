# Task 03 implementation report

Task 03 builds on `39eea1d8d98e61911f04d890c1c22fcd2795c85b` on `fix/srg-clm-rc2`. Both targets install the same committed package and use the same simulation configuration. The runtime gas model, financial calculations, balances, and console renderer are preserved.

## Installation paths

- Ubuntu Server uses `sudo bash scripts/install-server.sh --start` from the checkout. Versioned releases live under `/opt/arbitrage/`. The service runs under the dedicated `arbitrage` account with private state at `/var/lib/arbitrage/`. The systemd unit attaches to explicitly prepared start or resume intent, or to a recoverable prior session. Abnormal restarts are bounded. The installer leaves boot enablement and host journal retention to the owner.
- WSL uses `bash scripts/install-wsl.sh --start` from the checkout. It creates `./arbitrage-ctl` and uses the normal Linux user. Runtime state and console logs are private under `$HOME/.local/share/arbitrage/`. The same foreground CLI handles signals and returns its original result. Log cleanup covers only timestamped console files older than 14 days or outside the newest 30 files.

The installers preserve existing configuration and SQLite files and require an explicit inactive upgrade for a different revision. The installed controls support start, logs, status, pause, resume, stop, emergency-stop, recover, report, and help. INFO and DEBUG are available for the foreground commands. Excel export reports the created path. A safe normal expiry attempts a final export while preserving the foreground exit status.

## Offline structural verification

Bash syntax, Python 3.12 compilation and imports, package installation and dependency checks, CLI help and invalid arguments, and configuration loading passed. Disposable SQLite initialization, private modes and ownership, state preservation on reinitialization checks, idle recovery, and workbook export passed. Stubbed service checks covered explicit activation, original deadline preservation through resume and recovery, refusal of active and emergency-latched operations, and a recoverable service-launch failure. WSL wrapper checks covered its private paths, lock, DEBUG selection, signal and exit propagation, console copying and retention, and report path and failure behavior.

The unit passed static `systemd-analyze --user --generators=no verify` with disposable executable and state paths. System-mode verification could not create its runtime directory in this environment. Ubuntu Server and WSL 2 were not provisioned or run for this task. Target APT installation, actual systemd service and journald operation, and Windows/WSL host behavior remain unverified. No live market providers were contacted and no service was activated.
