"""Closed readiness status and diagnostic contract shared by runtime and logging."""

from enum import StrEnum


class ReadinessState(StrEnum):
    STARTING = "STARTING"
    PREFLIGHT_CHECKING = "PREFLIGHT_CHECKING"
    WAITING_RETRY = "WAITING_RETRY"
    READY = "READY"
    RUNNING = "RUNNING"
    STARTUP_BLOCKED = "STARTUP_BLOCKED"
    CANCELLED = "CANCELLED"


class Check(StrEnum):
    CONFIGURATION = "configuration"
    STORAGE = "storage"
    RECOVERY = "recovery"
    HEDERA = "hedera"
    KUCOIN = "kucoin"
    WEBSOCKET = "kucoin_websocket"
    REST = "kucoin_rest"
    SNAPSHOTS = "snapshots"
    SENDER = "sender"
    BUY_GAS = "buy_gas"
    SELL_GAS = "sell_gas"
    BALANCES = "balances"


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    DEGRADED = "DEGRADED"


class Diagnostic(StrEnum):
    INVALID_CONFIGURATION = "invalid_configuration"
    STORAGE_INCONSISTENT = "storage_inconsistent"
    RECOVERY_UNRESOLVED = "recovery_unresolved"
    MARKET_IDENTITY_MISMATCH = "market_identity_mismatch"
    MARKET_UNAVAILABLE = "market_unavailable"
    MARKET_UNVERIFIED = "market_unverified"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    SENDER_UNCONFIGURED = "estimate_sender_unconfigured"
    PROVIDER_DISAGREEMENT = "provider_disagreement"
    SENDER_UNVERIFIED = "sender_prerequisites_unverified"
    SENDER_ACCOUNT_NOT_FOUND = "sender_account_not_found"
    MISSING_TOKEN_ASSOCIATION = "missing_token_association"
    INSUFFICIENT_SENDER_WHBAR = "insufficient_sender_whbar"
    INSUFFICIENT_SENDER_USDC = "insufficient_sender_usdc"
    INSUFFICIENT_SENDER_HBAR = "insufficient_sender_hbar"
    INSUFFICIENT_ALLOWANCE = "insufficient_allowance"
    GAS_PRICE_UNAVAILABLE = "gas_price_unavailable"
    GAS_PRICE_INVALID = "gas_price_invalid"
    ROUTE_CONTEXT_UNVERIFIED = "route_context_unverified"
    ROUTE_ESTIMATE_UNAVAILABLE = "route_estimate_unavailable"
    ROUTE_ESTIMATE_INVALID = "route_estimate_invalid"
    BLOCK_PROVENANCE_MISMATCH = "route_estimate_head_changed"
    INSUFFICIENT_VIRTUAL_BALANCE = "insufficient_virtual_balance"


CRITICAL_GAS = frozenset({
    Diagnostic.SENDER_UNCONFIGURED, Diagnostic.SENDER_ACCOUNT_NOT_FOUND,
    Diagnostic.MISSING_TOKEN_ASSOCIATION, Diagnostic.INSUFFICIENT_SENDER_WHBAR,
    Diagnostic.INSUFFICIENT_SENDER_USDC, Diagnostic.INSUFFICIENT_SENDER_HBAR,
    Diagnostic.INSUFFICIENT_ALLOWANCE,
})


class StartupBlocked(ValueError):
    def __init__(self):
        super().__init__("startup readiness blocked")
