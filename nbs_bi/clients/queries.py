"""Database queries for client revenue, cohort, and LTV analysis.

Reads from the production read-only replica using READONLY_DATABASE_URL.
Schema reference: docs/specs/clients.md, docs/specs/database.md

Monetary scaling:
  - *_brl bigint   : centavos ÷ 100       → BRL
  - billing_charges amount bigint : micros ÷ 1_000_000 → USDC (applied in SQL)
  - card_annual_fees.amount_usdc : already real USDC — no divisor
  - swap / cashback / revenue_share amounts : already real USD (numeric columns)

Parquet cache keyed by SHA-256(SQL + params) when DB_CACHE_DIR is set.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from nbs_bi.config import DB_CACHE_DIR, READONLY_DATABASE_URL

logger = logging.getLogger(__name__)

_BRL_DIVISOR = 100

# ---------------------------------------------------------------------------
# SQL constants
# ---------------------------------------------------------------------------

_COHORT_BASE_SQL = """
SELECT
    u.id::TEXT                      AS user_id,
    u.created_at                    AS signup_date,
    u.last_active_at,
    u.status::TEXT                  AS status,
    u.account_type::TEXT            AS account_type,
    COALESCE(
        ur.source_type,
        CASE WHEN f.invite_code IS NOT NULL AND f.invite_code <> ''
             THEN 'founder_invite'
             ELSE 'unknown' END
    )                               AS acquisition_source,
    ur.attributed_referral_code_id::TEXT AS referral_code_id,
    rc.code                         AS referral_code,
    rc.public_name                  AS referral_code_name,
    rc.commission_rate_basis_points AS commission_rate_bps,
    rc.code_type                    AS referral_code_type,
    (f.user_id IS NOT NULL)         AS is_founder,
    f.founder_number,
    f.network_size                  AS founder_network_size,
    f.invites_remaining,
    f.invite_code,
    up.country_code,
    up.preferred_currency,
    up.onboarding_completed
FROM users u
LEFT JOIN user_registrations ur ON ur.user_id = u.id
LEFT JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
LEFT JOIN founders f ON f.user_id = u.id
LEFT JOIN user_profiles up ON up.user_id = u.id
"""

_ONRAMP_REVENUE_SQL = """
SELECT
    user_id::TEXT                                           AS user_id,
    SUM(fee_amount_brl + spread_revenue_brl)                AS onramp_revenue_brl,
    COUNT(*)                                                AS n_conversions,
    SUM(CASE WHEN direction = 'brl_to_usdc'
             THEN (from_amount_brl)::FLOAT / 100.0
             ELSE 0 END)                                    AS onramp_volume_brl,
    SUM(CASE WHEN direction = 'usdc_to_brl'
             THEN (from_amount_usdc)::FLOAT / 1000000.0
             ELSE 0 END)                                    AS offramp_volume_usdc
FROM conversion_quotes
WHERE used = TRUE
  AND created_at >= :start
  AND created_at <  :end
GROUP BY user_id
"""

_ONRAMP_MONTHLY_SQL = """
SELECT
    user_id::TEXT                                       AS user_id,
    DATE_TRUNC('month', created_at)::DATE               AS month,
    SUM(fee_amount_brl + spread_revenue_brl)            AS onramp_revenue_brl
FROM conversion_quotes
WHERE used = TRUE
GROUP BY user_id, DATE_TRUNC('month', created_at)
ORDER BY user_id, month
"""

_CARD_FEES_SQL = """
SELECT
    user_id::TEXT           AS user_id,
    SUM(amount_usdc::FLOAT) AS card_fee_usd
FROM card_annual_fees
WHERE status = 'paid'
  AND paid_at >= :start
  AND paid_at <  :end
GROUP BY user_id
"""

_CARD_TXS_SQL = """
SELECT
    user_id::TEXT       AS user_id,
    COUNT(*)            AS user_tx_count,
    SUM(COUNT(*)) OVER () AS total_tx_count
FROM card_transactions
WHERE status = 'posted'
  AND posted_at >= :start
  AND posted_at <  :end
GROUP BY user_id
"""

_BILLING_CHARGES_SQL = """
SELECT
    user_id::TEXT                       AS user_id,
    SUM(amount::FLOAT / 1000000.0)      AS card_tx_fee_usd
FROM billing_charges
WHERE status = 'settled'
  AND created_at >= :start
  AND created_at <  :end
GROUP BY user_id
"""

_CASHBACK_SQL = """
SELECT
    user_id::TEXT               AS user_id,
    SUM(reward_usd_value::FLOAT) AS cashback_usd
FROM cashback_rewards
WHERE status = 'completed'
  AND created_at >= :start
  AND created_at <  :end
GROUP BY user_id
"""

_REVENUE_SHARE_SQL = """
SELECT
    source_user_id::TEXT        AS user_id,
    SUM(reward_usd_value::FLOAT) AS revenue_share_paid_usd
FROM revenue_share_rewards
WHERE status = 'completed'
  AND created_at >= :start
  AND created_at <  :end
GROUP BY source_user_id
"""

_SWAP_SQL = """
SELECT
    user_id::TEXT                                                         AS user_id,
    SUM(input_amount::FLOAT / 1000000.0
        * platform_fee_bps::FLOAT / 10000.0)                             AS swap_fee_usd,
    COUNT(*)                                                              AS n_swaps
FROM swap_transactions
WHERE "timestamp" >= :start
  AND "timestamp" <  :end
GROUP BY user_id
"""

_PAYOUT_SQL = """
SELECT
    user_id::TEXT               AS user_id,
    SUM(unblockpay_fee::FLOAT)  AS payout_fee_usd
FROM unblockpay_payouts
WHERE status = 'completed'
  AND created_at >= :start
  AND created_at <  :end
GROUP BY user_id
"""

_FX_RATE_SQL = """
SELECT
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY effective_rate) AS fx_rate
FROM conversion_quotes
WHERE used = TRUE
  AND direction = 'brl_to_usdc'
  AND created_at >= :start
  AND created_at <  :end
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_exclusive_end(end_date: str) -> str:
    """Convert an inclusive ISO end date to an exclusive upper bound.

    Args:
        end_date: ISO date string (``"2026-03-31"``). Strings with a time
            component are returned unchanged.

    Returns:
        Start of the day after *end_date* as an ISO date string.
    """
    s = end_date.strip()
    if "T" in s or " " in s or s.count(":") >= 2:
        return s
    return (date.fromisoformat(s) + timedelta(days=1)).isoformat()


def _cache_path(prefix: str, sql: str, params: dict[str, Any]) -> Path | None:
    """Return the Parquet cache path for this query, or None if caching is disabled.

    Args:
        prefix: Short label used in the filename.
        sql: Raw SQL — included in the cache key.
        params: Bound parameters — included in the cache key.

    Returns:
        Path to the Parquet file, or None when DB_CACHE_DIR is not set.
    """
    if not DB_CACHE_DIR:
        return None
    payload = {"sql": sql.strip(), "params": params}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]
    cache_dir = Path(DB_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{prefix}_{digest}.parquet"


def _scale_brl(df: pd.DataFrame) -> pd.DataFrame:
    """Scale ``*_brl`` bigint columns from centavos to BRL.

    Args:
        df: DataFrame as returned from the database.

    Returns:
        New DataFrame with ``*_brl`` columns divided by 100.
    """
    out = df.copy()
    for col in out.columns:
        if col.endswith("_brl"):
            out[col] = pd.to_numeric(out[col], errors="coerce") / _BRL_DIVISOR
    return out


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class ClientQueries:
    """Fetches all client revenue, cohort, and attribution data from the DB.

    Args:
        start_date: Inclusive analysis window start (``"2026-01-01"``).
        end_date: Inclusive analysis window end (``"2026-04-13"``).
        db_url: Override for the database URL; falls back to
            ``READONLY_DATABASE_URL`` from the environment.

    Raises:
        ValueError: If no database URL can be resolved.
    """

    def __init__(
        self,
        *,
        start_date: str,
        end_date: str,
        db_url: str | None = None,
    ) -> None:
        resolved = db_url or READONLY_DATABASE_URL
        if not resolved:
            raise ValueError("No database URL. Set READONLY_DATABASE_URL in .env.")
        self._db_url = resolved
        self.start_date = start_date
        self.end_date = end_date
        self._engine_inst: Engine | None = None
        self._fx_rate_cached: float | None = None

    @property
    def _engine(self) -> Engine:
        """Lazy SQLAlchemy engine — created on first use."""
        if self._engine_inst is None:
            self._engine_inst = create_engine(self._db_url, pool_pre_ping=True)
        return self._engine_inst

    def _run(self, name: str, sql: str, params: dict[str, Any]) -> pd.DataFrame:
        """Execute a parameterised query with Parquet caching.

        Args:
            name: Cache filename prefix.
            sql: SQL with named placeholders (``:param``).
            params: Bound parameters; ``end`` is converted to exclusive.

        Returns:
            DataFrame with ``*_brl`` columns scaled to BRL.
        """
        if "end" in params:
            params = {**params, "end": _to_exclusive_end(params["end"])}
        cache = _cache_path(name, sql, params)

        if cache and cache.exists():
            logger.debug("Cache hit: %s", cache.name)
            return _scale_brl(pd.read_parquet(cache))

        logger.info("DB query [%s] %s → %s", name, params.get("start"), params.get("end"))
        with self._engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params)

        if cache:
            df.to_parquet(cache, index=False)
            logger.debug("Cached → %s", cache.name)

        return _scale_brl(df)

    def _date_params(self) -> dict[str, str]:
        return {"start": self.start_date, "end": self.end_date}

    def cohort_base(self) -> pd.DataFrame:
        """All users with attribution, founders, and profile data.

        No date filter — returns every user ever registered.

        Returns:
            DataFrame with columns: user_id, signup_date, last_active_at,
            status, account_type, acquisition_source, referral_code_id,
            referral_code, referral_code_name, commission_rate_bps,
            referral_code_type, is_founder, founder_number,
            founder_network_size, invites_remaining, invite_code,
            country_code, preferred_currency, onboarding_completed.
        """
        return self._run("cohort_base", _COHORT_BASE_SQL, {})

    def onramp_revenue(self) -> pd.DataFrame:
        """Per-user onramp/offramp revenue aggregated over the analysis window.

        Returns:
            DataFrame with columns: user_id, onramp_revenue_brl,
            n_conversions, onramp_volume_brl, offramp_volume_usdc.
        """
        return self._run("onramp_rev", _ONRAMP_REVENUE_SQL, self._date_params())

    def onramp_monthly(self) -> pd.DataFrame:
        """Full-history monthly onramp revenue per user (no date filter).

        Used to build the cohort LTV time-series.

        Returns:
            DataFrame with columns: user_id, month (date), onramp_revenue_brl.
        """
        return self._run("onramp_monthly", _ONRAMP_MONTHLY_SQL, {})

    def card_fees(self) -> pd.DataFrame:
        """Per-user paid card annual fees over the analysis window.

        Returns:
            DataFrame with columns: user_id, card_fee_usd.
        """
        return self._run("card_fees", _CARD_FEES_SQL, self._date_params())

    def card_transactions(self) -> pd.DataFrame:
        """Per-user posted card transaction counts with window total.

        The ``total_tx_count`` column is identical for every row (window fn).
        Used for Rain invoice pro-rata cost allocation.

        Returns:
            DataFrame with columns: user_id, user_tx_count, total_tx_count.
        """
        return self._run("card_txs", _CARD_TXS_SQL, self._date_params())

    def billing_charges(self) -> pd.DataFrame:
        """Per-user settled billing charges (actual card tx fee revenue).

        Covers 2026-04-03+. For earlier periods, fallback to Rain pro-rata.

        Returns:
            DataFrame with columns: user_id, card_tx_fee_usd.
        """
        return self._run("billing", _BILLING_CHARGES_SQL, self._date_params())

    def cashback(self) -> pd.DataFrame:
        """Per-user completed cashback paid out (cost item).

        Returns:
            DataFrame with columns: user_id, cashback_usd.
        """
        return self._run("cashback", _CASHBACK_SQL, self._date_params())

    def revenue_share(self) -> pd.DataFrame:
        """Revenue share paid out per source user (cost item).

        Joins on ``source_user_id`` — the user whose activity generated the fee.

        Returns:
            DataFrame with columns: user_id, revenue_share_paid_usd.
        """
        return self._run("rev_share", _REVENUE_SHARE_SQL, self._date_params())

    def swaps(self) -> pd.DataFrame:
        """Per-user swap fee revenue over the analysis window.

        Returns:
            DataFrame with columns: user_id, swap_fee_usd, n_swaps.
        """
        return self._run("swaps", _SWAP_SQL, self._date_params())

    def payouts(self) -> pd.DataFrame:
        """Per-user completed international payout fee revenue.

        Returns:
            DataFrame with columns: user_id, payout_fee_usd.
        """
        return self._run("payouts", _PAYOUT_SQL, self._date_params())

    def fx_rate(self) -> float:
        """Median BRL/USDC effective rate over the analysis window.

        Used to convert BRL revenues to USD: ``amount_usd = amount_brl / fx_rate``.
        Falls back to 5.80 if no conversions exist in the window.

        Returns:
            Median effective rate (BRL per 1 USDC).
        """
        if self._fx_rate_cached is not None:
            return self._fx_rate_cached
        df = self._run("fx_rate", _FX_RATE_SQL, self._date_params())
        if df.empty or pd.isna(df["fx_rate"].iloc[0]):
            self._fx_rate_cached = 5.80
        else:
            self._fx_rate_cached = float(df["fx_rate"].iloc[0])
        return self._fx_rate_cached
