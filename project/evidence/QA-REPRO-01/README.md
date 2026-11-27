# QA-REPRO-01 independent RC1 defect reproduction

This is newly generated offline evidence from published RC1. The historical QA-01 Revision 4 archive was unavailable. All provider responses, transactions and balances in the tests are synthetic. No test contacts Hedera or KuCoin.

| Reviewed candidate | Identity |
| --- | --- |
| Branch | `release/srg-clm-rc1` |
| Commit | `659d470ac919613f7f50c1d04936bd713ba750a9` |
| Tree | `8b2cff77b134aaae6f29bc653273883adea52682` |
| Tracked paths | 172 before the evidence commit |

## Environment and reproduction

The recorded runs used Linux and CPython 3.12.14 with pytest 9.2.0, pydantic 2.14.4, aiohttp 3.14.5 and openpyxl 3.1.5. Use a Python 3.12 virtual environment with the project's test dependencies. The pins below reproduce the primary versions. Supply packages from your authorized dependency source or a prepared wheelhouse.

```bash
python3.12 -m venv /tmp/arbitrage-qa-repro-venv
source /tmp/arbitrage-qa-repro-venv/bin/activate
python -m pip install '.[test]' pytest==9.2.0 pydantic==2.14.4 aiohttp==3.14.5 openpyxl==3.1.5
python -m pip check
```

Run from the evidence branch. Its parent is exact RC1, with the evidence commit limited to this directory.

```bash
(cd project/evidence/QA-REPRO-01 && sha256sum -c SHA256SUMS)
bash project/evidence/QA-REPRO-01/reproduce.sh baseline --tb=short
bash project/evidence/QA-REPRO-01/reproduce.sh independent --tb=short
bash project/evidence/QA-REPRO-01/reproduce.sh combined --tb=short
```

The baseline exits 0. The independent and combined runs exit 1 on RC1 because six correct-behavior assertions fail. The test file is outside default pytest discovery. To regenerate captures, set `QA_REPRO_CAPTURE_DIR` to a fresh scratch directory before the independent run. Synthetic IDs can differ between runs. The manifest verifies the committed captures and outputs. No runtime database or workbook is included.

## Results and traceability

The independent suite used seven cases, including the passing foreground outage control. See [defect register](defects.md), [baseline output](results/baseline.txt), [independent output](results/independent.txt), and [combined output](results/combined.txt). Commands in the register use `bash project/evidence/QA-REPRO-01/reproduce.sh independent -k SELECTOR`.

| Defect | Requirements | Selector |
| --- | --- | --- |
| QA-R4-01 | SRG-001, SRG-002, SRG-005, SRG-014 | `qa_r4_01` |
| QA-R4-02 | SRG-002, SRG-017 | `qa_r4_02` |
| QA-R4-03 | SRG-015, SRG-016, CLM-006, CLM-007, CLM-008 | `qa_r4_03` |
| QA-R4-04 | CLM-011, CLM-012 | `qa_r4_04` |
| QA-R4-05 | SRG-006, SRG-017, CLM-004 | `qa_r4_05` |

FIX-01 should reproduce the six assertion failures, then run the same assertions against its corrective candidate alongside the default offline suite. The foreground outage control must continue to pass. Operational gas readiness remains blocked by the previously documented live evidence gap.
Baseline: 305 passed, 0 failed, 3 skipped.
Independent: 1 passed, 6 failed, 0 skipped.
Combined: 306 passed, 6 failed, 3 skipped.

Captured pytest output has trailing line whitespace trimmed for repository checks.
