"""Unit tests for nbs_bi.reporting.marketing.

Tests cover the pure data-transform functions (_build_cumulative_spend,
_build_channel_comparison) and KPI formula logic.  All Streamlit render
methods are excluded via pragma: no cover.
"""

from __future__ import annotations

import pandas as pd
import pytest

from nbs_bi.reporting.marketing import (
    MetaAdsSection,
    _build_channel_comparison,
    _build_cumulative_spend,
    _fig_cumulative_profit,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def campaign_summary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "campaign_id": "campaign_1",
                "start": "2026-02-15",
                "end": "2026-02-20",
                "duration_days": 6,
                "total_spend_usd": 120.0,
                "cohort_users": 40,
                "transacting_users": 8,
                "transacting_rate": 0.2,
                "baseline_rate_per_day": 5.0,
                "incremental_users_est": 10.0,
                "total_revenue_usd": 252.0,
                "roas": 2.10,
                "cac_full": 3.0,
                "cac_incremental": 12.0,
                "avg_rev_per_transacting_user": 31.5,
            },
            {
                "campaign_id": "campaign_2",
                "start": "2026-04-14",
                "end": "2026-04-20",
                "duration_days": 7,
                "total_spend_usd": 230.0,
                "cohort_users": 180,
                "transacting_users": 12,
                "transacting_rate": 0.0667,
                "baseline_rate_per_day": 20.0,
                "incremental_users_est": 40.0,
                "total_revenue_usd": 94.3,
                "roas": 0.41,
                "cac_full": 1.28,
                "cac_incremental": 5.75,
                "avg_rev_per_transacting_user": 7.86,
            },
        ]
    )


@pytest.fixture()
def daily_spend() -> pd.DataFrame:
    import datetime

    dates = [datetime.date(2026, 2, 15) + pd.Timedelta(days=i) for i in range(6)]
    return pd.DataFrame(
        {"date": dates, "daily_spend_usd": [20.0, 22.0, 18.0, 25.0, 17.0, 18.0]}
    )


@pytest.fixture()
def campaigns() -> list[dict]:
    import datetime

    return [
        {
            "campaign_id": "campaign_1",
            "start": datetime.date(2026, 2, 15),
            "end": datetime.date(2026, 2, 20),
        }
    ]


@pytest.fixture()
def acquisition_summary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "acquisition_source": "organic",
                "n_users": 5000,
                "avg_net_revenue_usd": 12.5,
                "total_net_revenue_usd": 62500.0,
                "conversion_rate": 0.18,
            },
            {
                "acquisition_source": "referral",
                "n_users": 3000,
                "avg_net_revenue_usd": 18.0,
                "total_net_revenue_usd": 54000.0,
                "conversion_rate": 0.22,
            },
            {
                "acquisition_source": "founder_invite",
                "n_users": 1200,
                "avg_net_revenue_usd": 28.0,
                "total_net_revenue_usd": 33600.0,
                "conversion_rate": 0.30,
            },
            {
                "acquisition_source": "unknown",
                "n_users": 2278,
                "avg_net_revenue_usd": 5.0,
                "total_net_revenue_usd": 11390.0,
                "conversion_rate": 0.08,
            },
        ]
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_cumulative_spend_monotonic(daily_spend, campaigns):
    result = _build_cumulative_spend(daily_spend, campaigns)
    cumulative = result["cumulative_spend_usd"].tolist()
    assert cumulative == sorted(cumulative), "cumulative_spend_usd must be non-decreasing"


def test_build_cumulative_spend_total(daily_spend, campaigns):
    result = _build_cumulative_spend(daily_spend, campaigns)
    assert result["cumulative_spend_usd"].iloc[-1] == pytest.approx(120.0)


def test_build_cumulative_spend_campaign_flags(daily_spend, campaigns):
    result = _build_cumulative_spend(daily_spend, campaigns)
    start_str = str(campaigns[0]["start"])
    start_rows = result[result["date"].astype(str) == start_str]
    assert len(start_rows) == 1
    assert bool(start_rows.iloc[0]["is_campaign_start"]) is True
    non_starts = result[result["date"].astype(str) != start_str]
    assert non_starts["is_campaign_start"].sum() == 0


def test_build_channel_comparison_row_count(campaign_summary, acquisition_summary):
    result = _build_channel_comparison(campaign_summary, acquisition_summary)
    assert len(result) == len(acquisition_summary) + 1


def test_build_channel_comparison_meta_row(campaign_summary, acquisition_summary):
    result = _build_channel_comparison(campaign_summary, acquisition_summary)
    meta = result[result["acquisition_source"] == "meta_ads"].iloc[0]
    assert meta["spend_usd"] == pytest.approx(campaign_summary["total_spend_usd"].sum())


def test_build_channel_comparison_non_meta_spend_nan(campaign_summary, acquisition_summary):
    result = _build_channel_comparison(campaign_summary, acquisition_summary)
    non_meta = result[result["acquisition_source"] != "meta_ads"]
    assert non_meta["spend_usd"].isna().all()
    assert non_meta["roas"].isna().all()


def test_kpi_cac_formula(campaign_summary):
    total_spend = float(campaign_summary["total_spend_usd"].sum())
    n_users = int(campaign_summary["cohort_users"].sum())
    cac = total_spend / n_users
    assert cac == pytest.approx(350.0 / 220.0)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_summary_no_exception():
    section = MetaAdsSection(campaign_data=None, acquisition=None)
    # render() is excluded from coverage; verify construction doesn't raise
    assert section._data is None


def test_empty_acquisition(campaign_summary):
    result = _build_channel_comparison(campaign_summary, pd.DataFrame())
    # Only the meta_ads row should be present
    assert len(result) == 1
    assert result.iloc[0]["acquisition_source"] == "meta_ads"


def test_single_campaign_cumulative(campaigns, daily_spend):
    result = _build_cumulative_spend(daily_spend, campaigns)
    assert len(result) == len(daily_spend)
    assert result["is_campaign_start"].sum() == 1


def test_zero_cohort_users_no_division_error():
    summary = pd.DataFrame(
        [
            {
                "campaign_id": "campaign_1",
                "total_spend_usd": 100.0,
                "cohort_users": 0,
                "transacting_users": 0,
                "total_revenue_usd": 0.0,
            }
        ]
    )
    result = _build_channel_comparison(summary, pd.DataFrame())
    assert result.iloc[0]["avg_net_revenue_usd"] == 0.0
    assert result.iloc[0]["conversion_rate"] == 0.0


def test_zero_spend_roas_is_nan():
    summary = pd.DataFrame(
        [
            {
                "campaign_id": "campaign_1",
                "total_spend_usd": 0.0,
                "cohort_users": 50,
                "transacting_users": 5,
                "total_revenue_usd": 100.0,
            }
        ]
    )
    result = _build_channel_comparison(summary, pd.DataFrame())
    assert pd.isna(result.iloc[0]["roas"])


# ---------------------------------------------------------------------------
# _fig_cumulative_profit — new 3-line schema
# ---------------------------------------------------------------------------


def _make_cum_profit_df() -> pd.DataFrame:
    """Minimal cum_profit_df with the current column schema."""
    dates = pd.date_range("2026-04-14", periods=5, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "daily_rev_conversion_usd": [5.0, 8.0, 6.0, 7.0, 9.0],
            "daily_rev_card_fees_usd": [1.0, 2.0, 1.5, 1.0, 2.0],
            "daily_rev_total_usd": [6.0, 10.0, 7.5, 8.0, 11.0],
            "daily_card_cogs_usd": [3.0, 4.0, 3.5, 3.0, 4.5],
            "daily_ad_spend_usd": [18.0, 11.0, 0.0, 0.0, 0.0],
            "daily_profit_usd": [-15.0, -5.0, 4.0, 5.0, 6.5],
            "cum_rev_usd": [6.0, 16.0, 23.5, 31.5, 42.5],
            "cum_card_cogs_usd": [3.0, 7.0, 10.5, 13.5, 18.0],
            "cum_profit_usd": [-15.0, -20.0, -16.0, -11.0, -4.5],
            "cum_contribution_margin_usd": [-33.0, -44.0, -40.0, -35.0, -28.5],
            "cum_txn_count": [10, 20, 28, 35, 44],
            "cum_conversion_count": [3, 7, 10, 13, 17],
        }
    )


def test_fig_cumulative_profit_returns_figure():
    df = _make_cum_profit_df()
    fig = _fig_cumulative_profit(df)
    assert fig is not None


def test_fig_cumulative_profit_six_traces():
    df = _make_cum_profit_df()
    fig = _fig_cumulative_profit(df)
    assert fig is not None
    # Revenue, Card Cost, Contribution Margin, Operational Profit, Card Txns, Conversions
    assert len(fig.data) == 6


def test_fig_cumulative_profit_returns_none_when_empty():
    assert _fig_cumulative_profit(pd.DataFrame()) is None


def test_fig_cumulative_profit_returns_none_missing_columns():
    # Old schema (missing cum_card_cogs_usd) should return None
    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-04-14", periods=2, freq="D"),
            "cum_profit_usd": [-5.0, 2.0],
        }
    )
    assert _fig_cumulative_profit(df) is None
