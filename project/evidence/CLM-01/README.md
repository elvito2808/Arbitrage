# CLM-01 Revision 2 offline console evidence

The text files are direct captures from deterministic test fixtures using the foreground renderer. The clock, public provider replies, and simulated execution scenarios are synthetic. The configured operational account remains blocked pending valid live gas evidence for both routes.

| Capture | Contents |
| --- | --- |
| [Ten analysis cycles and shutdown](ten_cycles_graceful_shutdown.txt) | Startup, ten cycle IDs and both direction results per cycle, graceful shutdown and persisted session summary |
| [Retry recovery](retry.txt) | A transient timeout, the effective delay, and a successful offline retry |
| [Missing token associations](missing_associations.txt) | Critical startup block |
| [Unavailable gas evidence](unavailable_gas.txt) | Unavailable SELL route estimate and retry exhaustion |
| [Successful virtual execution](success.txt) | DEX-first journal, committed balance updates and terminal fill |
| [DEX failure](dex_failure.txt) | Three simulated reverts, gas debits, and released holds |
| [KuCoin partial fill](cex_partial.txt) | Half-size CEX fill, resulting balance updates and incomplete terminal status |
| [Unknown outcome](unknown.txt) | Manual review with a retained CEX hold |
| [WebSocket to REST](websocket_to_rest.txt) | Expired synthetic WebSocket depth and validated REST fallback |
| [Crash recovery](crash_recovery.txt) | Recovery after the DEX commit, with only the remaining CEX leg advanced |
| [Slow sink](slow_sink.txt) and [failed sink](failed_sink_recovered.txt) | Lost-event warning after the sink became available |

The current execution policy releases the CEX input hold after a known partial fill. Unknown outcomes retain the submitted leg's hold for manual review. The two captures illustrate the existing outcomes.

Regenerate the captures with `CLM_EVIDENCE_DIR=project/evidence/CLM-01 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_console_logging.py`.
