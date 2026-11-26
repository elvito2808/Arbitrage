# Round 1 baseline verification

Arbitrage commit `813d9f840c6283f62836a7162d23ed1ee9b08fe8`. Framework A pin `cb23c4b08d2103aead243b59d6b66f325361a4bc`. Python 3.12.14, Linux.

| Command | Exit | Result |
| --- | --- | --- |
| `git ls-remote --symref origin HEAD refs/heads/main` | 0 | Remote main and HEAD matched the baseline SHA |
| `python -m pytest -q -p no:cacheprovider` in the host interpreter | 1 | pytest absent from the host interpreter |
| `git archive HEAD` and extraction into a fresh staging directory | 0 | Baseline source staged outside both repositories |
| `python -m venv /workspace/work/arb-review-venv` | 0 | Isolated environment created |
| `/workspace/work/arb-review-venv/bin/python -m pip -q install '/workspace/work/arb-review[test]'` | 0 | Clean build and installation |
| `/workspace/work/arb-review-venv/bin/python -m pip check` | 0 | No broken requirements found |
| `/workspace/work/arb-review-venv/bin/python -m pytest -q -p no:cacheprovider` | 0 | 209 passed, 3 skipped in 7.18s |
| `git diff --check` | 0 | No whitespace errors |

Agent 3 repeated the clean install on an independent archive and ran `python -m pytest -q -rs -p no:cacheprovider` with exit 0, 209 passed and 3 skipped in 6.56s. The three skips are opt-in public network probes in `tests/test_market_smoke.py`. The offline replay used a test-controlled transport.
