"""Meta Ads (FACEBK) campaign ROI analysis.

Loads company card spend from a Rain CSV export, identifies ad campaign windows,
queries the DB for signups and revenue from those cohorts, and computes
ROAS / CAC for each campaign.

Usage::

    from nbs_bi.clients.campaigns import load_ad_spend, CampaignAnalyzer

    spend = load_ad_spend("data/nbs_corp_card/rain-transactions-export-2026-04-20.csv")
    analyzer = CampaignAnalyzer(spend, db_url=None)
    summary = analyzer.roi_summary()
    daily = analyzer.daily_context()
"""

from __future__ import annotations

import hashlib
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from nbs_bi.clients.models import _KYC_COST_USD
from nbs_bi.config import INCLUDE_SWAP_FEES

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_DB_CACHE_DIR = Path(os.environ.get("DB_CACHE_DIR", "data/processed/db_cache"))

# Solana USDC mint address — same constant as in clients/queries.py.
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ------------------------------------------------------------------
# SQL — fixed, schema-grounded
# ------------------------------------------------------------------

_DAILY_SIGNUPS_SQL = """
SELECT
    DATE(created_at AT TIME ZONE 'UTC')  AS signup_date,
    COUNT(*)                              AS new_signups
FROM users
WHERE created_at >= :start
  AND created_at <  :end
GROUP BY 1
ORDER BY 1
"""

_COHORT_KYC_SQL = """
SELECT
    DATE(kv.completed_at AT TIME ZONE 'UTC') AS kyc_date,
    COUNT(*)                                  AS kyc_count
FROM kyc_verifications kv
WHERE kv.status = 'completed'
  AND kv.review_answer = 'GREEN'
  AND kv.user_id::TEXT IN (
      SELECT id::TEXT FROM users
      WHERE created_at >= :cohort_start
        AND created_at <  :cohort_end
        AND (:referral_code = '' OR id IN (
            SELECT ur.user_id FROM user_registrations ur
            JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
            WHERE rc.code = :referral_code
        ))
  )
GROUP BY 1
ORDER BY 1
"""

_COHORT_KYC_LEVEL_SQL = """
SELECT COUNT(*) AS kyc_count
FROM users
WHERE created_at >= :cohort_start
  AND created_at <  :cohort_end
  AND kyc_level >= 1
  AND (:referral_code = '' OR id IN (
      SELECT ur.user_id FROM user_registrations ur
      JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
      WHERE rc.code = :referral_code
  ))
"""

_REFERRAL_CODES_SQL = """
SELECT DISTINCT rc.code
FROM referral_codes rc
JOIN user_registrations ur ON ur.attributed_referral_code_id = rc.id
ORDER BY rc.code
"""

_COHORT_REVENUE_SQL = """
WITH cohort AS (
    SELECT id::TEXT AS user_id
    FROM users
    WHERE created_at >= :cohort_start
      AND created_at <  :cohort_end
      AND (:referral_code = '' OR id IN (
          SELECT ur.user_id FROM user_registrations ur
          JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
          WHERE rc.code = :referral_code
      ))
),
onramp_rev AS (
    SELECT user_id::TEXT,
           SUM(fee_amount_brl + spread_revenue_brl) / 100.0       AS onramp_brl,
           SUM(fee_amount_usdc + spread_revenue_usdc) / 1000000.0 AS onramp_usdc
    FROM conversion_quotes
    WHERE used = TRUE
    GROUP BY user_id::TEXT
),
card_fee_rev AS (
    SELECT user_id::TEXT, SUM(amount_usdc::FLOAT) AS card_fee_usd
    FROM card_annual_fees
    WHERE status = 'paid'
    GROUP BY user_id::TEXT
),
billing_rev AS (
    SELECT user_id::TEXT, SUM(amount::FLOAT / 1000000.0) AS billing_usd
    FROM billing_charges
    WHERE status = 'settled'
    GROUP BY user_id::TEXT
),
swap_rev AS (
    SELECT user_id::TEXT,
           SUM(
               CASE
                   WHEN input_mint  = :usdc_mint
                   THEN input_amount::FLOAT  / 1000000.0 * platform_fee_bps::FLOAT / 10000.0
                   WHEN output_mint = :usdc_mint
                   THEN output_amount::FLOAT / 1000000.0 * platform_fee_bps::FLOAT / 10000.0
                   ELSE 0
               END
           ) AS swap_fee_usd
    FROM swap_transactions
    GROUP BY user_id::TEXT
),
cashback_cost AS (
    SELECT user_id::TEXT, SUM(reward_usd_value::FLOAT) AS cashback_usd
    FROM cashback_rewards
    WHERE status = 'completed'
    GROUP BY user_id::TEXT
),
rev_share_cost AS (
    SELECT source_user_id::TEXT AS user_id,
           SUM(reward_usd_value::FLOAT) AS revenue_share_usd
    FROM revenue_share_rewards
    WHERE status = 'completed'
    GROUP BY source_user_id::TEXT
),
fx AS (
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY effective_rate) AS rate
    FROM conversion_quotes
    WHERE used = TRUE AND direction = 'brl_to_usdc'
),
transacting AS (
    SELECT DISTINCT user_id::TEXT AS uid FROM conversion_quotes WHERE used = TRUE
    UNION SELECT DISTINCT user_id::TEXT FROM card_annual_fees WHERE status = 'paid'
    UNION SELECT DISTINCT user_id::TEXT FROM billing_charges  WHERE status = 'settled'
    UNION SELECT DISTINCT user_id::TEXT FROM swap_transactions
    UNION SELECT DISTINCT user_id::TEXT FROM unblockpay_payouts WHERE status = 'completed'
)
SELECT
    COUNT(DISTINCT c.user_id)                                                AS cohort_users,
    COUNT(DISTINCT t.uid)                                                    AS transacting_users,
    ROUND(SUM(
        COALESCE(or_.onramp_brl, 0) / fx.rate + COALESCE(or_.onramp_usdc, 0)
    )::NUMERIC, 4) AS onramp_rev_usd,
    ROUND(SUM(COALESCE(cf.card_fee_usd, 0))::NUMERIC, 4)                    AS card_fee_usd,
    ROUND(SUM(COALESCE(br.billing_usd,  0))::NUMERIC, 4)                    AS billing_usd,
    ROUND(SUM(COALESCE(sw.swap_fee_usd, 0))::NUMERIC, 4)                    AS swap_fee_usd,
    ROUND(SUM(COALESCE(cb.cashback_usd,  0))::NUMERIC, 4)                   AS cashback_usd,
    ROUND(SUM(COALESCE(rs.revenue_share_usd, 0))::NUMERIC, 4)               AS revenue_share_usd,
    ROUND(SUM(
        COALESCE(or_.onramp_brl, 0) / fx.rate + COALESCE(or_.onramp_usdc, 0)
        + COALESCE(cf.card_fee_usd, 0)
        + COALESCE(br.billing_usd,  0)
        + COALESCE(sw.swap_fee_usd, 0)
        - COALESCE(cb.cashback_usd,  0)
        - COALESCE(rs.revenue_share_usd, 0)
    )::NUMERIC, 4)                                                           AS total_revenue_usd
FROM cohort c
CROSS JOIN fx
LEFT JOIN onramp_rev    or_ ON or_.user_id = c.user_id
LEFT JOIN card_fee_rev  cf  ON cf.user_id  = c.user_id
LEFT JOIN billing_rev   br  ON br.user_id  = c.user_id
LEFT JOIN swap_rev      sw  ON sw.user_id  = c.user_id
LEFT JOIN cashback_cost cb  ON cb.user_id  = c.user_id
LEFT JOIN rev_share_cost rs ON rs.user_id  = c.user_id
LEFT JOIN transacting   t   ON t.uid       = c.user_id
"""


_DAILY_COHORT_REVENUE_SQL = """
WITH cohort AS (
    SELECT id::TEXT AS user_id
    FROM users
    WHERE created_at >= :cohort_start
      AND created_at <  :cohort_end
      AND (:referral_code = '' OR id IN (
          SELECT ur.user_id FROM user_registrations ur
          JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
          WHERE rc.code = :referral_code
      ))
),
fx AS (
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY effective_rate) AS rate
    FROM conversion_quotes
    WHERE used = TRUE AND direction = 'brl_to_usdc'
),
conversion_rev AS (
    SELECT DATE(cq.created_at AT TIME ZONE 'UTC') AS rev_date,
           (cq.fee_amount_brl + cq.spread_revenue_brl)::FLOAT / 100.0
               / NULLIF(fx.rate, 0)
           + (cq.fee_amount_usdc + cq.spread_revenue_usdc)::FLOAT / 1000000.0 AS rev_usd
    FROM conversion_quotes cq, fx
    WHERE cq.used = TRUE
      AND cq.user_id::TEXT IN (SELECT user_id FROM cohort)
),
card_fee_rev AS (
    SELECT DATE(cf.created_at AT TIME ZONE 'UTC') AS rev_date,
           cf.amount_usdc::FLOAT AS rev_usd
    FROM card_annual_fees cf
    WHERE cf.status = 'paid'
      AND cf.user_id::TEXT IN (SELECT user_id FROM cohort)
)
SELECT
    rev_date,
    ROUND(SUM(CASE WHEN src = 'conversion' THEN rev_usd ELSE 0 END)::NUMERIC, 4)
        AS daily_rev_conversion_usd,
    ROUND(SUM(CASE WHEN src = 'card_fee'   THEN rev_usd ELSE 0 END)::NUMERIC, 4)
        AS daily_rev_card_fees_usd,
    ROUND(SUM(CASE WHEN src = 'billing'    THEN rev_usd ELSE 0 END)::NUMERIC, 4)
        AS daily_rev_billing_usd,
    ROUND(SUM(CASE WHEN src = 'swap'       THEN rev_usd ELSE 0 END)::NUMERIC, 4)
        AS daily_rev_swap_usd,
    ROUND(-SUM(CASE WHEN src = 'cashback'  THEN rev_usd ELSE 0 END)::NUMERIC, 4)
        AS daily_cost_cashback_usd,
    ROUND(-SUM(CASE WHEN src = 'rev_share' THEN rev_usd ELSE 0 END)::NUMERIC, 4)
        AS daily_cost_rev_share_usd,
    ROUND(SUM(rev_usd)::NUMERIC, 4) AS daily_rev_usd
FROM (
    SELECT rev_date, rev_usd, 'conversion' AS src FROM conversion_rev
    UNION ALL
    SELECT rev_date, rev_usd, 'card_fee'   AS src FROM card_fee_rev
    UNION ALL
    SELECT DATE(bc.created_at AT TIME ZONE 'UTC') AS rev_date,
           bc.amount::FLOAT / 1000000.0            AS rev_usd,
           'billing'                               AS src
    FROM billing_charges bc
    WHERE bc.status = 'settled'
      AND bc.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(st."timestamp" AT TIME ZONE 'UTC')                     AS rev_date,
           CASE
               WHEN st.input_mint  = :usdc_mint
               THEN st.input_amount::FLOAT  / 1000000.0
               WHEN st.output_mint = :usdc_mint
               THEN st.output_amount::FLOAT / 1000000.0
               ELSE 0
           END * st.platform_fee_bps::FLOAT / 10000.0                  AS rev_usd,
           'swap'                                                        AS src
    FROM swap_transactions st
    WHERE st.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(cr.created_at AT TIME ZONE 'UTC') AS rev_date,
           -cr.reward_usd_value::FLOAT             AS rev_usd,
           'cashback'                              AS src
    FROM cashback_rewards cr
    WHERE cr.status = 'completed'
      AND cr.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(rsr.created_at AT TIME ZONE 'UTC') AS rev_date,
           -rsr.reward_usd_value::FLOAT             AS rev_usd,
           'rev_share'                              AS src
    FROM revenue_share_rewards rsr
    WHERE rsr.status = 'completed'
      AND rsr.source_user_id::TEXT IN (SELECT user_id FROM cohort)
) all_rev
GROUP BY rev_date
ORDER BY rev_date
"""


_COHORT_CARD_TXNS_SQL = """
SELECT
    DATE(ct.authorized_at AT TIME ZONE 'UTC') AS txn_date,
    COUNT(*)                                   AS txn_count
FROM card_transactions ct
WHERE ct.user_id::TEXT IN (
    SELECT id::TEXT FROM users
    WHERE created_at >= :cohort_start
      AND created_at <  :cohort_end
      AND (:referral_code = '' OR id IN (
          SELECT ur.user_id FROM user_registrations ur
          JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
          WHERE rc.code = :referral_code
      ))
)
  AND ct.status IN ('completed', 'pending')
  AND ct.transaction_type = 'spend'
  AND ct.authorized_at >= :cohort_start
GROUP BY 1
ORDER BY 1
"""

_COHORT_CONVERSIONS_SQL = """
SELECT
    DATE(cq.created_at AT TIME ZONE 'UTC') AS conv_date,
    COUNT(*)                                AS conv_count
FROM conversion_quotes cq
WHERE cq.used = TRUE
  AND cq.user_id::TEXT IN (
      SELECT id::TEXT FROM users
      WHERE created_at >= :cohort_start
        AND created_at <  :cohort_end
        AND (:referral_code = '' OR id IN (
            SELECT ur.user_id FROM user_registrations ur
            JOIN referral_codes rc ON rc.id = ur.attributed_referral_code_id
            WHERE rc.code = :referral_code
        ))
  )
  AND cq.created_at >= :cohort_start
GROUP BY 1
ORDER BY 1
"""


# ------------------------------------------------------------------
# Public loader
# ------------------------------------------------------------------


def load_ad_spend(csv_path: str | Path) -> pd.DataFrame:
    """Load per-platform daily ad spend from a Rain card expense CSV.

    Includes Meta (FACEBK) and Google Ads rows.

    Args:
        csv_path: Path to the Rain CSV export.

    Returns:
        DataFrame with columns ``date`` (date), ``platform`` (str),
        and ``daily_spend_usd`` (float), one row per (date, platform).
        Use :func:`aggregate_spend` to collapse to a single daily total.
    """
    df = pd.read_csv(csv_path, parse_dates=["date"])
    meta_mask = df["merchantName"].str.startswith("FACEBK", na=False)
    google_mask = df["merchantName"].str.contains("GOOGLE ADS", case=False, na=False)

    parts: list[pd.DataFrame] = []
    for mask, plat in [(meta_mask, "meta"), (google_mask, "google")]:
        sub = df[mask].copy()
        if not sub.empty:
            sub["platform"] = plat
            parts.append(sub)

    if not parts:
        return pd.DataFrame(columns=["date", "platform", "daily_spend_usd"])

    combined = pd.concat(parts, ignore_index=True)
    combined["date"] = combined["date"].dt.date
    combined["amount_abs"] = combined["amount"].abs()
    daily = combined.groupby(["date", "platform"])["amount_abs"].sum().reset_index()
    daily.columns = ["date", "platform", "daily_spend_usd"]
    return daily.sort_values(["date", "platform"]).reset_index(drop=True)


def load_ad_spend_from_db(db_url: str) -> pd.DataFrame | None:
    """Query meta_ads_spend and return per-platform daily spend.

    Args:
        db_url: PostgreSQL connection string (read-only is sufficient).

    Returns:
        DataFrame with columns ``date`` (datetime64), ``platform`` (str),
        and ``daily_spend_usd`` (float), or ``None`` on failure/empty.
    """
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url)
        sql = text(
            "SELECT date, platform, SUM(amount_usd) AS daily_spend_usd"
            " FROM meta_ads_spend GROUP BY date, platform ORDER BY date, platform"
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn)
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df["daily_spend_usd"] = df["daily_spend_usd"].astype(float)
        return df.reset_index(drop=True)
    except Exception:
        return None


def aggregate_spend(spend_df: pd.DataFrame, platform: str | None = None) -> pd.DataFrame:
    """Collapse per-platform spend to the ``date, daily_spend_usd`` shape CampaignAnalyzer expects.

    Args:
        spend_df: Output of :func:`load_ad_spend` or :func:`load_ad_spend_from_db`.
        platform: If given, filter to that platform before summing.
            Pass ``None`` to sum all platforms.

    Returns:
        DataFrame with columns ``date`` and ``daily_spend_usd``, sorted by date.
    """
    if spend_df.empty:
        return pd.DataFrame(columns=["date", "daily_spend_usd"])
    df = spend_df if platform is None else spend_df[spend_df["platform"] == platform]
    if df.empty:
        return pd.DataFrame(columns=["date", "daily_spend_usd"])
    result = df.groupby("date")["daily_spend_usd"].sum().reset_index()
    return result.sort_values("date").reset_index(drop=True)


# ------------------------------------------------------------------
# Campaign window detection
# ------------------------------------------------------------------


def _detect_campaigns(
    spend: pd.DataFrame,
    gap_days: int = 7,
) -> list[dict]:
    """Split spend into contiguous campaign windows separated by gaps.

    Args:
        spend: Output of :func:`load_ad_spend`.
        gap_days: Minimum number of zero-spend days to consider a new campaign.

    Returns:
        List of dicts: ``{campaign_id, start, end, total_spend_usd}``.
    """
    if spend.empty:
        return []

    dates = pd.to_datetime(spend["date"]).sort_values().reset_index(drop=True)
    windows: list[dict] = []
    w_start = dates.iloc[0]
    w_end = dates.iloc[0]

    for i in range(1, len(dates)):
        gap = (dates.iloc[i] - w_end).days
        if gap > gap_days:
            windows.append({"start": w_start, "end": w_end})
            w_start = dates.iloc[i]
        w_end = dates.iloc[i]
    windows.append({"start": w_start, "end": w_end})

    result = []
    for idx, w in enumerate(windows, start=1):
        mask = (pd.to_datetime(spend["date"]) >= w["start"]) & (
            pd.to_datetime(spend["date"]) <= w["end"]
        )
        total = float(spend.loc[mask, "daily_spend_usd"].sum())
        result.append(
            {
                "campaign_id": f"campaign_{idx}",
                "start": w["start"].date(),
                "end": w["end"].date(),
                "total_spend_usd": total,
            }
        )
    return result


# ------------------------------------------------------------------
# COGS helpers
# ------------------------------------------------------------------


def _cost_per_txn_from_invoices(
    invoice_history: list[tuple[str, float | Decimal, int]],
) -> dict[str, float]:
    """Compute cost-per-transaction for each invoice period.

    Args:
        invoice_history: List of ``(period, invoice_total_usd, txn_count)``
            tuples, e.g.
            ``[("2026-02", 6693.58, 6885), ("2026-03", 7857.40, 6990)]``.

    Returns:
        Dict mapping period string (``"YYYY-MM"``) to USD cost per transaction.
        Periods with zero transactions are skipped with a warning.
    """
    result: dict[str, float] = {}
    for period, total_usd, txn_count in invoice_history:
        if txn_count <= 0:
            logger.warning("Period %s has zero transactions — skipping cost-per-txn", period)
            continue
        result[period] = float(total_usd) / txn_count
        logger.debug(
            "Period %s: $%.2f / %d txns = $%.4f/txn",
            period,
            float(total_usd),
            txn_count,
            result[period],
        )
    return result


def _cogs_for_cohort_txns(
    txn_df: pd.DataFrame,
    cost_per_txn: dict[str, float],
) -> pd.Series:
    """Map daily cohort transaction counts to daily card-program COGS.

    Each row's COGS = ``txn_count × cost_per_txn[YYYY-MM]``.  If the period
    has no entry in ``cost_per_txn``, the most recent prior rate is used as a
    fallback (or the earliest available rate if no prior period exists).

    Args:
        txn_df: DataFrame with columns ``txn_date`` (datetime64) and
            ``txn_count`` (int).
        cost_per_txn: Output of :func:`_cost_per_txn_from_invoices`.

    Returns:
        Series of float64 daily COGS values, indexed same as ``txn_df``.
        Returns all-zero Series when ``cost_per_txn`` is empty.
    """
    if not cost_per_txn:
        logger.warning("cost_per_txn is empty — card COGS treated as zero")
        return pd.Series(0.0, index=txn_df.index, dtype="float64")

    sorted_periods = sorted(cost_per_txn.keys())

    def _rate_for_dt(dt: pd.Timestamp) -> float:
        period = dt.strftime("%Y-%m")
        if period in cost_per_txn:
            return cost_per_txn[period]
        earlier = [p for p in sorted_periods if p <= period]
        return cost_per_txn[earlier[-1]] if earlier else cost_per_txn[sorted_periods[0]]

    rates = pd.to_datetime(txn_df["txn_date"]).apply(_rate_for_dt)
    return (txn_df["txn_count"].astype("float64") * rates).astype("float64")


# ------------------------------------------------------------------
# Core analyser
# ------------------------------------------------------------------


class CampaignAnalyzer:
    """Compute ROI / CAC for ad campaigns using Rain CSV spend data + DB revenue.

    Args:
        spend_df: Daily ad spend DataFrame from :func:`load_ad_spend`.
        db_url: SQLAlchemy DB URL (overrides ``READONLY_DATABASE_URL`` env var).
        baseline_window_days: Days before campaign used to estimate organic
            baseline signup rate.  Defaults to 7.
        gap_days: Minimum zero-spend gap (days) to split into separate campaigns.
        _engine: Inject a pre-built SQLAlchemy engine (for testing).
    """

    def __init__(
        self,
        spend_df: pd.DataFrame,
        db_url: str | None = None,
        baseline_window_days: int = 7,
        gap_days: int = 7,
        _engine: Engine | None = None,
    ) -> None:
        from dotenv import load_dotenv

        load_dotenv()
        self._spend = spend_df.copy()
        self._campaigns = _detect_campaigns(spend_df, gap_days=gap_days)
        self._baseline_days = baseline_window_days
        self._db_url = db_url or os.environ.get("READONLY_DATABASE_URL", "")
        self._engine = _engine

    def _get_engine(self) -> Engine:
        if self._engine is not None:
            return self._engine
        from sqlalchemy import create_engine

        return create_engine(self._db_url)

    def _cache_path(self, sql: str, params: dict) -> Path:
        key = hashlib.sha256((sql + str(sorted(params.items()))).encode()).hexdigest()[:16]
        _DB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return _DB_CACHE_DIR / f"campaign_{key}.parquet"

    def _run(self, sql: str, params: dict) -> pd.DataFrame:
        # Unlike ClientQueries._run(), no _scale_brl() is needed here: all SQL
        # in this module converts BRL to USD inline (÷100 then ÷fx.rate), so
        # output columns are already USD-denominated and no *_brl column reaches Python.
        path = self._cache_path(sql, params)
        if path.exists():
            return pd.read_parquet(path)
        from sqlalchemy import text

        engine = self._get_engine()
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params)
        df.to_parquet(path, index=False)
        return df

    def _daily_signups(self, start: str, end: str) -> pd.DataFrame:
        """Return daily signup counts for the given window (end is exclusive)."""
        return self._run(_DAILY_SIGNUPS_SQL, {"start": start, "end": end})

    def _cohort_kyc(
        self, cohort_start: str, cohort_end: str, referral_code: str = ""
    ) -> pd.DataFrame:
        """Return daily completed-KYC counts for the campaign cohort.

        Counts only GREEN-approved verifications from ``kyc_verifications``,
        scoped to cohort users by the same referral-code filter used by revenue
        and card-COGS queries.  Grouped by ``completed_at`` date, not signup date.
        """
        return self._run(
            _COHORT_KYC_SQL,
            {
                "cohort_start": cohort_start,
                "cohort_end": cohort_end,
                "referral_code": referral_code,
            },
        )

    def _cohort_revenue(self, cohort_start: str, cohort_end: str, referral_code: str = "") -> dict:
        """Return aggregate revenue metrics for the signup cohort (end exclusive)."""
        df = self._run(
            _COHORT_REVENUE_SQL,
            {
                "cohort_start": cohort_start,
                "cohort_end": cohort_end,
                "referral_code": referral_code,
                "usdc_mint": _USDC_MINT,
            },
        )
        if df.empty:
            return {
                "cohort_users": 0,
                "transacting_users": 0,
                "total_revenue_usd": 0.0,
                "onramp_rev_usd": 0.0,
                "card_fee_usd": 0.0,
                "billing_usd": 0.0,
            }
        row = df.iloc[0]
        result = {k: float(row[k]) if row[k] is not None else 0.0 for k in df.columns}
        if not INCLUDE_SWAP_FEES:
            result["total_revenue_usd"] -= result.get("swap_fee_usd", 0.0)
            result["swap_fee_usd"] = 0.0
        return result

    def _baseline_rate(self, campaign_start) -> float:
        """Avg daily signups in the ``baseline_window_days`` before campaign_start."""
        end_dt = pd.Timestamp(campaign_start)
        start_dt = end_dt - pd.Timedelta(days=self._baseline_days)
        df = self._daily_signups(
            str(start_dt.date()),
            str(end_dt.date()),
        )
        if df.empty or df["new_signups"].sum() == 0:
            return 0.0
        return float(df["new_signups"].mean())

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    @property
    def campaigns(self) -> list[dict]:
        """Detected campaign windows with spend totals."""
        return self._campaigns

    def roi_summary(self) -> pd.DataFrame:
        """Compute per-campaign ROI / CAC metrics.

        Returns:
            DataFrame with one row per campaign and columns:
            ``campaign_id``, ``start``, ``end``, ``duration_days``,
            ``total_spend_usd``, ``cohort_users``, ``transacting_users``,
            ``transacting_rate``, ``baseline_rate_per_day``,
            ``incremental_users_est``, ``total_revenue_usd``,
            ``roas``, ``cac_full``, ``cac_incremental``,
            ``avg_rev_per_transacting_user``.

        Notes:
            ``incremental_users_est`` is estimated as
            ``max(0, cohort_users − baseline_rate × duration_days)``.
            This is a lower-bound on Meta-attributed users; the actual
            number is unknown without UTM tracking in the DB.
        """
        rows = []
        for c in self._campaigns:
            start_str = str(c["start"])
            # end is inclusive → exclusive for SQL
            end_dt = pd.Timestamp(c["end"]) + pd.Timedelta(days=1)
            end_str = str(end_dt.date())

            duration = (pd.Timestamp(c["end"]) - pd.Timestamp(c["start"])).days + 1
            baseline = self._baseline_rate(c["start"])
            expected_organic = baseline * duration
            rev = self._cohort_revenue(start_str, end_str)

            cohort_users = int(rev["cohort_users"])
            transacting = int(rev["transacting_users"])
            total_rev = float(rev["total_revenue_usd"])
            spend = c["total_spend_usd"]

            incremental = max(0.0, cohort_users - expected_organic)
            roas = total_rev / spend if spend > 0 else np.nan
            cac_full = spend / transacting if transacting > 0 else np.nan
            cac_incr = spend / incremental if incremental > 0 else np.nan
            avg_rev_tx = total_rev / transacting if transacting > 0 else np.nan

            rows.append(
                {
                    "campaign_id": c["campaign_id"],
                    "start": c["start"],
                    "end": c["end"],
                    "duration_days": duration,
                    "total_spend_usd": round(spend, 2),
                    "cohort_users": cohort_users,
                    "transacting_users": transacting,
                    "transacting_rate": round(transacting / cohort_users, 4)
                    if cohort_users > 0
                    else np.nan,
                    "baseline_rate_per_day": round(baseline, 1),
                    "incremental_users_est": round(incremental, 0),
                    "total_revenue_usd": round(total_rev, 2),
                    "roas": round(roas, 4) if not np.isnan(roas) else np.nan,
                    "cac_full": round(cac_full, 2) if not np.isnan(cac_full) else np.nan,
                    "cac_incremental": round(cac_incr, 2) if not np.isnan(cac_incr) else np.nan,
                    "avg_rev_per_transacting_user": round(avg_rev_tx, 2)
                    if not np.isnan(avg_rev_tx)
                    else np.nan,
                }
            )
        return pd.DataFrame(rows)

    def cohort_kyc_count(self, campaign_id: str, referral_code: str = "") -> int:
        """Return count of cohort users who have completed KYC (kyc_level >= 1).

        Args:
            campaign_id: Campaign identifier from :attr:`campaigns`.
            referral_code: Optional referral-code filter (empty = all sources).

        Returns:
            Count of KYC-verified users in the cohort, or 0 if unavailable.
        """
        campaign = next((c for c in self._campaigns if c["campaign_id"] == campaign_id), None)
        if campaign is None:
            return 0
        start_str = str(campaign["start"])
        end_str = str((pd.Timestamp(campaign["end"]) + pd.Timedelta(days=1)).date())
        try:
            df = self._run(
                _COHORT_KYC_LEVEL_SQL,
                {
                    "cohort_start": start_str,
                    "cohort_end": end_str,
                    "referral_code": referral_code,
                },
            )
            return int(df["kyc_count"].iloc[0]) if not df.empty else 0
        except Exception:
            logger.warning("Could not fetch KYC count for cohort funnel", exc_info=True)
            return 0

    def daily_context(self, context_days_before: int = 14) -> pd.DataFrame:
        """Daily signups and ad spend for all campaign windows + context window.

        Args:
            context_days_before: Days of pre-campaign data to include for
                baseline comparison.

        Returns:
            DataFrame with columns: ``date``, ``new_signups``,
            ``daily_spend_usd``, ``is_campaign``, ``campaign_id``.
            Rows are sorted by date.
        """
        if not self._campaigns:
            return pd.DataFrame(
                columns=["date", "new_signups", "daily_spend_usd", "is_campaign", "campaign_id"]
            )

        from datetime import date as _date

        first_start = pd.Timestamp(self._campaigns[0]["start"]) - pd.Timedelta(
            days=context_days_before
        )
        # Extend to today so signups after the last ad spend date are included.
        last_end = max(
            pd.Timestamp(self._campaigns[-1]["end"]) + pd.Timedelta(days=1),
            pd.Timestamp(_date.today()) + pd.Timedelta(days=1),
        )

        signups = self._daily_signups(str(first_start.date()), str(last_end.date()))
        signups["date"] = pd.to_datetime(signups["signup_date"]).dt.date
        signups = signups.drop(columns=["signup_date"])

        spend = self._spend.copy()
        spend["date"] = pd.to_datetime(spend["date"]).dt.date

        merged = signups.merge(spend, on="date", how="left")
        merged["daily_spend_usd"] = merged["daily_spend_usd"].fillna(0.0)

        # Tag campaign membership
        merged["is_campaign"] = False
        merged["campaign_id"] = ""
        for c in self._campaigns:
            mask = (pd.to_datetime(merged["date"]) >= pd.Timestamp(c["start"])) & (
                pd.to_datetime(merged["date"]) <= pd.Timestamp(c["end"])
            )
            merged.loc[mask, "is_campaign"] = True
            merged.loc[mask, "campaign_id"] = c["campaign_id"]

        return merged.sort_values("date").reset_index(drop=True)

    def referral_code_options(self) -> list[str]:
        """Return referral codes that have attributed users in the DB.

        Returns:
            Sorted list of code strings (e.g. ``["GOOGLE", "INSTAGRAM"]``).
            Returns an empty list when the DB is unavailable or the table is empty.
        """
        try:
            df = self._run(_REFERRAL_CODES_SQL, {})
            return df["code"].dropna().tolist() if not df.empty else []
        except Exception:
            logger.warning("Could not fetch referral codes", exc_info=True)
            return []

    def cumulative_revenue(
        self, campaign_id: str | None = None, referral_code: str = ""
    ) -> pd.DataFrame:
        """Daily and cumulative revenue generated by users acquired during a campaign.

        Revenue is tracked from the campaign start through today, covering all
        revenue sources (onramp, card fees, billing, swaps, payouts) minus costs
        (cashback, revenue share).

        Args:
            campaign_id: Which campaign's cohort to track. Defaults to the most
                recent campaign.
            referral_code: When non-empty, restricts the cohort to users whose
                signup was attributed to this referral code (e.g. ``"GOOGLE"``).

        Returns:
            DataFrame with columns: ``date`` (datetime),
            ``daily_rev_conversion_usd``, ``daily_rev_card_fees_usd``,
            ``daily_rev_usd``, ``cum_rev_usd``.
            Sorted by date; days with no revenue are filled with 0.
        """
        _rev_cols = [
            "date",
            "daily_rev_conversion_usd",
            "daily_rev_card_fees_usd",
            "daily_rev_billing_usd",
            "daily_rev_swap_usd",
            "daily_cost_cashback_usd",
            "daily_cost_rev_share_usd",
            "daily_rev_usd",
            "cum_rev_usd",
        ]
        if not self._campaigns:
            return pd.DataFrame(columns=_rev_cols)

        if campaign_id is not None:
            matches = [c for c in self._campaigns if c["campaign_id"] == campaign_id]
            c = matches[-1] if matches else self._campaigns[-1]
        else:
            c = self._campaigns[-1]

        cohort_start = str(c["start"])
        cohort_end = str((pd.Timestamp(c["end"]) + pd.Timedelta(days=1)).date())

        rev_df = self._run(
            _DAILY_COHORT_REVENUE_SQL,
            {
                "cohort_start": cohort_start,
                "cohort_end": cohort_end,
                "referral_code": referral_code,
                "usdc_mint": _USDC_MINT,
            },
        )
        if rev_df.empty:
            return pd.DataFrame(columns=_rev_cols)

        today = pd.Timestamp.today().normalize()
        all_dates = pd.DataFrame({"date": pd.date_range(start=cohort_start, end=today, freq="D")})
        rev_df["date"] = pd.to_datetime(rev_df["rev_date"])
        rev_df = rev_df.drop(columns=["rev_date"])
        result = all_dates.merge(rev_df, on="date", how="left")
        for col in (
            "daily_rev_conversion_usd",
            "daily_rev_card_fees_usd",
            "daily_rev_billing_usd",
            "daily_rev_swap_usd",
            "daily_cost_cashback_usd",
            "daily_cost_rev_share_usd",
            "daily_rev_usd",
        ):
            result[col] = result[col].fillna(0.0).astype("float64")
        if not INCLUDE_SWAP_FEES:
            result["daily_rev_usd"] -= result["daily_rev_swap_usd"]
            result["daily_rev_swap_usd"] = 0.0
        result["cum_rev_usd"] = result["daily_rev_usd"].cumsum()
        return result

    def _cohort_card_txns(
        self, cohort_start: str, cohort_end: str, referral_code: str = ""
    ) -> pd.DataFrame:
        """Return daily card transaction counts for cohort users.

        Args:
            cohort_start: Inclusive start (ISO date string).
            cohort_end: Exclusive end (ISO date string).
            referral_code: Optional referral code to filter cohort users.

        Returns:
            DataFrame with columns ``txn_date`` (datetime64) and
            ``txn_count`` (int).  Empty when no transactions exist.
        """
        df = self._run(
            _COHORT_CARD_TXNS_SQL,
            {
                "cohort_start": cohort_start,
                "cohort_end": cohort_end,
                "referral_code": referral_code,
            },
        )
        if df.empty:
            return pd.DataFrame(columns=["txn_date", "txn_count"])
        df["txn_date"] = pd.to_datetime(df["txn_date"])
        df["txn_count"] = df["txn_count"].astype("int64")
        return df

    def _cohort_conversions(
        self, cohort_start: str, cohort_end: str, referral_code: str = ""
    ) -> pd.DataFrame:
        """Return daily BRL↔USDC conversion counts for cohort users.

        Args:
            cohort_start: Inclusive start (ISO date string).
            cohort_end: Exclusive end (ISO date string).
            referral_code: Optional referral code to filter cohort users.

        Returns:
            DataFrame with columns ``conv_date`` (datetime64) and
            ``conv_count`` (int).  Empty when no conversions exist.
        """
        df = self._run(
            _COHORT_CONVERSIONS_SQL,
            {
                "cohort_start": cohort_start,
                "cohort_end": cohort_end,
                "referral_code": referral_code,
            },
        )
        if df.empty:
            return pd.DataFrame(columns=["conv_date", "conv_count"])
        df["conv_date"] = pd.to_datetime(df["conv_date"])
        df["conv_count"] = df["conv_count"].astype("int64")
        return df

    def cumulative_profit(
        self,
        campaign_id: str | None = None,
        invoice_history: list[tuple[str, float | Decimal, int]] | None = None,
        referral_code: str = "",
    ) -> pd.DataFrame:
        """Daily and cumulative profit for a campaign cohort.

        Card-program COGS is computed as cohort daily transaction count
        multiplied by the cost-per-transaction derived from Rain invoices.
        Revenue includes both conversion (onramp/offramp) and card fees.

        Args:
            campaign_id: Which campaign's cohort to track. Defaults to the
                most recent campaign.
            invoice_history: List of
                ``(period, invoice_total_usd, txn_count)`` tuples, e.g.
                ``[("2026-02", 6693.58, 6885), ("2026-03", 7857.40, 6990)]``.
                Used to compute per-transaction cost.  When ``None`` or empty,
                COGS is treated as zero and a warning is logged.
            referral_code: When non-empty, restricts the cohort to users whose
                signup was attributed to this referral code (e.g. ``"GOOGLE"``).

        Returns:
            DataFrame with columns:
            ``date``, ``daily_rev_conversion_usd``,
            ``daily_rev_card_fees_usd``, ``daily_rev_total_usd``,
            ``daily_card_cogs_usd``, ``daily_ad_spend_usd``,
            ``daily_kyc_cost_usd``, ``daily_profit_usd``,
            ``cum_rev_usd``, ``cum_card_cogs_usd``, ``cum_kyc_cost_usd``,
            ``cum_profit_usd`` (operational profit: all revenue minus all costs).
            Sorted by date; missing days are zero-filled.
        """
        _empty_cols = [
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
            "daily_kyc_cost_usd",
            "daily_contribution_margin_usd",
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
            "cum_kyc_cost_usd",
            "cum_contribution_margin_usd",
            "cum_profit_usd",
            "cum_txn_count",
            "cum_conversion_count",
        ]
        empty = pd.DataFrame(columns=_empty_cols)

        rev_df = self.cumulative_revenue(campaign_id, referral_code=referral_code)
        if rev_df.empty:
            return empty

        if not self._campaigns:
            return empty
        if campaign_id is not None:
            matches = [c for c in self._campaigns if c["campaign_id"] == campaign_id]
            c = matches[-1] if matches else self._campaigns[-1]
        else:
            c = self._campaigns[-1]

        cohort_start = str(c["start"])
        cohort_end = str((pd.Timestamp(c["end"]) + pd.Timedelta(days=1)).date())

        cost_per_txn = _cost_per_txn_from_invoices(invoice_history or [])
        txn_df = self._cohort_card_txns(cohort_start, cohort_end, referral_code=referral_code)
        conv_df = self._cohort_conversions(cohort_start, cohort_end, referral_code=referral_code)

        today = pd.Timestamp.today().normalize()
        all_dates = pd.DataFrame({"date": pd.date_range(start=cohort_start, end=today, freq="D")})

        txn_full = all_dates.rename(columns={"date": "txn_date"}).merge(
            txn_df, on="txn_date", how="left"
        )
        txn_full["txn_count"] = txn_full["txn_count"].fillna(0).astype("int64")

        conv_full = all_dates.rename(columns={"date": "conv_date"}).merge(
            conv_df, on="conv_date", how="left"
        )
        conv_full["conv_count"] = conv_full["conv_count"].fillna(0).astype("int64")

        kyc_df = self._cohort_kyc(cohort_start, cohort_end, referral_code=referral_code)
        if kyc_df.empty:
            kyc_df = pd.DataFrame(columns=["kyc_date", "kyc_count"])
        else:
            kyc_df["kyc_date"] = pd.to_datetime(kyc_df["kyc_date"])
        kyc_full = all_dates.rename(columns={"date": "kyc_date"}).merge(
            kyc_df, on="kyc_date", how="left"
        )
        kyc_full["kyc_count"] = kyc_full["kyc_count"].fillna(0).astype("int64")

        cogs_series = _cogs_for_cohort_txns(txn_full, cost_per_txn)

        spend = self._spend.copy()
        spend["date"] = pd.to_datetime(spend["date"]).dt.normalize()
        spend_indexed = spend.set_index("date")["daily_spend_usd"]

        result = rev_df[
            [
                "date",
                "daily_rev_conversion_usd",
                "daily_rev_card_fees_usd",
                "daily_rev_billing_usd",
                "daily_rev_swap_usd",
                "daily_cost_cashback_usd",
                "daily_cost_rev_share_usd",
                "daily_rev_usd",
            ]
        ].copy()
        result = result.rename(columns={"daily_rev_usd": "daily_rev_total_usd"})

        result["daily_card_cogs_usd"] = cogs_series.values
        result["daily_ad_spend_usd"] = (
            result["date"].map(spend_indexed).fillna(0.0).astype("float64")
        )
        result["daily_kyc_cost_usd"] = (
            kyc_full["kyc_count"].values.astype("float64") * _KYC_COST_USD
        )
        result["daily_profit_usd"] = (
            result["daily_rev_total_usd"]
            - result["daily_card_cogs_usd"]
            - result["daily_kyc_cost_usd"]
        )
        result["daily_contribution_margin_usd"] = (
            result["daily_profit_usd"] - result["daily_ad_spend_usd"]
        )
        result["daily_txn_count"] = txn_full["txn_count"].values
        result["daily_conversion_count"] = conv_full["conv_count"].values
        result["cum_rev_conversion_usd"] = result["daily_rev_conversion_usd"].cumsum()
        result["cum_rev_card_fees_usd"] = result["daily_rev_card_fees_usd"].cumsum()
        result["cum_rev_billing_usd"] = result["daily_rev_billing_usd"].cumsum()
        result["cum_rev_swap_usd"] = result["daily_rev_swap_usd"].cumsum()
        result["cum_cost_cashback_usd"] = result["daily_cost_cashback_usd"].cumsum()
        result["cum_cost_rev_share_usd"] = result["daily_cost_rev_share_usd"].cumsum()
        result["cum_rev_usd"] = result["daily_rev_total_usd"].cumsum()
        result["cum_card_cogs_usd"] = result["daily_card_cogs_usd"].cumsum()
        result["cum_kyc_cost_usd"] = result["daily_kyc_cost_usd"].cumsum()
        result["cum_contribution_margin_usd"] = result["daily_contribution_margin_usd"].cumsum()
        result["cum_profit_usd"] = result["daily_profit_usd"].cumsum()
        result["cum_txn_count"] = result["daily_txn_count"].cumsum()
        result["cum_conversion_count"] = result["daily_conversion_count"].cumsum()
        return result.reset_index(drop=True)
