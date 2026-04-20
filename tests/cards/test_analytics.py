"""Tests for card transaction analytics fee coverage helpers."""

import math

import pandas as pd
import pytest

from nbs_bi.cards import analytics as ca


@pytest.fixture()
def raw_spend() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "posted_at": pd.to_datetime(
                ["2026-01-01T10:00:00Z", "2026-01-03T10:00:00Z"],
                utc=True,
            ),
            "amount_usd": [100.0, 50.0],
        }
    )


def test_monthly_revenue_uses_inclusive_observed_days(raw_spend: pd.DataFrame) -> None:
    monthly = ca.monthly_revenue(raw_spend)
    assert monthly["A — 1% volume"] == 15.0


def test_flat_pct_monthly_revenue(raw_spend: pd.DataFrame) -> None:
    revenue = ca.flat_pct_monthly_revenue(raw_spend, flat_fee_usd=0.30, pct_fee=0.01)
    assert revenue == 21.0


def test_flat_pct_coverage_metrics(raw_spend: pd.DataFrame) -> None:
    metrics = ca.flat_pct_coverage_metrics(
        raw_spend,
        rain_cost_usd=21.0,
        flat_fee_usd=0.30,
        pct_fee=0.01,
    )

    assert metrics["n_days"] == 3.0
    assert metrics["tx_month"] == 20.0
    assert metrics["volume_month_usd"] == 1500.0
    assert metrics["coverage_ratio"] == 1.0
    assert metrics["required_pct_with_flat"] == 0.01
    assert metrics["required_flat_with_pct"] == 0.30


def test_coverage_grid_includes_flat_plus_pct_breakeven(raw_spend: pd.DataFrame) -> None:
    grid = ca.coverage_grid(
        raw_spend,
        rain_cost_usd=21.0,
        flat_range=[0.30],
        pct_range=[0.01],
    )
    assert grid.loc[0.30, 0.01] == 1.0


# ---------------------------------------------------------------------------
# Bin-based tiered fee
# ---------------------------------------------------------------------------


def test_bin_fee_revenue_shape(raw_spend: pd.DataFrame) -> None:
    fees = [0.10] * len(ca._BIN_LABELS)
    df = ca.bin_fee_revenue(raw_spend, fees)
    assert len(df) == len(ca._BIN_LABELS)
    assert list(df.columns) == [
        "label",
        "count",
        "pct_count",
        "fee_usd",
        "revenue_obs_usd",
        "revenue_month_usd",
    ]
    assert (df["revenue_month_usd"] >= 0).all()


def test_bin_fee_revenue_values(raw_spend: pd.DataFrame) -> None:
    # raw_spend: amount_usd=[100.0, 50.0], dates Jan-1 and Jan-3 → n_days=3
    # 50.0 → bin index 3 ($50–100), 100.0 → bin index 4 ($100–200)
    fees = [0.0, 0.0, 0.0, 1.0, 2.0, 0.0, 0.0]
    df = ca.bin_fee_revenue(raw_spend, fees)
    # observed revenue: 1×$1.00 + 1×$2.00 = $3.00; monthly: 3.00/3*30 = $30.00
    assert df.loc[df["label"] == "$50–100", "count"].iloc[0] == 1
    assert df.loc[df["label"] == "$100–200", "count"].iloc[0] == 1
    assert abs(df["revenue_month_usd"].sum() - 30.0) < 0.01


def test_bin_fee_coverage_metrics_keys(raw_spend: pd.DataFrame) -> None:
    fees = [0.10] * len(ca._BIN_LABELS)
    metrics = ca.bin_fee_coverage_metrics(raw_spend, fees, rain_cost_usd=10.0)
    expected_keys = (
        "tx_month",
        "revenue_usd",
        "coverage_ratio",
        "margin_usd",
        "breakeven_uniform_flat",
    )
    for key in expected_keys:
        assert key in metrics


def test_bin_fee_sweep_shape(raw_spend: pd.DataFrame) -> None:
    fee_range = [0.10, 0.20, 0.30]
    fixed = ca.BIN_FEE_DEFAULTS[:]
    df = ca.bin_fee_sweep(raw_spend, i_bin=0, j_bin=1, fee_range=fee_range, fixed_fees=fixed)
    assert df.shape == (3, 3)


# ---------------------------------------------------------------------------
# Progressive fee model
# ---------------------------------------------------------------------------


def test_progressive_fee_revenue_keys(raw_spend: pd.DataFrame) -> None:
    result = ca.progressive_fee_revenue(raw_spend, n_bins=5, gap=50.0)
    assert {"revenue_usd", "coverage_ratio", "gap"} <= result.keys()
    assert result["coverage_ratio"] > 0


def test_progressive_fee_last_bin_has_no_upper_limit() -> None:
    raw = pd.DataFrame(
        {
            "posted_at": pd.to_datetime(
                ["2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z"],
                utc=True,
            ),
            "amount_usd": [5.0, 25.0],
        }
    )

    result = ca.progressive_fee_revenue(
        raw,
        n_bins=2,
        gap=10.0,
        flat_start=1.0,
        flat_end=5.0,
        pct_start=0.0,
        pct_end=0.0,
    )
    breakdown = ca.progressive_fee_breakdown(
        raw,
        n_bins=2,
        gap=10.0,
        flat_start=1.0,
        flat_end=5.0,
        pct_start=0.0,
        pct_end=0.0,
    )

    assert result["revenue_usd"] == 180.0
    assert breakdown.loc[0, "revenue_month_usd"] == 30.0
    assert breakdown.loc[1, "revenue_month_usd"] == 150.0
    assert math.isinf(breakdown.loc[1, "to_usd"])


def test_progressive_fee_breakdown_accepts_custom_flat_fees(raw_spend: pd.DataFrame) -> None:
    breakdown = ca.progressive_fee_breakdown(
        raw_spend,
        n_bins=3,
        gap=50.0,
        flat_start=0.0,
        flat_end=0.0,
        pct_start=0.0,
        pct_end=0.0,
        rain_cost_usd=30.0,
        flat_fees=[0.0, 1.0, 2.0],
    )

    assert breakdown.loc[1, "flat_usd"] == 1.0
    assert breakdown.loc[2, "flat_usd"] == 2.0
    assert breakdown.loc[1, "count"] == 1
    assert breakdown.loc[2, "count"] == 1
    assert breakdown["revenue_month_usd"].sum() == 30.0
    assert breakdown["invoice_factor"].sum() == 1.0


def test_progressive_fee_sweep_shape(raw_spend: pd.DataFrame) -> None:
    df = ca.progressive_fee_sweep(raw_spend, gap_values=[10.0, 20.0, 30.0], n_bins=5)
    assert df.shape == (3, 3)
    assert list(df.columns) == ["gap", "revenue_usd", "coverage_ratio"]
