# Simulation management commands

Use `sudo arbitrage-ctl` on Ubuntu Server. Use `./arbitrage-ctl` from the WSL source checkout as the normal Linux user.

| Command | Meaning |
| --- | --- |
| `start` | Authorize a new 24-hour simulation. Stop a prior running or paused session explicitly before starting another. Startup processes prior accepted virtual work and blocks new analysis if it remains unresolved. |
| `logs` | Follow the server unit's journal or the latest WSL console file. Ctrl+C exits the log viewer. |
| `status` | Show persisted session intent, readiness, workers, execution status, and emergency latch as JSON. |
| `pause` | Persist paused intent and let the foreground exit. Accepted virtual work stays in the journal. |
| `resume` | Continue the paused session with its original deadline. The current private configuration is loaded. |
| `stop` | Persist stopped intent and let the foreground exit. Existing committed balances and journal records remain. |
| `emergency-stop` | Persist the emergency latch and stop progression. This interface has no latch-clearing command. |
| `recover` | Reattach to an interrupted running session using the last applied config. An expired session may drain pending accepted work. Review status and logs if readiness blocked startup. |
| `report` | Export a private Excel workbook from the current SQLite snapshot and print the resulting path. |
| `help` | Show syntax. |

Start, resume, and recover accept `--log-level INFO` or `--log-level DEBUG`. The default is INFO. DEBUG includes structured correlation records. On WSL, Ctrl+C in the **runtime** terminal requests a graceful stop. To keep paused intent for later resume, use the separate `pause` command. On the server, use the application lifecycle commands so SQLite records the intended state before the process exits.

```sh
# Server
sudo arbitrage-ctl start --log-level INFO
sudo arbitrage-ctl logs

# WSL, from the source checkout
./arbitrage-ctl start --log-level INFO
# Use a second shell to follow the file or issue controls.
./arbitrage-ctl logs
```

[A server installation](INSTALL_SERVER.md) · [A WSL installation](INSTALL_WSL.md)
