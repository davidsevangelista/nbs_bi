"""Database queries for on/off ramp (BRL ⇄ USDC) conversions.

Reads from the production read-only replica using READONLY_DATABASE_URL.
Schema reference: docs/specs/database.md

Monetary scaling applied on load:
  - *_brl bigint columns  : centavos ÷ 100       → BRL
  - *_usdc bigint columns : micros ÷ 1_000_000   → USDC
  - exchange_rate / effective_rate / spread_percentage : numeric, already real

Results are cached as Parquet files in DB_CACHE_DIR when that env var is set.
Cache key = SHA-256 of (SQL + params), so changing dates invalidates the cache.
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
_USDC_DIVISOR = 1_000_000

# ---------------------------------------------------------------------------
# SQL — grounded in the schema documented in docs/specs/database.md
# ---------------------------------------------------------------------------

# conversion_quotes: one row per BRL⇄USDC quote.
# used=TRUE filters to completed (executed) conversions only.
# UUIDs are cast to TEXT so pyarrow can serialise them without conversion.
_CONVERSIONS_SQL = """
SELECT
    cq.id::TEXT                     AS id,
    cq.user_id::TEXT                AS user_id,
    cq.direction::TEXT              AS direction,
    cq.from_amount_brl              AS from_amount_brl,
    cq.from_amount_usdc             AS from_amount_usdc,
    cq.to_amount_brl                AS to_amount_brl,
    cq.to_amount_usdc               AS to_amount_usdc,
    cq.exchange_rate,
    cq.effective_rate,
    cq.fee_amount_brl               AS fee_amount_brl,
    cq.fee_amount_usdc              AS fee_amount_usdc,
    cq.spread_revenue_brl           AS spread_revenue_brl,
    cq.spread_revenue_usdc          AS spread_revenue_usdc,
    cq.spread_percentage,
    cq.conversion_request_id::TEXT  AS conversion_request_id,
    cq.processing_mode,
    cq.created_at,
    cq.updated_at
FROM conversion_quotes cq
WHERE cq.used = TRUE
  AND cq.created_at >= :start_date
  AND cq.created_at <  :end_date
"""

# pix_requests: inbound PIX (BRL deposits). Only settled states.
# Settled state set from docs/specs/database.md + production observation.
_PIX_DEPOSITS_SQL = """
SELECT
    pr.id::TEXT         AS id,
    pr.user_id::TEXT    AS user_id,
    pr.amount_brl,
    pr.state,
    pr.provider_name,
    pr.created_at
FROM pix_requests pr
WHERE pr.created_at >= :start_date
  AND pr.created_at <  :end_date
  AND pr.state IN ('pix_paid', 'brl_credited', 'completed')
"""

# pix_transfers: outbound PIX (BRL withdrawals). Only completed.
# Includes fee_brl / net_amount_brl for accurate liquidity tracking.
_PIX_TRANSFERS_SQL = """
SELECT
    pt.id::TEXT         AS id,
    pt.user_id::TEXT    AS user_id,
    pt.amount_brl,
    pt.fee_brl,
    pt.net_amount_brl,
    pt.pix_key_type,
    pt.status,
    pt.executed_at,
    pt.created_at
FROM pix_transfers pt
WHERE pt.created_at >= :start_date
  AND pt.created_at <  :end_date
  AND pt.status = 'completed'
"""


_CARD_TXS_ACTIVE_SQL = """
SELECT user_id::TEXT AS user_id, posted_at AS created_at,
       amount / 100.0 AS amount_usd
FROM card_transactions
WHERE status = 'completed'
  AND transaction_type = 'spend'
  AND posted_at >= :start_date
  AND posted_at <  :end_date
"""

_CARD_FEES_ACTIVE_SQL = """
SELECT user_id::TEXT AS user_id, paid_at AS created_at
FROM card_annual_fees
WHERE status = 'paid'
  AND paid_at >= :start_date
  AND paid_at <  :end_date
"""

_BILLING_ACTIVE_SQL = """
SELECT user_id::TEXT AS user_id, created_at
FROM billing_charges
WHERE status = 'settled'
  AND created_at >= :start_date
  AND created_at <  :end_date
"""

_CARD_FEES_REVENUE_SQL = """
SELECT COALESCE(SUM(amount_usdc::FLOAT), 0.0) AS total
FROM card_annual_fees
WHERE status = 'paid'
  AND paid_at >= :start_date
  AND paid_at <  :end_date
"""

_BILLING_REVENUE_SQL = """
SELECT COALESCE(SUM(amount::FLOAT / 1000000.0), 0.0) AS total
FROM billing_charges
WHERE status = 'settled'
  AND created_at >= :start_date
  AND created_at <  :end_date
"""

_CARD_FEES_MONTHLY_SQL = """
SELECT
    date_trunc('month', paid_at)::DATE AS month,
    COALESCE(SUM(amount_usdc::FLOAT), 0.0) AS card_fee_usd
FROM card_annual_fees
WHERE status = 'paid'
  AND paid_at >= :start_date
  AND paid_at <  :end_date
GROUP BY 1
ORDER BY 1
"""

_BILLING_MONTHLY_SQL = """
SELECT
    date_trunc('month', created_at)::DATE AS month,
    COALESCE(SUM(amount::FLOAT / 1000000.0), 0.0) AS billing_usd
FROM billing_charges
WHERE status = 'settled'
  AND created_at >= :start_date
  AND created_at <  :end_date
GROUP BY 1
ORDER BY 1
"""

_SWAPS_ACTIVE_SQL = """
SELECT user_id::TEXT AS user_id, "timestamp" AS created_at
FROM swap_transactions
WHERE "timestamp" >= :start_date
  AND "timestamp" <  :end_date
"""

_PAYOUTS_ACTIVE_SQL = """
SELECT user_id::TEXT AS user_id, created_at
FROM unblockpay_payouts
WHERE status = 'completed'
  AND created_at >= :start_date
  AND created_at <  :end_date
"""


# user attribution: acquisition source, referral code, founder status — no date filter.
# Joined Python-side to top_users via user_id.
_USER_ATTRIBUTION_SQL = """
SELECT
    u.id::TEXT                                   AS user_id,
    COALESCE(ur.source_type, 'organic')          AS acquisition_source,
    COALESCE(rc.public_name, '')                 AS referral_code_name,
    CASE WHEN f.user_id IS NOT NULL THEN TRUE ELSE FALSE END AS is_founder
FROM users u
LEFT JOIN user_registrations ur ON ur.user_id = u.id
LEFT JOIN referral_codes rc     ON rc.id = ur.attributed_referral_code_id
LEFT JOIN founders f            ON f.user_id = u.id
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_exclusive_end(end_date: str) -> str:
    """Convert an inclusive end date to an exclusive upper bound (start of next day).

    Args:
        end_date: ISO date string, e.g. ``"2026-03-31"``.
            If the string already contains a time component it is returned as-is.

    Returns:
        ISO date string for the start of the day after *end_date*.
    """
    s = end_date.strip()
    if "T" in s or " " in s or s.count(":") >= 2:
        return s
    return (date.fromisoformat(s) + timedelta(days=1)).isoformat()


def _cache_path(prefix: str, sql: str, params: dict[str, Any]) -> Path | None:
    """Return the Parquet cache path for this query, or None if caching is disabled.

    Args:
        prefix: Short label used in the filename (e.g. ``"conversions"``).
        sql: Raw SQL text — included in the cache key.
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


def _scale_currency(df: pd.DataFrame) -> pd.DataFrame:
    """Scale monetary integer columns to real units in-place.

    Applies:
      - columns ending ``_brl``  : ÷ 100         (centavos → BRL)
      - columns ending ``_usdc`` : ÷ 1_000_000   (micros → USDC)

    Args:
        df: DataFrame as returned by the database driver.

    Returns:
        New DataFrame with monetary columns scaled.
    """
    out = df.copy()
    for col in out.columns:
        if col.endswith("_brl"):
            out[col] = pd.to_numeric(out[col], errors="coerce") / _BRL_DIVISOR
        elif col.endswith("_usdc"):
            out[col] = pd.to_numeric(out[col], errors="coerce") / _USDC_DIVISOR
    return out


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class OnrampQueries:
    """Fetches on/off ramp data from the NBS production read-only database.

    All monetary columns are returned in real units (BRL, USDC) after scaling.
    UUID columns are returned as strings. Timestamps remain as-is.

    Args:
        start_date: Inclusive window start, ISO date string (``"2026-01-01"``).
        end_date: Inclusive window end, ISO date string (``"2026-03-31"``).
        db_url: Override for the database URL. Falls back to
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
        resolved_url = db_url or READONLY_DATABASE_URL
        if not resolved_url:
            raise ValueError(
                "No database URL available. Set READONLY_DATABASE_URL in your .env file."
            )
        self._db_url = resolved_url
        self.start_date = start_date
        self.end_date = end_date
        self._engine: Engine | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _engine_lazy(self) -> Engine:
        """Lazy SQLAlchemy engine — created on first query."""
        if self._engine is None:
            self._engine = create_engine(self._db_url, pool_pre_ping=True)
        return self._engine

    def _run_static(self, name: str, sql: str) -> pd.DataFrame:
        """Execute a date-independent query with optional Parquet caching.

        Args:
            name: Short label for the cache filename.
            sql: SQL string with no bind parameters.

        Returns:
            DataFrame with monetary columns scaled.
        """
        cache = _cache_path(name, sql, {})
        if cache and cache.exists():
            logger.debug("Cache hit: %s", cache.name)
            return _scale_currency(pd.read_parquet(cache))
        logger.info("DB query [%s] (no date range)", name)
        with self._engine_lazy.connect() as conn:
            df = pd.read_sql(text(sql), conn)
        if cache:
            df.to_parquet(cache, index=False)
            logger.debug("Cached → %s", cache.name)
        return _scale_currency(df)

    def _run(self, name: str, sql: str, params: dict[str, Any]) -> pd.DataFrame:
        """Execute a parameterised SQL query with optional Parquet caching.

        Args:
            name: Short label for the cache filename.
            sql: Parameterised SQL string using named placeholders (``:name``).
            params: Named bind parameters. ``end_date`` is automatically
                converted to an exclusive upper bound.

        Returns:
            DataFrame with monetary columns already scaled.
        """
        bound = {**params, "end_date": _to_exclusive_end(params["end_date"])}
        cache = _cache_path(name, sql, bound)

        if cache and cache.exists():
            logger.debug("Cache hit: %s", cache.name)
            return _scale_currency(pd.read_parquet(cache))

        logger.info("DB query [%s] %s → %s", name, bound["start_date"], bound["end_date"])
        with self._engine_lazy.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=bound)

        if cache:
            df.to_parquet(cache, index=False)
            logger.debug("Cached → %s", cache.name)

        return _scale_currency(df)

    def _run_scalar(self, sql: str, params: dict[str, Any]) -> float:
        """Execute a single-value aggregation query and return the scalar.

        Args:
            sql: SQL with named placeholders; must SELECT a single column 'total'.
            params: Named bind parameters (``end_date`` converted to exclusive).

        Returns:
            Float scalar; 0.0 on empty result.
        """
        bound = {**params, "end_date": _to_exclusive_end(params["end_date"])}
        with self._engine_lazy.connect() as conn:
            row = conn.execute(text(sql), bound).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def _date_params(
        self,
        start_date: str | None,
        end_date: str | None,
    ) -> dict[str, str]:
        """Resolve per-call date overrides, falling back to instance defaults."""
        return {
            "start_date": start_date or self.start_date,
            "end_date": end_date or self.end_date,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def conversions(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch completed BRL ⇄ USDC conversions (``used = TRUE``).

        Returns one row per executed quote. Monetary columns are in real units:
        ``*_brl`` in BRL, ``*_usdc`` in USDC. ``direction`` is a plain string:
        ``"brl_to_usdc"`` (onramp) or ``"usdc_to_brl"`` (offramp).

        Columns returned:
            id, user_id, direction,
            from_amount_brl, from_amount_usdc,
            to_amount_brl, to_amount_usdc,
            exchange_rate, effective_rate,
            fee_amount_brl, fee_amount_usdc,
            spread_revenue_brl, spread_revenue_usdc, spread_percentage,
            conversion_request_id, processing_mode,
            created_at, updated_at

        Args:
            start_date: Override instance start_date for this call.
            end_date: Override instance end_date for this call.

        Returns:
            DataFrame with one row per completed conversion.
        """
        return self._run("conversions", _CONVERSIONS_SQL, self._date_params(start_date, end_date))

    def pix_deposits(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch settled inbound PIX deposits (``pix_requests``).

        Only rows with state in ``pix_paid``, ``brl_credited``, ``completed``
        are returned — these represent funds that successfully entered NBS.

        Columns returned:
            id, user_id, amount_brl, state, provider_name, created_at

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with one row per settled PIX deposit.
        """
        return self._run("pix_deposits", _PIX_DEPOSITS_SQL, self._date_params(start_date, end_date))

    def user_attribution(self) -> pd.DataFrame:
        """Fetch acquisition source, referral code, and founder flag for all users.

        No date filter — returns one row per user across all time. The result
        is intended for a Python-side left-join onto top_users or similar tables.

        Columns returned:
            user_id, acquisition_source, referral_code_name, is_founder

        Returns:
            DataFrame with one row per user.
        """
        return self._run_static("ramp_attr", _USER_ATTRIBUTION_SQL)

    def pix_transfers(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch completed outbound PIX transfers (``pix_transfers``).

        Only ``status = 'completed'`` rows are returned.

        Columns returned:
            id, user_id, amount_brl, fee_brl, net_amount_brl,
            pix_key_type, status, executed_at, created_at

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with one row per completed PIX withdrawal.
        """
        return self._run(
            "pix_transfers", _PIX_TRANSFERS_SQL, self._date_params(start_date, end_date)
        )

    def card_transactions_active(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch posted card transactions for active-user counting.

        Columns returned: user_id, created_at

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with one row per posted card transaction.
        """
        return self._run(
            "card_txs_active", _CARD_TXS_ACTIVE_SQL, self._date_params(start_date, end_date)
        )

    def card_fees_active(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch paid card annual fees for active-user counting.

        Columns returned: user_id, created_at

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with one row per paid card annual fee.
        """
        return self._run(
            "card_fees_active", _CARD_FEES_ACTIVE_SQL, self._date_params(start_date, end_date)
        )

    def billing_charges_active(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch settled billing charges (card tx fees) for active-user counting.

        Columns returned: user_id, created_at

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with one row per settled billing charge.
        """
        return self._run(
            "billing_active", _BILLING_ACTIVE_SQL, self._date_params(start_date, end_date)
        )

    def swaps_active(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch swap transactions for active-user counting.

        Columns returned: user_id, created_at

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with one row per swap transaction.
        """
        return self._run("swaps_active", _SWAPS_ACTIVE_SQL, self._date_params(start_date, end_date))

    def payouts_active(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Fetch completed Unblockpay payouts for active-user counting.

        Columns returned: user_id, created_at

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with one row per completed payout.
        """
        return self._run(
            "payouts_active", _PAYOUTS_ACTIVE_SQL, self._date_params(start_date, end_date)
        )

    def card_fees_revenue_total(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> float:
        """Total card annual fee revenue (USD) for the period.

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            Sum of amount_usdc for paid card_annual_fees records.
        """
        return self._run_scalar(_CARD_FEES_REVENUE_SQL, self._date_params(start_date, end_date))

    def billing_charges_revenue_total(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> float:
        """Total billing charges revenue (USD) for the period.

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            Sum of amount / 1_000_000 for settled billing_charges records.
        """
        return self._run_scalar(_BILLING_REVENUE_SQL, self._date_params(start_date, end_date))

    def card_revenue_monthly(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Monthly card revenue split into card fees and billing charges (USD).

        Args:
            start_date: Override instance start_date.
            end_date: Override instance end_date.

        Returns:
            DataFrame with columns: month (timestamp), card_fee_usd, billing_usd.
        """
        params = self._date_params(start_date, end_date)
        fees = self._run("card_fees_monthly", _CARD_FEES_MONTHLY_SQL, params)
        billing = self._run("billing_monthly", _BILLING_MONTHLY_SQL, params)

        if fees.empty and billing.empty:
            return pd.DataFrame(columns=["month", "card_fee_usd", "billing_usd"])

        fees["month"] = pd.to_datetime(fees["month"])
        billing["month"] = pd.to_datetime(billing["month"])

        merged = fees.merge(billing, on="month", how="outer").fillna(0.0)
        return merged.sort_values("month").reset_index(drop=True)
