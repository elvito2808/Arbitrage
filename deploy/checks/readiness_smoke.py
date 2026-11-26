"""Exercise the installed simulator with disposable state and no active session."""

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory
from zipfile import ZipFile


def smoke(executable: Path, config: Path) -> None:
    with TemporaryDirectory(prefix="arb-p08-") as scratch:
        state = Path(scratch) / "state"

        def run(*args: str) -> str:
            completed = subprocess.run(
                [str(executable), "--data-dir", str(state), *args],
                check=True, capture_output=True, text=True, timeout=10,
            )
            return completed.stdout

        run("init", "--config", str(config))
        before = json.loads(run("status"))
        run("recover")
        after = json.loads(run("status"))
        if before != after or after["mode"] != "simulation" or after["session"] is not None:
            raise RuntimeError("idle recovery changed session state")
        if after["analysis_cycles"] or after["simulated_trades"]:
            raise RuntimeError("idle recovery generated work")
        run("report")
        if S_IMODE(state.stat().st_mode) != 0o700:
            raise RuntimeError("state directory permissions")
        for name in ("state.sqlite3", "simulation.toml", "report.xlsx", "owner.lock"):
            if S_IMODE((state / name).stat().st_mode) != 0o600:
                raise RuntimeError(f"private file permissions failed for {name}")
        with ZipFile(state / "report.xlsx") as workbook:
            if workbook.testzip() is not None or "xl/workbook.xml" not in workbook.namelist():
                raise RuntimeError("invalid workbook")
        with sqlite3.connect(f"{(state / 'state.sqlite3').as_uri()}?mode=ro", uri=True) as database:
            if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("SQLite integrity check failed")
            if database.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("SQLite foreign key check failed")
            for table in ("reservations", "ledger", "executions", "simulation_events"):
                if database.execute(f"SELECT COUNT(*) FROM {table}").fetchone() != (0,):
                    raise RuntimeError(f"unexpected entries in {table}")
        print("PASS: installed CLI, idle recovery, private state, workbook, SQLite integrity and zero financial effects")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.executable.resolve(strict=True), args.config.resolve(strict=True))
