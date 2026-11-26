# ARB-056–060 verification record

UTC date 2026-11-26. Accepted main `602dc7e374f6c0bea3d327358996a570432c3754`. The isolated branch started exactly at that SHA with a clean index and no local-only content. Accepted P07 integration is in its history. The same public main SHA was checked again before publishing.

Environment: Linux x86_64, Python 3.12.14, SQLite 3.53.1, systemd-analyze 257.13, UID 1000, PID 1 `tail`. The checks use a test runner rather than the intended deployment host. The installed dependency list is [recorded here](environment-packages.txt).

| Command | Exit | Result |
| --- | ---: | --- |
| `git ls-remote origin refs/heads/main` | 0 | Authorized main matched |
| `python -m venv /workspace/work/arb-p08-venv` | 0 | Separate Python environment |
| `python -m pip -q install '.[test]'` | 0 | Installed package and development dependencies in the virtual environment |
| `python -m pip check` | 0 | No broken requirements |
| `python deploy/checks/readiness_smoke.py --executable /workspace/work/arb-p08-venv/bin/arbitrage --config config/simulation.toml` | 0 | Init/status/idle recovery/report, private files, unchanged opening balances, zero financial rows, valid workbook and SQLite integrity |
| `systemd-analyze --generators=no verify "$unit"` | 1 | System scope could not create `/run/systemd/` |
| `XDG_RUNTIME_DIR="$scratch/run" systemd-analyze --user --generators=no verify "$unit"` | 0 | User-scope parser and installed executable resolution |
| `unshare --user --map-root-user --mount --propagation private sh -c 'mount -t tmpfs tmpfs /run && systemd-analyze --generators=no --man=no verify "$1"' sh "$unit"` | 32 | Sandbox denied isolated tmpfs mount |
| `python -m pytest -q -rs -p no:cacheprovider` | 0 | 258 passed, 0 failed, 3 opt-in public smoke tests skipped in 7.54 seconds |
| `git diff --check 602dc7e..HEAD` | 0 | No whitespace defects |

Each static attempt used a disposable copy named `arbitrage.service` with only the `ExecStart` executable path substituted. No installation or activation was performed. The suite uses controlled transports for active lifecycle, monitoring, restart, cancellation, recovery, and financial isolation.

The bounded opt-in public diagnostic ran separately with
`ARB_PUBLIC_SMOKE=1 timeout 70 python -m pytest -q -rs -p no:cacheprovider tests/test_market_smoke.py`.
It exited 1 in 19.57 seconds. Hedera and KuCoin REST passed, while the KuCoin
WebSocket ticker reached its reconnect limit after timeouts. The bounded retry
`ARB_PUBLIC_SMOKE=1 WSS_PROXY="$HTTPS_PROXY" timeout 30 python -m pytest -q -rs -p no:cacheprovider tests/test_market_smoke.py -k kucoin_ws`
exited 0 with one pass and two deselected in 1.45 seconds. This retry explicitly
used the test environment proxy and does not establish direct runtime egress.
[Initial public smoke](public-smoke.txt) · [WebSocket retry](public-wss-retry.txt)

Agent 3 independently passed the readiness preparation check, including a
consistent SQLite backup and restore in disposable state. See the [independent
report](agent-3-p08.md) and [restore probe](verify_restore.py).
