import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory

EXE = Path("/workspace/work/arb-qa-p08-venv/bin/arbitrage")
CONFIG = Path("config/simulation.toml").resolve()


def call(path, *operation):
    return subprocess.run([EXE, "--data-dir", path, *operation], check=True,
                          capture_output=True, text=True, timeout=10).stdout


def read(path):
    return sqlite3.connect(f"{(path / 'state.sqlite3').as_uri()}?mode=ro", uri=True)


def values(db):
    return db.execute(
        "SELECT holding, total_units, reserved_units FROM balances ORDER BY holding"
    ).fetchall()


with TemporaryDirectory() as root:
    base = Path(root)
    state = base / "current"
    restored = base / "restored"
    call(state, "init", "--config", CONFIG)
    with read(state) as source:
        initial_balances = values(source)
    baseline = json.loads(call(state, "status"))
    assert baseline["session"] is None and baseline["mode"] == "simulation"
    call(state, "recover")
    call(state, "report")
    assert json.loads(call(state, "status")) == baseline
    restored.mkdir(mode=0o700)
    shutil.copy2(state / "simulation.toml", restored / "simulation.toml")
    with read(state) as source, sqlite3.connect(restored / "state.sqlite3") as copy:
        assert values(source) == initial_balances
        assert len(initial_balances) == 5 and all(item[2] == 0 for item in initial_balances)
        assert source.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        source.backup(copy)
    (restored / "state.sqlite3").chmod(0o600)
    assert json.loads(call(restored, "status")) == baseline
    call(restored, "report")
    with read(restored) as db:
        assert values(db) == initial_balances
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in ("analysis_cycles", "simulated_trades", "reservations", "ledger", "executions", "simulation_events"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    assert S_IMODE((restored / "report.xlsx").stat().st_mode) == 0o600
print(f"SQLite {sqlite3.sqlite_version}: WAL through source, private SQLite backup and restore, exact balances and no journal delta PASS")
