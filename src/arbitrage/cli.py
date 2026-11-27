"""English command line for private, foreground public market analysis."""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from stat import S_IMODE, S_ISDIR, S_ISREG


from .config import load_config, parse_config
from .operational_log import Level
from .reporting import export
from .runtime import WORKERS, Runtime, foreground


def private_directory(raw, create):
    path = Path(raw)
    if not path.is_absolute() or path != path.resolve():
        raise ValueError("use an absolute data directory without aliases or symlinks")
    if any((ancestor / ".git").exists() for ancestor in (path, *path.parents)):
        raise ValueError("the data directory must be outside Git repositories")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or S_IMODE(info.st_mode) != 0o700:
        raise ValueError("the data directory must be owned by you with mode 0700")
    for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm",
                 "state.sqlite3-journal", "simulation.toml", "owner.lock", "report.xlsx"):
        member = path / name
        try:
            info = member.lstat()
        except FileNotFoundError:
            continue
        if (not S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or S_IMODE(info.st_mode) & 0o077):
            raise ValueError("runtime files must be private, regular and owned by you")
    if not create and not (path / "state.sqlite3").is_file():
        raise ValueError("initialize the data directory first")
    return path


def parser():
    root = SafeParser(description="Arbitrage public market analysis. Foreground only.")
    root.add_argument("--data-dir", required=True, help="private absolute directory outside the repository")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="initialize private runtime and three stopped workers")
    init.add_argument("--config", required=True, type=Path, help="validated simulation TOML")
    start = commands.add_parser("start", help="start a new analysis session in the foreground")
    start.add_argument("--worker", choices=WORKERS, default=WORKERS[0])
    resume = commands.add_parser("resume", help="resume the paused session in the foreground")
    recover = commands.add_parser("recover", help="reattach in the foreground after untimely process exit")
    for foreground_command in (start, resume, recover):
        foreground_command.add_argument("--log-level", choices=("INFO", "DEBUG"), default="INFO")
    commands.add_parser("pause", help="pause the current session")
    commands.add_parser("stop", help="stop the current session")
    commands.add_parser("emergency-stop", help="latch a persistent emergency stop")
    commands.add_parser("status", help="show session intent, counters and public data health")
    commands.add_parser("report", help="replace the private report.xlsx with a consistent export")
    return root


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Error: invalid arguments. See --help.\n")


def main(argv=None):
    args = parser().parse_args(argv)
    previous = os.umask(0o077)
    try:
        directory = private_directory(args.data_dir, args.command == "init")
        runtime = Runtime(directory)
        config_path = directory / "simulation.toml"
        command = args.command
        if command == "init":
            with runtime.owner():
                supplied = args.config.read_bytes()
                parse_config(supplied)
                if not config_path.exists():
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                            temporary = Path(stream.name)
                            stream.write(supplied)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, config_path)
                    finally:
                        if temporary:
                            temporary.unlink(missing_ok=True)
                runtime.initialize(load_config(config_path))
            print("Runtime initialized.")
        elif command in ("start", "resume", "recover"):
            with runtime.owner():
                config = None if command == "recover" else load_config(config_path)
                activation = runtime.activate(command, getattr(args, "worker", None), config)
                if activation:
                    asyncio.run(foreground(runtime, *activation, log_level=Level(args.log_level.lower())))
                elif command == "resume":
                    raise ValueError("there is no resumable session")
        elif command == "status":
            print(json.dumps(runtime.status(), indent=2))
        elif command == "report":
            export(runtime.storage.path, directory / "report.xlsx")
            print("Private report.xlsx exported.")
        else:
            runtime.control({"pause": "paused", "stop": "stopped", "emergency-stop": "emergency"}[command])
            print("Session intent recorded.")
    except (OSError, ValueError, sqlite3.Error) as error:
        # Only application-owned messages may appear in an error printed by the CLI.
        safe = {
            "invalid simulation configuration", "there is no resumable session", "startup readiness blocked",
            "use an absolute data directory without aliases or symlinks",
            "the data directory must be outside Git repositories",
            "the data directory must be owned by you with mode 0700",
            "runtime files must be private, regular and owned by you",
            "initialize the data directory first", "a foreground worker still owns the runtime",
            "emergency stop is latched", "there is no session", "only a running session can be paused",
            "a session already exists, use pause, resume, recover or stop",
        }
        detail = error.args[0] if isinstance(error, ValueError) and error.args else None
        print(f"Error: {detail if isinstance(detail, str) and detail in safe else 'runtime operation failed'}.",
              file=sys.stderr)
        return 1
    except Exception:
        print("Error: runtime operation failed.", file=sys.stderr)
        return 1
    finally:
        os.umask(previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
