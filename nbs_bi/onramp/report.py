"""Aggregated on/off ramp report combining queries, PIX flows, and analytics.

OnrampReport is the top-level entry point. It fetches all data for a date
window and returns a dict of DataFrames that mirrors the output of the
OnOffRampStoryReport class in contabil_pipeline.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from nbs_bi.onramp.models import OnrampModel
from nbs_bi.onramp.queries import OnrampQueries

logger = logging.getLogger(__name__)


class OnrampReport:
    """Builds a full on/off ramp analytics report for a given date window.

    Args:
        queries: An OnrampQueries instance (already initialised with a date range).
            If None, you must call build() with explicit start_date / end_date
            and supply a db_url, or set READONLY_DATABASE_URL in the environment.
        db_url: Database URL override. Used only when queries is None.
    """

    def __init__(
        self,
        queries: OnrampQueries | None = None,
        *,
        db_url: str | None = None,
    ) -> None:
        self._queries = queries
        self._db_url = db_url

    def build(
        self,
        start_date: str,
        end_date: str,
    ) -> dict[str, pd.DataFrame]:
        """Fetch data and compute all KPI DataFrames.

        Args:
            start_date: ISO date string, e.g. "2026-01-01".
            end_date: ISO date string, e.g. "2026-03-31".

        Returns:
            Dict with keys:
              - summary: One-row-per-metric KPI table (conversions, volumes, revenues).
              - conv_daily: Daily conversion volumes pivoted by direction.
              - pix_daily: Daily PIX IN / OUT / NET (BRL).
              - fx_stats: Daily FX implicit rate stats by side.
              - active_daily: Daily active user counts.
              - position: Running USDC position with PnL.
              - top_users: Top 20 users by BRL volume.
              - cohort: Monthly cohort retention pivot (PIX deposits).
        """
        q = self._queries or OnrampQueries(
            start_date=start_date,
            end_date=end_date,
            db_url=self._db_url,
        )

        logger.info("Building OnrampReport %s → %s", start_date, end_date)

        conv_df = q.conversions(start_date=start_date, end_date=end_date)
        dep_df = q.pix_deposits(start_date=start_date, end_date=end_date)
        trf_df = q.pix_transfers(start_date=start_date, end_date=end_date)
        card_tx_df = q.card_transactions_active(start_date=start_date, end_date=end_date)
        card_fee_df = q.card_fees_active(start_date=start_date, end_date=end_date)
        billing_df = q.billing_charges_active(start_date=start_date, end_date=end_date)
        swap_df = q.swaps_active(start_date=start_date, end_date=end_date)
        payout_df = q.payouts_active(start_date=start_date, end_date=end_date)
        attr_df = q.user_attribution()

        model = OnrampModel(conv_df) if not conv_df.empty else None

        top = model.top_users(n=50) if model else pd.DataFrame()
        if not top.empty and not attr_df.empty:
            top = top.merge(
                attr_df[["user_id", "acquisition_source", "referral_code_name"]],
                on="user_id",
                how="left",
            )

        return {
            "summary": self._build_summary(conv_df, dep_df, trf_df),
            "conv_daily": self._build_conv_daily(model),
            "revenue_monthly": self._build_revenue_monthly(conv_df),
            "pix_daily": self._build_pix_daily(dep_df, trf_df),
            "fx_stats": model.fx_stats(freq="D") if model else pd.DataFrame(),
            "active_daily": self._build_active_daily(
                dep_df, trf_df, card_tx_df, card_fee_df, billing_df, swap_df, payout_df
            ),
            "position": model.position() if model else pd.DataFrame(),
            "top_users": top,
            "cohort": self._build_cohort(dep_df),
            "user_attribution": attr_df,
            "user_behavior": model.user_behavior() if model else {},
            "spread_stats": model.spread_stats() if model else pd.DataFrame(),
            "revenue_by_direction": model.revenue_by_direction() if model else pd.DataFrame(),
            "new_vs_returning": model.monthly_new_vs_returning() if model else pd.DataFrame(),
            "card_daily": self._build_card_daily(card_tx_df),
            "card_revenue": {
                "card_fee_usd": q.card_fees_revenue_total(start_date=start_date, end_date=end_date),
                "billing_usd": q.billing_charges_revenue_total(
                    start_date=start_date, end_date=end_date
                ),
            },
            "card_revenue_monthly": q.card_revenue_monthly(
                start_date=start_date, end_date=end_date
            ),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(
        conv_df: pd.DataFrame,
        dep_df: pd.DataFrame,
        trf_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Produce the flat KPI summary table.

        Args:
            conv_df: Conversions DataFrame.
            dep_df: PIX deposits DataFrame.
            trf_df: PIX transfers DataFrame.

        Returns:
            DataFrame with columns: metric, value, note.
        """
        pix_in = float(dep_df["amount_brl"].sum()) if not dep_df.empty else 0.0
        pix_out = float(trf_df["amount_brl"].sum()) if not trf_df.empty else 0.0
        avg_in = float(dep_df["amount_brl"].mean()) if not dep_df.empty else 0.0
        avg_out = float(trf_df["amount_brl"].mean()) if not trf_df.empty else 0.0
        unique_in = int(dep_df["user_id"].nunique()) if "user_id" in dep_df.columns else 0
        unique_out = int(trf_df["user_id"].nunique()) if "user_id" in trf_df.columns else 0

        conv_brl_onramp = 0.0
        conv_brl_offramp = 0.0
        total_conversions = 0
        unique_conv_users = 0
        revenue_brl = 0.0

        if not conv_df.empty and "direction" in conv_df.columns:
            on_mask = conv_df["direction"].str.lower().isin({"brl_to_usdc", "buy", "onramp"})
            conv_brl_onramp = (
                float(conv_df.loc[on_mask, "from_amount_brl"].sum())
                if "from_amount_brl" in conv_df.columns
                else 0.0
            )
            conv_brl_offramp = (
                float(conv_df.loc[~on_mask, "to_amount_brl"].sum())
                if "to_amount_brl" in conv_df.columns
                else 0.0
            )
            total_conversions = len(conv_df)
            if "user_id" in conv_df.columns:
                unique_conv_users = int(conv_df["user_id"].nunique())
            rev_brl = conv_df.get("fee_amount_brl", pd.Series(0.0)).fillna(0.0) + conv_df.get(
                "spread_revenue_brl", pd.Series(0.0)
            ).fillna(0.0)
            revenue_brl = float(rev_brl.sum())

        rows = [
            ("PIX IN (dep)", pix_in, "BRL deposits via PIX"),
            ("PIX OUT (transf)", pix_out, "BRL withdrawals via PIX"),
            ("PIX NET", pix_in - pix_out, "Net PIX liquidity"),
            ("Avg ticket IN (BRL)", avg_in, "Mean PIX deposit size"),
            ("Avg ticket OUT (BRL)", avg_out, "Mean PIX transfer size"),
            ("Unique users IN", unique_in, "Users who deposited"),
            ("Unique users OUT", unique_out, "Users who withdrew"),
            ("Total conversions", total_conversions, "Completed BRL⇄USDC conversions"),
            ("Unique conversion users", unique_conv_users, "Users who converted"),
            ("Onramp volume BRL", conv_brl_onramp, "BRL→USDC client volume"),
            ("Offramp volume BRL", conv_brl_offramp, "USDC→BRL client volume"),
            ("Total revenue BRL", revenue_brl, "Fees + spread captured in BRL"),
        ]

        if not conv_df.empty and "exchange_rate" in conv_df.columns:
            rev_brl = conv_df.get("fee_amount_brl", pd.Series(0.0)).fillna(0.0) + conv_df.get(
                "spread_revenue_brl", pd.Series(0.0)
            ).fillna(0.0)
            rate = pd.to_numeric(conv_df["exchange_rate"], errors="coerce").replace(0, float("nan"))
            revenue_usd = float((rev_brl / rate).sum())
        else:
            revenue_usd = 0.0
        rows.append(
            ("Total revenue USD", revenue_usd, "BRL fees + spread converted at per-tx rate")
        )

        return pd.DataFrame(rows, columns=["metric", "value", "note"])

    @staticmethod
    def _build_revenue_monthly(conv_df: pd.DataFrame) -> pd.DataFrame:
        """Monthly revenue split into explicit fees vs spread, in BRL and USD.

        Args:
            conv_df: Conversions DataFrame (monetary columns already scaled).

        Returns:
            DataFrame with columns: month, fee_brl, spread_brl,
            total_revenue_brl, fee_usd, spread_usd.
        """
        if conv_df.empty or "fee_amount_brl" not in conv_df.columns:
            return pd.DataFrame()
        df = conv_df.copy()
        df["month"] = (
            pd.to_datetime(df["created_at"], errors="coerce", utc=True)
            .dt.tz_convert(None)
            .dt.to_period("M")
            .dt.to_timestamp()
        )
        raw_rate = df.get("exchange_rate", pd.Series(dtype=float))
        rate = pd.to_numeric(raw_rate, errors="coerce").replace(0, float("nan"))
        df["fee_usd"] = df["fee_amount_brl"].fillna(0.0) / rate
        spread_brl = df.get("spread_revenue_brl", pd.Series(0.0, index=df.index))
        df["spread_usd"] = spread_brl.fillna(0.0) / rate
        agg = (
            df.groupby("month")
            .agg(
                fee_brl=("fee_amount_brl", "sum"),
                spread_brl=("spread_revenue_brl", "sum"),
                fee_usd=("fee_usd", "sum"),
                spread_usd=("spread_usd", "sum"),
            )
            .reset_index()
        )
        agg["total_revenue_brl"] = agg["fee_brl"] + agg["spread_brl"]
        return agg.sort_values("month")

    @staticmethod
    def _build_card_daily(card_tx_df: pd.DataFrame) -> pd.DataFrame:
        """Daily card spend (USD) and transaction count.

        Args:
            card_tx_df: Output of ``OnrampQueries.card_transactions_active()``
                — columns ``created_at``, ``amount_usd``.

        Returns:
            DataFrame with columns ``date``, ``amount_usd``, ``n_txns``.
        """
        if card_tx_df is None or card_tx_df.empty or "amount_usd" not in card_tx_df.columns:
            return pd.DataFrame(columns=["date", "amount_usd", "n_txns"])
        df = card_tx_df.copy()
        df["date"] = pd.to_datetime(df["created_at"], errors="coerce").dt.date
        return (
            df.groupby("date")
            .agg(amount_usd=("amount_usd", "sum"), n_txns=("amount_usd", "count"))
            .reset_index()
            .sort_values("date")
        )

    @staticmethod
    def _build_conv_daily(model: OnrampModel | None) -> pd.DataFrame:
        """Daily BRL volume pivoted by direction.

        Args:
            model: Initialised OnrampModel, or None.

        Returns:
            DataFrame indexed by date with columns brl_to_usdc, usdc_to_brl.
        """
        if model is None:
            return pd.DataFrame()
        vol = model.volume_by_period(freq="D")
        if vol.empty:
            return pd.DataFrame()
        pivot = (
            vol.pivot_table(
                index="period",
                columns="side",
                values="volume_brl",
                fill_value=0.0,
            )
            .reset_index()
            .rename(columns={"period": "date"})
        )
        return pivot

    @staticmethod
    def _build_pix_daily(dep_df: pd.DataFrame, trf_df: pd.DataFrame) -> pd.DataFrame:
        """Daily PIX IN, OUT, and NET in BRL.

        Args:
            dep_df: PIX deposits DataFrame.
            trf_df: PIX transfers DataFrame.

        Returns:
            DataFrame with columns: date, pix_in, pix_out, pix_net.
        """

        def _agg(df: pd.DataFrame, label: str) -> pd.DataFrame:
            if df.empty or "amount_brl" not in df.columns:
                return pd.DataFrame(columns=["date", label])
            tmp = df.copy()
            tmp["date"] = pd.to_datetime(tmp["created_at"], errors="coerce").dt.date
            return (
                tmp.groupby("date")["amount_brl"]
                .sum()
                .reset_index()
                .rename(columns={"amount_brl": label})
            )

        pix_in = _agg(dep_df, "pix_in")
        pix_out = _agg(trf_df, "pix_out")

        if pix_in.empty and pix_out.empty:
            return pd.DataFrame()

        merged = pix_in.merge(pix_out, on="date", how="outer").fillna(0.0)
        merged["pix_net"] = merged["pix_in"] - merged["pix_out"]
        return merged.sort_values("date")

    @staticmethod
    def _build_active_daily(
        dep_df: pd.DataFrame,
        trf_df: pd.DataFrame,
        card_tx_df: pd.DataFrame | None = None,
        card_fee_df: pd.DataFrame | None = None,
        billing_df: pd.DataFrame | None = None,
        swap_df: pd.DataFrame | None = None,
        payout_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Daily unique active users across all revenue-generating activity.

        Args:
            dep_df: PIX deposits DataFrame.
            trf_df: PIX transfers DataFrame.
            card_tx_df: Card transactions DataFrame (user_id, created_at).
            card_fee_df: Card annual fees DataFrame (user_id, created_at).
            billing_df: Billing charges DataFrame (user_id, created_at).
            swap_df: Swap transactions DataFrame (user_id, created_at).
            payout_df: Unblockpay payouts DataFrame (user_id, created_at).

        Returns:
            DataFrame with columns: date, active_total, active_in, active_out.
        """

        def _daily_unique(df: pd.DataFrame, label: str) -> pd.DataFrame:
            if df is None or df.empty or "user_id" not in df.columns:
                return pd.DataFrame(columns=["date", label])
            tmp = df.copy()
            tmp["date"] = pd.to_datetime(tmp["created_at"], errors="coerce").dt.date
            tmp["user_id"] = tmp["user_id"].astype(str)
            return tmp.groupby("date")["user_id"].nunique().reset_index(name=label)

        active_in = _daily_unique(dep_df, "active_in")
        active_out = _daily_unique(trf_df, "active_out")

        all_sources = [dep_df, trf_df, card_tx_df, card_fee_df, billing_df, swap_df, payout_df]
        frames = [
            f for f in all_sources if f is not None and not f.empty and "user_id" in f.columns
        ]
        if not frames:
            return pd.DataFrame()

        all_users = pd.concat(
            [
                f[["created_at", "user_id"]].assign(
                    user_id=f["user_id"].astype(str),
                    date=pd.to_datetime(f["created_at"], errors="coerce").dt.date,
                )
                for f in frames
            ],
            ignore_index=True,
        ).dropna(subset=["date"])

        active_total = (
            all_users.groupby("date")["user_id"].nunique().reset_index(name="active_total")
        )
        return (
            active_total.merge(active_in, on="date", how="left")
            .merge(active_out, on="date", how="left")
            .fillna(0)
            .sort_values("date")
        )

    @staticmethod
    def _build_cohort(dep_df: pd.DataFrame) -> pd.DataFrame:
        """Monthly cohort retention matrix from PIX deposit activity.

        Rows = cohort month (first deposit), columns = M+0, M+1, M+2 …
        Values = retention rate (0–1).

        Args:
            dep_df: PIX deposits DataFrame.

        Returns:
            Pivot DataFrame with cohort_month as index and M+N as columns.
        """
        if dep_df.empty or "user_id" not in dep_df.columns:
            return pd.DataFrame()

        df = dep_df.copy()
        df["created_at"] = pd.to_datetime(
            df["created_at"], errors="coerce", utc=True
        ).dt.tz_convert(None)
        df = df.dropna(subset=["created_at"])
        if df.empty:
            return pd.DataFrame()

        df["user_id"] = df["user_id"].astype(str)
        df["activity_month"] = df["created_at"].dt.to_period("M").dt.to_timestamp()

        first_seen = df.groupby("user_id")["activity_month"].min().rename("cohort_month")
        df = df.merge(first_seen, on="user_id", how="left")

        cohort = (
            df.groupby(["cohort_month", "activity_month"])["user_id"]
            .nunique()
            .reset_index(name="active_users")
        )
        cohort = cohort[cohort["activity_month"] >= cohort["cohort_month"]]
        cohort["months_since"] = (
            cohort["activity_month"].dt.year - cohort["cohort_month"].dt.year
        ) * 12 + (cohort["activity_month"].dt.month - cohort["cohort_month"].dt.month)

        cohort_sizes = (
            df.groupby("cohort_month")["user_id"].nunique().reset_index(name="cohort_size")
        )
        cohort = cohort.merge(cohort_sizes, on="cohort_month", how="left")
        cohort["retention"] = np.where(
            cohort["cohort_size"] > 0,
            cohort["active_users"] / cohort["cohort_size"],
            np.nan,
        )

        pivot = cohort.pivot_table(
            index="cohort_month",
            columns="months_since",
            values="retention",
            fill_value=0.0,
        ).sort_index()
        pivot = pivot.reindex(sorted(pivot.columns), axis=1)
        pivot.index = pivot.index.strftime("%Y-%m")
        pivot.columns = [f"M+{int(c)}" for c in pivot.columns]
        return pivot
