"""Tests for nbs_bi.reporting.ramp — figure builders and helpers.

All tests are pure (no Streamlit server required). The Streamlit render
methods are integration-level and covered by manual smoke tests.

Note: ramp.py imports streamlit at module level, so this test file must be
run in an environment where streamlit is installed.  In CI environments
without streamlit the file will fail to collect — this is a known pre-existing
limitation documented in docs/PROGRESS.md.
"""

import pandas as pd
import plotly.graph_objects as go
import pytest

from nbs_bi.reporting.ramp import (
    _fig_fx_rate,
    _fig_new_vs_returning,
    _fig_pix,
    _fig_revenue_monthly,
    _fig_spread_histogram,
    _fig_volume,
    _kpi,
    _mom_annotations,
    _resample_conv,
)
from nbs_bi.reporting.theme import mask_user_id

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def summary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"metric": "Total conversions", "value": 120.0, "note": ""},
            {"metric": "Onramp volume BRL", "value": 500_000.0, "note": ""},
            {"metric": "Offramp volume BRL", "value": 200_000.0, "note": ""},
            {"metric": "Total revenue BRL", "value": 8_500.0, "note": ""},
        ]
    )


@pytest.fixture()
def conv_daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=5),
            "onramp": [10_000.0, 15_000.0, 8_000.0, 20_000.0, 12_000.0],
            "offramp": [5_000.0, 3_000.0, 7_000.0, 4_000.0, 6_000.0],
        }
    )


@pytest.fixture()
def revenue_monthly() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "month": pd.date_range("2026-01-01", periods=3, freq="MS"),
            "fee_brl": [1_000.0, 1_200.0, 1_500.0],
            "spread_brl": [3_000.0, 3_500.0, 4_000.0],
            "total_revenue_brl": [4_000.0, 4_700.0, 5_500.0],
        }
    )


@pytest.fixture()
def fx_stats() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=4)
    onramp = pd.DataFrame(
        {
            "period": dates,
            "side": "onramp",
            "fx_mean": [5.80, 5.85, 5.78, 5.90],
            "fx_p10": [5.70, 5.75, 5.68, 5.80],
            "fx_p90": [5.90, 5.95, 5.88, 6.00],
            "n": [30, 28, 35, 32],
        }
    )
    offramp = pd.DataFrame(
        {
            "period": dates,
            "side": "offramp",
            "fx_mean": [5.70, 5.75, 5.68, 5.80],
            "fx_p10": [5.60, 5.65, 5.58, 5.70],
            "fx_p90": [5.80, 5.85, 5.78, 5.90],
            "n": [10, 12, 8, 15],
        }
    )
    return pd.concat([onramp, offramp], ignore_index=True)


@pytest.fixture()
def pix_daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=5).date,
            "pix_in": [50_000.0, 60_000.0, 45_000.0, 70_000.0, 55_000.0],
            "pix_out": [20_000.0, 25_000.0, 18_000.0, 30_000.0, 22_000.0],
            "pix_net": [30_000.0, 35_000.0, 27_000.0, 40_000.0, 33_000.0],
        }
    )


@pytest.fixture()
def spread_stats() -> pd.DataFrame:
    import numpy as np

    np.random.seed(42)
    n = 100
    return pd.DataFrame(
        {
            "side": ["onramp"] * n + ["offramp"] * n,
            "spread_percentage": list(np.random.normal(2.5, 0.3, n))
            + list(np.random.normal(2.0, 0.25, n)),
            "volume_brl": list(np.random.uniform(500, 5000, n)) * 2,
        }
    )


@pytest.fixture()
def new_vs_returning() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "month": pd.date_range("2026-01-01", periods=3, freq="MS"),
            "new_users": [80, 95, 110],
            "returning_users": [20, 30, 45],
        }
    )


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_kpi_extracts_known_metric(summary: pd.DataFrame) -> None:
    assert _kpi(summary, "Total conversions") == 120.0


def test_kpi_returns_default_for_unknown(summary: pd.DataFrame) -> None:
    assert _kpi(summary, "nonexistent", default=99.0) == 99.0


def test_kpi_default_zero(summary: pd.DataFrame) -> None:
    assert _kpi(summary, "missing") == 0.0


def test_mask_user_id_truncates() -> None:
    uid = "550e8400-e29b-41d4-a716-446655440000"
    result = mask_user_id(uid)
    assert result == "550e8400…"
    assert len(result) == 9


def test_mask_user_id_short_string() -> None:
    assert mask_user_id("abc") == "abc…"


def test_mom_annotations_first_is_empty() -> None:
    series = pd.Series([1000.0, 1200.0, 900.0])
    result = _mom_annotations(series)
    assert result[0] == ""


def test_mom_annotations_positive_change() -> None:
    series = pd.Series([1000.0, 1200.0])
    result = _mom_annotations(series)
    assert result[1] == "+20.0%"


def test_mom_annotations_negative_change() -> None:
    series = pd.Series([1000.0, 800.0])
    result = _mom_annotations(series)
    assert result[1] == "-20.0%"


def test_mom_annotations_zero_prev_skips() -> None:
    series = pd.Series([0.0, 500.0])
    result = _mom_annotations(series)
    assert result[1] == ""


def test_resample_conv_daily_unchanged(conv_daily: pd.DataFrame) -> None:
    result = _resample_conv(conv_daily, "Diaria")
    assert len(result) == len(conv_daily)


def test_resample_conv_monthly_reduces_rows(conv_daily: pd.DataFrame) -> None:
    # 5 days spanning a single month → 1 monthly bucket
    result = _resample_conv(conv_daily, "Mensal")
    assert len(result) == 1


def test_resample_conv_sums_correctly(conv_daily: pd.DataFrame) -> None:
    result = _resample_conv(conv_daily, "Mensal")
    assert float(result["onramp"].iloc[0]) == pytest.approx(65_000.0)


# ---------------------------------------------------------------------------
# Figure builder tests
# ---------------------------------------------------------------------------


def test_fig_volume_returns_figure(conv_daily: pd.DataFrame) -> None:
    fig = _fig_volume(conv_daily)
    assert isinstance(fig, go.Figure)


def test_fig_volume_has_two_traces(conv_daily: pd.DataFrame) -> None:
    fig = _fig_volume(conv_daily)
    assert len(fig.data) == 2


def test_fig_volume_missing_column() -> None:
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=3), "onramp": [1, 2, 3]})
    fig = _fig_volume(df)
    assert len(fig.data) == 1  # only onramp trace


def test_fig_volume_weekly_granularity(conv_daily: pd.DataFrame) -> None:
    fig = _fig_volume(conv_daily, "Semanal")
    assert isinstance(fig, go.Figure)


def test_fig_revenue_monthly_stacked(revenue_monthly: pd.DataFrame) -> None:
    fig = _fig_revenue_monthly(revenue_monthly)
    assert isinstance(fig, go.Figure)
    assert fig.layout.barmode == "stack"


def test_fig_revenue_monthly_includes_mom_trace(revenue_monthly: pd.DataFrame) -> None:
    fig = _fig_revenue_monthly(revenue_monthly)
    # 2 bar traces + 1 invisible scatter for MoM annotations
    assert len(fig.data) == 3


def test_fig_fx_rate_creates_bands(fx_stats: pd.DataFrame) -> None:
    fig = _fig_fx_rate(fx_stats)
    assert isinstance(fig, go.Figure)
    # 3 traces per side (p90 invisible, p10 fill, mean line) × 2 sides = 6
    assert len(fig.data) == 6


def test_fig_fx_rate_single_side() -> None:
    df = pd.DataFrame(
        {
            "period": pd.date_range("2026-01-01", periods=3),
            "side": "onramp",
            "fx_mean": [5.8, 5.9, 5.85],
            "fx_p10": [5.7, 5.8, 5.75],
            "fx_p90": [5.9, 6.0, 5.95],
            "n": [10, 10, 10],
        }
    )
    fig = _fig_fx_rate(df)
    assert len(fig.data) == 3  # p90 + p10 band + mean for one side


def test_fig_pix_two_traces(pix_daily: pd.DataFrame) -> None:
    fig = _fig_pix(pix_daily)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2


def test_fig_pix_missing_column() -> None:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=3).date,
            "pix_in": [1000.0, 2000.0, 1500.0],
        }
    )
    fig = _fig_pix(df)
    assert len(fig.data) == 1


def test_fig_spread_histogram_returns_figure(spread_stats: pd.DataFrame) -> None:
    fig = _fig_spread_histogram(spread_stats)
    assert isinstance(fig, go.Figure)


def test_fig_spread_histogram_two_histogram_traces(spread_stats: pd.DataFrame) -> None:
    fig = _fig_spread_histogram(spread_stats)
    hist_traces = [t for t in fig.data if isinstance(t, go.Histogram)]
    assert len(hist_traces) == 2


def test_fig_new_vs_returning_stacked(new_vs_returning: pd.DataFrame) -> None:
    fig = _fig_new_vs_returning(new_vs_returning)
    assert isinstance(fig, go.Figure)
    assert fig.layout.barmode == "stack"
    assert len(fig.data) == 2
