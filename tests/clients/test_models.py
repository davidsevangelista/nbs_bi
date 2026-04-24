"""Tests for nbs_bi.clients.models — revenue calculations, cohort LTV, CAC breakeven.

All tests use fixture DataFrames — no database required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from nbs_bi.clients.models import ClientModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_cohort_base(n: int = 4) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": [f"user_{i:04d}-xxxx" for i in range(n)],
            "signup_date": pd.to_datetime(["2026-01-10", "2026-01-20", "2026-02-05", "2026-03-01"]),
            "last_active_at": pd.to_datetime(
                ["2026-04-15", "2026-03-01", "2026-01-20", "2026-03-10"]
            ),
            "status": ["active"] * n,
            "account_type": ["personal"] * n,
            "acquisition_source": ["founder_invite", "referral", "organic", "unknown"],
            "referral_code_id": [None, "rc1", None, None],
            "referral_code": [None, "CODE1", None, None],
            "referral_code_name": [None, "Partner A", None, None],
            "commission_rate_bps": [0, 50, 0, 0],
            "referral_code_type": [None, "referral", None, None],
            "is_founder": [True, False, False, False],
            "founder_number": [1, None, None, None],
            "founder_network_size": [10, None, None, None],
            "invites_remaining": [5, None, None, None],
            "invite_code": ["INV01", None, None, None],
            "country_code": ["BRA"] * n,
            "preferred_currency": ["BRL"] * n,
            "onboarding_completed": [True] * n,
            "kyc_level": [1, 1, 0, 0],
        }
    )


def _make_onramp(fx: float = 5.80) -> pd.DataFrame:
    # onramp_revenue_brl + offramp_revenue_brl already scaled (BRL, not centavos)
    return pd.DataFrame(
        {
            "user_id": ["user_0000-xxxx", "user_0001-xxxx"],
            "onramp_revenue_brl": [116.0, 58.0],  # $20 and $10 at fx=5.80
            "offramp_revenue_brl": [29.0, 0.0],  # $5 extra for user_0000
            "n_conversions": [5, 2],
            "onramp_volume_brl": [1000.0, 500.0],
            "offramp_volume_usdc": [5.0, 0.0],
        }
    )


def _make_card_fees() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": ["user_0000-xxxx"],
            "card_fee_usd": [14.99],
        }
    )


def _make_card_txs() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": ["user_0000-xxxx", "user_0001-xxxx"],
            "user_tx_count": [10, 5],
            "total_tx_count": [100, 100],
        }
    )


def _make_billing() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": ["user_0000-xxxx"],
            "card_tx_fee_usd": [3.50],
        }
    )


def _make_cashback() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": ["user_0000-xxxx"],
            "cashback_usd": [1.00],
        }
    )


def _make_empty() -> pd.DataFrame:
    return pd.DataFrame()


def _make_queries_mock(fx: float = 5.80, invoice_total: float = 6693.58) -> MagicMock:
    q = MagicMock()
    q.cohort_base.return_value = _make_cohort_base()
    q.conversion_revenue.return_value = _make_onramp(fx)
    q.card_fees.return_value = _make_card_fees()
    q.card_transactions.return_value = _make_card_txs()
    q.billing_charges.return_value = _make_billing()
    q.cashback.return_value = _make_cashback()
    q.revenue_share.return_value = _make_empty()
    q.swaps.return_value = _make_empty()
    q.payouts.return_value = _make_empty()
    q.fx_rate.return_value = fx
    q.conversion_monthly.return_value = _make_empty()
    q.revenue_generating_count.return_value = 2
    return q


def _build_model(fx: float = 5.80, invoice_total: float = 6693.58) -> ClientModel:
    mock = _make_queries_mock(fx, invoice_total)
    return ClientModel(
        "2026-01-01",
        "2026-04-13",
        card_invoice_total_usd=invoice_total,
        _queries=mock,
    )


# ---------------------------------------------------------------------------
# FX conversion
# ---------------------------------------------------------------------------


def test_fx_conversion_brl_to_usd():
    model = _build_model(fx=5.80)
    df = model.master_df
    # user_0000: onramp_revenue_brl = 116.0, fx = 5.80 → $20.00
    u0 = df[df["user_id"].str.startswith("user_0000")].iloc[0]
    assert pytest.approx(u0["onramp_revenue_usd"], rel=1e-4) == 20.0


def test_fx_conversion_fallback_rate():
    mock = _make_queries_mock(fx=5.80)
    mock.fx_rate.return_value = 5.80
    model = ClientModel("2026-01-01", "2026-04-13", _queries=mock)
    df = model.master_df
    u1 = df[df["user_id"].str.startswith("user_0001")].iloc[0]
    assert pytest.approx(u1["onramp_revenue_usd"], rel=1e-4) == 10.0


# ---------------------------------------------------------------------------
# Card cost pro-rata
# ---------------------------------------------------------------------------


def test_card_cost_prorata():
    model = _build_model(invoice_total=6693.58)
    df = model.master_df
    u0 = df[df["user_id"].str.startswith("user_0000")].iloc[0]
    expected = 6693.58 * 10 / 100
    assert pytest.approx(u0["card_cost_allocated_usd"], rel=1e-4) == expected


def test_card_cost_prorata_zero_when_no_txs():
    mock = _make_queries_mock()
    mock.card_transactions.return_value = _make_empty()
    model = ClientModel("2026-01-01", "2026-04-13", _queries=mock)
    df = model.master_df
    assert (df["card_cost_allocated_usd"] == 0.0).all()


# ---------------------------------------------------------------------------
# Net revenue calculation
# ---------------------------------------------------------------------------


def test_net_revenue_calculation():
    model = _build_model(fx=5.80, invoice_total=6693.58)
    df = model.master_df
    u0 = df[df["user_id"].str.startswith("user_0000")].iloc[0]
    expected = (
        20.0  # onramp_revenue_usd  (116 BRL / 5.80)
        + 5.0  # offramp_revenue_usd (29 BRL / 5.80)
        + 14.99  # card_fee_usd
        + 3.50  # card_tx_fee_usd
        + 0.0  # swap_fee_usd
        + 0.0  # payout_fee_usd
        - 1.00  # cashback_usd
        - 0.0  # revenue_share
        - (6693.58 * 10 / 100)  # card_cost_allocated
    )
    assert pytest.approx(u0["net_revenue_usd"], rel=1e-3) == expected


# ---------------------------------------------------------------------------
# Revenue leaderboard — PII masking
# ---------------------------------------------------------------------------


def test_revenue_leaderboard_masked():
    model = _build_model()
    lb = model.revenue_leaderboard()
    assert lb["user_id"].str.endswith("...").all()
    # No full UUID visible (UUIDs are 36 chars)
    assert (lb["user_id"].str.len() <= 12).all()


def test_revenue_leaderboard_ordered():
    model = _build_model()
    lb = model.revenue_leaderboard()
    assert lb["net_revenue_usd"].is_monotonic_decreasing


# ---------------------------------------------------------------------------
# Acquisition summary
# ---------------------------------------------------------------------------


def test_acquisition_summary_conversion_rate_bounded():
    model = _build_model()
    acq = model.acquisition_summary()
    assert (acq["conversion_rate"] <= 1.0).all()
    assert (acq["conversion_rate"] >= 0.0).all()


def test_acquisition_summary_has_expected_sources():
    model = _build_model()
    acq = model.acquisition_summary()
    sources = set(acq["acquisition_source"].tolist())
    assert "founder_invite" in sources
    assert "referral" in sources


# ---------------------------------------------------------------------------
# Cohort LTV
# ---------------------------------------------------------------------------


def _make_conversion_monthly() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": [
                "user_0000-xxxx",
                "user_0000-xxxx",
                "user_0000-xxxx",
                "user_0001-xxxx",
                "user_0001-xxxx",
            ],
            "month": pd.to_datetime(["2026-01", "2026-02", "2026-03", "2026-01", "2026-02"]),
            "conversion_revenue_brl": [58.0, 58.0, 58.0, 29.0, 29.0],
        }
    )


def _make_daily_activity() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": [
                "user_0000-xxxx",
                "user_0000-xxxx",
                "user_0000-xxxx",
                "user_0001-xxxx",
                "user_0001-xxxx",
            ],
            "activity_date": pd.to_datetime(
                ["2026-01-10", "2026-01-15", "2026-02-03", "2026-01-10", "2026-02-20"]
            ).date,
        }
    )


def _build_model_with_monthly(fx: float = 5.80) -> ClientModel:
    mock = _make_queries_mock(fx)
    mock.conversion_monthly.return_value = _make_conversion_monthly()
    return ClientModel("2026-01-01", "2026-04-13", _queries=mock)


def _build_model_with_daily(fx: float = 5.80) -> ClientModel:
    mock = _make_queries_mock(fx)
    mock.conversion_monthly.return_value = _make_conversion_monthly()
    mock.daily_activity.return_value = _make_daily_activity()
    return ClientModel("2026-01-01", "2026-04-13", _queries=mock)


def test_cohort_matrix_not_empty():
    model = _build_model_with_monthly()
    ltv = model.cohort_ltv()
    assert not ltv.empty


def test_cohort_matrix_columns_are_month_offsets():
    model = _build_model_with_monthly()
    ltv = model.cohort_ltv()
    # Columns should be 0, 1, 2 (months_since_signup)
    assert 0 in ltv.columns


def test_cohort_matrix_ltv_is_cumulative():
    model = _build_model_with_monthly()
    ltv = model.cohort_ltv()
    # For a given cohort row, later months should have >= LTV than earlier
    for idx in ltv.index:
        row = ltv.loc[idx].dropna()
        if len(row) >= 2:
            assert (
                row.is_monotonic_increasing
                or pytest.approx(row.diff().dropna().min(), abs=1e-6) >= 0
            )


# ---------------------------------------------------------------------------
# CAC breakeven
# ---------------------------------------------------------------------------


def test_cac_breakeven_payback_found():
    model = _build_model_with_monthly()
    result = model.cac_breakeven(cac_usd=5.0)
    assert not result.empty
    # At least one source should find payback within the data
    found = result[result["payback_months"].notna()]
    assert len(found) > 0


def test_cac_breakeven_never_pays_back():
    model = _build_model_with_monthly()
    # CAC far exceeds any user's LTV
    result = model.cac_breakeven(cac_usd=999_999.0)
    assert result["payback_months"].isna().all()


def test_cac_breakeven_returns_expected_columns():
    model = _build_model_with_monthly()
    result = model.cac_breakeven(cac_usd=10.0)
    assert "acquisition_source" in result.columns
    assert "payback_months" in result.columns
    assert "ltv_at_month_12" in result.columns


# ---------------------------------------------------------------------------
# Activation funnel
# ---------------------------------------------------------------------------


def test_activation_funnel_keys():
    model = _build_model()
    funnel = model.activation_funnel()
    assert "total_users" in funnel
    assert "kyc_done" in funnel
    assert "active_users" in funnel


def test_activation_funnel_total_equals_master_len():
    model = _build_model()
    funnel = model.activation_funnel()
    assert funnel["total_users"] == len(model.master_df)


def test_activation_funnel_kyc_done_uses_kyc_level():
    # Fixture: kyc_level = [1, 1, 0, 0] → 2 users with KYC done
    model = _build_model()
    funnel = model.activation_funnel()
    assert funnel["kyc_done"] == 2


def test_activation_funnel_active_counts_transacting():
    # user_0000 has conversions + card fees → active; user_0001 has conversions → active
    # user_0002, user_0003 have nothing → not active
    model = _build_model()
    funnel = model.activation_funnel()
    assert funnel["active_users"] == 2


# ---------------------------------------------------------------------------
# Product adoption — new taxonomy
# ---------------------------------------------------------------------------


def test_product_adoption_has_conversion():
    model = _build_model()
    pa = model.product_adoption()
    assert "has_conversion" in pa.columns
    # user_0000 and user_0001 both have n_conversions > 0
    assert pa["has_conversion"].sum() == 2


def test_product_adoption_has_offramp():
    model = _build_model()
    pa = model.product_adoption()
    assert "has_offramp" in pa.columns
    # Only user_0000 has offramp_volume_usdc > 0
    assert pa["has_offramp"].sum() == 1


def test_product_adoption_has_card_merged():
    model = _build_model()
    pa = model.product_adoption()
    assert "has_card" in pa.columns
    # At least user_0000 has card fees → has_card should be True for at least 1 user
    assert pa["has_card"].sum() >= 1


def test_product_adoption_n_products_uses_four_categories():
    model = _build_model()
    pa = model.product_adoption()
    # n_products counts: conversion, card, swap, crossborder (max 4)
    assert pa["n_products"].max() <= 4


# ---------------------------------------------------------------------------
# Cohort avg DAU
# ---------------------------------------------------------------------------


def test_cohort_avg_dau_not_empty():
    model = _build_model_with_daily()
    result = model.cohort_avg_dau()
    assert not result.empty


def test_cohort_avg_dau_columns_are_nonneg_ints():
    model = _build_model_with_daily()
    result = model.cohort_avg_dau()
    assert all(isinstance(c, int) and c >= 0 for c in result.columns)


def test_cohort_avg_dau_values_nonneg():
    model = _build_model_with_daily()
    result = model.cohort_avg_dau()
    assert (result.fillna(0) >= 0).all().all()


def test_cohort_avg_dau_empty_when_no_daily_activity():
    mock = _make_queries_mock()
    mock.conversion_monthly.return_value = _make_conversion_monthly()
    mock.daily_activity.return_value = pd.DataFrame()
    model = ClientModel("2026-01-01", "2026-04-13", _queries=mock)
    result = model.cohort_avg_dau()
    assert result.empty
