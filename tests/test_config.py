from decimal import Decimal
from pathlib import Path

import pytest

from arbitrage.config import load_config
from arbitrage.models import Holding


EXAMPLE = Path(__file__).resolve().parents[1] / "config/simulation.toml"


def test_default_config():
    config = load_config(EXAMPLE)
    assert config.simulation.trade_hbar == 1_000
    assert config.simulation.duration_hours == 24
    assert config.simulation.worker_capacity == 3
    assert config.simulation.max_active_workers == 1
    assert config.simulation.cex_fee_fraction == Decimal("0.001")
    assert config.simulation.minimum_profit_usdt == Decimal("0.10")
    assert {balance.holding: balance.total_units for balance in config.balances.as_balances()} == {
        Holding.DEX_WHBAR: 5_000_000_000_000,
        Holding.DEX_USDC: 3_000_000_000,
        Holding.DEX_HBAR: 100_000_000_000,
        Holding.CEX_HBAR: 5_000_000_000_000,
        Holding.CEX_USDT: 3_000_000_000,
    }


@pytest.mark.parametrize(("old", "new"), [
    ("schema_version = 1", "schema_version = true"),
    ('mode = "simulation"', 'mode = "live"'),
    ("trade_hbar = 1000", "trade_hbar = 999"),
    ("worker_capacity = 3", "worker_capacity = 4"),
    ("max_active_workers = 1", "max_active_workers = true"),
    ("duration_hours = 24", "duration_hours = 25"),
    ("cache_ttl_seconds = 10", "cache_ttl_seconds = 11"),
    ('cex_fee_fraction = "0.001"', 'cex_fee_fraction = "1"'),
    ('dex_usdc = "3000"', 'dex_usdc = "0.0000001"'),
    ('dex_usdc = "3000"', 'dex_usdc = "1e-10000000"'),
    ('dex_usdc = "3000"', 'dex_usdc = "99999999999999"'),
    ('dex_usdc = "3000"', 'dex_usdc = "-1"'),
    ('dex_usdc = "3000"', 'dex_usdc = "NaN"'),
    ('dex_usdc = "3000"', 'unexpected = "test-only-sensitive-value"'),
    ('dex_usdc = "3000"', 'dex_usdc = ['),
])
def test_invalid_config_is_sanitized(tmp_path, old, new):
    path = tmp_path / "simulation.toml"
    path.write_text(EXAMPLE.read_text().replace(old, new))
    with pytest.raises(ValueError, match="^invalid simulation configuration$"):
        load_config(path)


def test_toml_decimal_literal_preserves_precision(tmp_path):
    path = tmp_path / "simulation.toml"
    path.write_text(EXAMPLE.read_text().replace('"0.001"', "0.001000000000000000123"))
    assert load_config(path).simulation.cex_fee_fraction == Decimal("0.001000000000000000123")


def test_invalid_utf8_is_sanitized(tmp_path):
    path = tmp_path / "simulation.toml"
    path.write_bytes(bytes([255]))
    with pytest.raises(ValueError, match="^invalid simulation configuration$"):
        load_config(path)
