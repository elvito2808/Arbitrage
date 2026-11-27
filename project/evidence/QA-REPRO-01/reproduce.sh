#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
mode=${1:-independent}
if (( $# > 0 )); then shift; fi
suite=project/evidence/QA-REPRO-01/test_rc1_defects.py
case "$mode" in
  independent) paths=("$suite");;
  combined) paths=(tests "$suite");;
  baseline) paths=(tests);;
  *) printf 'Usage: %s {independent|combined|baseline} [pytest options]\n' "$0" >&2; exit 2;;
esac
export PYTHONPATH=src:tests
export PYTHONDONTWRITEBYTECODE=1
unset ARB_PUBLIC_SMOKE PUBLIC_MARKET_SMOKE CLM_EVIDENCE_DIR
exec "${PYTHON:-python}" -m pytest -q -p no:cacheprovider -c pyproject.toml --confcutdir=. "${paths[@]}" "$@"
