"""A consistent, private Excel export of the observed and simulated records."""

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .config import Config
from .coordinator import NETWORK
from .models import AnalysisCycle, Execution, Holding, Opportunity, Reason
from .simulation import Effect, Event, Request

UNKNOWN = "unknown"
PAIR = "HBAR / USDC-USDT"
EXCEL_ROWS = 1_048_576
QUOTE = (
    "venue", "side", "base asset", "quote asset", "base units", "quote units",
    "snapshot", "source UTC", "observed UTC",
)


def reference(value):
    # Identifiers can come from imported records. Keep joins without revealing raw values.
    return hashlib.sha256(value.encode()).hexdigest()[:24] if value is not None else None


def quote(value):
    if value is None:
        return (None,) * len(QUOTE)
    return (
        value.venue, value.side, value.base_asset, value.quote_asset, value.base_units,
        value.quote_units, reference(value.snapshot_id),
        value.source_at.isoformat() if value.source_at else None, value.observed_at.isoformat(),
    )


def append(sheet, values):
    cells = []
    for value in values:
        if value is None:
            value = UNKNOWN
        if isinstance(value, Decimal) or type(value) is int and abs(value) >= 10**15:
            value = str(value)
        if isinstance(value, str):
            cell = WriteOnlyCell(sheet, value=value)
            cell.data_type = "s"
            cells.append(cell)
        else:
            cells.append(value)
    sheet.append(cells)


class Sheets:
    def __init__(self, book):
        self.book = book
        self.pages = {}

    def add(self, name, headers):
        sheet = self.book.create_sheet(name)
        sheet.freeze_panes = "A2"
        append(sheet, headers)
        self.pages[name] = (sheet, headers, 1, 1)

    def write(self, name, *row):
        sheet, headers, page, rows = self.pages[name]
        if rows == EXCEL_ROWS:
            page += 1
            sheet = self.book.create_sheet(f"{name} {page}")
            sheet.freeze_panes = "A2"
            append(sheet, headers)
            rows = 1
        append(sheet, row)
        self.pages[name] = (sheet, headers, page, rows + 1)


def observations(db, sheets):
    for row in db.execute("""SELECT a.payload, s.status AS trade_status, m.reason AS miss,
                                    json_extract(s.request, '$.evaluation.direction') AS admitted_direction
                             FROM analysis_cycles a LEFT JOIN simulated_trades s ON s.id=a.id
                             LEFT JOIN simulation_misses m ON m.cycle_id=a.id ORDER BY a.rowid"""):
        cycle = AnalysisCycle.model_validate_json(row["payload"])
        provenance = tuple(
            value
            for snap in (cycle.dex_snapshot, cycle.cex_snapshot)
            for value in ((reference(snap.id), snap.source_at.isoformat(), snap.received_at.isoformat())
                          if snap else (None, None, None))
        )
        miss = row["miss"] if row["miss"] in {
            "unresolved_network_execution", "cycle_fence_lost", "risk_revalidation_failed",
            "insufficient_credit_headroom", *Reason,
        } else None
        for item in cycle.evaluations:
            admitted = row["admitted_direction"] == item.direction
            sheets.write("Observations", reference(cycle.id), reference(cycle.worker_id), NETWORK, PAIR,
                         cycle.observed_at.isoformat(), item.direction, item.status, item.reason,
                         admitted, row["trade_status"] if admitted else None,
                         (miss if item.status == "eligible_for_simulation" else None),
                         *provenance, item.indicative_profit_usdt, item.gas_cost_usdt,
                         item.net_profit_usdt, item.stressed_profit_usdt, item.gas_budget_tinybars,
                         reference(item.gas_evidence_id))
            for requested in (item.requested_dex, item.requested_cex):
                if requested:
                    sheets.write("Requested quotes", reference(cycle.id), item.direction, *quote(requested))


def journal(db, sheets):
    for row in db.execute("SELECT * FROM simulated_trades ORDER BY rowid"):
        request = Request.model_validate_json(row["request"])
        trade = reference(row["id"])
        direction = request.evaluation.direction
        for hold in request.funding:
            sheets.write("Reservations", trade, "admitted", reference(hold.id), hold.holding, hold.units)
        deltas = Counter()
        gas = fees = 0
        journal_ledger = {}
        for event_row in db.execute("SELECT payload FROM simulation_events WHERE trade_id=? ORDER BY rowid",
                                    (row["id"],)):
            event = Event.model_validate_json(event_row["payload"])
            event_ref = reference(event.id)
            known = event.outcome is not None and event.outcome != Effect.UNKNOWN
            gas += event.gas_units if known else 0
            fees += event.fee_units if known else 0
            for entry in event.ledger:
                deltas[entry.holding] += entry.delta_units
                journal_ledger[entry.id] = (entry.holding, entry.delta_units)
            sheets.write("Execution events", trade, event_ref, direction, event.venue, event.at.isoformat(),
                         event.due_at.isoformat() if event.due_at else None, event.attempt,
                         event.outcome, "see durable journal" if event.note else None,
                         event.fee_units if known else None, event.gas_units if known else None,
                         *quote(event.fill))
            for hold in event.remaining or ():
                sheets.write("Reservations", trade, event_ref, reference(hold.id), hold.holding, hold.units)
        base = deltas[Holding.DEX_WHBAR] + deltas[Holding.CEX_HBAR]
        stable = deltas[Holding.DEX_USDC] + deltas[Holding.CEX_USDT]
        prefix = f"sim:{row['id']}:"
        persisted = {
            entry["id"]: (entry["holding"], entry["delta_units"])
            for entry in db.execute("SELECT * FROM ledger WHERE id >= ? AND id < ?",
                                    (prefix, prefix[:-1] + ";"))
        }
        reconciled = journal_ledger == persisted
        provable = (
            reconciled and row["status"] in ("filled", "failed", "incomplete")
            and base == 0 and gas == 0
        )
        sheets.write("Trades", trade, direction, row["status"], request.accepted_at.isoformat(),
                     request.gas.observed_at.isoformat(), request.gas.route_gas,
                     *request.gas.price_weibars, request.gas.debit_tinybars,
                     request.evaluation.gas_budget_tinybars, request.evaluation.gas_cost_usdt,
                     request.fee if len(request.fee) <= 80 and
                     all(c in "0123456789./" for c in request.fee) else None,
                     fees, gas, stable, base, reconciled, stable if provable else None)


def balances(db, sheets):
    changes = Counter()
    for row in db.execute("SELECT * FROM ledger ORDER BY rowid"):
        changes[row["holding"]] += row["delta_units"]
        sheets.write("Ledger", reference(row["id"]), row["holding"], row["delta_units"], row["recorded_at"])
    reserved = Counter()
    for row in db.execute("SELECT * FROM reservations ORDER BY rowid"):
        reserved[row["holding"]] += row["units"]
        sheets.write("Current holds", reference(row["id"]), row["holding"], row["units"])
    for row in db.execute("SELECT * FROM balances ORDER BY holding"):
        holding = Holding(row["holding"])
        total, holds = row["total_units"], row["reserved_units"]
        change = changes[holding]
        opening = db.execute("SELECT units FROM runtime_openings WHERE holding=?", (holding,)).fetchone()
        original = opening["units"] if opening else total - change
        sheets.write("Balances", holding, holding.asset.decimals, original, change, total, holds,
                     total - holds, total - original - change, holds - reserved[holding],
                     "recorded at initialization" if opening else "reconstructed from current minus ledger")


def metadata(db, sheets):
    for row in db.execute("SELECT * FROM runtime_sessions ORDER BY rowid"):
        sheets.write("Sessions", reference(row["id"]), reference(row["worker_id"]), row["state"],
                     row["start"], row["deadline"], row["high_water"], row["ended"])
    latch = db.execute("SELECT at FROM runtime_stop_latch WHERE id=1").fetchone()
    sheets.write("Health", "emergency latched", latch is not None)
    sheets.write("Health", "emergency latch UTC", latch["at"] if latch else None)
    for row in db.execute("SELECT * FROM runtime_configs ORDER BY rowid"):
        config = Config.model_validate_json(row["config"])
        for section, values in (("simulation", config.simulation), ("opening balances", config.balances)):
            for key, value in values:
                sheets.write("Configuration", reference(row["session_id"]), row["applied_at"], section, key, value)
    health = db.execute("SELECT payload FROM runtime_health WHERE id=1").fetchone()
    for table in ("analysis_cycles", "simulated_trades", "simulation_misses", "simulation_events"):
        sheets.write("Health", table, db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    for row in db.execute("SELECT * FROM workers ORDER BY id"):
        sheets.write("Health", f"worker {reference(row['id'])}", row["state"])
    if health:
        health = json.loads(health["payload"])
        for key in ("observed_at", "ws", "kucoin_source", "retries", "cache_hits", "queue_drops"):
            sheets.write("Health", key, health.get(key))
        for venue, status in health["rest"].items():
            sheets.write("Health", f"{venue} REST", status)
        for venue, snap in (health["snapshots"] or {}).items():
            for key in ("source_at", "received_at", "age_seconds"):
                sheets.write("Health", f"{venue} {key} at health sample", snap.get(key) if snap else None)
        for event in health["recent_events"]:
            sheets.write("Operational events", event["at"], event["event"], event["level"],
                         reference(event.get("worker")), reference(event.get("cycle")),
                         event.get("source"), event.get("reason"))


def legacy(db, sheets):
    for row in db.execute("SELECT payload FROM opportunities ORDER BY rowid"):
        item = Opportunity.model_validate_json(row["payload"])
        for request in (item.requested_dex, item.requested_cex):
            sheets.write("Legacy opportunities", reference(item.id), reference(item.worker_id), item.direction,
                         item.observed_at.isoformat(), item.estimated_profit_usdt,
                         "rejected" if item.rejection_reason else "recorded", *quote(request))
    for row in db.execute("SELECT payload FROM executions ORDER BY rowid"):
        item = Execution.model_validate_json(row["payload"])
        for fill in (item.dex_fill, item.cex_fill):
            if fill is not None:
                sheets.write("Legacy executions", reference(item.id), reference(item.opportunity_id),
                             item.outcome, item.observed_at.isoformat(), *quote(fill))
        if item.dex_fill is None and item.cex_fill is None:
            sheets.write("Legacy executions", reference(item.id), reference(item.opportunity_id),
                         item.outcome, item.observed_at.isoformat(), *quote(None))


def export(database: Path, destination: Path):
    book = Workbook(write_only=True)
    sheets = Sheets(book)
    headers = {
        "Guide": ("field", "meaning"),
        "Observations": ("cycle", "worker", "network", "pair", "observed UTC", "direction", "evaluation",
                         "reason", "admitted", "trade status", "simulation miss", "DEX snapshot",
                         "DEX source UTC", "DEX received UTC", "CEX snapshot", "CEX source UTC",
                         "CEX received UTC", "indicative USDT", "estimated gas USDT", "evaluated net USDT",
                         "stressed USDT", "gas budget tinybars", "gas provenance"),
        "Requested quotes": ("cycle", "direction", *QUOTE),
        "Trades": ("trade", "direction", "status", "accepted UTC", "gas quote UTC", "route gas units",
                   "network gas weibars", "cap gas weibars", "modeled gas per attempt tinybars",
                   "gas budget tinybars", "estimated gas USDT", "configured CEX fee rate",
                   "known CEX fees microUSDT", "known gas tinybars", "booked stablecoin delta micro units",
                   "booked base delta 1e-8 units", "ledger reconciled", "realized net microUSDT under parity"),
        "Execution events": ("trade", "event", "direction", "leg", "recorded UTC", "due UTC", "attempt",
                             "outcome", "note", "CEX fee microUSDT", "gas tinybars", *QUOTE),
        "Reservations": ("trade", "stage or event", "hold", "holding", "units"),
        "Current holds": ("hold", "holding", "units"),
        "Ledger": ("entry", "holding", "delta units", "recorded UTC"),
        "Balances": ("holding", "decimals", "opening units", "posted delta units", "ending units",
                     "held units", "available units", "balance discrepancy", "held discrepancy", "opening provenance"),
        "Sessions": ("session", "worker", "state", "start UTC", "deadline UTC", "last wall time UTC", "ended UTC"),
        "Configuration": ("session", "applied UTC", "section", "setting", "value"),
        "Health": ("metric", "value"),
        "Operational events": ("at UTC", "event", "severity", "worker", "cycle", "source", "reason"),
        "Legacy opportunities": ("opportunity", "worker", "direction", "observed UTC", "indicative USDT",
                                 "status", *QUOTE),
        "Legacy executions": ("execution", "opportunity", "outcome", "observed UTC", *QUOTE),
    }
    for name, columns in headers.items():
        sheets.add(name, columns)
    for field, meaning in (
        ("scope", "Public analysis only. Simulated fills are historical offline fixture results when present."),
        ("identity", f"{NETWORK}; {PAIR}. Record references are pseudonyms."),
        ("parity", "Assumes 1 USDC equals 1 USDT. Stablecoin delta is not net PnL when gas or open inventory remains."),
        ("fees", "CEX fee is included in the fill and shown separately for audit. DEX pool fee is embedded in the quote."),
        ("precision", "Decimals and integers at 16 or more digits are exact text to preserve precision."),
        ("valuation", "Realized net requires reconciled ledger, terminal known outcomes, flat base inventory and zero gas, since no realized conversion rate was recorded."),
        ("missing", "Unknown includes unavailable and inapplicable values. Recorded zero is a known zero."),
    ):
        sheets.write("Guide", field, meaning)
    temporary = None
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            observations(db, sheets)
            journal(db, sheets)
            balances(db, sheets)
            metadata(db, sheets)
            legacy(db, sheets)
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".xlsx", delete=False) as output:
            temporary = Path(output.name)
            book.save(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        for sheet in book.worksheets:
            writer = sheet._writer
            if writer is not None and os.path.exists(writer.out):
                sheet.close()
                writer.cleanup()
