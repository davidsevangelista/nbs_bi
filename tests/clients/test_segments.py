"""Tests for nbs_bi.clients.segments — segment classification boundaries."""

from __future__ import annotations

import pandas as pd
import pytest

from nbs_bi.clients.segments import ClientSegments

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_master(records: list[dict]) -> pd.DataFrame:
    base = {
        "user_id": "u0001-xxxx",
        "is_founder": False,
        "founder_number": None,
        "founder_network_size": 0,
        "invites_remaining": 0,
        "net_revenue_usd": 0.0,
        "days_since_last_active": 0,
        "acquisition_source": "organic",
        "referral_code": None,
        "commission_rate_bps": 0,
        "referral_code_name": None,
    }
    rows = [{**base, **r, "user_id": f"u{i:04d}-xxxx"} for i, r in enumerate(records)]
    return pd.DataFrame(rows)


def _classify(records: list[dict]) -> pd.DataFrame:
    df = _make_master(records)
    return ClientSegments(df).classify()


# ---------------------------------------------------------------------------
# Segment boundary tests
# ---------------------------------------------------------------------------


def test_champion_active_recent_high_revenue():
    # Needs ≥ 80th percentile revenue. With 1 user they are 100th percentile.
    df = _classify([{"days_since_last_active": 10, "net_revenue_usd": 500.0}])
    assert df.iloc[0]["segment"] == "champion"


def test_active_recent_low_revenue():
    # Two users: one high, one low. Low one should be 'active'.
    records = [
        {"days_since_last_active": 10, "net_revenue_usd": 500.0},
        {"days_since_last_active": 5, "net_revenue_usd": 1.0},
    ]
    df = _classify(records)
    low = df.sort_values("net_revenue_usd").iloc[0]
    assert low["segment"] == "active"


def test_at_risk_31_days():
    df = _classify([{"days_since_last_active": 31, "net_revenue_usd": 100.0}])
    assert df.iloc[0]["segment"] == "at_risk"


def test_at_risk_90_days():
    df = _classify([{"days_since_last_active": 90, "net_revenue_usd": 100.0}])
    assert df.iloc[0]["segment"] == "at_risk"


def test_dormant_91_days():
    df = _classify([{"days_since_last_active": 91, "net_revenue_usd": 100.0}])
    assert df.iloc[0]["segment"] == "dormant"


def test_active_boundary_exactly_30_days():
    records = [
        {"days_since_last_active": 30, "net_revenue_usd": 1.0},
        {"days_since_last_active": 30, "net_revenue_usd": 1000.0},
    ]
    df = _classify(records)
    # Both ≤ 30 days → active or champion
    assert df["segment"].isin(["champion", "active"]).all()


# ---------------------------------------------------------------------------
# Segment summary
# ---------------------------------------------------------------------------


def test_segment_summary_pct_sums_to_one():
    records = [
        {"days_since_last_active": 5, "net_revenue_usd": 500.0},
        {"days_since_last_active": 5, "net_revenue_usd": 10.0},
        {"days_since_last_active": 50, "net_revenue_usd": 50.0},
        {"days_since_last_active": 120, "net_revenue_usd": 5.0},
    ]
    summary = ClientSegments(_make_master(records)).segment_summary()
    assert pytest.approx(summary["pct_users"].sum(), rel=1e-6) == 1.0


def test_segment_summary_has_all_segments():
    records = [
        {"days_since_last_active": 5, "net_revenue_usd": 500.0},
        {"days_since_last_active": 5, "net_revenue_usd": 1.0},
        {"days_since_last_active": 50, "net_revenue_usd": 50.0},
        {"days_since_last_active": 120, "net_revenue_usd": 5.0},
    ]
    summary = ClientSegments(_make_master(records)).segment_summary()
    assert set(summary["segment"]) == {"champion", "active", "at_risk", "dormant"}


# ---------------------------------------------------------------------------
# Referral performance
# ---------------------------------------------------------------------------


def test_referral_performance_net_value():
    """net_value_usd = avg_revenue − commission_cost."""
    records = [
        {
            "days_since_last_active": 5,
            "net_revenue_usd": 100.0,
            "referral_code": "CODE1",
            "referral_code_name": "P1",
            "commission_rate_bps": 50,
            "acquisition_source": "referral",
        },
        {
            "days_since_last_active": 5,
            "net_revenue_usd": 200.0,
            "referral_code": "CODE1",
            "referral_code_name": "P1",
            "commission_rate_bps": 50,
            "acquisition_source": "referral",
        },
    ]
    df = _make_master(records)
    out = ClientSegments(df).referral_performance()
    row = out.iloc[0]
    avg_rev = (100.0 + 200.0) / 2  # 150.0
    commission = avg_rev * 50 / 10_000  # 0.75
    assert pytest.approx(row["avg_revenue"], rel=1e-4) == avg_rev
    assert pytest.approx(row["commission_cost_usd"], rel=1e-4) == commission
    assert pytest.approx(row["net_value_usd"], rel=1e-4) == avg_rev - commission


def test_referral_performance_empty_when_no_referrals():
    records = [
        {
            "days_since_last_active": 5,
            "net_revenue_usd": 100.0,
            "referral_code": None,
            "commission_rate_bps": 0,
        },
    ]
    out = ClientSegments(_make_master(records)).referral_performance()
    assert out.empty
