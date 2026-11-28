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
    RPC = "hedera_rpc"
    MIRROR = "hedera_mirror"
    KUCOIN = "kucoin"
    WEBSOCKET = "kucoin_websocket"
    REST = "kucoin_rest"
    SNAPSHOTS = "snapshots"
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
    PROVIDER_HTTP_ERROR = "provider_http_error"
    PROVIDER_NETWORK_FAILURE = "provider_network_failure"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    BLOCK_NOT_FOUND = "block_not_found"
    MIRROR_INDEXING_DELAY = "mirror_indexing_delay"
    BLOCK_NUMBER_MISMATCH = "block_number_mismatch"
    BLOCK_HASH_MISMATCH = "block_hash_mismatch"
    BLOCK_TIMESTAMP_MISMATCH = "block_timestamp_mismatch"
    STALE_SNAPSHOT = "stale_snapshot"
    CROSS_VENUE_SKEW = "cross_venue_skew"
    ROUTER_QUOTE_MISMATCH = "router_quote_mismatch"
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



class StartupBlocked(ValueError):
    def __init__(self):
        super().__init__("startup readiness blocked")
