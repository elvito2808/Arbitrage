#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $(uname -s) == Linux && $(id -u) != 0 ]] || { echo "Run as a regular Linux user." >&2; exit 1; }
release=fca1a7c657b083c3698693b947cd2e788a7bceab
base="$HOME/.local/share/arbitrage"
state="$base/state"
src="$base/releases/$release"
if ! command -v python3.12 >/dev/null || ! python3.12 -m venv --help >/dev/null || ! command -v git >/dev/null; then
  command -v apt-get >/dev/null && command -v sudo >/dev/null || { echo "Install Git and Python 3.12 with venv using your distribution package manager." >&2; exit 1; }
  sudo apt-get update
  sudo apt-get install -y git python3.12 python3.12-venv ca-certificates
fi
mkdir -p "$base/releases" "$HOME/.local/bin"
if [[ ! -d "$src" ]]; then
  tmp=$(mktemp -d "$base/releases/.install.XXXXXX")
  trap 'rm -rf -- "$tmp"' EXIT
  git clone --quiet https://github.com/elvito2808/Arbitrage.git "$tmp/source"
  git -C "$tmp/source" checkout --quiet --detach "$release"
  mv "$tmp/source" "$src"
  rmdir "$tmp"
  trap - EXIT
fi
[[ $(git -C "$src" rev-parse HEAD) == "$release" ]]
[[ -z $(git -C "$src" status --porcelain --untracked-files=no) ]]
venv="$base/venv-$release"
python3.12 -m venv "$venv"
"$venv/bin/python" -m pip install "$src"
"$venv/bin/python" -m pip check
"$venv/bin/python" "$src/deploy/checks/readiness_smoke.py" --executable "$venv/bin/arbitrage" --config "$src/config/simulation.toml"
"$venv/bin/arbitrage" --data-dir "$state" init --config "$src/config/simulation.toml"
cat > "$HOME/.local/bin/arb" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
umask 077
base="$HOME/.local/share/arbitrage"
exe="$base/venv-fca1a7c657b083c3698693b947cd2e788a7bceab/bin/arbitrage"
state="$base/state"
case "${1:-}" in
  start|resume|recover)
    mkdir -p "$base/logs"
    "$exe" --data-dir "$state" "$@" 2>&1 | tee -a "$base/logs/console.log"
    ;;
  status|report|stop|pause|emergency-stop) exec "$exe" --data-dir "$state" "$@" ;;
  *) echo "Usage: arb {start|resume|recover|status|report|stop|pause|emergency-stop}"; exit 2 ;;
esac
WRAPPER
chmod 700 "$HOME/.local/bin/arb"
echo 'Installation complete. Run: ~/.local/bin/arb start'
echo 'Status: ~/.local/bin/arb status'
echo 'Simulation only. Gas admission requires a separately validated public estimate sender.'
