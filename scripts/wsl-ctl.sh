#!/usr/bin/env bash
set -euo pipefail
root="$HOME/.local/share/arbitrage"
release="$root/app/current"
data="$root/data"
logs="$root/logs"
command=${1:-help}
if (($#)); then shift; fi
if [[ $command == help || $command == --help ]]; then
    printf '%s\n' 'Usage: ./arbitrage-ctl start|resume|recover [--log-level INFO|DEBUG]' \
        '       ./arbitrage-ctl logs|status|pause|stop|emergency-stop|report|help'
    exit 0
fi
(( EUID != 0 )) || { echo 'Run ./arbitrage-ctl as the normal Linux user.' >&2; exit 1; }
case "$command" in
    start|resume|recover)
        level=INFO
        if (($#)); then
            [[ $# == 2 && $1 == --log-level && ( $2 == INFO || $2 == DEBUG ) ]] ||
                { echo 'Expected --log-level INFO or DEBUG.' >&2; exit 2; }
            level=$2
        fi
        umask 077
        exec 8>"$root/.foreground.lock"
        flock -n 8 || { echo 'Another foreground runtime or installer is active.' >&2; exit 1; }
        export ARBITRAGE_LOG_LEVEL="$level" ARBITRAGE_LOG_DIR="$logs" PYTHONUTF8=1
        exec "$release/venv/bin/python" "$release/deploy/managed.py" "$data" "$command"
        ;;
    logs|status|pause|stop|emergency-stop|report)
        (($# == 0)) || { echo 'This command takes no arguments.' >&2; exit 2; }
        case "$command" in
            logs)
                [[ -f $logs/latest.log ]] || { echo 'No console log has been created.' >&2; exit 1; }
                exec tail -n 100 -F -- "$logs/latest.log"
                ;;
            report) exec "$release/venv/bin/python" "$release/deploy/managed.py" "$data" report ;;
            *) exec "$release/venv/bin/arbitrage" --data-dir "$data" "$command" ;;
        esac
        ;;
    *) echo 'Unknown command. Use ./arbitrage-ctl help.' >&2; exit 2 ;;
esac
