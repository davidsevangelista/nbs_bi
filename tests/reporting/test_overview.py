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
