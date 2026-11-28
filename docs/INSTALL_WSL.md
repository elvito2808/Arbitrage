# WSL 2 Ubuntu 24.04 installation

From Windows PowerShell, install Ubuntu 24.04 and check that it runs under WSL version 2.

```powershell
wsl --install -d Ubuntu-24.04
wsl --list --verbose
```

If the distribution shows version 1, run `wsl --set-version Ubuntu-24.04 2`. Complete Ubuntu's first-run setup and create a normal Linux user with sudo access. Use the distribution's standard WSL 2 kernel and Linux home filesystem.

## Install and start

Open Ubuntu from PowerShell with `wsl -d Ubuntu-24.04`, then run these commands in the Linux shell.

```sh
sudo apt-get update
sudo apt-get install -y git ca-certificates
cd "$HOME"
git clone --branch fix/srg-clm-rc2 --single-branch https://github.com/elvito2808/Arbitrage.git
cd Arbitrage
bash scripts/install-wsl.sh --start
```

For a private repository, authenticate as the Linux user through GitHub CLI if available or configure a GitHub SSH key and clone from `git@github.com:elvito2808/Arbitrage.git`. Use a Git credential store for HTTPS authentication. The simulator uses public data and needs no market or wallet credentials.

Run `bash scripts/install-wsl.sh` for installation without launch. The installer uses sudo for missing Ubuntu packages. The simulator and the checkout's generated `./arbitrage-ctl` run as the Linux user, in the foreground, without systemd. From PowerShell, replace `LINUX_USER` with that user's name and launch the installed application directly.

```powershell
wsl.exe -d Ubuntu-24.04 --cd /home/LINUX_USER/Arbitrage ./arbitrage-ctl start --log-level INFO
```

Windows shutdown or sleep, closing the foreground terminal, and WSL termination can interrupt the experiment. Leave the foreground terminal open. Open a second Ubuntu shell for controls. Use recovery after unexpected interruption when the saved session still has running intent. Session expiry is based on the original stored deadline.

## Controls, logs and workbook

In a second shell, from the checkout, run the command appropriate to the session's state.

```sh
./arbitrage-ctl logs
./arbitrage-ctl status
./arbitrage-ctl pause
./arbitrage-ctl resume --log-level DEBUG
./arbitrage-ctl stop
./arbitrage-ctl emergency-stop
./arbitrage-ctl recover --log-level INFO
./arbitrage-ctl report
./arbitrage-ctl help
```

Application releases are under `$HOME/.local/share/arbitrage/app/`. The private state is under `$HOME/.local/share/arbitrage/data/` and the workbook is `$HOME/.local/share/arbitrage/data/report.xlsx`. Logs stream to your terminal and to `$HOME/.local/share/arbitrage/logs/console-*.log`. The wrapper follows `latest.log`. Each foreground launch removes only timestamped console files older than 14 days or outside the newest 30 files, including the new file. Copy logs you need before future launches.

Use Windows Explorer to copy completed logs and workbook files to your computer. From Ubuntu, these commands open their locations.

```sh
(cd "$HOME/.local/share/arbitrage/logs" && explorer.exe .)
(cd "$HOME/.local/share/arbitrage/data" && explorer.exe .)
```

A safe normal expiry attempts the workbook export automatically. The explicit report command prints its complete path after export. Keep configuration and the database on the Ubuntu filesystem. Copy only exported artifacts to Windows unless making a deliberate, private offline backup.

## Configuration, updates and common errors

Pause or stop and wait for the foreground process to exit. Keep the private TOML at mode 0600. Validate edited configuration before resuming.

```sh
nano "$HOME/.local/share/arbitrage/data/simulation.toml"
"$HOME/.local/share/arbitrage/app/current/venv/bin/python" -c 'import sys; from arbitrage.config import load_config; load_config(sys.argv[1])' "$HOME/.local/share/arbitrage/data/simulation.toml"
```

Confirm the session is stopped or expired and has no unsettled execution before upgrading. Take a private cold backup with the runtime closed.

```sh
./arbitrage-ctl status
umask 077
tar -C "$HOME/.local/share/arbitrage" -czf "$HOME/arbitrage-state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" data
git pull --ff-only origin fix/srg-clm-rc2
git rev-parse HEAD
bash scripts/install-wsl.sh --upgrade
./arbitrage-ctl start
```

The installer builds the checked-out Git commit and keeps older releases. A venv error usually means Ubuntu's `python3.12-venv` is missing. For a filesystem error, check that `HOME` and the checkout are on the distribution's Linux filesystem. A foreground-lock error means a runtime or installer still holds the lock. Check status and let it finish. An incomplete state error requires restoring the private config and database. An emergency latch or unresolved execution requires review. See [management commands](COMMANDS.md).

For a blocked market startup and a diagnostic upgrade, see [startup market diagnostics](STARTUP_MARKET_DIAGNOSTICS.md).
