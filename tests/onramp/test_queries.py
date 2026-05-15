"""Tests for nbs_bi.onramp.queries.

Unit tests use in-memory DataFrames — no database required.
Integration tests (marked db) connect to READONLY_DATABASE_URL.
"""

import math

import pandas as pd
import pytest

from nbs_bi.onramp.queries import conv_revenue_usd


def _onramp_row(
    fee_brl: float = 10.0,
    spread_brl: float = 5.0,
    rate: float = 5.0,
    fee_usdc: float = float("nan"),
    spread_usdc: float = float("nan"),
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fee_amount_brl": [fee_brl],
            "spread_revenue_brl": [spread_brl],
            "fee_amount_usdc": [fee_usdc],
            "spread_revenue_usdc": [spread_usdc],
            "exchange_rate": [rate],
        }
    )


def _offramp_row(
    fee_usdc: float = 2.0,
    spread_usdc: float = 3.0,
    rate: float = 5.0,
    fee_brl: float = float("nan"),
    spread_brl: float = float("nan"),
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fee_amount_brl": [fee_brl],
            "spread_revenue_brl": [spread_brl],
            "fee_amount_usdc": [fee_usdc],
            "spread_revenue_usdc": [spread_usdc],
            "exchange_rate": [rate],
        }
    )


# ---------------------------------------------------------------------------
# Onramp (BRL columns only)
# ---------------------------------------------------------------------------


def test_conv_revenue_usd_onramp_uses_brl_columns() -> None:
    """Onramp row: revenue = (fee_brl + spread_brl) / exchange_rate."""
    df = _onramp_row(fee_brl=10.0, spread_brl=5.0, rate=5.0)
    result = conv_revenue_usd(df)
    assert result.iloc[0] == pytest.approx(3.0)  # (10+5)/5


def test_conv_revenue_usd_onramp_ignores_nan_usdc() -> None:
    """NaN USDC columns on an onramp row contribute 0, not NaN."""
    df = _onramp_row(fee_brl=10.0, spread_brl=5.0, rate=5.0, fee_usdc=float("nan"))
    result = conv_revenue_usd(df)
    assert not math.isnan(result.iloc[0])


# ---------------------------------------------------------------------------
# Offramp (USDC columns only)
# ---------------------------------------------------------------------------


def test_conv_revenue_usd_offramp_uses_usdc_columns() -> None:
    """Offramp row: revenue = fee_usdc + spread_usdc (BRL cols are NaN)."""
    df = _offramp_row(fee_usdc=2.0, spread_usdc=3.0)
    result = conv_revenue_usd(df)
    assert result.iloc[0] == pytest.approx(5.0)  # 2+3


def test_conv_revenue_usd_offramp_nan_brl_does_not_propagate() -> None:
    """NaN BRL columns on an offramp row contribute 0, not NaN."""
    df = _offramp_row(fee_brl=float("nan"), spread_brl=float("nan"), fee_usdc=4.0, spread_usdc=1.0)
    result = conv_revenue_usd(df)
    assert result.iloc[0] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Mixed DataFrame (realistic production shape)
# ---------------------------------------------------------------------------


def test_conv_revenue_usd_mixed_df_sums_both_directions() -> None:
    """Mixed DataFrame: onramp BRL + offramp USDC totalled correctly."""
    df = pd.DataFrame(
        {
            "fee_amount_brl": [10.0, float("nan")],
            "spread_revenue_brl": [5.0, float("nan")],
            "fee_amount_usdc": [float("nan"), 2.0],
            "spread_revenue_usdc": [float("nan"), 3.0],
            "exchange_rate": [5.0, 5.0],
        }
    )
    result = conv_revenue_usd(df)
    # row 0: (10+5)/5 = 3.0; row 1: 2+3 = 5.0; total = 8.0
    assert result.sum() == pytest.approx(8.0)


def test_conv_revenue_usd_returns_series_of_correct_length() -> None:
    df = pd.concat([_onramp_row(), _offramp_row()], ignore_index=True)
    result = conv_revenue_usd(df)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_conv_revenue_usd_zero_exchange_rate_returns_nan() -> None:
    """Zero exchange_rate should produce NaN (same as division by zero → nan)."""
    df = _onramp_row(fee_brl=10.0, spread_brl=5.0, rate=0.0)
    result = conv_revenue_usd(df)
    assert math.isnan(result.iloc[0])


def test_conv_revenue_usd_all_zero_revenue_returns_zero() -> None:
    df = _onramp_row(fee_brl=0.0, spread_brl=0.0, rate=5.0, fee_usdc=0.0, spread_usdc=0.0)
    result = conv_revenue_usd(df)
    assert result.iloc[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Integration tests — require READONLY_DATABASE_URL
# ---------------------------------------------------------------------------

_db = pytest.mark.skipif(
    not __import__("nbs_bi.config", fromlist=["READONLY_DATABASE_URL"]).READONLY_DATABASE_URL,
    reason="READONLY_DATABASE_URL not set",
)


@_db
def test_card_transactions_active_excludes_internal_users() -> None:
    """card_transactions_active must not return transactions from @neobankless.com users."""
    from sqlalchemy import create_engine, text

    from nbs_bi.config import READONLY_DATABASE_URL
    from nbs_bi.onramp.queries import OnrampQueries

    engine = create_engine(READONLY_DATABASE_URL)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id::text FROM users WHERE email LIKE '%@neobankless.com'")
        )
        internal_ids = {row[0] for row in rows}

    q = OnrampQueries(start_date="2024-01-01", end_date="2026-12-31")
    df = q.card_transactions_active()
    leaked = set(df["user_id"]).intersection(internal_ids)
    assert not leaked, f"Internal user IDs leaked into card_transactions_active: {leaked}"
