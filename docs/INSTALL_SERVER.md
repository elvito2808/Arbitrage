# Ubuntu 24.04 server installation

The simulator uses public market data and a virtual SQLite portfolio. Use a dedicated Ubuntu 24.04 server with systemd and Python package repository access. Runtime commands run as the unprivileged `arbitrage` account. The installer needs root for the system packages, service account, and systemd files.

## Install and start

Connect over SSH using your server login. Replace `OWNER` and `SERVER` with your Linux account and host.

```sh
ssh OWNER@SERVER
sudo apt-get update
sudo apt-get install -y git ca-certificates
git clone --branch fix/srg-clm-rc2 --single-branch https://github.com/elvito2808/Arbitrage.git
cd Arbitrage
sudo bash scripts/install-server.sh --start
```

If GitHub asks for authentication, use `gh auth login` if the GitHub CLI is installed, or configure a GitHub SSH key and use `git@github.com:elvito2808/Arbitrage.git` as the clone URL. Clone as your login user before invoking sudo. Keep repository authentication in your Git credential store.

Run `sudo bash scripts/install-server.sh` to install without starting. The explicit `--start` records a new session through the CLI and then asks systemd to attach. Check readiness in the logs. Systemd continues hosting the foreground process when SSH disconnects. The installer performs no boot enablement.

## Operations

```sh
sudo arbitrage-ctl logs
sudo arbitrage-ctl status
sudo arbitrage-ctl pause
sudo arbitrage-ctl resume --log-level DEBUG
sudo arbitrage-ctl stop
sudo arbitrage-ctl emergency-stop
sudo arbitrage-ctl recover --log-level INFO
sudo arbitrage-ctl report
sudo arbitrage-ctl help
```

Use one lifecycle command at a time. After pause or stop, wait for the foreground owner to release the database before invoking resume or start. `recover` attaches to an interrupted running session or drains accepted work at expiry. The stored deadline still applies. Bounded systemd restarts cover abnormal process termination. Startup failures surface in the journal and require inspection. An emergency latch persists across reboot and installation. See the [management guide](COMMANDS.md) for state-specific behavior.

## Logs and workbook

Console output goes to `journalctl -u arbitrage.service`. The workbook is `/var/lib/arbitrage/report.xlsx` after a successful export. An expired session with no unresolved execution gets an automatic final export when the foreground wrapper exits successfully. You can explicitly regenerate it with `sudo arbitrage-ctl report`.

Set the UTC range to cover the whole experiment, including any resume or recovery processes. Inspect disk use and current host retention before starting a long session.

```sh
sudo journalctl --disk-usage
sudo journalctl --list-boots
sudo systemd-analyze cat-config systemd/journald.conf
umask 077
sudo journalctl -u arbitrage.service --utc --since '2026-11-28 08:00:00 UTC' --until '2026-11-29 09:00:00 UTC' --no-pager > "$HOME/arbitrage-console.log"
sudo install -m 0600 -o "$(id -un)" /var/lib/arbitrage/report.xlsx "$HOME/arbitrage-report.xlsx"
```

Persistent journals reside under `/var/log/journal` when the host's journald storage is persistent, or when its automatic mode finds that directory. A volatile journal under `/run/log/journal` can disappear after reboot. Configure persistence and limits through the host's journald policy if required. Review `Storage`, `SystemMaxUse`, and `MaxRetentionSec`. The installer leaves host retention settings alone. Export important logs before the host rotates them.

From your computer, download the private copies created above.

```sh
scp OWNER@SERVER:arbitrage-console.log .
scp OWNER@SERVER:arbitrage-report.xlsx .
```

## Configuration and upgrades

The active private configuration is `/var/lib/arbitrage/simulation.toml`. Pause or stop first. Keep mode 0600 and ownership `arbitrage:arbitrage`. Edit only approved settings. The example below uses `nano`, which must be available on the host.

```sh
sudo runuser -u arbitrage -- sh -c 'umask 077; exec nano /var/lib/arbitrage/simulation.toml'
sudo runuser -u arbitrage -- /opt/arbitrage/current/venv/bin/python -c 'from arbitrage.config import load_config; load_config("/var/lib/arbitrage/simulation.toml")'
```

Resume applies the edited file to the paused session. Recovery uses the last applied settings. Editing opening balances in TOML does not change an existing portfolio.

Stop or finish the experiment, confirm there is no owner or unsettled execution, and take a private cold backup of the full data directory before changing releases. Allow the foreground service to exit before copying SQLite and its sidecar files.

```sh
sudo arbitrage-ctl status
sudo systemctl is-active arbitrage.service
sudo sh -c 'umask 077; tar -C /var/lib -czf "/root/arbitrage-state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" arbitrage'
git pull --ff-only origin fix/srg-clm-rc2
git rev-parse HEAD
sudo bash scripts/install-server.sh --upgrade
sudo arbitrage-ctl start
```

The installer builds the checked-out Git commit. Commit any approved source edits before selecting that revision. A new release is built under `/opt/arbitrage/releases/` before its selection via `/opt/arbitrage/current`. Existing private state and prior releases are kept. For a failed launch after successful session preparation, inspect status and use `recover` if the saved running intent is recoverable. For permission or incomplete-state errors, restore the correct private files from backup before retrying. An existing unmanaged service, launcher, or unexpected account requires review by the server operator.
