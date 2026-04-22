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
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_DB_CACHE_DIR = Path(os.environ.get("DB_CACHE_DIR", "data/processed/db_cache"))

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

_COHORT_REVENUE_SQL = """
WITH cohort AS (
    SELECT id::TEXT AS user_id
    FROM users
    WHERE created_at >= :cohort_start
      AND created_at <  :cohort_end
),
onramp_rev AS (
    SELECT user_id::TEXT,
           SUM(fee_amount_brl + spread_revenue_brl) / 100.0 AS onramp_brl
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
           SUM(input_amount::FLOAT / 1000000.0
               * platform_fee_bps::FLOAT / 10000.0) AS swap_fee_usd
    FROM swap_transactions
    GROUP BY user_id::TEXT
),
payout_rev AS (
    SELECT user_id::TEXT, SUM(unblockpay_fee::FLOAT) AS payout_fee_usd
    FROM unblockpay_payouts
    WHERE status = 'completed'
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
    ROUND(SUM(COALESCE(or_.onramp_brl,  0) / fx.rate)::NUMERIC, 4)          AS onramp_rev_usd,
    ROUND(SUM(COALESCE(cf.card_fee_usd, 0))::NUMERIC, 4)                    AS card_fee_usd,
    ROUND(SUM(COALESCE(br.billing_usd,  0))::NUMERIC, 4)                    AS billing_usd,
    ROUND(SUM(COALESCE(sw.swap_fee_usd, 0))::NUMERIC, 4)                    AS swap_fee_usd,
    ROUND(SUM(COALESCE(po.payout_fee_usd, 0))::NUMERIC, 4)                  AS payout_fee_usd,
    ROUND(SUM(COALESCE(cb.cashback_usd,  0))::NUMERIC, 4)                   AS cashback_usd,
    ROUND(SUM(COALESCE(rs.revenue_share_usd, 0))::NUMERIC, 4)               AS revenue_share_usd,
    ROUND(SUM(
        COALESCE(or_.onramp_brl,  0) / fx.rate
        + COALESCE(cf.card_fee_usd, 0)
        + COALESCE(br.billing_usd,  0)
        + COALESCE(sw.swap_fee_usd, 0)
        + COALESCE(po.payout_fee_usd, 0)
        - COALESCE(cb.cashback_usd,  0)
        - COALESCE(rs.revenue_share_usd, 0)
    )::NUMERIC, 4)                                                           AS total_revenue_usd
FROM cohort c
CROSS JOIN fx
LEFT JOIN onramp_rev    or_ ON or_.user_id = c.user_id
LEFT JOIN card_fee_rev  cf  ON cf.user_id  = c.user_id
LEFT JOIN billing_rev   br  ON br.user_id  = c.user_id
LEFT JOIN swap_rev      sw  ON sw.user_id  = c.user_id
LEFT JOIN payout_rev    po  ON po.user_id  = c.user_id
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
),
fx AS (
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY effective_rate) AS rate
    FROM conversion_quotes
    WHERE used = TRUE AND direction = 'brl_to_usdc'
),
rev_rows AS (
    SELECT DATE(cq.created_at AT TIME ZONE 'UTC') AS rev_date,
           (cq.fee_amount_brl + cq.spread_revenue_brl)::FLOAT / 100.0
               / NULLIF(fx.rate, 0) AS rev_usd
    FROM conversion_quotes cq, fx
    WHERE cq.used = TRUE
      AND cq.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(cf.created_at AT TIME ZONE 'UTC'),
           cf.amount_usdc::FLOAT
    FROM card_annual_fees cf
    WHERE cf.status = 'paid'
      AND cf.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(bc.created_at AT TIME ZONE 'UTC'),
           bc.amount::FLOAT / 1000000.0
    FROM billing_charges bc
    WHERE bc.status = 'settled'
      AND bc.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(st."timestamp" AT TIME ZONE 'UTC'),
           st.input_amount::FLOAT / 1000000.0
               * st.platform_fee_bps::FLOAT / 10000.0
    FROM swap_transactions st
    WHERE st.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(pp.created_at AT TIME ZONE 'UTC'),
           pp.unblockpay_fee::FLOAT
    FROM unblockpay_payouts pp
    WHERE pp.status = 'completed'
      AND pp.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(cr.created_at AT TIME ZONE 'UTC'),
           -cr.reward_usd_value::FLOAT
    FROM cashback_rewards cr
    WHERE cr.status = 'completed'
      AND cr.user_id::TEXT IN (SELECT user_id FROM cohort)
    UNION ALL
    SELECT DATE(rsr.created_at AT TIME ZONE 'UTC'),
           -rsr.reward_usd_value::FLOAT
    FROM revenue_share_rewards rsr
    WHERE rsr.status = 'completed'
      AND rsr.source_user_id::TEXT IN (SELECT user_id FROM cohort)
)
SELECT rev_date, ROUND(SUM(rev_usd)::NUMERIC, 4) AS daily_rev_usd
FROM rev_rows
GROUP BY rev_date
ORDER BY rev_date
"""


# ------------------------------------------------------------------
# Public loader
# ------------------------------------------------------------------


def load_ad_spend(csv_path: str | Path, merchant_prefix: str = "FACEBK") -> pd.DataFrame:
    """Load and aggregate daily ad spend from a Rain card expense CSV.

    Args:
        csv_path: Path to the Rain CSV export
            (e.g. ``data/nbs_corp_card/rain-transactions-export-2026-04-20.csv``).
        merchant_prefix: Merchant name prefix to filter on (case-sensitive).
            Defaults to ``"FACEBK"`` (Meta / Facebook Ads).

    Returns:
        DataFrame with columns ``date`` (date) and ``daily_spend_usd`` (float),
        sorted by date.  Amount signs are normalised so spend is positive.
    """
    df = pd.read_csv(csv_path, parse_dates=["date"])
    mask = df["merchantName"].str.startswith(merchant_prefix, na=False)
    fb = df[mask].copy()
    if fb.empty:
        return pd.DataFrame(columns=["date", "daily_spend_usd"])
    fb["date"] = fb["date"].dt.date
    fb["amount_abs"] = fb["amount"].abs()
    daily = fb.groupby("date")["amount_abs"].sum().reset_index()
    daily.columns = ["date", "daily_spend_usd"]
    return daily.sort_values("date").reset_index(drop=True)


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

    def _cohort_revenue(self, cohort_start: str, cohort_end: str) -> dict:
        """Return aggregate revenue metrics for the signup cohort (end exclusive)."""
        df = self._run(
            _COHORT_REVENUE_SQL, {"cohort_start": cohort_start, "cohort_end": cohort_end}
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
        return {k: float(row[k]) if row[k] is not None else 0.0 for k in df.columns}

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
            cac_full = spend / cohort_users if cohort_users > 0 else np.nan
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

    def cumulative_revenue(self, campaign_id: str | None = None) -> pd.DataFrame:
        """Daily and cumulative revenue generated by users acquired during a campaign.

        Revenue is tracked from the campaign start through today, covering all
        revenue sources (onramp, card fees, billing, swaps, payouts) minus costs
        (cashback, revenue share).

        Args:
            campaign_id: Which campaign's cohort to track. Defaults to the most
                recent campaign.

        Returns:
            DataFrame with columns: ``date`` (datetime), ``daily_rev_usd``,
            ``cum_rev_usd``.  Sorted by date; days with no revenue are filled
            with 0.
        """
        if not self._campaigns:
            return pd.DataFrame(columns=["date", "daily_rev_usd", "cum_rev_usd"])

        if campaign_id is not None:
            matches = [c for c in self._campaigns if c["campaign_id"] == campaign_id]
            c = matches[-1] if matches else self._campaigns[-1]
        else:
            c = self._campaigns[-1]

        cohort_start = str(c["start"])
        cohort_end = str((pd.Timestamp(c["end"]) + pd.Timedelta(days=1)).date())

        rev_df = self._run(
            _DAILY_COHORT_REVENUE_SQL,
            {"cohort_start": cohort_start, "cohort_end": cohort_end},
        )
        if rev_df.empty:
            return pd.DataFrame(columns=["date", "daily_rev_usd", "cum_rev_usd"])

        today = pd.Timestamp.today().normalize()
        all_dates = pd.DataFrame(
            {"date": pd.date_range(start=cohort_start, end=today, freq="D")}
        )
        rev_df["date"] = pd.to_datetime(rev_df["rev_date"])
        rev_df = rev_df.drop(columns=["rev_date"])
        result = all_dates.merge(rev_df, on="date", how="left")
        result["daily_rev_usd"] = result["daily_rev_usd"].fillna(0.0)
        result["cum_rev_usd"] = result["daily_rev_usd"].cumsum()
        return result
