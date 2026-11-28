"""Packaging entry point for foreground logging and final reports."""

import os
import re
import stat
import sys
import tempfile
from contextlib import redirect_stderr
from datetime import UTC, datetime
from pathlib import Path

from arbitrage.cli import main as cli, private_directory
from arbitrage.runtime import Runtime


class ConsoleCopy:
    def __init__(self, terminal, log):
        self.terminal = terminal
        self.log = log
        self.failed = False

    def write(self, text):
        if not self.failed:
            try:
                self.log.write(text)
            except OSError:
                self.failed = True
                self.terminal.write("Console file logging failed. Terminal output continues.\n")
        return self.terminal.write(text)

    def flush(self):
        if not self.failed:
            try:
                self.log.flush()
            except OSError:
                self.failed = True
                self.terminal.write("Console file logging failed. Terminal output continues.\n")
        self.terminal.flush()


def report(data):
    result = cli(["--data-dir", str(data), "report"])
    if result:
        return result
    workbook = data / "report.xlsx"
    if not workbook.is_file():
        print("The report command did not produce a workbook.", file=sys.stderr)
        return 1
    print(f"Report location: {workbook}", flush=True)
    return 0


def run_foreground(data, action, level):
    result = cli(["--data-dir", str(data), action, "--log-level", level])
    if result == 0:
        # Hold the normal owner lock across the final state check and export.
        # An intervening start is permitted to win the lock.
        try:
            runtime = Runtime(data)
            with runtime.owner():
                status = runtime.status()
                if (status["session"] and status["session"]["state"] == "expired"
                        and status["unsettled"] is None and not status["emergency_latched"]):
                    if report(data):
                        print("Automatic report generation failed. Use the report command.", file=sys.stderr)
        except Exception:
            print("Automatic report unavailable for this exit. Use the report command.", file=sys.stderr)
    return result


def run_logged(data, action, level, directory):
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("console log directory must be private")
    # Keep at most 29 old console logs, and expire any older than 14 days.
    cutoff = datetime.now(UTC).timestamp() - 14 * 86400
    existing = [
        (entry.stat().st_mtime, entry) for entry in directory.iterdir()
        if re.fullmatch(r"console-\d{8}T\d{6}Z-[a-z0-9_]+\.log", entry.name)
        and not entry.is_symlink() and entry.is_file()
    ]
    for index, (modified, entry) in enumerate(sorted(existing, reverse=True)):
        if index >= 29 or modified < cutoff:
            entry.unlink(missing_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    descriptor, name = tempfile.mkstemp(prefix=f"console-{stamp}-", suffix=".log", dir=directory)
    log = os.fdopen(descriptor, "w", buffering=1, encoding="utf-8")
    try:
        path = Path(name)
        marker = path.with_suffix(".link")
        try:
            marker.symlink_to(path.name)
            marker.replace(directory / "latest.log")
        finally:
            marker.unlink(missing_ok=True)
        print(f"Console log location: {path}", file=sys.stderr, flush=True)
        with redirect_stderr(ConsoleCopy(sys.stderr, log)):
            return run_foreground(data, action, level)
    finally:
        try:
            log.close()
        except OSError:
            print("Could not close the console log cleanly.", file=sys.stderr)


def main():
    if len(sys.argv) != 3 or os.geteuid() == 0:
        print("Run the simulation manager as the unprivileged runtime user.", file=sys.stderr)
        return 1
    os.umask(0o077)
    data = private_directory(sys.argv[1], False)
    action = sys.argv[2]
    level = os.environ.get("ARBITRAGE_LOG_LEVEL", "INFO")
    if action == "report":
        return report(data)
    if action not in ("start", "resume", "recover") or level not in ("INFO", "DEBUG"):
        print("Invalid managed simulation command or logging level.", file=sys.stderr)
        return 2
    logs = os.environ.get("ARBITRAGE_LOG_DIR")
    if logs:
        return run_logged(data, action, level, Path(logs))
    return run_foreground(data, action, level)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        print("Could not access the private runtime or console log directory.", file=sys.stderr)
        raise SystemExit(1) from None
