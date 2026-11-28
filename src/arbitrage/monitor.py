"""Project closed console events from applied settings and committed runtime records."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from .coordinator import MIN_INTERVAL, NETWORK
from .kucoin import SYMBOL
from .models import GAS_SOURCE, Balance, EvaluationStatus, Holding, Venue
from .operational_log import Event, Level
from .saucerswap import CHAIN_ID
from .simulation import Effect, Event as JournalEvent, Request


def units(value, decimals):
    return Decimal(value).scaleb(-decimals) if value is not None else None


def exact(fraction):
    return Decimal(fraction.numerator) / Decimal(fraction.denominator)


class Monitor:
    def __init__(self, runtime, worker):
        self.runtime = runtime
        self.worker = worker
        self.session = runtime.session_id
        self.number = 0
        self.started = None
        self.execution = None
        self.cursor = 0

    def emit(self, event, level=Level.INFO, **fields):
        self.runtime.log.emit(event, level, worker=self.worker, session=self.session, **fields)

    def startup(self, config, engine):
        with self.runtime.storage.observe() as tx:
            row = self.runtime.latest(tx)
            self.number = self.count(tx)[0]
        s = config.simulation
        self.emit(Event.STARTUP)
        self.emit(Event.CONFIGURATION, mode=s.mode, capacity=s.worker_capacity, active_limit=s.max_active_workers,
                  chain_id=CHAIN_ID, network=NETWORK, market=SYMBOL, dex_market="WHBAR-USDC",
                  trade_hbar=s.trade_hbar, profit_usdt=exact(engine.profit_floor), loss_usdt=exact(engine.loss_limit),
                  slippage=exact(engine.slippage), fee=exact(engine.fee), gas_cap=exact(engine.gas_cap),
                  ttl_seconds=s.cache_ttl_seconds, interval_seconds=int(MIN_INTERVAL.total_seconds()),
                  gas_method=GAS_SOURCE, buy_gas_units=s.buy_gas_units, sell_gas_units=s.sell_gas_units,
                  gas_limit=s.gas_limit, deadline=datetime.fromisoformat(row["deadline"]), parity=s.usdc_usdt_parity)

    def opening(self):
        with self.runtime.storage.observe() as tx:
            balances = tuple(tx.balance(holding) for holding in Holding)
        for balance in balances:
            self.balance(balance, None, "opening", "PASS")

    def balance(self, balance, delta, phase, reconciliation, execution=None):
        decimals = balance.holding.asset.decimals
        self.emit(Event.BALANCE, holding=balance.holding, phase=phase, reconciliation=reconciliation, cycle=execution, execution=execution,
                  previous=units(balance.total_units - delta, decimals) if delta is not None else None,
                  delta=units(delta, decimals), total=units(balance.total_units, decimals),
                  reserved=units(balance.reserved_units, decimals), available=units(balance.available_units, decimals))

    def cycle_start(self, cycle):
        self.number += 1
        self.started = self.runtime.timer()
        self.emit(Event.ANALYSIS_START, cycle=cycle, number=self.number)

    def cycle_finished(self, cycle):
        with self.runtime.storage.observe() as tx:
            cycle = tx.analysis(cycle.id)
            trade = tx._db.execute("SELECT request FROM simulated_trades WHERE id=?", (cycle.id,)).fetchone()
            miss = tx._db.execute("SELECT reason FROM simulation_misses WHERE cycle_id=?", (cycle.id,)).fetchone()
        request = Request.model_validate_json(trade[0]) if trade else None
        admitted = request.evaluation.direction if request else None
        book = cycle.cex_snapshot.kucoin_book if cycle.cex_snapshot else None
        source = book.transport if book else None
        for item in cycle.evaluations:
            dex, cex = item.requested_dex, item.requested_cex
            basis = item.configured_gas
            self.emit(Event.EVALUATION, cycle=cycle.id, number=self.number, direction=item.direction,
                      status=item.status, reason=item.reason, source=source,
                      trade_hbar=units((dex or cex).base_units, 8) if (dex or cex) else None,
                      dex_side=dex.side.value if dex else None, dex_usdc=units(dex.quote_units, 6) if dex else None,
                      cex_side=cex.side.value if cex else None, cex_usdt=units(cex.quote_units, 6) if cex else None,
                      gas_method=basis.source if basis else None,
                      gas_price_weibars=basis.gas_price_weibars if basis else None,
                      gas_estimated_hbar=item.estimated_gas_hbar, gas_estimated_usdt=item.estimated_gas_usdc,
                      gas_capped_hbar=units(item.gas_budget_tinybars, 8), gas_capped_usdt=item.gas_cost_usdt,
                      indicative_usdt=item.indicative_profit_usdt, net_usdt=item.net_profit_usdt,
                      stressed_usdt=item.stressed_profit_usdt,
                      admission="admitted" if item.direction == admitted else
                      "declined" if item.status == EvaluationStatus.ELIGIBLE else "not_eligible",
                      guard=miss[0] if miss and item.status == EvaluationStatus.ELIGIBLE else None)
        self.emit(Event.ANALYSIS, cycle=cycle.id, number=self.number,
                  duration_ms=max(0, round((self.runtime.timer() - self.started) * 1000)),
                  result="admitted" if admitted else "not_admitted", source=source)
        if request:
            self.execution = cycle.id
            self.cursor = 1  # the acceptance is represented by this event
            self.emit(Event.SIMULATED_EXECUTION, cycle=cycle.id, execution=cycle.id, phase="admitted",
                      direction=admitted, trade_hbar=units(request.evaluation.requested_dex.base_units, 8),
                      net_usdt=request.evaluation.net_profit_usdt, stressed_usdt=request.evaluation.stressed_profit_usdt,
                      **self.holds(request.funding))

    @staticmethod
    def holds(reservations):
        if reservations is None:
            return {holding: None for holding in Holding}
        current = {reservation.holding: reservation.units for reservation in reservations}
        return {holding: units(current.get(holding, 0), holding.asset.decimals) for holding in Holding}

    def recovery_start(self):
        with self.runtime.storage.observe() as tx:
            pending = tx.unresolved_simulation(NETWORK)
            if pending is None or pending["id"] == self.execution:
                return
            self.execution = pending["id"]
            events = tx.simulation_events(self.execution)
            self.cursor = len(events)
            remaining = JournalEvent.model_validate_json(events[-1]).remaining if events else None
        self.emit(Event.SIMULATED_EXECUTION, cycle=self.execution, execution=self.execution, phase="recovery",
                  result=pending["status"], **self.holds(remaining))

    def journal(self, execution):
        with self.runtime.storage.observe() as tx:
            data = tx.simulation_events(execution)[self.cursor:]
            status = tx.simulation_status(execution)
            events = tuple(JournalEvent.model_validate_json(entry) for entry in data)
            balances = {entry.holding: tx.balance(entry.holding) for event in events for entry in event.ledger}
            reconciled = self.runtime._reconciled(tx) if balances else False
        if not events:
            return
        self.cursor += len(events)
        later_deltas = defaultdict(int)
        for event in events:
            for entry in event.ledger:
                later_deltas[entry.holding] += entry.delta_units
        final_holds = {hold.holding: hold.units for hold in events[-1].remaining or ()}
        for event in events:
            event_holds = {hold.holding: hold.units for hold in event.remaining or ()}
            fill = event.fill
            known = event.outcome is not None and event.outcome != Effect.UNKNOWN
            self.emit(Event.SIMULATED_EXECUTION, cycle=execution, execution=execution,
                      phase="attempt" if event.due_at else "result" if event.outcome else "review",
                      recorded_at=event.at, venue=event.venue, attempt=event.attempt or None, result=event.outcome.value if event.outcome else None,
                      filled_hbar=units(fill.base_units, 8) if fill else None,
                      fill_usdc=units(fill.quote_units, 6) if fill and fill.venue == Venue.DEX else None,
                      fill_usdt=units(fill.quote_units, 6) if fill and fill.venue == Venue.CEX else None,
                      fee_usdt=units(event.fee_units, 6) if event.venue == Venue.CEX and known else None,
                      gas_hbar=units(event.gas_units, 8) if event.venue == Venue.DEX and known else None,
                      **self.holds(event.remaining))
            for entry in event.ledger:
                holding = entry.holding
                later_deltas[holding] -= entry.delta_units
                final = balances[holding]
                balance = Balance(holding=holding, total_units=final.total_units - later_deltas[holding],
                                  reserved_units=final.reserved_units - final_holds.get(holding, 0)
                                  + event_holds.get(holding, 0))
                self.balance(balance, entry.delta_units, "commit", "PASS" if reconciled else "FAIL", execution)
        if status != "active":
            self.emit(Event.SIMULATED_EXECUTION, cycle=execution, execution=execution, phase="terminal", result=status,
                      **self.holds(events[-1].remaining))

    def count(self, tx):
        return tx._db.execute(
            """SELECT count(*), coalesce(sum(CASE WHEN EXISTS
               (SELECT 1 FROM json_each(a.payload, '$.evaluations') e
                WHERE json_extract(e.value, '$.status') = 'insufficient_data') THEN 1 ELSE 0 END),0)
               FROM analysis_cycles a WHERE json_extract(a.payload, '$.session_id')=?""", (self.session,),
        ).fetchone()

    def summary(self, shutdown):
        with self.runtime.storage.observe() as tx:
            row = self.runtime.latest(tx)
            cycles, errors = self.count(tx)
            outcomes = dict(tx._db.execute(
                """SELECT s.status, count(*) FROM simulated_trades s JOIN analysis_cycles a ON a.id=s.id
                   WHERE json_extract(a.payload, '$.session_id')=? GROUP BY s.status""", (self.session,),
            ))
        intent = "shutdown" if shutdown else row["state"]
        if self.runtime.readiness and self.runtime.readiness["state"] == "STARTUP_BLOCKED":
            intent = "startup_blocked"
        elif intent == "running":
            intent = "interrupted"
        self.emit(Event.LIFECYCLE, intent=intent)
        sink = self.runtime.log.sink
        self.emit(Event.SESSION_SUMMARY, cycles=cycles, admissions=sum(outcomes.values()), cycle_errors=errors,
                  console_drops=sink.dropped + sink.failed if sink else 0,
                  **{status: outcomes.get(status, 0) for status in
                     ("filled", "failed", "incomplete", "manual_review", "active")})
