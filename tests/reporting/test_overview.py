"""Tests for nbs_bi.reporting.overview — combined volume figure helpers."""

import pandas as pd
import pytest
import plotly.graph_objects as go

from nbs_bi.reporting.overview import _resample_combined, _fig_combined_volume


def _make_combined(n: int = 6) -> pd.DataFrame:
    """n days of merged conv+card data starting 2026-01-01."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "conv_usd": [10.0] * n,
        "card_usd": [5.0] * n,
    })


def test_resample_combined_daily_unchanged() -> None:
    df = _make_combined(6)
    result = _resample_combined(df, "Daily")
    assert len(result) == 6


def test_resample_combined_weekly_reduces_rows() -> None:
    # 14 days → 2 weeks
    df = _make_combined(14)
    result = _resample_combined(df, "Weekly")
    assert len(result) == 2


def test_resample_combined_monthly_reduces_rows() -> None:
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "conv_usd": [10.0] * 60,
        "card_usd": [5.0] * 60,
    })
    result = _resample_combined(df, "Monthly")
    assert len(result) == 2


def test_resample_combined_weekly_sums_correctly() -> None:
    # 7 days, conv_usd=10 each → weekly sum = 70
    df = _make_combined(7)
    result = _resample_combined(df, "Weekly")
    assert result["conv_usd"].iloc[0] == pytest.approx(70.0)
    assert result["card_usd"].iloc[0] == pytest.approx(35.0)


def test_resample_combined_daily_preserves_values() -> None:
    df = _make_combined(3)
    result = _resample_combined(df, "Daily")
    assert result["conv_usd"].iloc[0] == pytest.approx(10.0)
    assert result["card_usd"].iloc[0] == pytest.approx(5.0)


def _make_conv_daily(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "onramp": [1000.0] * n,
        "offramp": [500.0] * n,
    })


def _make_card_daily(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "amount_usd": [200.0] * n,
    })


def test_fig_combined_volume_returns_figure() -> None:
    fig = _fig_combined_volume(_make_conv_daily(), _make_card_daily(), fx_rate=5.0, granularity="Monthly")
    assert isinstance(fig, go.Figure)


def test_fig_combined_volume_has_two_bar_traces() -> None:
    fig = _fig_combined_volume(_make_conv_daily(), _make_card_daily(), fx_rate=5.0, granularity="Monthly")
    bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
    assert len(bar_traces) == 2


def test_fig_combined_volume_returns_none_when_both_empty() -> None:
    fig = _fig_combined_volume(pd.DataFrame(), pd.DataFrame(), fx_rate=5.0, granularity="Monthly")
    assert fig is None


def test_fig_combined_volume_conv_usd_uses_fx_rate() -> None:
    # onramp=1000 BRL, offramp=0, fx_rate=5.0 → conv_usd=200 per day
    conv = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=1), "onramp": [1000.0], "offramp": [0.0]})
    card = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=1), "amount_usd": [0.0]})
    fig = _fig_combined_volume(conv, card, fx_rate=5.0, granularity="Daily")
    conv_trace = fig.data[0]
    assert float(conv_trace.y[0]) == pytest.approx(200.0)


def test_fig_combined_volume_card_only_when_no_conv() -> None:
    # Empty conv_daily → conv bar is zero, card bar has value
    card = _make_card_daily(1)
    fig = _fig_combined_volume(pd.DataFrame(), card, fx_rate=5.0, granularity="Daily")
    assert isinstance(fig, go.Figure)
    bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
    assert len(bar_traces) == 2
    # card trace has non-zero value
    card_trace = bar_traces[1]
    assert float(card_trace.y[0]) == pytest.approx(200.0)


def test_fig_combined_volume_zero_fx_rate_does_not_raise() -> None:
    fig = _fig_combined_volume(_make_conv_daily(), _make_card_daily(), fx_rate=0.0, granularity="Monthly")
    # Should not raise; conversion bars will be NaN/inf → handled gracefully
    assert fig is not None
