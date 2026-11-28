#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source "$source_dir/scripts/install-common.sh"
parse_install_args "$@"
require_ubuntu
(( EUID == 0 )) || die "Run the server installer as root."
[[ $(cat /proc/1/comm) == systemd ]] || die "The server requires systemd as PID 1."

base=/opt/arbitrage
data=/var/lib/arbitrage
unit=/etc/systemd/system/arbitrage.service
launcher=/usr/local/bin/arbitrage-ctl
unit_source=deploy/templates/arbitrage.service.example

umask 022
check_layout
exec 9>"$base/.manage.lock"
flock -n 9 || die "Another installation or service command holds the management lock."
inspect_release
repair_unit=0
if [[ -e $unit || -L $unit ]]; then
    [[ -n $current && -f $unit && ! -L $unit ]] || die "An unmanaged systemd unit already exists."
    if ! cmp -s "$current/$unit_source" "$unit"; then
        (( upgrade )) || die "The installed service unit differs. Inspect it before retrying with --upgrade."
        for previous_unit in "$base"/releases/*/"$unit_source"; do
            if cmp -s "$previous_unit" "$unit"; then
                repair_unit=1
                break
            fi
        done
        (( repair_unit )) || die "The installed service unit has local changes."
    fi
fi
if [[ -n $current && ( -z $release || $repair_unit == 1 ) ]]; then
    service_state=$(systemctl show --property=ActiveState --value "$unit")
    [[ $service_state == inactive || $service_state == failed ]] ||
        die "Wait for the inactive service before upgrading."
    runuser -u arbitrage -- "$current/venv/bin/arbitrage" --data-dir "$data" status | require_idle_state
fi
if [[ -e $launcher || -L $launcher ]]; then
    [[ -L $launcher && $(readlink -- "$launcher") == "$base/current/scripts/server-ctl.sh" ]] ||
        die "An unmanaged arbitrage-ctl already exists."
fi

if [[ -z $release ]]; then
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates git python3.12 python3.12-venv
fi
if ! getent passwd arbitrage >/dev/null; then
    useradd --system --user-group --home-dir "$data" --shell /usr/sbin/nologin arbitrage
fi
[[ $(id -u arbitrage) != 0 && $(id -Gn arbitrage) == arbitrage &&
   $(getent passwd arbitrage | cut -d: -f6-7) == "$data:/usr/sbin/nologin" ]] ||
    die "The existing arbitrage account is not a dedicated unprivileged service account."

private_dir "$data" "$(id -u arbitrage)" "$(id -g arbitrage)"
reject_git_state "$data"
build_release
initialize_data "$release/venv/bin/arbitrage" runuser -u arbitrage --
publish_release

if [[ ! -e $unit ]] || ! cmp -s "$release/$unit_source" "$unit"; then
    unit_tmp=$(mktemp /etc/systemd/system/.arbitrage.XXXXXXXX)
    install -m 0644 "$release/$unit_source" "$unit_tmp"
    mv -Tf -- "$unit_tmp" "$unit"
fi
if [[ ! -L $launcher ]]; then
    ln -s -- "$base/current/scripts/server-ctl.sh" "$launcher"
fi
systemctl daemon-reload

printf 'Installed simulation revision %s at %s/current. Private state: %s.\n' "$revision" "$base" "$data"
printf 'Management: arbitrage-ctl help | start | logs | status | report\n'
flock -u 9
exec 9>&-
if (( start )); then
    exec "$launcher" start
fi
