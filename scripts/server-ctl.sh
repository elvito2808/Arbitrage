#!/usr/bin/env bash
set -euo pipefail
base=/opt/arbitrage
data=/var/lib/arbitrage
unit=arbitrage.service
command=${1:-help}
if (($#)); then shift; fi
if [[ $command == help || $command == --help ]]; then
    printf '%s\n' 'Usage: arbitrage-ctl start|resume|recover [--log-level INFO|DEBUG]' \
        '       arbitrage-ctl logs|status|pause|stop|emergency-stop|report|help'
    exit 0
fi
(( EUID == 0 )) || { echo 'Run arbitrage-ctl as root or with sudo.' >&2; exit 1; }

release="$base/current"
cli=(runuser -u arbitrage -- "$release/venv/bin/arbitrage" --data-dir "$data")
case "$command" in
    start|resume|recover)
        level=INFO
        if (($#)); then
            [[ $# == 2 && $1 == --log-level && ( $2 == INFO || $2 == DEBUG ) ]] ||
                { echo 'Expected --log-level INFO or DEBUG.' >&2; exit 2; }
            level=$2
        fi
        exec 9>"$base/.manage.lock"
        flock -n 9 || { echo 'Another management operation is in progress.' >&2; exit 1; }
        state=$(systemctl show --property=ActiveState --value "$unit")
        [[ $state == inactive || $state == failed ]] ||
            { echo 'The service is active or changing state. Check status and logs.' >&2; exit 1; }
        if [[ $command != recover ]]; then
            "${cli[@]}" "$command" --prepare-only
        else
            # Direct service attachment may drain accepted expired work, but may
            # not attach to a blocked, latched, or inactive session.
            "${cli[@]}" status | "$release/venv/bin/python" -c '
import json, sys
status = json.load(sys.stdin)
session, readiness = status["session"] or {}, status["readiness"] or {}
intent = session.get("state")
if (status["foreground_owner_present"] or status["emergency_latched"]
        or (intent == "running" and readiness.get("state") == "STARTUP_BLOCKED")
        or (intent != "running" and not (intent == "expired" and status["execution_status"] == "active"))):
    sys.exit("No recoverable simulation session. Check status.")
'
        fi
        umask 077
        level_file=$(mktemp "$base/.service-env.XXXXXXXX")
        trap 'rm -f -- "$level_file"' EXIT
        printf 'ARBITRAGE_LOG_LEVEL=%s\n' "$level" > "$level_file"
        mv -Tf -- "$level_file" "$base/service.env"
        if [[ $state == failed ]]; then
            systemctl reset-failed "$unit"
        fi
        if ! systemctl start "$unit"; then
            echo 'Service launch failed. Check status and logs before using recover.' >&2
            exit 1
        fi
        printf 'Service launch requested. Check arbitrage-ctl status and arbitrage-ctl logs.\n'
        ;;
    logs|status|pause|stop|emergency-stop|report)
        (($# == 0)) || { echo 'This command takes no arguments.' >&2; exit 2; }
        case "$command" in
            logs) exec journalctl --unit "$unit" --lines=100 --follow ;;
            report) exec runuser -u arbitrage -- "$release/venv/bin/python" "$release/deploy/managed.py" "$data" report ;;
            *) exec "${cli[@]}" "$command" ;;
        esac
        ;;
    *) echo 'Unknown command. Use arbitrage-ctl help.' >&2; exit 2 ;;
esac
