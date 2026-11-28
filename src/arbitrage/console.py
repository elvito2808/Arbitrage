"""Best-effort foreground stderr sink for closed operational events."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from queue import Empty, Full, Queue
from threading import Event, Thread

from .operational_log import Level

LEVELS = tuple(Level)
HOLDINGS = {
    "dex.WHBAR": "DEX WHBAR", "dex.USDC": "DEX USDC", "dex.HBAR": "DEX HBAR gas",
    "cex.HBAR": "KuCoin HBAR", "cex.USDT": "KuCoin USDT",
}
DIRECTIONS = {"buy_dex": "DEX → CEX", "sell_dex": "CEX → DEX"}
SOURCES = {"websocket": "WebSocket", "rest": "REST", "unavailable": "unavailable"}
CHECKS = {
    "configuration": ("CONFIG", "Simulation configuration validation"),
    "storage": ("STORAGE", "SQLite initialization and integrity validation"),
    "recovery": ("RECOVERY", "Prior virtual executions and outstanding holds"),
    "hedera": ("HEDERA", "Hedera market verification"),
    "hedera_rpc": ("HEDERA", "Hedera RPC chain and block header"),
    "hedera_mirror": ("HEDERA", "Hedera Mirror block provenance"),
    "kucoin": ("KUCOIN", "Market identity and depth"),
    "kucoin_websocket": ("KUCOIN", "WebSocket depth"),
    "kucoin_rest": ("KUCOIN", "REST depth"),
    "snapshots": ("HEALTH", "Market snapshot freshness and consistency"),
    "buy_gas": ("GAS", "DEX BUY gas"),
    "sell_gas": ("GAS", "DEX SELL gas"),
    "balances": ("BALANCE", "Virtual funding for both directions"),
}
LIFECYCLE = {
    "paused": "Session paused.", "stopped": "Session stopped.",
    "emergency": "Emergency stop latched.", "expired": "Session expired.",
    "shutdown": "Foreground shutdown requested.", "startup_blocked": "Session blocked by readiness.",
    "interrupted": "Foreground session interrupted.", "running": "Session active.",
}
DIAGNOSTICS = {
    "gas_unknown": "Gas price unavailable", "gas_invalid": "Gas data invalid or stale",
    "profit_threshold": "Net profit did not exceed the threshold",
    "maximum_loss": "Stressed loss exceeds the configured limit",
    "insufficient_balance": "Insufficient available virtual balance",
    "cross_venue_skew": "Market timestamps are too far apart",
    "stale_or_invalid_clock": "Stale market data or invalid clock",
    "route_estimate_invalid": "Invalid or stale gas inputs",
    "route_context_unverified": "SaucerSwap router quote could not be verified",
    "mirror_indexing_delay": "Mirror head was behind the requested RPC block",
    "block_not_found": "Requested block is absent despite the Mirror index reaching it",
    "block_number_mismatch": "Mirror returned a different block number",
    "block_hash_mismatch": "Mirror hash prefix differs from the RPC block hash",
    "block_timestamp_mismatch": "Mirror block end time differs from the RPC timestamp beyond tolerance",
    "stale_snapshot": "Snapshot exceeded freshness or clock skew limits",
    "invalid_provider_response": "The response has an invalid schema or field",
    "provider_http_error": "Provider returned an unsuccessful HTTP status",
    "provider_network_failure": "Connection to the provider failed",
    "router_quote_mismatch": "Router output disagrees with the integer quote",
    "gas_price_unavailable": "RPC gas price unavailable",
    "gas_price_invalid": "RPC gas price invalid",
    "websocket_unavailable": "WebSocket unavailable",
    "fresh_depth": "Fresh depth available", "no_fresh_depth": "No fresh depth available",
    "recovery_unresolved": "A prior virtual execution or hold remains unresolved",
}


def value(record, key, unit=""):
    result = record.get(key)
    return "UNKNOWN" if result is None else f"{result}{unit}"


def diagnosis(code):
    return DIAGNOSTICS.get(code, (code or "unknown").replace("_", " ").capitalize())


def holds(record):
    amounts = [f"{name} {value(record, key)}" for key, name in HOLDINGS.items()
               if record.get(key) is not None and Decimal(record[key]) != 0]
    if any(record.get(key) is None for key in HOLDINGS):
        amounts.append("other amounts UNKNOWN" if amounts else "UNKNOWN")
    return ", ".join(amounts) or "none"


def render_market_failure(failure):
    module, provider = {
        "hedera_rpc": ("HEDERA", "Hedera RPC"),
        "hedera_mirror": ("HEDERA", "Hedera Mirror"),
        "saucerswap": ("DEX", "SaucerSwap"),
        "kucoin_rest": ("KUCOIN", "KuCoin REST"),
        "market": ("HEALTH", "Cross-venue market data"),
    }[failure["provider"]]
    operation = {
        "block_by_number": "fetch requested block", "latest_indexed_block": "read indexed head",
        "block_match": "match RPC block", "block_freshness": "check block freshness",
        "pool_snapshot": "verify pool snapshot", "pool_identity": "verify pool and tokens",
        "router_quote": "verify router quote", "depth_snapshot": "verify market depth",
        "snapshot_pair": "verify market snapshots",
    }.get(failure["operation"], failure["operation"])
    reason = diagnosis(failure["category"])
    if "field" in failure:
        reason += f" ({failure['field']})"
    if "http_status" in failure:
        reason += f". HTTP {failure['http_status']}"
    return module, f"{provider} · {operation}. {reason}."


def readiness_check(record):
    check = record.get("check")
    module, subject = CHECKS.get(check, ("HEALTH", "Readiness check"))
    result = value(record, "outcome")
    reason = record.get("diagnostic")
    message = f"{result} · {subject}"
    if reason:
        message += f". {diagnosis(reason)}"
    failure = record.get("market_failure")
    if failure and (check in ("hedera_rpc", "hedera_mirror", "kucoin_rest") or
                    check == "hedera" and failure["operation"] == "eth_call" or
                    failure["provider"] in ("saucerswap", "market")):
        module, explanation = render_market_failure(failure)
        yield module, f"{result} · {explanation}"
        return
    if check == "hedera_rpc" and result == "PASS":
        yield module, f"PASS · Hedera RPC verified chain ID {value(record, 'chain_id')} and the block header."
        return
    if check == "kucoin" and record.get("source"):
        message += f" via {SOURCES[record['source']]}"
    if check == "hedera" and result == "PASS":
        yield "DEX", (f"PASS · SaucerSwap {value(record, 'pair')} token identity and decimals verified. "
                      f"Pool {value(record, 'pool')}, router 0.0.{value(record, 'router_number')}.")
    elif check in ("buy_gas", "sell_gas") and result == "PASS":
        yield module, (f"{message}. RPC price {value(record, 'gas_price_weibars', ' weibars per gas')}. "
                       f"Estimated cost {value(record, 'estimated_gas_hbar', ' HBAR')}, "
                       f"maximum budget {value(record, 'maximum_gas_hbar', ' HBAR')}.")
    else:
        yield module, f"{message}."


def evaluate(record):
    def v(key, unit=""):
        return value(record, key, unit)

    dex_side = v("dex_side").upper()
    cex_side = v("cex_side").upper()
    if dex_side == "UNKNOWN":
        dex_side = "BUY" if record.get("direction") == "buy_dex" else "SELL"
    if cex_side == "UNKNOWN":
        cex_side = "SELL" if dex_side == "BUY" else "BUY"
    yield "DEX", f"{dex_side} {v('trade_hbar', ' WHBAR')} · gross quote {v('dex_usdc', ' USDC')}."
    yield "KUCOIN", (f"{cex_side} {v('trade_hbar', ' HBAR')} · fee-inclusive quote {v('cex_usdt', ' USDT')} "
                     f"via {SOURCES.get(record.get('source'), 'UNKNOWN')}.")
    yield "GAS", (f"DEX {dex_side} estimated cost {v('gas_estimated_hbar', ' HBAR')} / "
                  f"{v('gas_estimated_usdt', ' USDT')}. Maximum budget {v('gas_capped_hbar', ' HBAR')} / "
                  f"{v('gas_capped_usdt', ' USDT')}.")
    direction = DIRECTIONS.get(record.get("direction"), "UNKNOWN")
    reason = record.get("reason")
    guard = record.get("guard")
    gas_error = record.get("gas_error")
    detail = f" · {diagnosis(reason)}" if reason else ""
    if gas_error and gas_error != reason:
        detail += f" · {diagnosis(gas_error)}"
    if guard:
        detail += f" · Admission blocked: {diagnosis(guard)}"
    yield "ARB", (f"{direction} · Indicative {v('indicative_usdt', ' USDT')}, net {v('net_usdt', ' USDT')}, "
                  f"stressed {v('stressed_usdt', ' USDT')}. {v('status').upper()}{detail}. "
                  f"Admission {v('admission').replace('_', ' ')}.")


def execution(record):
    phase = record.get("phase")
    identifier = value(record, "execution")
    result = value(record, "result").replace("_", " ").upper()
    venue = {"dex": "DEX", "cex": "KuCoin"}.get(record.get("venue"), "UNKNOWN venue")
    retained = holds(record)
    if phase == "admitted":
        direction = DIRECTIONS.get(record.get("direction"), "UNKNOWN")
        return "SIMULATION", (f"Admitted SIMULATED execution {identifier} · {direction} · "
                              f"{value(record, 'trade_hbar', ' HBAR')}. Estimated net "
                              f"{value(record, 'net_usdt', ' USDT')}, stressed {value(record, 'stressed_usdt', ' USDT')}. "
                              f"Reserved virtual balances: {retained}.")
    if phase == "recovery":
        return "RECOVERY", f"Recovering SIMULATED execution {identifier} · journal status {result}. Retained holds: {retained}."
    if phase in ("attempt", "result"):
        attempt = record.get("attempt")
        label = "Retry attempt" if isinstance(attempt, int) and attempt > 1 else "Attempt"
        message = f"SIMULATED {venue} execution {identifier} · {label} {value(record, 'attempt')}"
        if phase == "attempt":
            return "SIMULATION", f"{message} started. Reserved virtual balances: {retained}."
        is_dex = record.get("venue") == "dex"
        amount = value(record, "fill_usdc", " USDC") if is_dex else value(record, "fill_usdt", " USDT")
        base = " WHBAR" if is_dex else " HBAR"
        charge = (f"Gas charged {value(record, 'gas_hbar', ' HBAR')}" if is_dex else
                  f"Fee {value(record, 'fee_usdt', ' USDT')}")
        filled = f" Filled {value(record, 'filled_hbar', base)} for {amount}." if (
            record.get("filled_hbar") is not None or result == "UNKNOWN") else ""
        return "SIMULATION", f"{message} · {result}.{filled} {charge}. Retained holds: {retained}."
    if phase == "terminal":
        return "SIMULATION", (f"SIMULATED execution {identifier} finished · {result}. Committed stablecoin changes: "
                              f"DEX {value(record, 'delta_usdc', ' USDC')}, KuCoin {value(record, 'delta_usdt', ' USDT')}. "
                              f"Retained holds: {retained}.")
    return "SIMULATION", f"SIMULATED execution {identifier} requires review. Retained holds: {retained}."


def messages(record, at):
    def v(key, unit=""):
        return value(record, key, unit)

    event = record["event"]
    worker = f"WORKER-{record['worker'][-1]:0>2}" if record.get("worker") else "ARB"
    check = f"CHECK #{record['number']:03d}" if record.get("number") is not None else "Check"
    if event == "startup":
        yield "SYSTEM", f"Arbitrage Bot Starting ({at:%Y-%m-%d} UTC)."
        yield "LOGGER", "Foreground logger initialized on stderr."
        yield "SECURITY", "SIMULATION ONLY. Real trading, wallet signing and order submission are disabled."
    elif event == "configuration":
        yield "CONFIG", f"Applied simulation configuration · {v('dex_market')} on Hedera and {v('market')} on KuCoin."
        yield "CONFIG", (f"Workers {v('capacity')}, active limit {v('active_limit')}. Trade quantity {v('trade_hbar', ' HBAR')}. "
                         f"Minimum profit {v('profit_usdt', ' USDT')}, maximum loss {v('loss_usdt', ' USDT')}.")
        yield "CONFIG", (f"Slippage fraction {v('slippage')}, KuCoin fee fraction {v('fee')}, gas multiplier {v('gas_cap', '×')}. "
                         f"Snapshot TTL {v('ttl_seconds', ' s')}, cycle interval {v('interval_seconds', ' s')}. "
                         f"Session deadline {v('deadline')}. USDC/USDT parity {'enabled' if record.get('parity') else 'UNKNOWN'}.")
        yield "GAS", (f"{v('gas_method')}. BUY {v('buy_gas_units', ' gas units')}, SELL {v('sell_gas_units', ' gas units')}, "
                      f"validation limit {v('gas_limit')}.")
    elif event == "readiness_check":
        yield from readiness_check(record)
    elif event == "readiness_state":
        state = record.get("state")
        reason = diagnosis(record.get("diagnostic"))
        if state == "WAITING_RETRY":
            delay = v("delay_seconds", " s")
            wait = f"Retry in {delay}." if record.get("next_attempt_at") else f"Session expires in {delay} before another retry."
            yield "HEALTH", f"WAITING_RETRY · Attempt {v('attempt')}. {reason}. {wait}"
        elif state == "STARTUP_BLOCKED":
            suffix = " Retry budget exhausted." if record.get("retry_exhausted") else ""
            yield "HEALTH", f"STARTUP_BLOCKED · Attempt {v('attempt')}. {reason}.{suffix}"
        elif state == "RUNNING":
            yield worker, "RUNNING · Readiness verified."
        else:
            message = {
                "STARTING": "STARTING · Validating the simulation startup.",
                "PREFLIGHT_CHECKING": f"PREFLIGHT_CHECKING · Market and gas checks, attempt {v('attempt')}.",
                "READY": "READY · All required readiness checks passed.",
                "CANCELLED": "Readiness supervision ended.",
            }.get(state, "Readiness state UNKNOWN.")
            yield "HEALTH", message
    elif event == "connection_status":
        yield "KUCOIN", (f"Market data source changed to {SOURCES.get(record.get('source'), 'UNKNOWN')}. "
                         f"{diagnosis(record.get('reason'))}.")
    elif event == "analysis_start":
        yield worker, f"{check} · Analysis started."
    elif event == "evaluation":
        yield from evaluate(record)
    elif event == "analysis":
        conclusion = {"admitted": "Simulation admitted", "not_admitted": "No simulation admitted",
                      "no_opportunity": "NO_OPPORTUNITY"}.get(record.get("conclusion") or record.get("result"),
                                                              "Result UNKNOWN")
        yield worker, f"{check} · Analysis completed in {v('duration_ms', ' ms')}. {conclusion}."
    elif event == "simulated_execution":
        yield execution(record)
    elif event == "balance":
        label = HOLDINGS.get(record.get("holding"), "Virtual balance")
        balance = f"Total {v('total')}, reserved {v('reserved')}, available {v('available')}"
        change = f"Previous {v('previous')}, change {v('delta')}. " if record.get("phase") == "commit" else ""
        yield "BALANCE", f"{label} · {change}{balance}. Reconciliation {v('reconciliation')}."
    elif event == "lifecycle":
        yield "SYSTEM", LIFECYCLE.get(record.get("intent"), "Session intent UNKNOWN.")
    elif event == "session_summary":
        yield "SYSTEM", (f"Session summary · Duration {v('duration_seconds', ' s')}, {v('cycles')} completed cycles, "
                         f"{v('admissions')} admissions, {v('filled')} filled, {v('failed')} failed, "
                         f"{v('incomplete')} incomplete, {v('manual_review')} manual review, {v('active')} pending. "
                         f"Cycle errors {v('cycle_errors')}, console events dropped or failed {v('console_drops')}.")
        balances = ", ".join(f"{name} {v(key)}" for key, name in HOLDINGS.items())
        yield "BALANCE", f"Remaining virtual balances: {balances}."
        yield "SYSTEM", "Generate or refresh the Excel workbook with the report command."
    elif event == "storage_status":
        yield "STORAGE", "Could not read or project the operational status."
    elif event == "market_failure":
        if failure := record.get("market_failure"):
            yield render_market_failure(failure)
    elif event == "network_sample":
        yield "HEALTH", f"Network sample · HTTP retries {v('http_retries')}, cache hits {v('cache_hits')}."


def format_line(at, level, module, message):
    return f"[{at:%H:%M:%S}.{at.microsecond // 1000:03d}] {level.upper():<8} [{module}] {message}"


def render(record, *, technical=False):
    at = datetime.fromisoformat(record["at"]).astimezone(UTC)
    level = record["level"]
    lines = [format_line(at, level, module, message) for module, message in messages(record, at)]
    if technical:
        lines.append(format_line(at, "debug", "LOGGER", json.dumps(record, ensure_ascii=True, sort_keys=True)))
    return "\n".join(lines)


class ConsoleSink:
    """A blocked stream can exhaust only this queue and this daemon thread."""

    def __init__(self, stream, level=Level.INFO, *, capacity=256):
        self.stream = stream
        self.level = LEVELS.index(level)
        self.queue = Queue(maxsize=capacity)
        self.closed = Event()
        self.dropped = 0
        self.failed = 0
        self._thread = Thread(target=self._drain, name="arbitrage-console", daemon=True)
        self._thread.start()

    def offer(self, line, level):
        if LEVELS.index(level) < self.level:
            return
        try:
            self.queue.put_nowait(line)
        except Full:
            self.dropped += 1

    def _drain(self):
        outstanding = 0
        while not self.closed.is_set() or not self.queue.empty():
            try:
                line = self.queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                record = json.loads(line)
                if missing := self.dropped + self.failed - outstanding:
                    at = datetime.fromisoformat(record["at"]).astimezone(UTC)
                    print(format_line(at, "warning", "LOGGER", f"Console sink resumed. {missing} event(s) lost."),
                          file=self.stream, flush=True)
                    outstanding += missing
                rendered = render(record, technical=self.level == LEVELS.index(Level.DEBUG))
                if rendered:
                    print(rendered, file=self.stream, flush=True)
            except Exception:
                self.failed += 1

    def close(self):
        self.closed.set()
        self._thread.join(timeout=0.25)
