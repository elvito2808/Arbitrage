# P08 deployment readiness and transfer

The candidate is for Linux simulation. P08 authorizes preparation and disposable
checks. Host provisioning, installation or activation of the systemd unit, and
a sustained experiment require separate owner authorization.

| Assignment | Artifact or check |
| --- | --- |
| ARB-056 Transfer guide | Versioned source, package environment, configuration and private state instructions below |
| ARB-057 Host readiness | Read-only host checklist below and recorded isolated environment results |
| ARB-058 Service readiness | Static validation of the inert unit in `deploy/templates/` |
| ARB-059 Smoke and recovery | Installed CLI smoke plus lifecycle, monitoring, recovery and ledger regressions |
| ARB-060 Release checkpoint | Versioned P08 report, verification evidence, draft PR and independent review |

## Transfer and host checklist

1. Record the approved Git SHA, intended release path, Python 3.12 patch version
   and dependency versions. Build from that revision in an isolated virtual
   environment. Run `python -m pip install '.[test]'` and `python -m pip check`.
2. Confirm Linux, a supported systemd, a dedicated unprivileged service account,
   a private state directory on a local filesystem, SQLite WAL and advisory
   locks, free space for SQLite and XLSX output, and synchronized UTC time.
   Verify permissions on every parent directory and that the approved service
   account can read the release and write its own state.
3. Confirm that the actual host can directly reach the approved public HTTPS
   and WSS providers. The production runtime uses direct transport. Evidence
   from a proxy-assisted probe is specific to that configuration. No private
   exchange credentials, wallet keys or order permissions are required.
4. Keep the release, private TOML, runtime DB and reports separate. Keep
   configuration in simulation mode. Transfer existing state only via a
   consistent private backup, using SQLite's backup API or a cold copy after
   all connections close. Verify the backup by restoring into a disposable
   directory and checking integrity and ledger reconciliation before use.
5. Verify there is no competing foreground owner. Review persisted session
   intent, the emergency latch, execution status and unresolved holds before
   any approved service activation. The unit invokes `recover` and never
   creates a fresh session on service startup.

## Verification without deployment

Run the installed entry point against disposable private SQLite state.

```sh
python deploy/checks/readiness_smoke.py \
  --executable /path/to/venv/bin/arbitrage --config config/simulation.toml
python -m pytest -q -rs -p no:cacheprovider
```

The standalone check initializes, reads status, invokes idle recovery and
exports a workbook. It verifies 0700/0600 permissions, integrity and zero
financial side effects. The full regression suite exercises active session
recovery, cancellation, stopped and latched behavior, source transitions,
quote expiry, private reporting and ledger reconciliation with controlled
transports and disposable state.

For static systemd verification, copy the example to a disposable directory
as `arbitrage.service`. Substitute only the `ExecStart` executable path
with the installed test executable and run `systemd-analyze verify` on the
copy. This validates syntax and executable resolution. Host access, service
identity, sandboxing, restart limits and journal delivery under PID 1 require
a separately authorized target-host check.

## Monitoring and recovery

Use `status` to inspect session intent, owner, workers, active source, WS
last-valid receipt, snapshot age, retries, recent events, and execution status.
An idle state after `recover` is expected when there is no session. Treat
unavailable or stale data as a rejected cycle and compare its reason with the
provider health entry. Use `report` for private reconciliation. Do not include
raw workbook or state files in shared evidence.

On an unexpected exit, inspect durable state and recover only under existing
intent and the exclusive owner lock. Honor an emergency latch and preserve
pending holds for review. For rollback after any separately authorized host
operation, stop the process, preserve the current state for diagnosis, and
restore a matching version and private backup into a verified state path.
Confirm integrity and reconciliation before resuming. See
[operations](operations.md) for lifecycle and migration details.
