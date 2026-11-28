#!/usr/bin/env bash
# Sourced by both installers.
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

parse_install_args() {
    start=0 upgrade=0
    while (($#)); do
        case "$1" in
            --start) start=1 ;;
            --upgrade) upgrade=1 ;;
            -h|--help) printf 'Usage: bash %s [--start] [--upgrade]\n' "$0"; exit 0 ;;
            *) die "Unknown argument: $1" ;;
        esac
        shift
    done
}

require_ubuntu() {
    . /etc/os-release
    [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || die "Ubuntu 24.04 is required."
}

private_dir() {
    local directory=$1 uid=$2 gid=$3
    if [[ ! -e $directory && ! -L $directory ]]; then
        install -d -m 0700 -o "$uid" -g "$gid" "$directory"
    fi
    [[ ! -L $directory && -d $directory && $(stat -c '%a:%u:%g' "$directory") == "700:$uid:$gid" ]] ||
        die "Expected a private 0700 directory owned by the runtime user: $directory"
}

reject_git_state() {
    local directory=$1
    while [[ $directory != / ]]; do
        [[ ! -e $directory/.git && ! -L $directory/.git ]] ||
            die "Runtime data must be outside Git repositories."
        directory=$(dirname -- "$directory")
    done
}

check_layout() {
    local path mode
    for path in "$base" "$base/releases"; do
        [[ ! -L $path && ( ! -e $path || -d $path ) ]] || die "Unexpected installation path: $path"
        mkdir -p -- "$path"
        mode=$(stat -c %a "$path")
        [[ $(stat -c %u "$path") == "$EUID" ]] && (( (8#$mode & 022) == 0 )) ||
            die "Installation directory must be owned by the installer and protected from other writers: $path"
    done
}

inspect_release() {
    revision=$(git -C "$source_dir" rev-parse --verify HEAD)
    current=
    if [[ -e $base/current || -L $base/current ]]; then
        [[ -L $base/current ]] || die "Unexpected current installation at $base/current"
        current=$(realpath -e -- "$base/current") || die "Current installation link is broken."
        [[ $(dirname -- "$current") == "$base/releases" && -f $current/.revision ]] ||
            die "Current installation is not managed by this installer."
    fi
    release=$current
    if [[ -n $current && $(<"$current/.revision") == "$revision" ]]; then
        return
    fi
    if [[ -n $current && $upgrade == 0 ]]; then
        die "An installation already exists. Stop it safely and rerun with --upgrade."
    fi
    release=
}

cleanup_staging() {
    if [[ -n ${staging:-} && $(readlink -- "$base/current" 2>/dev/null || true) != "$staging" ]]; then
        rm -rf -- "$staging"
    fi
}

build_release() {
    [[ -z $release ]] || return 0
    umask 022
    staging=$(mktemp -d "$base/releases/${revision:0:12}.XXXXXXXX")
    trap cleanup_staging EXIT
    chmod 0755 "$staging"
    git -C "$source_dir" archive "$revision" | tar -x -C "$staging"
    python3.12 -m venv "$staging/venv"
    "$staging/venv/bin/python" -m pip install --no-cache-dir "$staging"
    "$staging/venv/bin/python" -m pip check
    printf '%s\n' "$revision" > "$staging/.revision"
    release=$staging
}

publish_release() {
    [[ $release != "$current" ]] || return 0
    ln -s -- "$release" "$release/.current"
    mv -Tf -- "$release/.current" "$base/current"
    staging=
}

initialize_data() {
    local executable=$1
    shift
    if [[ -e $data/state.sqlite3 || -L $data/state.sqlite3 || -e $data/simulation.toml || -L $data/simulation.toml ]]; then
        [[ -f $data/state.sqlite3 && -f $data/simulation.toml ]] ||
            die "Existing state is incomplete. Restore the private configuration and database before retrying."
        "$@" "$executable" --data-dir "$data" status >/dev/null
    else
        "$@" "$executable" --data-dir "$data" init --config "$release/config/simulation.toml"
    fi
}

require_idle_state() {
    python3.12 -c '
import json, sys
status = json.load(sys.stdin)
session = status["session"]
if (status["foreground_owner_present"] or status["unsettled"] is not None
        or session and session["state"] in ("running", "paused")):
    sys.exit("Stop the session and resolve any accepted work before upgrading.")
'
}
