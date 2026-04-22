"""Tests for nbs_bi.clients.queries — helper functions and scaling logic."""

import pandas as pd
import pytest

from nbs_bi.clients.queries import _cache_path, _scale_brl, _to_exclusive_end

# ---------------------------------------------------------------------------
# _to_exclusive_end
# ---------------------------------------------------------------------------


def test_exclusive_end_adds_one_day():
    assert _to_exclusive_end("2026-03-31") == "2026-04-01"


def test_exclusive_end_jan_last_day():
    assert _to_exclusive_end("2026-01-31") == "2026-02-01"


def test_exclusive_end_passes_through_datetime_string():
    ts = "2026-03-31T00:00:00"
    assert _to_exclusive_end(ts) == ts


def test_exclusive_end_passes_through_space_separated():
    ts = "2026-03-31 00:00:00"
    assert _to_exclusive_end(ts) == ts


# ---------------------------------------------------------------------------
# _scale_brl
# ---------------------------------------------------------------------------


def test_scale_brl_divides_by_100():
    df = pd.DataFrame({"onramp_revenue_brl": [58000, 11000]})
    out = _scale_brl(df)
    assert pytest.approx(out["onramp_revenue_brl"].iloc[0], rel=1e-6) == 580.0
    assert pytest.approx(out["onramp_revenue_brl"].iloc[1], rel=1e-6) == 110.0


def test_scale_brl_does_not_touch_non_brl_columns():
    df = pd.DataFrame({"card_fee_usd": [9.99], "onramp_revenue_brl": [1000]})
    out = _scale_brl(df)
    assert out["card_fee_usd"].iloc[0] == 9.99


def test_scale_brl_coerces_non_numeric():
    df = pd.DataFrame({"amount_brl": ["bad", "100"]})
    out = _scale_brl(df)
    assert pd.isna(out["amount_brl"].iloc[0])
    assert pytest.approx(out["amount_brl"].iloc[1]) == 1.0


# ---------------------------------------------------------------------------
# _cache_path
# ---------------------------------------------------------------------------


def test_cache_path_returns_none_when_no_dir(monkeypatch):
    import nbs_bi.clients.queries as q

    monkeypatch.setattr(q, "DB_CACHE_DIR", "")
    result = _cache_path("test", "SELECT 1", {})
    assert result is None


def test_cache_path_deterministic(tmp_path, monkeypatch):
    import nbs_bi.clients.queries as q

    monkeypatch.setattr(q, "DB_CACHE_DIR", str(tmp_path))
    p1 = _cache_path("rev", "SELECT 1", {"start": "2026-01-01"})
    p2 = _cache_path("rev", "SELECT 1", {"start": "2026-01-01"})
    assert p1 == p2


def test_cache_path_changes_with_params(tmp_path, monkeypatch):
    import nbs_bi.clients.queries as q

    monkeypatch.setattr(q, "DB_CACHE_DIR", str(tmp_path))
    p1 = _cache_path("rev", "SELECT 1", {"start": "2026-01-01"})
    p2 = _cache_path("rev", "SELECT 1", {"start": "2026-02-01"})
    assert p1 != p2


# ---------------------------------------------------------------------------
# Scaling decisions documented in spec
# ---------------------------------------------------------------------------


def test_card_fee_usd_no_divisor():
    """card_annual_fees.amount_usdc is already real USDC — SQL returns it as-is."""
    df = pd.DataFrame({"card_fee_usd": [9.99, 14.99]})
    out = _scale_brl(df)
    # _scale_brl only touches *_brl columns — card_fee_usd must be unchanged
    assert out["card_fee_usd"].tolist() == [9.99, 14.99]


def test_billing_charges_scale_in_sql():
    """billing_charges.amount is divided in SQL (÷ 1_000_000), not in Python."""
    # Simulate what the SQL returns after division
    df = pd.DataFrame({"card_tx_fee_usd": [0.99, 0.25, 0.50]})
    out = _scale_brl(df)
    assert out["card_tx_fee_usd"].tolist() == [0.99, 0.25, 0.50]


def test_swap_fee_formula():
    """swap fee = input_amount / 1e6 × platform_fee_bps / 10000."""
    input_amount = 1_000_000  # 1 USDC in micros
    platform_fee_bps = 30
    expected = input_amount / 1e6 * platform_fee_bps / 10_000
    assert pytest.approx(expected) == 0.003
