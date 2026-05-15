"""Unit tests for nbs_bi.reporting.marketing.

Tests cover the pure data-transform functions (_build_cumulative_spend,
_build_channel_comparison) and KPI formula logic.  All Streamlit render
methods are excluded via pragma: no cover.
"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from nbs_bi.reporting.marketing import (
    MetaAdsSection,
    _build_channel_comparison,
    _build_cumulative_spend,
    _fig_cumulative_profit,
    _fig_cumulative_spend,
    _fig_daily_rev_all_vs_cohort,
    _load_acquisition_for_dates,
)


def test_default_ads_start_respects_constant():
    """_DEFAULT_ADS_START must be 2026-04-14 and be a datetime.date."""
    from nbs_bi.reporting.marketing import _DEFAULT_ADS_START

    assert _DEFAULT_ADS_START == datetime.date(2026, 4, 14)

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
                "acquisition_source": "founder",
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
    assert len(result) == len(campaign_summary) + len(acquisition_summary)


def test_build_channel_comparison_campaign_rows(campaign_summary, acquisition_summary):
    result = _build_channel_comparison(campaign_summary, acquisition_summary)
    campaign_ids = set(campaign_summary["campaign_id"])
    result_ids = set(result["acquisition_source"])
    assert campaign_ids.issubset(result_ids)
    c1 = result[result["acquisition_source"] == "campaign_1"].iloc[0]
    assert c1["spend_usd"] == pytest.approx(120.0)
    assert c1["roas"] == pytest.approx(2.10)


def test_build_channel_comparison_non_campaign_spend_nan(campaign_summary, acquisition_summary):
    result = _build_channel_comparison(campaign_summary, acquisition_summary)
    organic = result[~result["acquisition_source"].str.startswith("campaign_")]
    assert organic["spend_usd"].isna().all()
    assert organic["roas"].isna().all()


def test_build_channel_comparison_excludes_mkt_ads_from_acquisition(campaign_summary):
    acq_with_mkt_ads = pd.DataFrame(
        [
            {"acquisition_source": "mkt_ads", "n_users": 500, "avg_net_revenue_usd": 5.0, "total_net_revenue_usd": 2500.0, "conversion_rate": 0.1},
            {"acquisition_source": "organic", "n_users": 1000, "avg_net_revenue_usd": 10.0, "total_net_revenue_usd": 10000.0, "conversion_rate": 0.15},
        ]
    )
    result = _build_channel_comparison(campaign_summary, acq_with_mkt_ads)
    assert "mkt_ads" not in result["acquisition_source"].values
    assert "organic" in result["acquisition_source"].values


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
    assert len(result) == len(campaign_summary)
    assert set(result["acquisition_source"]) == set(campaign_summary["campaign_id"])


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
    assert result.iloc[0]["avg_operational_profit_usd"] == 0.0
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


# ---------------------------------------------------------------------------
# Per-platform spend chart behaviour
# ---------------------------------------------------------------------------


def _make_multi_platform_spend() -> pd.DataFrame:
    import datetime

    dates = [datetime.date(2026, 2, 15) + pd.Timedelta(days=i) for i in range(3)]
    rows = []
    for d in dates:
        rows.append({"date": d, "platform": "meta", "daily_spend_usd": 20.0})
        rows.append({"date": d, "platform": "google", "daily_spend_usd": 10.0})
    return pd.DataFrame(rows)


def _make_single_platform_spend() -> pd.DataFrame:
    import datetime

    dates = [datetime.date(2026, 2, 15) + pd.Timedelta(days=i) for i in range(3)]
    return pd.DataFrame(
        {"date": dates, "platform": ["meta"] * 3, "daily_spend_usd": [20.0, 22.0, 18.0]}
    )


def test_fig_cumulative_spend_multi_platform_has_two_spend_traces(campaigns):
    """When per_platform_spend has two platforms, two cumulative spend lines are drawn."""
    pp = _make_multi_platform_spend()
    agg = pp.groupby("date")["daily_spend_usd"].sum().reset_index()
    cum_df = _build_cumulative_spend(agg, campaigns)
    fig = _fig_cumulative_spend(cum_df, campaigns, per_platform_spend=pp)
    assert fig is not None
    spend_trace_names = [t.name for t in fig.data if "Spend" in (t.name or "")]
    assert len(spend_trace_names) == 2
    assert any("Meta" in n for n in spend_trace_names)
    assert any("Google" in n for n in spend_trace_names)


def test_fig_cumulative_spend_single_platform_has_one_spend_trace(campaigns, daily_spend):
    """When per_platform_spend has one platform, the single combined line is used."""
    pp = _make_single_platform_spend()
    cum_df = _build_cumulative_spend(daily_spend, campaigns)
    fig = _fig_cumulative_spend(cum_df, campaigns, per_platform_spend=pp)
    assert fig is not None
    spend_traces = [t for t in fig.data if "Spend" in (t.name or "")]
    assert len(spend_traces) == 1


def test_fig_cumulative_spend_no_platform_column_falls_back_to_combined(campaigns, daily_spend):
    """When per_platform_spend has no platform column, original combined line is kept."""
    pp = daily_spend.copy()  # no platform column
    cum_df = _build_cumulative_spend(daily_spend, campaigns)
    fig = _fig_cumulative_spend(cum_df, campaigns, per_platform_spend=pp)
    assert fig is not None
    spend_traces = [t for t in fig.data if "Cumulative Spend" in (t.name or "")]
    assert len(spend_traces) == 1


def test_fig_daily_rev_all_vs_cohort_multi_platform_spend_lines():
    """When per_platform_spend has two platforms, two ad-spend lines appear in the chart."""
    all_users = pd.DataFrame(
        {
            "date": pd.date_range("2026-02-15", periods=3, freq="D"),
            "daily_rev_conversion_usd": [100.0, 120.0, 90.0],
            "daily_rev_card_fees_usd": [10.0, 12.0, 8.0],
            "daily_rev_billing_usd": [5.0, 6.0, 4.0],
        }
    )
    spend_agg = pd.DataFrame(
        {
            "date": pd.date_range("2026-02-15", periods=3, freq="D"),
            "daily_spend_usd": [30.0, 32.0, 28.0],
        }
    )
    pp = _make_multi_platform_spend()
    fig = _fig_daily_rev_all_vs_cohort(all_users, pd.DataFrame(), spend_agg, per_platform_spend=pp)
    assert fig is not None
    spend_traces = [t for t in fig.data if "Spend" in (t.name or "")]
    assert len(spend_traces) == 2
    assert any("Meta" in t.name for t in spend_traces)
    assert any("Google" in t.name for t in spend_traces)


# ---------------------------------------------------------------------------
# MetaAdsSection invoice_total param (Task 2)
# ---------------------------------------------------------------------------


def test_metaads_section_stores_invoice_total():
    section = MetaAdsSection(campaign_data=None, acquisition=None, invoice_total=99.50)
    assert section._invoice_total == 99.50


def test_metaads_section_invoice_total_defaults_to_zero():
    section = MetaAdsSection(campaign_data=None, acquisition=None)
    assert section._invoice_total == 0.0


# ---------------------------------------------------------------------------
# _load_acquisition_for_dates (Task 3)
# ---------------------------------------------------------------------------


def test_load_acquisition_for_dates_returns_dataframe():
    """_load_acquisition_for_dates calls ClientReport and returns acquisition key."""
    from unittest.mock import MagicMock, patch

    expected = pd.DataFrame([{"acquisition_source": "organic", "n_users": 10}])
    mock_report = MagicMock()
    mock_report.build.return_value = {"acquisition": expected}

    with patch("nbs_bi.clients.report.ClientReport", return_value=mock_report):
        _load_acquisition_for_dates.clear()
        result = _load_acquisition_for_dates(
            "2026-04-14", "2026-05-15", "postgresql://test", 100.0
        )

    pd.testing.assert_frame_equal(result, expected)


def test_load_acquisition_for_dates_missing_key_returns_empty():
    """When ClientReport.build() has no 'acquisition' key, return empty DataFrame."""
    from unittest.mock import MagicMock, patch

    mock_report = MagicMock()
    mock_report.build.return_value = {}

    with patch("nbs_bi.clients.report.ClientReport", return_value=mock_report):
        _load_acquisition_for_dates.clear()
        result = _load_acquisition_for_dates(
            "2026-04-14", "2026-05-15", "postgresql://test", 0.0
        )

    assert isinstance(result, pd.DataFrame)
    assert result.empty


# ---------------------------------------------------------------------------
# MetaAdsSection analytics_db_url wiring (Task 6)
# ---------------------------------------------------------------------------


def test_metaads_section_uses_analytics_db_url_for_channel():
    """When analytics_db_url is set, section stores correct values for lazy load."""
    section = MetaAdsSection(
        campaign_data=None,
        acquisition=None,
        analytics_db_url="postgresql://test",
        invoice_total=150.0,
    )
    assert section._analytics_db_url == "postgresql://test"
    assert section._invoice_total == 150.0
    assert section._acquisition is None
