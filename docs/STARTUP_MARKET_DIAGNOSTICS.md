# Startup market diagnostics and safe upgrade

The owner observed startup blocked after the startup retry sequence, with the Hedera Mirror transport marked invalid. Those records did not include the HTTP status, failed operation, or block validation details. The precise live cause remains unresolved.

## Upgrade an existing WSL installation

Run this from the Linux checkout used for installation. A blocked startup can retain running session intent after releasing the foreground lock. Record a stop explicitly before upgrading. If status shows an emergency latch or unresolved accepted work, follow the existing lifecycle and manual-review rules.

```sh
cd "$HOME/Arbitrage"
./arbitrage-ctl status
./arbitrage-ctl stop
./arbitrage-ctl status
```

Confirm that `foreground_owner_present` is false, the session is stopped, and `unsettled` is null before continuing. Back up the private state while the foreground is closed, upgrade from the published branch, and start a fresh simulation with diagnostic logging.

```sh
umask 077
tar -C "$HOME/.local/share/arbitrage" -czf "$HOME/arbitrage-state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" data
git pull --ff-only origin fix/srg-clm-rc2
bash scripts/install-wsl.sh --upgrade
./arbitrage-ctl start --log-level DEBUG
```

Use a second shell in the checkout to inspect the status and follow the logs. The current console file is under `$HOME/.local/share/arbitrage/logs/`.

```sh
./arbitrage-ctl status
./arbitrage-ctl logs
```

## Upgrade an existing Ubuntu Server installation

Run from the server checkout. Stop through the application so its intent is persisted before the service exits.

```sh
cd "$HOME/Arbitrage"
sudo arbitrage-ctl status
sudo arbitrage-ctl stop
sudo arbitrage-ctl status
systemctl show --property=ActiveState --value arbitrage.service
```

Confirm the service is inactive or failed, the foreground lock is released, and `unsettled` is null. Then take the cold backup and install the new release.

```sh
sudo sh -c 'umask 077; tar -C /var/lib -czf "/root/arbitrage-state-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" arbitrage'
git pull --ff-only origin fix/srg-clm-rc2
sudo bash scripts/install-server.sh --upgrade
sudo arbitrage-ctl start --log-level DEBUG
sudo arbitrage-ctl status
sudo arbitrage-ctl logs
```

Both installers preserve the existing private configuration, SQLite database and sidecar files, virtual balances, and experiment history. The installer changes the selected application release. Starting explicitly authorizes a new simulation session.

## Read the result

- `session_intent` is the stored lifecycle intent. `runtime_state` describes the foreground state. After a blocked startup exits, `runtime_state` is `stopped` even if `session_intent` is `running`. `foreground_owner_present` reports the lock state.
- Read `readiness.market_failures` and the per-provider `readiness.checks` first. `health.recent_events` retains recent structured records, including preceding attempts. `health.rest` reports transport status. A successful HTTP request can still fail schema, provenance, or freshness verification.
- `mirror_indexing_delay` means an exact-block request returned HTTP 404 and a separate indexed-head request showed the Mirror behind the requested RPC block. `block_not_found` means the exact block was absent even though the Mirror reported reaching that height. If the indexed-head probe itself fails, the record includes that failure and the original lookup's 404.
- `invalid_provider_response` identifies a malformed response and its field when available. Block number, hash prefix, and timestamp disagreements have separate categories. `stale_snapshot` records signed source and receipt ages in milliseconds. Timeouts, throttling, network errors, and HTTP failures have separate categories.
- DEBUG provides the available HTTP status, expected and observed block heights, timestamp ages or differences, hash classification, and field names. Provider response values and raw error messages are excluded. Share those diagnostic fields and the readiness outcomes if the next startup blocks.
