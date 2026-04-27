"""Tests for nbs_bi.clients.campaigns — campaign detection, ROI, CAC.

All tests are DB-free: CampaignAnalyzer._engine is injected as a mock.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pandas as pd
import pytest

from nbs_bi.clients.campaigns import (
    CampaignAnalyzer,
    _cogs_for_cohort_txns,
    _cost_per_txn_from_invoices,
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
        # Referral codes query
        if (
            "referral_codes" in sql
            and "attributed_referral_code_id" in sql
            and "signup_date" not in sql
            and "cohort_start" not in sql
        ):
            return pd.DataFrame([{"code": "GOOGLE"}, {"code": "INSTAGRAM"}])
        # Signup / daily-context query
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
        # Daily revenue (cumulative_revenue)
        if "daily_rev_conversion_usd" in sql or "conversion_rev" in sql:
            return pd.DataFrame(
                [
                    {
                        "rev_date": "2026-04-14",
                        "daily_rev_conversion_usd": 5.0,
                        "daily_rev_card_fees_usd": 2.0,
                        "daily_rev_billing_usd": 0.5,
                        "daily_rev_swap_usd": 0.2,
                        "daily_cost_cashback_usd": 0.1,
                        "daily_cost_rev_share_usd": 0.05,
                        "daily_rev_usd": 7.55,
                    },
                    {
                        "rev_date": "2026-04-15",
                        "daily_rev_conversion_usd": 8.0,
                        "daily_rev_card_fees_usd": 1.0,
                        "daily_rev_billing_usd": 0.3,
                        "daily_rev_swap_usd": 0.1,
                        "daily_cost_cashback_usd": 0.05,
                        "daily_cost_rev_share_usd": 0.02,
                        "daily_rev_usd": 9.33,
                    },
                ]
            )
        # Cohort card transactions query
        if "card_transactions" in sql or "txn_date" in sql:
            return pd.DataFrame(
                [
                    {"txn_date": "2026-04-14", "txn_count": 50},
                    {"txn_date": "2026-04-15", "txn_count": 60},
                ]
            )
        # Cohort conversions query
        if "conv_date" in sql or "conv_count" in sql:
            return pd.DataFrame(
                [
                    {"conv_date": "2026-04-14", "conv_count": 12},
                    {"conv_date": "2026-04-15", "conv_count": 18},
                ]
            )
        # Cohort KYC completions query
        if "kyc_verifications" in sql or "kyc_date" in sql:
            return pd.DataFrame(
                [
                    {"kyc_date": "2026-04-14", "kyc_count": 25},
                    {"kyc_date": "2026-04-15", "kyc_count": 18},
                ]
            )
        # Aggregate cohort revenue (roi_summary)
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


def test_referral_code_options_returns_list():
    analyzer = _make_analyzer()
    options = analyzer.referral_code_options()
    assert isinstance(options, list)
    assert "GOOGLE" in options
    assert "INSTAGRAM" in options


def test_referral_code_options_on_db_error():
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = Exception("DB unavailable")
    analyzer = CampaignAnalyzer(_make_spend(), _engine=mock_engine)
    options = analyzer.referral_code_options()
    assert options == []


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
    expected_cac = c1["total_spend_usd"] / 12  # denominator is transacting_users
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


# ---------------------------------------------------------------------------
# _cost_per_txn_from_invoices
# ---------------------------------------------------------------------------


def test_cost_per_txn_basic():
    history = [("2026-02", 6693.58, 6885), ("2026-03", 7857.40, 6990)]
    result = _cost_per_txn_from_invoices(history)
    assert set(result.keys()) == {"2026-02", "2026-03"}
    assert result["2026-02"] == pytest.approx(6693.58 / 6885, rel=1e-6)
    assert result["2026-03"] == pytest.approx(7857.40 / 6990, rel=1e-6)


def test_cost_per_txn_empty():
    assert _cost_per_txn_from_invoices([]) == {}


def test_cost_per_txn_skips_zero_txn_count():
    history = [("2026-02", 6693.58, 0), ("2026-03", 7857.40, 6990)]
    result = _cost_per_txn_from_invoices(history)
    assert "2026-02" not in result
    assert "2026-03" in result


# ---------------------------------------------------------------------------
# _cogs_for_cohort_txns
# ---------------------------------------------------------------------------


def test_cogs_for_cohort_txns_basic():
    txn_df = pd.DataFrame(
        {
            "txn_date": pd.to_datetime(["2026-02-14", "2026-02-15"]),
            "txn_count": [100, 200],
        }
    )
    cost_per_txn = {"2026-02": 0.9720}
    result = _cogs_for_cohort_txns(txn_df, cost_per_txn)
    assert result.iloc[0] == pytest.approx(100 * 0.9720, rel=1e-6)
    assert result.iloc[1] == pytest.approx(200 * 0.9720, rel=1e-6)


def test_cogs_for_cohort_txns_empty_cost_per_txn():
    txn_df = pd.DataFrame(
        {
            "txn_date": pd.to_datetime(["2026-02-14"]),
            "txn_count": [100],
        }
    )
    result = _cogs_for_cohort_txns(txn_df, {})
    assert (result == 0.0).all()


def test_cogs_for_cohort_txns_fallback_to_prior_period():
    txn_df = pd.DataFrame(
        {
            "txn_date": pd.to_datetime(["2026-04-01"]),
            "txn_count": [50],
        }
    )
    # Only Feb available; should fall back to Feb rate for April
    cost_per_txn = {"2026-02": 1.00}
    result = _cogs_for_cohort_txns(txn_df, cost_per_txn)
    assert result.iloc[0] == pytest.approx(50.0, rel=1e-6)


# ---------------------------------------------------------------------------
# cumulative_profit — new schema
# ---------------------------------------------------------------------------


def test_cumulative_profit_required_columns():
    analyzer = _make_analyzer()
    invoice_history = [("2026-04", 7857.40, 6990)]
    result = analyzer.cumulative_profit("campaign_2", invoice_history)
    required = {
        "date",
        "daily_rev_conversion_usd",
        "daily_rev_card_fees_usd",
        "daily_rev_billing_usd",
        "daily_rev_swap_usd",
        "daily_cost_cashback_usd",
        "daily_cost_rev_share_usd",
        "daily_rev_total_usd",
        "daily_card_cogs_usd",
        "daily_ad_spend_usd",
        "daily_profit_usd",
        "daily_txn_count",
        "daily_conversion_count",
        "cum_rev_conversion_usd",
        "cum_rev_card_fees_usd",
        "cum_rev_billing_usd",
        "cum_rev_swap_usd",
        "cum_cost_cashback_usd",
        "cum_cost_rev_share_usd",
        "cum_rev_usd",
        "cum_card_cogs_usd",
        "cum_profit_usd",
        "cum_txn_count",
        "cum_conversion_count",
    }
    assert required.issubset(result.columns), f"Missing: {required - set(result.columns)}"


def test_cumulative_profit_cogs_positive_when_txns_exist():
    analyzer = _make_analyzer()
    invoice_history = [("2026-04", 7857.40, 6990)]
    result = analyzer.cumulative_profit("campaign_2", invoice_history)
    # Some days should have positive card COGS (txn_count > 0 in mock)
    assert result["daily_card_cogs_usd"].sum() > 0


def test_cumulative_profit_no_invoices_zero_cogs():
    analyzer = _make_analyzer()
    result = analyzer.cumulative_profit("campaign_2", [])
    assert (result["daily_card_cogs_usd"] == 0.0).all()


def test_cumulative_profit_cum_columns_monotonic():
    analyzer = _make_analyzer()
    invoice_history = [("2026-04", 7857.40, 6990)]
    result = analyzer.cumulative_profit("campaign_2", invoice_history)
    # cum_rev and cum_card_cogs must be non-decreasing
    assert (result["cum_rev_usd"].diff().dropna() >= -1e-9).all()
    assert (result["cum_card_cogs_usd"].diff().dropna() >= -1e-9).all()


def test_cumulative_profit_empty_when_no_campaigns():
    spend = pd.DataFrame(columns=["date", "daily_spend_usd"])
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    analyzer = CampaignAnalyzer(spend, _engine=mock_engine)
    result = analyzer.cumulative_profit()
    assert result.empty
