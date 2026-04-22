"""Tests for nbs_bi.onramp.models.

All tests use in-memory fixture DataFrames — no database required.
Monetary columns are already in real units (BRL / USDC) as OnrampModel expects.
"""

import pandas as pd
import pytest

from nbs_bi.onramp.models import OnrampModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_conversions() -> pd.DataFrame:
    """Minimal fixture: 4 conversions — 2 offramp then 2 onramp.

    Offramp rows come first so NBS builds inventory (buys USDC) before selling.
    This ensures the weighted-average cost (PM) is established before any PnL
    is realised on onramp events.
    """
    return pd.DataFrame(
        {
            "id": ["a1", "a2", "a3", "a4"],
            "user_id": ["u2", "u3", "u1", "u1"],
            # offramp first (NBS buys USDC), then onramp (NBS sells USDC)
            "direction": ["usdc_to_brl", "usdc_to_brl", "brl_to_usdc", "brl_to_usdc"],
            "from_amount_brl": [0.0, 0.0, 1000.0, 2000.0],
            "from_amount_usdc": [200.0, 400.0, 0.0, 0.0],
            "to_amount_brl": [1040.0, 2080.0, 0.0, 0.0],
            "to_amount_usdc": [0.0, 0.0, 192.31, 384.62],
            "exchange_rate": [5.20, 5.20, 5.20, 5.20],
            "effective_rate": [5.20, 5.20, 5.20, 5.20],
            "fee_amount_brl": [8.0, 16.0, 10.0, 20.0],
            "fee_amount_usdc": [0.0, 0.0, 0.0, 0.0],
            "spread_revenue_brl": [4.0, 8.0, 5.0, 10.0],
            "spread_revenue_usdc": [0.0, 0.0, 0.0, 0.0],
            "spread_percentage": [0.4, 0.4, 0.5, 0.5],
            "created_at": pd.to_datetime(["2026-01-05", "2026-01-10", "2026-02-03", "2026-02-15"]),
            "updated_at": pd.to_datetime(["2026-01-05", "2026-01-10", "2026-02-03", "2026-02-15"]),
        }
    )


@pytest.fixture()
def model() -> OnrampModel:
    return OnrampModel(_make_conversions())


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def test_model_requires_direction_column() -> None:
    bad = pd.DataFrame({"created_at": ["2026-01-01"]})
    with pytest.raises(ValueError, match="direction"):
        OnrampModel(bad)


def test_model_requires_created_at_column() -> None:
    bad = pd.DataFrame({"direction": ["brl_to_usdc"]})
    with pytest.raises(ValueError, match="created_at"):
        OnrampModel(bad)


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def test_kpis_total_conversions(model: OnrampModel) -> None:
    assert model.kpis()["total_conversions"] == 4


def test_kpis_unique_users(model: OnrampModel) -> None:
    assert model.kpis()["unique_users"] == 3


def test_kpis_onramp_offramp_split(model: OnrampModel) -> None:
    k = model.kpis()
    assert k["onramp_conversions"] == 2
    assert k["offramp_conversions"] == 2


def test_kpis_volume_brl_positive(model: OnrampModel) -> None:
    assert model.kpis()["volume_brl"] > 0


def test_kpis_revenue_brl(model: OnrampModel) -> None:
    # fee + spread for all 4 rows: (10+5) + (8+4) + (20+10) + (16+8) = 81
    assert model.kpis()["revenue_brl"] == pytest.approx(81.0)


# ---------------------------------------------------------------------------
# Volume by period
# ---------------------------------------------------------------------------


def test_volume_by_period_daily_shape(model: OnrampModel) -> None:
    vol = model.volume_by_period(freq="D")
    assert not vol.empty
    assert {"period", "side", "volume_brl", "volume_usdc", "n_conversions"}.issubset(vol.columns)


def test_volume_by_period_monthly(model: OnrampModel) -> None:
    vol = model.volume_by_period(freq="M")
    # fixture: 2 offramp in Jan, 2 onramp in Feb → 2 period×side groups
    assert len(vol) == 2
    assert set(vol["side"]) == {"onramp", "offramp"}


# ---------------------------------------------------------------------------
# FX stats
# ---------------------------------------------------------------------------


def test_fx_stats_columns(model: OnrampModel) -> None:
    fx = model.fx_stats(freq="D")
    assert {"period", "side", "fx_mean", "fx_p10", "fx_p90", "n"}.issubset(fx.columns)


def test_fx_stats_onramp_rate(model: OnrampModel) -> None:
    fx = model.fx_stats(freq="D")
    on_rows = fx[fx["side"] == "onramp"]
    assert not on_rows.empty
    # rate = from_amount_brl / to_amount_usdc ≈ 1000 / 192.31 ≈ 5.20
    assert on_rows["fx_mean"].iloc[0] == pytest.approx(5.20, rel=1e-2)


# ---------------------------------------------------------------------------
# Position and PnL
# ---------------------------------------------------------------------------


def test_position_columns(model: OnrampModel) -> None:
    pos = model.position()
    required = {
        "created_at",
        "side",
        "stock_in_usdc",
        "stock_out_usdc",
        "position_qty_usdc",
        "avg_price_brl_per_usdc",
        "pnl_brl",
        "pnl_cum_brl",
    }
    assert required.issubset(pos.columns)


def test_position_stock_signs(model: OnrampModel) -> None:
    pos = model.position()
    assert (pos["stock_in_usdc"] >= 0).all()
    assert (pos["stock_out_usdc"] >= 0).all()


def test_position_onramp_increases_stock_out(model: OnrampModel) -> None:
    pos = model.position()
    onramp_rows = pos[pos["side"] == "onramp"]
    assert (onramp_rows["stock_out_usdc"] > 0).all()


def test_position_offramp_increases_stock_in(model: OnrampModel) -> None:
    pos = model.position()
    offramp_rows = pos[pos["side"] == "offramp"]
    assert (offramp_rows["stock_in_usdc"] > 0).all()


def test_position_pnl_cum_is_nondecreasing_with_positive_spread(model: OnrampModel) -> None:
    pos = model.position().dropna(subset=["pnl_cum_brl"])
    diffs = pos["pnl_cum_brl"].diff().dropna()
    # All sell events have positive spread so cumulative PnL should not decrease
    assert (diffs >= -1e-9).all()


# ---------------------------------------------------------------------------
# Top users
# ---------------------------------------------------------------------------


def test_top_users_returns_dataframe(model: OnrampModel) -> None:
    top = model.top_users(n=10)
    assert isinstance(top, pd.DataFrame)
    assert "user_id" in top.columns


def test_top_users_sorted_descending(model: OnrampModel) -> None:
    top = model.top_users(n=10, metric="volume_brl")
    assert top["volume_brl"].is_monotonic_decreasing or len(top) <= 1


def test_top_users_limit(model: OnrampModel) -> None:
    top = model.top_users(n=2)
    assert len(top) <= 2


# ---------------------------------------------------------------------------
# Active users
# ---------------------------------------------------------------------------


def test_active_users_daily(model: OnrampModel) -> None:
    active = model.active_users(freq="D")
    assert not active.empty
    assert {"period", "active_users"}.issubset(active.columns)


def test_active_users_total_le_unique(model: OnrampModel) -> None:
    active = model.active_users(freq="D")
    # Each day's active users ≤ total unique users
    assert active["active_users"].max() <= model.kpis()["unique_users"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_dataframe_raises() -> None:
    with pytest.raises(ValueError):
        OnrampModel(pd.DataFrame())


# ---------------------------------------------------------------------------
# New analytical methods
# ---------------------------------------------------------------------------


def test_user_behavior_returns_dict(model: OnrampModel) -> None:
    beh = model.user_behavior()
    assert isinstance(beh, dict)
    assert {"unique_users", "repeat_users", "repeat_rate", "avg_conversions_per_user"}.issubset(beh)


def test_user_behavior_unique_users(model: OnrampModel) -> None:
    # fixture has u1, u2, u3 — 3 unique users
    assert model.user_behavior()["unique_users"] == 3


def test_user_behavior_repeat_users(model: OnrampModel) -> None:
    # u1 appears twice in fixture → 1 repeat user
    assert model.user_behavior()["repeat_users"] == 1


def test_user_behavior_repeat_rate_range(model: OnrampModel) -> None:
    rate = model.user_behavior()["repeat_rate"]
    assert 0.0 <= rate <= 1.0


def test_revenue_by_direction_columns(model: OnrampModel) -> None:
    rev = model.revenue_by_direction()
    assert {"month", "side", "fee_brl", "spread_brl", "total_revenue_brl"}.issubset(rev.columns)


def test_revenue_by_direction_sides(model: OnrampModel) -> None:
    rev = model.revenue_by_direction()
    assert set(rev["side"]) == {"onramp", "offramp"}


def test_revenue_by_direction_totals(model: OnrampModel) -> None:
    rev = model.revenue_by_direction()
    for _, row in rev.iterrows():
        assert row["total_revenue_brl"] == pytest.approx(row["fee_brl"] + row["spread_brl"])


def test_monthly_new_vs_returning_columns(model: OnrampModel) -> None:
    nvr = model.monthly_new_vs_returning()
    assert {"month", "new_users", "returning_users"}.issubset(nvr.columns)


def test_monthly_new_vs_returning_sorted(model: OnrampModel) -> None:
    nvr = model.monthly_new_vs_returning()
    assert list(nvr["month"]) == sorted(nvr["month"].tolist())


def test_spread_stats_columns(model: OnrampModel) -> None:
    ss = model.spread_stats()
    assert {"side", "spread_percentage", "volume_brl"}.issubset(ss.columns)


def test_spread_stats_no_nulls(model: OnrampModel) -> None:
    ss = model.spread_stats()
    assert ss["spread_percentage"].notna().all()


def test_single_conversion_onramp() -> None:
    df = pd.DataFrame(
        {
            "direction": ["brl_to_usdc"],
            "from_amount_brl": [500.0],
            "to_amount_usdc": [96.15],
            "exchange_rate": [5.20],
            "effective_rate": [5.20],
            "created_at": pd.to_datetime(["2026-03-01"]),
        }
    )
    m = OnrampModel(df)
    assert m.kpis()["total_conversions"] == 1
    assert m.kpis()["onramp_conversions"] == 1
    assert m.kpis()["offramp_conversions"] == 0
