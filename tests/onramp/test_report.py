"""Tests for nbs_bi.onramp.report — OnrampReport static builders.

All tests use in-memory fixture DataFrames — no database required.
"""

import pandas as pd
import pytest

from nbs_bi.onramp.report import OnrampReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_conv_df() -> pd.DataFrame:
    """Conversions already scaled to real units (BRL, USDC)."""
    return pd.DataFrame({
        "id": ["q1", "q2", "q3", "q4", "q5"],
        "user_id": ["u1", "u2", "u1", "u3", "u2"],
        "direction": ["brl_to_usdc", "brl_to_usdc", "usdc_to_brl", "brl_to_usdc", "usdc_to_brl"],
        "from_amount_brl": [1000.0, 2000.0, 0.0, 1500.0, 0.0],
        "from_amount_usdc": [0.0, 0.0, 200.0, 0.0, 100.0],
        "to_amount_brl": [0.0, 0.0, 1100.0, 0.0, 550.0],
        "to_amount_usdc": [175.0, 350.0, 0.0, 260.0, 0.0],
        "fee_amount_brl": [10.0, 20.0, 5.0, 15.0, 5.0],
        "fee_amount_usdc": [1.75, 3.50, 0.88, 2.63, 0.88],
        "spread_revenue_brl": [30.0, 60.0, 20.0, 45.0, 15.0],
        "spread_revenue_usdc": [5.25, 10.50, 3.50, 7.88, 2.63],
        "exchange_rate": [5.70, 5.70, 5.50, 5.76, 5.50],
        "effective_rate": [5.71, 5.71, 5.51, 5.77, 5.51],
        "spread_percentage": [0.5, 0.5, 0.5, 0.5, 0.5],
        # two rows in Jan, three in Feb — for monthly grouping
        "created_at": pd.to_datetime([
            "2026-01-10", "2026-01-20",
            "2026-02-05", "2026-02-15", "2026-02-25",
        ]),
        "updated_at": pd.to_datetime([
            "2026-01-10", "2026-01-20",
            "2026-02-05", "2026-02-15", "2026-02-25",
        ]),
        "conversion_request_id": ["r1", "r2", "r3", "r4", "r5"],
        "processing_mode": ["instant"] * 5,
        "used": [True] * 5,
    })


def _make_dep_df() -> pd.DataFrame:
    return pd.DataFrame({
        "id": ["d1", "d2"],
        "user_id": ["u1", "u2"],
        "amount_brl": [1000.0, 2000.0],
        "state": ["completed", "completed"],
        "provider_name": ["asaas", "asaas"],
        "created_at": pd.to_datetime(["2026-01-10", "2026-02-05"]),
    })


def _make_trf_df() -> pd.DataFrame:
    return pd.DataFrame({
        "id": ["t1"],
        "user_id": ["u3"],
        "amount_brl": [500.0],
        "fee_brl": [5.0],
        "net_amount_brl": [495.0],
        "pix_key_type": ["cpf"],
        "status": ["completed"],
        "executed_at": pd.to_datetime(["2026-02-10"]),
        "created_at": pd.to_datetime(["2026-02-10"]),
    })


# ---------------------------------------------------------------------------
# _build_revenue_monthly
# ---------------------------------------------------------------------------


def test_revenue_monthly_returns_dataframe() -> None:
    df = OnrampReport._build_revenue_monthly(_make_conv_df())
    assert isinstance(df, pd.DataFrame)


def test_revenue_monthly_columns() -> None:
    df = OnrampReport._build_revenue_monthly(_make_conv_df())
    assert set(df.columns) >= {"month", "fee_brl", "spread_brl", "total_revenue_brl"}


def test_revenue_monthly_two_months() -> None:
    df = OnrampReport._build_revenue_monthly(_make_conv_df())
    assert len(df) == 2


def test_revenue_monthly_totals_sum_correctly() -> None:
    df = OnrampReport._build_revenue_monthly(_make_conv_df())
    for _, row in df.iterrows():
        assert row["total_revenue_brl"] == pytest.approx(
            row["fee_brl"] + row["spread_brl"], rel=1e-6
        )


def test_revenue_monthly_jan_fee() -> None:
    df = OnrampReport._build_revenue_monthly(_make_conv_df())
    jan = df[df["month"].dt.month == 1].iloc[0]
    assert jan["fee_brl"] == pytest.approx(10.0 + 20.0, rel=1e-6)


def test_revenue_monthly_feb_spread() -> None:
    df = OnrampReport._build_revenue_monthly(_make_conv_df())
    feb = df[df["month"].dt.month == 2].iloc[0]
    assert feb["spread_brl"] == pytest.approx(20.0 + 45.0 + 15.0, rel=1e-6)


def test_revenue_monthly_sorted_ascending() -> None:
    df = OnrampReport._build_revenue_monthly(_make_conv_df())
    assert list(df["month"]) == sorted(df["month"].tolist())


def test_revenue_monthly_empty_input() -> None:
    df = OnrampReport._build_revenue_monthly(pd.DataFrame())
    assert df.empty


def test_revenue_monthly_missing_fee_column() -> None:
    df = OnrampReport._build_revenue_monthly(pd.DataFrame({"created_at": []}))
    assert df.empty


# ---------------------------------------------------------------------------
# _build_summary
# ---------------------------------------------------------------------------


def test_summary_shape() -> None:
    df = OnrampReport._build_summary(_make_conv_df(), _make_dep_df(), _make_trf_df())
    assert isinstance(df, pd.DataFrame)
    assert set(df.columns) == {"metric", "value", "note"}


def test_summary_has_expected_metrics() -> None:
    df = OnrampReport._build_summary(_make_conv_df(), _make_dep_df(), _make_trf_df())
    metrics = set(df["metric"].tolist())
    assert "Total conversions" in metrics
    assert "Total revenue BRL" in metrics
    assert "PIX IN (dep)" in metrics
    assert "PIX OUT (transf)" in metrics


def test_summary_total_conversions() -> None:
    df = OnrampReport._build_summary(_make_conv_df(), _make_dep_df(), _make_trf_df())
    row = df[df["metric"] == "Total conversions"].iloc[0]
    assert int(row["value"]) == 5


def test_summary_pix_net() -> None:
    df = OnrampReport._build_summary(_make_conv_df(), _make_dep_df(), _make_trf_df())
    pix_in = df[df["metric"] == "PIX IN (dep)"]["value"].iloc[0]
    pix_out = df[df["metric"] == "PIX OUT (transf)"]["value"].iloc[0]
    pix_net = df[df["metric"] == "PIX NET"]["value"].iloc[0]
    assert pix_net == pytest.approx(pix_in - pix_out, rel=1e-6)


def test_summary_empty_conversions() -> None:
    df = OnrampReport._build_summary(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert not df.empty
    row = df[df["metric"] == "Total conversions"].iloc[0]
    assert row["value"] == 0
