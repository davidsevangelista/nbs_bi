"""Regression tests for _fig_monthly_revenue and _resample_combined."""

import datetime

import pandas as pd
import pytest

from nbs_bi.reporting.overview import _fig_monthly_revenue


def _rev_df(dates):
    """Helper: daily revenue DataFrame with datetime64 dates."""
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "fee_usd": [10.0] * len(dates),
            "spread_usd": [5.0] * len(dates),
        }
    )


def _card_rev_df_date_objects(dates):
    """Helper: card revenue DataFrame with Python datetime.date objects (as from SQL)."""
    return pd.DataFrame(
        {
            "date": [
                datetime.date(int(d[:4]), int(d[5:7]), int(d[8:10])) for d in dates
            ],
            "card_fee_usd": [2.0] * len(dates),
            "billing_usd": [1.0] * len(dates),
        }
    )


def test_fig_monthly_revenue_non_zero_with_date_object_card_rev():
    """revenue rows must be non-zero even when card_revenue dates are datetime.date objects."""
    dates = ["2026-01-01", "2026-01-02"]
    rev = _rev_df(dates)
    card_rev = _card_rev_df_date_objects(dates)

    fig = _fig_monthly_revenue(rev, card_rev)
    assert fig is not None

    fee_trace = next(t for t in fig.data if t.name == "Conv Fees")
    assert all(y > 0 for y in fee_trace.y), f"fee_usd is zero: {fee_trace.y}"


def test_fig_monthly_revenue_non_zero_without_card_rev():
    """revenue rows must be non-zero when card_revenue is None."""
    dates = ["2026-01-01", "2026-01-02"]
    rev = _rev_df(dates)

    fig = _fig_monthly_revenue(rev, None)
    assert fig is not None

    fee_trace = next(t for t in fig.data if t.name == "Conv Fees")
    assert all(y > 0 for y in fee_trace.y), f"fee_usd is zero: {fee_trace.y}"
