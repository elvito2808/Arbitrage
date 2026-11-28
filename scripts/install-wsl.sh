#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source "$source_dir/scripts/install-common.sh"
parse_install_args "$@"
require_ubuntu
(( EUID != 0 )) || die "Run the WSL installer as a normal Linux user."
[[ $(uname -r | tr '[:upper:]' '[:lower:]') == *microsoft*wsl2* ]] ||
    die "A supported WSL 2 kernel is required."

linux_home=$(realpath -e -- "$HOME")
[[ $linux_home == "$HOME" && $(stat -c %u "$linux_home") == "$EUID" ]] ||
    die "HOME must be a canonical directory owned by the Linux user."
root="$linux_home/.local/share/arbitrage"
base="$root/app"
data="$root/data"
logs="$root/logs"
launcher="$source_dir/arbitrage-ctl"
ancestor=$root
while [[ ! -e $ancestor ]]; do ancestor=$(dirname -- "$ancestor"); done
[[ $(realpath -m -- "$root") == "$root" && $(stat -f -c %T "$ancestor") == ext2/ext3 ]] ||
    die "Use an Ubuntu Linux filesystem for the application and runtime data."
reject_git_state "$data"
if [[ -e $launcher || -L $launcher ]]; then
    [[ -L $launcher && $(readlink -- "$launcher") == "$base/current/scripts/wsl-ctl.sh" ]] ||
        die "The checkout already contains an unmanaged arbitrage-ctl."
fi

umask 077
private_dir "$root" "$EUID" "$(id -g)"
check_layout
exec 9>"$root/.install.lock"
flock -n 9 || die "Another installer is already running."
exec 8>"$root/.foreground.lock"
flock -n 8 || die "Close the foreground runtime before installing or upgrading."
inspect_release
if [[ -n $current && -z $release ]]; then
    "$current/venv/bin/arbitrage" --data-dir "$data" status | require_idle_state
fi

if ! command -v python3.12 >/dev/null || ! dpkg-query -W -f='${db:Status-Status}\n' python3.12-venv ca-certificates git 2>/dev/null |
        awk '$0 == "installed" { installed++ } END { exit installed != 3 }'; then
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends ca-certificates git python3.12 python3.12-venv
fi
python3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12)'
private_dir "$data" "$EUID" "$(id -g)"
private_dir "$logs" "$EUID" "$(id -g)"
build_release
initialize_data "$release/venv/bin/arbitrage"
publish_release
if [[ ! -L $launcher ]]; then
    ln -s -- "$base/current/scripts/wsl-ctl.sh" "$launcher"
fi

printf 'Installed simulation revision %s. Private state: %s. Console logs: %s.\n' "$revision" "$data" "$logs"
printf 'From this checkout: ./arbitrage-ctl help | start | logs | status | report\n'
flock -u 8
exec 8>&-
flock -u 9
exec 9>&-
if (( start )); then
    exec "$launcher" start
fi
