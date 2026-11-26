# Owner-authorized Linux simulation launch

P01–P07 accepted; P08 preparation accepted and merged at fca1a7c657b083c3698693b947cd2e788a7bceab. Owner removed P09 from final scope. Host-specific checks remain unverified. Owner will run the installer personally on WSL first, VPS if needed. No remote access or live trading authorized.

Use Ubuntu 24.04 or another Linux with Python 3.12, Git and venv available. Use Linux home storage, not /mnt/c. Run as a regular user. Installer uses apt/sudo only if prerequisites are missing; other distributions need their own package manager. It pins the accepted runtime, installs a private virtual environment, runs disposable readiness smoke and initializes private state. No systemd service is installed. No private keys or authenticated trading APIs. Existing state is preserved by init; stop any running process before reinstalling.

Run installer from the reviewed repository revision. Then:

```sh
~/.local/bin/arb start
~/.local/bin/arb status
watch -n 5 ~/.local/bin/arb status
tail -F ~/.local/share/arbitrage/logs/console.log
~/.local/bin/arb stop
~/.local/bin/arb report
```

Console output may be sparse; structured events and counters are in status and report.xlsx in the state directory. Console log is appended, not rotated automatically. Stop with Ctrl+C or stop from another terminal. WSL shutdown or closing the foreground terminal can interrupt operation; use recover after an unexpected exit, resume after pause. Existing session deadline remains 24 hours; this is not a mandatory P09 experiment. Each host has separate virtual balances and state. No synchronization is implied. Gas-verified admission requires a validated public estimate sender; default unset means analysis only. Do not enter a private key. Provider access, actual host execution and independent durable backup remain unverified.
