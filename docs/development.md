# P02 development

Arbitrage is a Python 3.12 modular monolith for simulation. Install and run the P02 tests from the checkout.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install '.[test]'
python -m pip check
python -m pytest
```

Load `config/simulation.toml` with `arbitrage.config.load_config`. Version 1 fixes the trade size at 1,000 HBAR, the session length at 24 hours, and worker capacity at three with one running at a time. Fee and risk settings are represented as decimal values. The USDC/USDT parity setting is a simulation assumption.

Initialize a SQLite file with `Storage(path).initialize()`. Use a path in private runtime storage. Use `Storage.transaction()` for a complete unit of work. An exception escaping the context rolls back the transaction. Initialize opening balances with `tx.seed_balances(config.balances.as_balances())`. Seeding preserves existing holdings. Holdings form a shared simulated portfolio. HBAR and WHBAR use eight decimal places. USDC uses six, and simulated USDT uses six as an accounting convention.

Persist worker, opportunity and execution records with the corresponding transaction methods. Opportunity records preserve requested quotes while the execution record holds separate fills. Reservation and ledger writes update balances inside the same SQLite transaction. Keep the transaction open only for database work. The experiment start and deadline are immutable per ID, so reopening the file preserves the deadline. The schema version uses SQLite `user_version`. Version 0 initializes version 1 and unsupported versions fail explicitly.

Use `OperationalLog.emit()` with its event and level enums. Caller metadata is discarded. The bounded producer returns false for rejected or dropped events. A consumer can retrieve JSON lines with `take()`. Financial ledger entries remain in SQLite. This phase provides the queue and event format, so a later lifecycle phase can attach the output sink.

`arbitrage.interfaces` defines the typed contracts for later phases. This package contains no market clients, swap execution, scheduler, CLI or deployment units.
