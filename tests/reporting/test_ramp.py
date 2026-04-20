"""Tests for nbs_bi.reporting.ramp — figure builders and helpers.

All tests are pure (no Streamlit server required). The Streamlit render
methods are integration-level and covered by manual smoke tests.
"""

import pandas as pd
import plotly.graph_objects as go
import pytest

from nbs_bi.reporting.ramp import (
    _fig_pix,
    _fig_pnl,
    _fig_position,
    _fig_fx_rate,
    _fig_revenue_monthly,
    _fig_volume,
    _hex_to_rgb,
    _kpi,
    _mask_user_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def summary() -> pd.DataFrame:
    return pd.DataFrame([
        {"metric": "Total conversions", "value": 120.0, "note": ""},
        {"metric": "Onramp volume BRL", "value": 500_000.0, "note": ""},
        {"metric": "Offramp volume BRL", "value": 200_000.0, "note": ""},
        {"metric": "Total revenue BRL", "value": 8_500.0, "note": ""},
    ])


@pytest.fixture()
def conv_daily() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5),
        "onramp": [10_000.0, 15_000.0, 8_000.0, 20_000.0, 12_000.0],
        "offramp": [5_000.0, 3_000.0, 7_000.0, 4_000.0, 6_000.0],
    })


@pytest.fixture()
def revenue_monthly() -> pd.DataFrame:
    return pd.DataFrame({
        "month": pd.date_range("2026-01-01", periods=3, freq="MS"),
        "fee_brl": [1_000.0, 1_200.0, 1_500.0],
        "spread_brl": [3_000.0, 3_500.0, 4_000.0],
        "total_revenue_brl": [4_000.0, 4_700.0, 5_500.0],
    })


@pytest.fixture()
def fx_stats() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=4)
    onramp = pd.DataFrame({
        "period": dates,
        "side": "onramp",
        "fx_mean": [5.80, 5.85, 5.78, 5.90],
        "fx_p10": [5.70, 5.75, 5.68, 5.80],
        "fx_p90": [5.90, 5.95, 5.88, 6.00],
        "n": [30, 28, 35, 32],
    })
    offramp = pd.DataFrame({
        "period": dates,
        "side": "offramp",
        "fx_mean": [5.70, 5.75, 5.68, 5.80],
        "fx_p10": [5.60, 5.65, 5.58, 5.70],
        "fx_p90": [5.80, 5.85, 5.78, 5.90],
        "n": [10, 12, 8, 15],
    })
    return pd.concat([onramp, offramp], ignore_index=True)


@pytest.fixture()
def position() -> pd.DataFrame:
    return pd.DataFrame({
        "created_at": pd.date_range("2026-01-01", periods=5),
        "side": ["offramp", "offramp", "onramp", "onramp", "onramp"],
        "position_qty_usdc": [1000.0, 1800.0, 1300.0, 800.0, 300.0],
        "avg_price_brl_per_usdc": [5.80, 5.82, 5.82, 5.82, 5.82],
        "pnl_brl": [0.0, 0.0, 26.0, 26.0, 26.0],
        "pnl_cum_brl": [0.0, 0.0, 26.0, 52.0, 78.0],
        "margin_sell_pct": [None, None, 0.045, 0.045, 0.045],
        "margin_buy_pct": [None, None, None, None, None],
    })


@pytest.fixture()
def pix_daily() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5).date,
        "pix_in": [50_000.0, 60_000.0, 45_000.0, 70_000.0, 55_000.0],
        "pix_out": [20_000.0, 25_000.0, 18_000.0, 30_000.0, 22_000.0],
        "pix_net": [30_000.0, 35_000.0, 27_000.0, 40_000.0, 33_000.0],
    })


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
    result = _mask_user_id(uid)
    assert result == "550e8400…"
    assert len(result) == 9


def test_mask_user_id_short_string() -> None:
    assert _mask_user_id("abc") == "abc…"


def test_hex_to_rgb_blue() -> None:
    assert _hex_to_rgb("#2196F3") == "33, 150, 243"


def test_hex_to_rgb_without_hash() -> None:
    assert _hex_to_rgb("FF9800") == "255, 152, 0"


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


def test_fig_revenue_monthly_stacked(revenue_monthly: pd.DataFrame) -> None:
    fig = _fig_revenue_monthly(revenue_monthly)
    assert isinstance(fig, go.Figure)
    assert fig.layout.barmode == "stack"
    assert len(fig.data) == 2


def test_fig_fx_rate_creates_bands(fx_stats: pd.DataFrame) -> None:
    fig = _fig_fx_rate(fx_stats)
    assert isinstance(fig, go.Figure)
    # 3 traces per side (p90 invisible, p10 fill, mean line) × 2 sides = 6
    assert len(fig.data) == 6


def test_fig_fx_rate_single_side() -> None:
    df = pd.DataFrame({
        "period": pd.date_range("2026-01-01", periods=3),
        "side": "onramp",
        "fx_mean": [5.8, 5.9, 5.85],
        "fx_p10": [5.7, 5.8, 5.75],
        "fx_p90": [5.9, 6.0, 5.95],
        "n": [10, 10, 10],
    })
    fig = _fig_fx_rate(df)
    assert len(fig.data) == 3  # p90 + p10 band + mean for one side


def test_fig_position_dual_axis(position: pd.DataFrame) -> None:
    fig = _fig_position(position)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2  # bar + line


def test_fig_pnl_returns_figure(position: pd.DataFrame) -> None:
    fig = _fig_pnl(position)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    assert fig.data[0].fill == "tozeroy"


def test_fig_pix_two_traces(pix_daily: pd.DataFrame) -> None:
    fig = _fig_pix(pix_daily)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2


def test_fig_pix_missing_column() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3).date,
        "pix_in": [1000.0, 2000.0, 1500.0],
    })
    fig = _fig_pix(df)
    assert len(fig.data) == 1
