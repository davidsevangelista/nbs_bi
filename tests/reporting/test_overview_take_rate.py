"""Unit tests for the take rate computation and figure helpers."""

import pandas as pd
import plotly.graph_objects as go
import pytest

from nbs_bi.reporting.overview import _compute_take_rate, _fig_take_rate


@pytest.fixture()
def daily_rev() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "fee_usd": [10.0, 20.0],
            "spread_usd": [5.0, 10.0],
        }
    )


@pytest.fixture()
def daily_card_rev() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "card_fee_usd": [2.0, 4.0],
            "billing_usd": [1.0, 2.0],
        }
    )


@pytest.fixture()
def daily_conv() -> pd.DataFrame:
    # BRL volumes; fx_rate=5.0 → USD = BRL/5
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "onramp": [500.0, 1000.0],
            "offramp": [0.0, 0.0],
        }
    )


@pytest.fixture()
def daily_card() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "amount_usd": [50.0, 100.0],
        }
    )


def test_compute_take_rate_basic(daily_rev, daily_card_rev, daily_conv, daily_card):
    # Day 1: rev = 10+5+2+1 = 18; vol = 500/5 + 50 = 150; rate = 18/150 = 12%
    # Day 2: rev = 20+10+4+2 = 36; vol = 1000/5 + 100 = 300; rate = 36/300 = 12%
    result = _compute_take_rate(
        daily_rev, daily_card_rev, daily_conv, daily_card,
        fx_rate=5.0,
        granularity="Daily",
    )
    assert list(result.columns) == ["date", "take_rate_pct"]
    assert len(result) == 2
    assert abs(result.loc[0, "take_rate_pct"] - 12.0) < 0.01
    assert abs(result.loc[1, "take_rate_pct"] - 12.0) < 0.01


def test_compute_take_rate_zero_volume_row_dropped(daily_rev, daily_card_rev):
    conv = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "onramp": [0.0, 1000.0],
            "offramp": [0.0, 0.0],
        }
    )
    card_zero = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "amount_usd": [0.0, 100.0],
        }
    )
    result = _compute_take_rate(
        daily_rev, daily_card_rev, conv, card_zero,
        fx_rate=5.0,
        granularity="Daily",
    )
    # Day 1 has zero volume → must be absent
    assert len(result) == 1
    assert pd.Timestamp("2026-01-01") not in result["date"].values


def test_compute_take_rate_all_empty():
    result = _compute_take_rate(
        pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        fx_rate=5.0,
        granularity="Daily",
    )
    assert result.empty
    assert list(result.columns) == ["date", "take_rate_pct"]


def test_compute_take_rate_weekly(daily_rev, daily_card_rev, daily_conv, daily_card):
    # Both dates (2026-01-01 and 2026-01-02) fall in the same W-MON bucket
    result = _compute_take_rate(
        daily_rev, daily_card_rev, daily_conv, daily_card,
        fx_rate=5.0,
        granularity="Weekly",
    )
    assert len(result) == 1
    assert "take_rate_pct" in result.columns
    assert result.loc[0, "take_rate_pct"] > 0


def test_fig_take_rate_returns_none_on_empty():
    assert _fig_take_rate(pd.DataFrame()) is None
    assert _fig_take_rate(pd.DataFrame(columns=["date", "take_rate_pct"])) is None


def test_fig_take_rate_has_one_scatter_trace():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "take_rate_pct": [5.0, 6.0],
        }
    )
    fig = _fig_take_rate(df)
    assert fig is not None
    assert len(fig.data) == 1
    assert isinstance(fig.data[0], go.Scatter)


def test_fig_take_rate_y_axis_labelled_pct():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"]),
            "take_rate_pct": [5.0],
        }
    )
    fig = _fig_take_rate(df)
    assert fig.layout.yaxis.title.text == "%"
