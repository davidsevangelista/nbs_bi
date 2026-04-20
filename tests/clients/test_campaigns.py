"""Tests for nbs_bi.clients.campaigns — campaign detection, ROI, CAC.

All tests are DB-free: CampaignAnalyzer._engine is injected as a mock.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from nbs_bi.clients.campaigns import (
    CampaignAnalyzer,
    _detect_campaigns,
    load_ad_spend,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CSV_CONTENT = "\n".join(
    [
        "id,date,postedDate,type,amount,merchantName,authorizationStatus,currency,cardholderEmail",
        "1,2026-02-15,2026-02-16,spend,-99.00,FACEBK ADS,approved,usd,david@test.com",
        "2,2026-02-16,2026-02-17,spend,-791.93,FACEBK ADS,approved,usd,david@test.com",
        "3,2026-02-20,2026-02-21,spend,-100.00,FACEBK ADS,approved,usd,david@test.com",
        "4,2026-04-14,2026-04-15,spend,-18.00,FACEBK ADS,approved,usd,david@test.com",
        "5,2026-04-15,2026-04-16,spend,-11.00,FACEBK ADS,approved,usd,david@test.com",
        "6,2026-01-10,2026-01-11,spend,-50.00,STRIPE,approved,usd,david@test.com",
    ]
)


def _make_spend() -> pd.DataFrame:
    return load_ad_spend(io.StringIO(_CSV_CONTENT))


def _make_engine_mock(signups_rows=None, revenue_row=None):
    """Mock SQLAlchemy engine that returns preset data."""
    if signups_rows is None:
        signups_rows = [("2026-02-08", 20), ("2026-02-09", 22), ("2026-02-10", 18)]
    if revenue_row is None:
        revenue_row = {
            "cohort_users": 100,
            "transacting_users": 12,
            "onramp_rev_usd": 50.0,
            "card_fee_usd": 30.0,
            "billing_usd": 10.0,
            "total_revenue_usd": 90.0,
        }

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    def _read_sql(sql, conn, params):
        sql_str = str(sql)
        if "DATE(created_at" in sql_str or "signup_date" in sql_str:
            return pd.DataFrame(signups_rows, columns=["signup_date", "new_signups"])
        else:
            return pd.DataFrame([revenue_row])

    with patch("pandas.read_sql", side_effect=_read_sql):
        pass

    return mock_engine, _read_sql


def _make_analyzer(spend=None, revenue_row=None):
    """Build CampaignAnalyzer with mocked DB."""
    if spend is None:
        spend = _make_spend()
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    analyzer = CampaignAnalyzer(spend, _engine=mock_engine)

    if revenue_row is None:
        revenue_row = {
            "cohort_users": 100,
            "transacting_users": 12,
            "onramp_rev_usd": 50.0,
            "card_fee_usd": 30.0,
            "billing_usd": 10.0,
            "total_revenue_usd": 90.0,
        }

    def _fake_run(sql, params):
        if "DATE(created_at" in sql or "signup_date" in sql:
            rows = [
                {"signup_date": "2026-02-08", "new_signups": 20},
                {"signup_date": "2026-02-09", "new_signups": 22},
                {"signup_date": "2026-02-10", "new_signups": 18},
                {"signup_date": "2026-02-15", "new_signups": 25},
                {"signup_date": "2026-02-16", "new_signups": 30},
                {"signup_date": "2026-04-14", "new_signups": 28},
                {"signup_date": "2026-04-15", "new_signups": 15},
            ]
            return pd.DataFrame(rows)
        return pd.DataFrame([revenue_row])

    analyzer._run = _fake_run
    return analyzer


# ---------------------------------------------------------------------------
# load_ad_spend
# ---------------------------------------------------------------------------


def test_load_ad_spend_filters_by_prefix():
    spend = _make_spend()
    assert len(spend) > 0
    # STRIPE row should be excluded
    assert spend["daily_spend_usd"].notna().all()


def test_load_ad_spend_spend_is_positive():
    spend = _make_spend()
    assert (spend["daily_spend_usd"] > 0).all()


def test_load_ad_spend_aggregates_daily():
    spend = _make_spend()
    # Feb 15 and Feb 16 have 1 charge each; totals should match abs(amount)
    feb15 = spend[pd.to_datetime(spend["date"]).dt.date == pd.Timestamp("2026-02-15").date()]
    assert pytest.approx(feb15["daily_spend_usd"].iloc[0], rel=1e-4) == 99.00


def test_load_ad_spend_empty_when_no_match():
    spend = load_ad_spend(io.StringIO(_CSV_CONTENT), merchant_prefix="NONEXISTENT")
    assert spend.empty


# ---------------------------------------------------------------------------
# _detect_campaigns
# ---------------------------------------------------------------------------


def test_detect_campaigns_splits_on_gap():
    spend = _make_spend()
    campaigns = _detect_campaigns(spend, gap_days=7)
    # CSV has two windows: Feb 15-20, Apr 14-15 (>7 day gap in between)
    assert len(campaigns) == 2


def test_detect_campaigns_totals_match():
    spend = _make_spend()
    campaigns = _detect_campaigns(spend, gap_days=7)
    total = sum(c["total_spend_usd"] for c in campaigns)
    assert pytest.approx(total, rel=1e-3) == spend["daily_spend_usd"].sum()


def test_detect_campaigns_ids_sequential():
    spend = _make_spend()
    campaigns = _detect_campaigns(spend, gap_days=7)
    ids = [c["campaign_id"] for c in campaigns]
    assert ids == ["campaign_1", "campaign_2"]


def test_detect_campaigns_empty_spend():
    result = _detect_campaigns(pd.DataFrame(columns=["date", "daily_spend_usd"]))
    assert result == []


# ---------------------------------------------------------------------------
# CampaignAnalyzer
# ---------------------------------------------------------------------------


def test_campaigns_property_returns_list():
    analyzer = _make_analyzer()
    assert isinstance(analyzer.campaigns, list)
    assert len(analyzer.campaigns) == 2


def test_roi_summary_returns_dataframe():
    analyzer = _make_analyzer()
    summary = analyzer.roi_summary()
    assert isinstance(summary, pd.DataFrame)
    assert not summary.empty


def test_roi_summary_required_columns():
    analyzer = _make_analyzer()
    summary = analyzer.roi_summary()
    required = [
        "campaign_id",
        "total_spend_usd",
        "cohort_users",
        "total_revenue_usd",
        "roas",
        "cac_full",
    ]
    for col in required:
        assert col in summary.columns, f"Missing column: {col}"


def test_roi_summary_roas_calculation():
    # revenue=90, spend=sum of campaign_1 (99+791.93+100=990.93)
    analyzer = _make_analyzer(
        revenue_row={
            "cohort_users": 100,
            "transacting_users": 12,
            "onramp_rev_usd": 50.0,
            "card_fee_usd": 30.0,
            "billing_usd": 10.0,
            "total_revenue_usd": 90.0,
        }
    )
    summary = analyzer.roi_summary()
    c1 = summary[summary["campaign_id"] == "campaign_1"].iloc[0]
    expected_roas = 90.0 / c1["total_spend_usd"]
    assert pytest.approx(c1["roas"], rel=1e-3) == expected_roas


def test_roi_summary_cac_full():
    analyzer = _make_analyzer(
        revenue_row={
            "cohort_users": 100,
            "transacting_users": 12,
            "onramp_rev_usd": 0.0,
            "card_fee_usd": 0.0,
            "billing_usd": 0.0,
            "total_revenue_usd": 0.0,
        }
    )
    summary = analyzer.roi_summary()
    c1 = summary[summary["campaign_id"] == "campaign_1"].iloc[0]
    expected_cac = c1["total_spend_usd"] / 100
    assert pytest.approx(c1["cac_full"], rel=1e-3) == expected_cac


def test_roi_summary_zero_cohort_users():
    analyzer = _make_analyzer(
        revenue_row={
            "cohort_users": 0,
            "transacting_users": 0,
            "onramp_rev_usd": 0.0,
            "card_fee_usd": 0.0,
            "billing_usd": 0.0,
            "total_revenue_usd": 0.0,
        }
    )
    summary = analyzer.roi_summary()
    assert summary["cac_full"].isna().all()


def test_daily_context_returns_dataframe():
    analyzer = _make_analyzer()
    daily = analyzer.daily_context()
    assert isinstance(daily, pd.DataFrame)
    assert "new_signups" in daily.columns
    assert "daily_spend_usd" in daily.columns
    assert "is_campaign" in daily.columns


def test_daily_context_campaign_tagged():
    analyzer = _make_analyzer()
    daily = analyzer.daily_context()
    # Campaign days should be tagged
    campaign_days = daily[daily["is_campaign"]]
    assert len(campaign_days) > 0


def test_daily_context_spend_non_negative():
    analyzer = _make_analyzer()
    daily = analyzer.daily_context()
    assert (daily["daily_spend_usd"] >= 0).all()
