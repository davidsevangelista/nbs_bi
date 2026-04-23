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
    u.full_name,
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
    up.onboarding_completed,
    u.kyc_level
FROM users u
LEFT JOIN user_registrations ur ON ur.user_id = u.id
LEFT JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
LEFT JOIN founders f ON f.user_id = u.id
LEFT JOIN user_profiles up ON up.user_id = u.id
"""

_CONVERSION_REVENUE_SQL = """
SELECT
    user_id::TEXT AS user_id,
    SUM(CASE WHEN direction = 'brl_to_usdc'
             THEN (fee_amount_brl + spread_revenue_brl)::FLOAT / 100.0
             ELSE 0 END) AS onramp_revenue_brl,
    SUM(CASE WHEN direction = 'usdc_to_brl'
             THEN (fee_amount_brl + spread_revenue_brl)::FLOAT / 100.0
             ELSE 0 END) AS offramp_revenue_brl,
    SUM(CASE WHEN direction = 'brl_to_usdc'
             THEN (fee_amount_usdc + spread_revenue_usdc)::FLOAT / 1000000.0
             ELSE 0 END) AS onramp_revenue_usdc,
    SUM(CASE WHEN direction = 'usdc_to_brl'
             THEN (fee_amount_usdc + spread_revenue_usdc)::FLOAT / 1000000.0
             ELSE 0 END) AS offramp_revenue_usdc,
    COUNT(*) AS n_conversions,
    SUM(CASE WHEN direction = 'brl_to_usdc'
             THEN (from_amount_brl)::FLOAT / 100.0
             ELSE 0 END) AS onramp_volume_brl,
    SUM(CASE WHEN direction = 'usdc_to_brl'
             THEN (from_amount_usdc)::FLOAT / 1000000.0
             ELSE 0 END) AS offramp_volume_usdc
FROM conversion_quotes
WHERE used = TRUE
  AND created_at >= :start
  AND created_at <  :end
GROUP BY user_id
"""

_CONVERSION_MONTHLY_SQL = """
SELECT
    user_id::TEXT                                                        AS user_id,
    DATE_TRUNC('month', created_at)::DATE                                AS month,
    SUM(fee_amount_brl + spread_revenue_brl)::FLOAT / 100.0             AS conversion_revenue_brl,
    SUM(fee_amount_usdc + spread_revenue_usdc)::FLOAT / 1000000.0       AS conversion_revenue_usdc
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

_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_SWAP_SQL = """
SELECT
    user_id::TEXT                                                            AS user_id,
    SUM(
        CASE
            WHEN input_mint  = :usdc_mint
            THEN input_amount::FLOAT  / 1e6 * platform_fee_bps::FLOAT / 1e4
            WHEN output_mint = :usdc_mint
            THEN output_amount::FLOAT / 1e6 * platform_fee_bps::FLOAT / 1e4
            ELSE 0
        END
    )                                                                        AS swap_fee_usd,
    COUNT(*)                                                                 AS n_swaps
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

_CARD_TXS_MONTHLY_SQL = """
SELECT
    user_id::TEXT                               AS user_id,
    DATE_TRUNC('month', posted_at)::DATE        AS month,
    COUNT(*)                                    AS n_card_txns
FROM card_transactions
WHERE status = 'completed'
  AND transaction_type = 'spend'
  AND posted_at IS NOT NULL
GROUP BY user_id, DATE_TRUNC('month', posted_at)
ORDER BY user_id, month
"""

_REVENUE_GENERATING_SQL = """
SELECT COUNT(DISTINCT user_id) AS revenue_generating_count
FROM (
    SELECT user_id::TEXT FROM conversion_quotes WHERE used = TRUE
    UNION SELECT user_id::TEXT FROM card_annual_fees WHERE status = 'paid'
    UNION SELECT user_id::TEXT FROM billing_charges WHERE status = 'settled'
    UNION SELECT user_id::TEXT FROM swap_transactions
    UNION SELECT user_id::TEXT FROM unblockpay_payouts WHERE status = 'completed'
    UNION SELECT user_id::TEXT FROM cashback_rewards WHERE status = 'completed'
    UNION SELECT user_id::TEXT FROM pix_transfers WHERE status = 'completed'
    UNION SELECT recipient_user_id::TEXT AS user_id FROM revenue_share_rewards WHERE status = 'completed'
) t
"""

_ACTIVITY_KPIS_SQL = """
SELECT
    COUNT(*) FILTER (WHERE last_active_at >= NOW() - INTERVAL '1 day')   AS dau,
    COUNT(*) FILTER (WHERE last_active_at >= NOW() - INTERVAL '7 days')  AS wau,
    COUNT(*) FILTER (WHERE last_active_at >= NOW() - INTERVAL '30 days') AS mau
FROM users
WHERE status != 'suspended'
"""

_SIGNUPS_24H_SQL = """
SELECT COUNT(*) AS new_signups_24h
FROM users
WHERE created_at >= NOW() - INTERVAL '24 hours'
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
        """All users with attribution, founders, profile, and KYC data.

        No date filter — returns every user ever registered.

        Returns:
            DataFrame with columns: user_id, signup_date, last_active_at,
            status, account_type, acquisition_source, referral_code_id,
            referral_code, referral_code_name, commission_rate_bps,
            referral_code_type, is_founder, founder_number,
            founder_network_size, invites_remaining, invite_code,
            country_code, preferred_currency, onboarding_completed,
            kyc_level.
        """
        return self._run("cohort_base", _COHORT_BASE_SQL, {})

    def conversion_revenue(self) -> pd.DataFrame:
        """Per-user on/off-ramp revenue aggregated over the analysis window.

        Both directions (BRL→USDC onramp and USDC→BRL offramp) are returned
        as separate columns so callers can analyse each independently.

        Returns:
            DataFrame with columns: user_id, onramp_revenue_brl,
            offramp_revenue_brl, n_conversions, onramp_volume_brl,
            offramp_volume_usdc.
        """
        return self._run("conv_rev", _CONVERSION_REVENUE_SQL, self._date_params())

    def conversion_monthly(self) -> pd.DataFrame:
        """Full-history monthly conversion revenue per user (no date filter).

        Sums both onramp and offramp directions. Used to build the cohort
        LTV time-series.

        Returns:
            DataFrame with columns: user_id, month (date),
            conversion_revenue_brl.
        """
        return self._run("conv_monthly", _CONVERSION_MONTHLY_SQL, {})

    def card_fees(self) -> pd.DataFrame:
        """All-time paid card annual fees per user (no date filter).

        The annual fee is a one-time/renewal commitment paid at signup; it
        should appear in a user's revenue profile regardless of the analysis
        window. Corresponds to the Founder Edition card annual signature.

        Returns:
            DataFrame with columns: user_id, card_fee_usd.
        """
        return self._run("card_fees", _CARD_FEES_SQL, {})

    def card_transactions(self) -> pd.DataFrame:
        """Per-user posted card transaction counts with window total.

        The ``total_tx_count`` column is identical for every row (window fn).
        Used for Rain invoice pro-rata cost allocation.

        Returns:
            DataFrame with columns: user_id, user_tx_count, total_tx_count.
        """
        return self._run("card_txs", _CARD_TXS_SQL, self._date_params())

    def card_transactions_monthly(self) -> pd.DataFrame:
        """Monthly card spend transaction counts per user (full history).

        No date filter — returns all months so that cohort LTV can deduct
        processing costs at the correct activity month regardless of the
        analysis window. Used by ``_build_monthly_ltv()`` for per-month cost
        attribution.

        Returns:
            DataFrame with columns: user_id, month (date), n_card_txns (int).
        """
        return self._run("card_txs_monthly", _CARD_TXS_MONTHLY_SQL, {})

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

        Only USDC-side amounts are used: input_amount for USDC-input swaps
        (USDC→token) and output_amount for USDC-output swaps (token→USDC).
        Non-USDC pairs are excluded — no on-chain price oracle is available.

        Returns:
            DataFrame with columns: user_id, swap_fee_usd, n_swaps.
        """
        return self._run("swaps", _SWAP_SQL, {**self._date_params(), "usdc_mint": _USDC_MINT})

    def payouts(self) -> pd.DataFrame:
        """Per-user completed international payout fee revenue.

        Returns:
            DataFrame with columns: user_id, payout_fee_usd.
        """
        return self._run("payouts", _PAYOUT_SQL, self._date_params())

    def revenue_generating_count(self) -> int:
        """All-time count of users with at least one completed transaction.

        No date filter — unions all activity tables to match the dashboard's
        "Revenue-Generating" funnel stage (~2,407 users as of 2026-04-20).

        Returns:
            Count of distinct revenue-generating user IDs.
        """
        df = self._run("rev_gen_count", _REVENUE_GENERATING_SQL, {})
        if df.empty:
            return 0
        return int(df["revenue_generating_count"].iloc[0])

    def activity_kpis(self) -> dict[str, int]:
        """DAU / WAU / MAU from users.last_active_at as of now.

        No date-range filter — always relative to the current timestamp so the
        values reflect real platform activity regardless of the dashboard window.

        Returns:
            Dict with keys ``dau``, ``wau``, ``mau`` (all int).
        """
        df = self._run("activity_kpis", _ACTIVITY_KPIS_SQL, {})
        if df.empty:
            return {"dau": 0, "wau": 0, "mau": 0}
        row = df.iloc[0]
        return {
            "dau": int(row.get("dau", 0) or 0),
            "wau": int(row.get("wau", 0) or 0),
            "mau": int(row.get("mau", 0) or 0),
        }

    def signups_24h(self) -> int:
        """Count of users registered in the last 24 hours as of now.

        No date-range filter — always relative to NOW() so the value is
        exact regardless of the dashboard window or timezone boundaries.

        Returns:
            Integer count of new signups in the last 24 hours.
        """
        df = self._run("signups_24h", _SIGNUPS_24H_SQL, {})
        if df.empty:
            return 0
        return int(df.iloc[0].get("new_signups_24h", 0) or 0)

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
