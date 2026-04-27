"""Per-user revenue model, cohort LTV, and CAC breakeven analysis.

All monetary outputs are in USD. BRL revenues are converted using the median
effective FX rate from completed conversions in the analysis window.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from nbs_bi.clients.queries import ClientQueries

logger = logging.getLogger(__name__)

_RAIN_INVOICE_DEFAULT_USD = 6_693.58
_KYC_COST_USD = 2.07  # One-time KYC verification cost per user at signup


class ClientModel:
    """Per-user revenue, LTV, cohort, and CAC analysis.

    Args:
        start_date: Inclusive window start (``"2026-01-01"``).
        end_date: Inclusive window end (``"2026-04-13"``).
        card_invoice_total_usd: Rain card processing invoice total for the
            period, used for pro-rata cost allocation per user.
            Defaults to the Feb 2026 validated invoice ($6,693.58).
        db_url: Override for the DB URL; falls back to env var.
        _queries: Inject a pre-built queries object (for testing).
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        card_invoice_total_usd: float = _RAIN_INVOICE_DEFAULT_USD,
        db_url: str | None = None,
        _queries: ClientQueries | None = None,
    ) -> None:
        from nbs_bi.clients.queries import ClientQueries

        self._q = _queries or ClientQueries(start_date=start_date, end_date=end_date, db_url=db_url)
        self._invoice_total = card_invoice_total_usd
        self._master: pd.DataFrame = self._build_master()

    # ------------------------------------------------------------------
    # Build master DataFrame
    # ------------------------------------------------------------------

    def _join_streams(self) -> pd.DataFrame:
        """Join all revenue/cost streams onto the cohort base.

        Returns:
            Wide DataFrame with one row per user and all revenue columns,
            NaN filled with 0.
        """
        base = self._q.cohort_base().set_index("user_id")
        streams = {
            "conversion_rev": self._q.conversion_revenue(),
            "card_fees": self._q.card_fees(),
            "card_txs": self._q.card_transactions(),
            "billing": self._q.billing_charges(),
            "cashback": self._q.cashback(),
            "rev_share": self._q.revenue_share(),
            "swaps": self._q.swaps(),
        }
        for _, df in streams.items():
            if not df.empty:
                base = base.join(df.set_index("user_id"), how="left")
        return base.reset_index().fillna(0)

    def _compute_card_cost(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add ``card_cost_allocated_usd`` column via pro-rata Rain invoice.

        Args:
            df: Master DataFrame with ``user_tx_count`` and
                ``total_tx_count`` columns.

        Returns:
            DataFrame with new ``card_cost_allocated_usd`` column.
        """
        out = df.copy()
        total = out["total_tx_count"].max() if "total_tx_count" in out.columns else 0
        if total > 0:
            out["card_cost_allocated_usd"] = self._invoice_total * out["user_tx_count"] / total
        else:
            out["card_cost_allocated_usd"] = 0.0
        return out

    def _compute_revenues(self, df: pd.DataFrame, fx: float) -> pd.DataFrame:
        """Compute USD-normalised revenue and net_revenue_usd columns.

        Args:
            df: Master DataFrame after cost allocation.
            fx: Median BRL/USDC rate (BRL per 1 USDC).

        Returns:
            DataFrame with ``onramp_revenue_usd`` and ``net_revenue_usd`` added.
        """
        out = df.copy()
        out["onramp_revenue_usd"] = out.get("onramp_revenue_brl", 0) / fx + out.get(
            "onramp_revenue_usdc", 0
        )
        out["offramp_revenue_usd"] = out.get("offramp_revenue_brl", 0) / fx + out.get(
            "offramp_revenue_usdc", 0
        )
        out["kyc_cost_usd"] = _KYC_COST_USD
        out["net_revenue_usd"] = (
            out["onramp_revenue_usd"]
            + out["offramp_revenue_usd"]
            + out.get("card_fee_usd", 0)
            + out.get("card_tx_fee_usd", 0)
            + out.get("swap_fee_usd", 0)
            - out.get("cashback_usd", 0)
            - out.get("revenue_share_paid_usd", 0)
            - out["card_cost_allocated_usd"]
            - out["kyc_cost_usd"]
        )
        return out

    def _compute_time_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add ``days_since_last_active`` and ``tenure_months`` columns.

        Args:
            df: Master DataFrame with ``signup_date`` and
                ``last_active_at`` columns.

        Returns:
            DataFrame with time signal columns added.
        """
        out = df.copy()
        now = pd.Timestamp.now(tz="UTC")
        out["signup_date"] = pd.to_datetime(out["signup_date"], utc=True)
        out["last_active_at"] = pd.to_datetime(out["last_active_at"], utc=True, errors="coerce")
        out["days_since_last_active"] = (now - out["last_active_at"]).dt.days.clip(lower=0)
        out["tenure_months"] = ((now - out["signup_date"]).dt.days / 30.44).clip(lower=0)
        return out

    def _build_master(self) -> pd.DataFrame:
        """Build the fully-joined, revenue-computed master user DataFrame."""
        df = self._join_streams()
        df = self._compute_card_cost(df)
        fx = self._q.fx_rate()
        df = self._compute_revenues(df, fx)
        df = self._compute_time_signals(df)
        return df

    @property
    def master_df(self) -> pd.DataFrame:
        """Full master DataFrame with all users, revenue, and time signals."""
        return self._master

    # ------------------------------------------------------------------
    # Public analysis methods
    # ------------------------------------------------------------------

    def revenue_leaderboard(self, n: int = 50) -> pd.DataFrame:
        """Top N users by net revenue with all revenue stream columns.

        Args:
            n: Number of rows to return.

        Returns:
            DataFrame with masked user_id (first 8 chars + ``"..."``),
            acquisition_source, referral_code, and all revenue columns.
        """
        cols = [
            "user_id",
            "full_name",
            "acquisition_source",
            "referral_code",
            "referral_code_name",
            "net_revenue_usd",
            "onramp_revenue_usd",
            "offramp_revenue_usd",
            "card_fee_usd",
            "card_tx_fee_usd",
            "swap_fee_usd",
            "cashback_usd",
            "card_cost_allocated_usd",
            "kyc_cost_usd",
            "n_conversions",
            "n_swaps",
            "tenure_months",
        ]
        present = [c for c in cols if c in self._master.columns]
        df = self._master[present].copy().nlargest(n, "net_revenue_usd").reset_index(drop=True)
        if "user_id" in df.columns:
            df["user_id"] = df["user_id"].apply(lambda v: str(v)[:8] + "...")
        return df

    def product_adoption(self) -> pd.DataFrame:
        """Per-user product activation flags and product count.

        Products are the four top-level categories: conversion (on OR off
        ramp), card (annual fee OR tx fee), swap, cross-border (unblockpay).
        Detailed ``has_onramp`` / ``has_offramp`` flags are also included
        for drill-down analysis.

        Returns:
            DataFrame with columns: user_id (masked), has_conversion,
            has_onramp, has_offramp, has_card, has_swap, has_crossborder,
            n_products.
        """
        df = self._master[["user_id"]].copy()
        df["user_id"] = df["user_id"].str[:8] + "..."
        df["has_conversion"] = self._master.get("n_conversions", 0) > 0
        df["has_onramp"] = self._master.get("onramp_volume_brl", 0) > 0
        df["has_offramp"] = self._master.get("offramp_volume_usdc", 0) > 0
        df["has_card"] = (self._master.get("card_fee_usd", 0) > 0) | (
            self._master.get("card_tx_fee_usd", 0) > 0
        )
        df["has_swap"] = self._master.get("n_swaps", 0) > 0
        df["has_crossborder"] = self._master.get("payout_fee_usd", 0) > 0
        product_cols = ["has_conversion", "has_card", "has_swap", "has_crossborder"]
        df["n_products"] = df[product_cols].sum(axis=1)
        return df

    def activation_funnel(self) -> dict[str, int]:
        """Counts for the 3-stage user activation funnel.

        Stages:
          - **total_users**: all registered users
          - **kyc_done**: users with ``kyc_level >= 1``
          - **active_users**: users with at least one completed transaction
            (conversion, card fee, card tx, swap, or cross-border payout)

        Returns:
            Dict with keys ``total_users``, ``kyc_done``, ``active_users``.
        """
        df = self._master
        total = len(df)
        kyc_col = df.get("kyc_level", pd.Series(0, index=df.index))
        kyc_done = int((pd.to_numeric(kyc_col, errors="coerce").fillna(0) >= 1).sum())
        active_users = self._q.revenue_generating_count()
        return {"total_users": total, "kyc_done": kyc_done, "active_users": active_users}

    def signups_daily(self) -> pd.DataFrame:
        """Daily new user signup counts derived from the master DataFrame.

        Returns:
            DataFrame with columns ``date`` (datetime.date) and
            ``new_signups`` (int), sorted ascending.
        """
        df = self._master.copy()
        df["date"] = pd.to_datetime(df["signup_date"], utc=True, errors="coerce").dt.date
        return (
            df.dropna(subset=["date"])
            .groupby("date")
            .size()
            .reset_index(name="new_signups")
            .sort_values("date")
        )

    def acquisition_summary(self) -> pd.DataFrame:
        """Aggregate revenue and conversion metrics by acquisition source.

        Returns:
            DataFrame with columns: acquisition_source, n_users,
            n_transacting, avg_net_revenue_usd, median_net_revenue_usd,
            total_net_revenue_usd, conversion_rate.
        """
        df = self._master.copy()
        df["is_transacting"] = df["net_revenue_usd"] > 0
        grp = df.groupby("acquisition_source")
        return pd.DataFrame(
            {
                "n_users": grp["user_id"].count(),
                "n_transacting": grp["is_transacting"].sum(),
                "avg_net_revenue_usd": grp["net_revenue_usd"].mean(),
                "median_net_revenue_usd": grp["net_revenue_usd"].median(),
                "total_net_revenue_usd": grp["net_revenue_usd"].sum(),
                "conversion_rate": grp["is_transacting"].mean(),
            }
        ).reset_index()

    def cumulative_profit_by_source(self) -> pd.DataFrame:
        """Cumulative operational profit by acquisition source over signup date.

        Groups users by signup date and acquisition source, sums
        ``net_revenue_usd`` (revenue after card COGS and KYC cost), then
        computes a cumulative total per channel ordered by date.

        Returns:
            DataFrame with columns: ``signup_date``, ``acquisition_source``,
            ``daily_net_revenue_usd``, ``cumulative_net_revenue_usd``.
        """
        df = self._master.copy()
        df["signup_date"] = (
            pd.to_datetime(df["signup_date"], utc=True).dt.tz_convert(None).dt.normalize()
        )
        daily = (
            df.groupby(["signup_date", "acquisition_source"], as_index=False)["net_revenue_usd"]
            .sum()
            .rename(columns={"net_revenue_usd": "daily_net_revenue_usd"})
            .sort_values("signup_date")
        )
        daily["cumulative_net_revenue_usd"] = daily.groupby("acquisition_source")[
            "daily_net_revenue_usd"
        ].cumsum()
        return daily.reset_index(drop=True)

    def referral_code_summary(self) -> pd.DataFrame:
        """Per referral-code user economics including commission cost.

        Returns:
            DataFrame with columns: referral_code, referral_code_name,
            n_users, avg_net_revenue_usd, commission_rate_bps,
            avg_commission_cost_usd, net_arpu_after_commission.
        """
        df = self._master[self._master["referral_code"] != 0].copy()
        df = df[df["referral_code"].notna() & (df["referral_code"] != "")]
        grp = df.groupby(["referral_code", "referral_code_name", "commission_rate_bps"])
        out = pd.DataFrame(
            {
                "n_users": grp["user_id"].count(),
                "avg_net_revenue_usd": grp["net_revenue_usd"].mean(),
            }
        ).reset_index()
        out["avg_commission_cost_usd"] = (
            out["avg_net_revenue_usd"] * out["commission_rate_bps"] / 10_000
        ).clip(lower=0)
        out["net_arpu_after_commission"] = (
            out["avg_net_revenue_usd"] - out["avg_commission_cost_usd"]
        )
        return out.sort_values("net_arpu_after_commission", ascending=False)

    def founders_report(self) -> pd.DataFrame:
        """Revenue and network metrics for Founders Club members.

        Returns:
            DataFrame with columns: user_id (masked), founder_number,
            founder_network_size, invites_remaining, net_revenue_usd,
            n_products, revenue_per_network_member.
        """
        df = self._master[self._master["is_founder"] == True].copy()  # noqa: E712
        # Count products per user using full (unmasked) user_id to avoid collision
        # product_adoption() already masks user_id — rebuild from master
        product_cols = ["has_onramp", "has_card_fee", "has_card_tx", "has_swap", "has_payout"]
        present = [c for c in product_cols if c in self._master.columns]
        n_products_series = (self._master[present] > 0).sum(axis=1)
        df = df.copy()
        df["n_products"] = n_products_series.values[df.index]
        df["revenue_per_network_member"] = np.where(
            df["founder_network_size"].fillna(0) > 0,
            df["net_revenue_usd"] / df["founder_network_size"],
            np.nan,
        )
        cols = [
            "full_name",
            "referral_code",
            "referral_code_name",
            "founder_number",
            "founder_network_size",
            "invites_remaining",
            "net_revenue_usd",
            # --- revenue sources ---
            "onramp_revenue_usd",
            "offramp_revenue_usd",
            "card_fee_usd",
            "card_tx_fee_usd",
            "swap_fee_usd",
            # --- deductions ---
            "cashback_usd",
            "revenue_share_paid_usd",
            "card_cost_allocated_usd",
            # --- other ---
            "n_products",
            "revenue_per_network_member",
        ]
        present_cols = [c for c in cols if c in df.columns]
        return (
            df[present_cols].sort_values("net_revenue_usd", ascending=False).reset_index(drop=True)
        )

    def at_risk_users(
        self,
        min_revenue_usd: float = 0.0,
        inactive_days_min: int = 30,
        inactive_days_max: int = 90,
    ) -> pd.DataFrame:
        """Users who are inactive but had meaningful revenue (outreach targets).

        Args:
            min_revenue_usd: Minimum net revenue to be included.
            inactive_days_min: Minimum days since last activity.
            inactive_days_max: Maximum days since last activity.

        Returns:
            DataFrame sorted by net_revenue_usd descending. user_id masked.
        """
        df = self._master.copy()
        mask = (
            (df["days_since_last_active"] >= inactive_days_min)
            & (df["days_since_last_active"] <= inactive_days_max)
            & (df["net_revenue_usd"] >= min_revenue_usd)
        )
        df = df[mask].copy()
        cols = [
            "full_name",
            "referral_code",
            "referral_code_name",
            "acquisition_source",
            "days_since_last_active",
            "net_revenue_usd",
            "onramp_revenue_usd",
            "n_conversions",
            "tenure_months",
        ]
        present = [c for c in cols if c in df.columns]
        return df[present].sort_values("net_revenue_usd", ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Cohort LTV
    # ------------------------------------------------------------------

    def _cost_per_tx(self, card_monthly: pd.DataFrame) -> float:
        """Derive per-transaction card processing cost from the Rain invoice.

        Args:
            card_monthly: Full-history monthly card tx counts per user
                (columns: user_id, month, n_card_txns).

        Returns:
            USD cost per card transaction (invoice_total / total_txns).
        """
        total_txns = int(card_monthly["n_card_txns"].sum()) if not card_monthly.empty else 0
        return self._invoice_total / max(total_txns, 1)

    @staticmethod
    def _merge_monthly(base: pd.DataFrame, other: pd.DataFrame, col: str) -> pd.DataFrame:
        """Left-join a monthly revenue/cost stream into the base DataFrame.

        Args:
            base: Monthly base with user_id and month columns.
            other: Monthly stream DataFrame with user_id, month, and *col*.
            col: Column name to merge in (filled with 0.0 when absent).

        Returns:
            base with *col* added and NaNs filled to 0.
        """
        if other.empty:
            base[col] = 0.0
            return base
        other = other.copy()
        other["month"] = pd.to_datetime(other["month"])
        merged = base.merge(other[["user_id", "month", col]], on=["user_id", "month"], how="left")
        merged[col] = merged[col].fillna(0.0)
        return merged

    def _build_monthly_ltv(self) -> pd.DataFrame:
        """Build monthly net revenue per user for cohort LTV computation.

        Includes all revenue streams (conversion, card annual fee, billing,
        swap, payout) and deducts costs (cashback, revenue share, and
        pro-rata Rain card processing cost).

        Returns:
            Long-format DataFrame with columns: user_id, signup_month,
            acquisition_source, months_since_signup, cum_ltv.
        """
        monthly = self._q.conversion_monthly()
        if monthly.empty:
            return pd.DataFrame(
                columns=["user_id", "signup_month", "months_since_signup", "cum_ltv"]
            )
        fx = self._q.fx_rate()
        usdc_rev = monthly.get(
            "conversion_revenue_usdc", pd.Series(0.0, index=monthly.index)
        ).fillna(0.0)
        monthly["revenue_usd"] = monthly["conversion_revenue_brl"] / fx + usdc_rev
        monthly["month"] = pd.to_datetime(monthly["month"])

        # Merge all additional revenue streams
        monthly = self._merge_monthly(monthly, self._q.card_fees_monthly(), "card_fee_usd")
        monthly = self._merge_monthly(monthly, self._q.billing_monthly(), "billing_usd")
        monthly = self._merge_monthly(monthly, self._q.swap_fees_monthly(), "swap_fee_usd")
        monthly = self._merge_monthly(monthly, self._q.cashback_monthly(), "cashback_usd")
        monthly = self._merge_monthly(monthly, self._q.revenue_share_monthly(), "revenue_share_usd")

        # Gross = all fee/spread income before cost deductions
        monthly["gross_revenue_usd"] = (
            monthly["revenue_usd"]
            + monthly["card_fee_usd"]
            + monthly["billing_usd"]
            + monthly["swap_fee_usd"]
        )
        # Net = gross minus costs (cashback, revenue share, card COGS below)
        monthly["revenue_usd"] = (
            monthly["gross_revenue_usd"] - monthly["cashback_usd"] - monthly["revenue_share_usd"]
        )

        # Deduct pro-rata Rain card processing cost
        card_monthly = self._q.card_transactions_monthly()
        cost_per_tx = self._cost_per_tx(card_monthly)
        logger.debug("Card cost_per_tx=%.4f USD (invoice=%.2f)", cost_per_tx, self._invoice_total)

        if not card_monthly.empty:
            card_monthly = card_monthly.copy()
            card_monthly["month"] = pd.to_datetime(card_monthly["month"])
            card_monthly["card_cost_usd"] = card_monthly["n_card_txns"] * cost_per_tx
            monthly = monthly.merge(
                card_monthly[["user_id", "month", "card_cost_usd"]],
                on=["user_id", "month"],
                how="left",
            )
            monthly["card_cost_usd"] = monthly["card_cost_usd"].fillna(0.0)
        else:
            monthly["card_cost_usd"] = 0.0

        monthly["revenue_usd"] = monthly["revenue_usd"] - monthly["card_cost_usd"]

        base = self._master[["user_id", "signup_date", "acquisition_source"]].copy()
        base["signup_month"] = (
            pd.to_datetime(base["signup_date"], utc=True).dt.tz_convert(None).dt.to_period("M")
        )
        df = monthly.merge(base, on="user_id", how="inner")
        df["activity_month"] = df["month"].dt.to_period("M")
        df["months_since_signup"] = df["activity_month"].apply(
            lambda p: p.year * 12 + p.month
        ) - df["signup_month"].apply(lambda p: p.year * 12 + p.month)
        df = df[df["months_since_signup"] >= 0].sort_values(["user_id", "months_since_signup"])
        # Deduct one-time KYC cost in each user's first active month
        first_active = df.groupby("user_id")["months_since_signup"].transform("min")
        df.loc[df["months_since_signup"] == first_active, "revenue_usd"] -= _KYC_COST_USD
        df["cum_ltv"] = df.groupby("user_id")["revenue_usd"].cumsum()
        df["cum_gross_ltv"] = df.groupby("user_id")["gross_revenue_usd"].cumsum()
        return df[
            [
                "user_id",
                "signup_month",
                "acquisition_source",
                "months_since_signup",
                "revenue_usd",
                "gross_revenue_usd",
                "card_fee_usd",
                "billing_usd",
                "swap_fee_usd",
                "cum_ltv",
                "cum_gross_ltv",
            ]
        ]

    def _active_user_counts(self) -> pd.Series:
        """Count of users per cohort who had at least one transaction.

        Returns:
            Series indexed by signup_month with count of ever-transacted users.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.Series(dtype=int)
        return df.groupby("signup_month")["user_id"].nunique()

    def cohort_ltv(self) -> pd.DataFrame:
        """Cohort LTV matrix: cohort_month × months_since_signup → avg cumulative net LTV.

        Denominator is the count of ever-transacted users in the cohort, not
        all registered users. Churned users contribute 0 to months after their
        last transaction, which lowers later-month averages accurately.

        Returns:
            Pivot DataFrame indexed by cohort_month (Period), columns are
            months_since_signup (int). Values are avg cumulative USD net profit
            per ever-transacted user.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        n_active = self._active_user_counts()
        totals = (
            df.groupby(["signup_month", "months_since_signup"])["cum_ltv"]
            .sum()
            .unstack("months_since_signup")
        )
        return totals.div(n_active, axis=0)

    def cohort_ltv_gross(self) -> pd.DataFrame:
        """Cohort LTV matrix using gross revenue (before cashback/revshare/card COGS).

        Denominator is ever-transacted users per cohort (see ``cohort_ltv``).

        Returns:
            Pivot DataFrame indexed by cohort_month (Period), columns are
            months_since_signup (int). Values are avg cumulative USD gross revenue
            per ever-transacted user.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        n_active = self._active_user_counts()
        totals = (
            df.groupby(["signup_month", "months_since_signup"])["cum_gross_ltv"]
            .sum()
            .unstack("months_since_signup")
        )
        return totals.div(n_active, axis=0)

    def cohort_summary(self) -> pd.DataFrame:
        """Per-cohort aggregates: users, gross revenue, net profit, months observed.

        ``n_active_users`` counts users who ever transacted (the denominator for
        per-user averages). ``n_users`` retains the total registered count for
        funnel context.

        Returns:
            DataFrame with columns: cohort_month, n_users, n_active_users,
            total_gross_revenue_usd, total_net_revenue_usd, months_observed,
            avg_gross_per_user_usd, avg_net_per_user_usd.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        grp = df.groupby("signup_month")
        n_active = self._active_user_counts().rename("n_active_users")
        summary = pd.DataFrame(
            {
                "n_users": grp["user_id"].nunique(),
                "total_gross_revenue_usd": grp["gross_revenue_usd"].sum(),
                "total_net_revenue_usd": grp["revenue_usd"].sum(),
                "total_card_fee_usd": grp["card_fee_usd"].sum(),
                "total_billing_usd": grp["billing_usd"].sum(),
                "total_swap_fee_usd": grp["swap_fee_usd"].sum(),
                "months_observed": grp["months_since_signup"].max(),
            }
        ).reset_index()
        summary = summary.join(n_active, on="signup_month")
        denom = summary["n_active_users"].replace(0, float("nan"))
        summary["total_conversion_revenue_usd"] = (
            summary["total_gross_revenue_usd"]
            - summary["total_card_fee_usd"]
            - summary["total_billing_usd"]
            - summary["total_swap_fee_usd"]
        )
        summary["avg_gross_per_user_usd"] = summary["total_gross_revenue_usd"] / denom
        summary["avg_net_per_user_usd"] = summary["total_net_revenue_usd"] / denom
        summary["cohort_month"] = summary["signup_month"].astype(str)
        return summary

    def cohort_total_profit(self) -> pd.DataFrame:
        """Cohort profit matrix: cohort_month × months_since_signup → total cumulative net profit.

        Same structure as ``cohort_ltv()`` but values are the cohort-level sum
        (not divided by active-user count) — useful for seeing absolute company
        profit contribution per cohort over time.

        Returns:
            Pivot DataFrame indexed by cohort_month (Period), columns are
            months_since_signup (int). Values are total cumulative USD net profit
            for the whole cohort.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        totals = (
            df.groupby(["signup_month", "months_since_signup"])["cum_ltv"]
            .sum()
            .unstack("months_since_signup")
        )
        return totals

    def cohort_active_users(self) -> pd.DataFrame:
        """Cohort active-user count matrix: cohort_month × months_since_signup → n active users.

        Returns:
            Pivot DataFrame indexed by cohort_month (Period), columns are
            months_since_signup (int). Values are count of distinct users with
            at least one transaction at that tenure month.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        counts = (
            df.groupby(["signup_month", "months_since_signup"])["user_id"]
            .nunique()
            .unstack("months_since_signup")
        )
        return counts

    def cohort_avg_dau(self) -> pd.DataFrame:
        """Cohort average daily active users: cohort_month × months_since_signup → avg DAU.

        Avg DAU for a cell = mean over calendar days of distinct active user
        count per day. More sensitive than monthly active users: reveals
        engagement depth within each tenure month.

        Returns:
            Pivot DataFrame indexed by cohort_month (Period), columns are
            months_since_signup (int). Values are mean DAU (float).
        """
        df = self._q.daily_activity()
        if df.empty:
            return pd.DataFrame()

        base = self._master[["user_id", "signup_date"]].copy()
        base["signup_month"] = (
            pd.to_datetime(base["signup_date"], utc=True).dt.tz_convert(None).dt.to_period("M")
        )
        df = df.merge(base, on="user_id", how="inner")
        df["activity_month"] = pd.to_datetime(df["activity_date"]).dt.to_period("M")
        df["months_since_signup"] = df["activity_month"].apply(
            lambda p: p.year * 12 + p.month
        ) - df["signup_month"].apply(lambda p: p.year * 12 + p.month)
        df = df[df["months_since_signup"] >= 0]

        daily_counts = (
            df.groupby(["signup_month", "months_since_signup", "activity_date"])["user_id"]
            .nunique()
            .reset_index(name="dau")
        )
        avg_dau = (
            daily_counts.groupby(["signup_month", "months_since_signup"])["dau"]
            .mean()
            .unstack("months_since_signup")
        )
        return avg_dau

    def cohort_monthly_profit(self) -> pd.DataFrame:
        """Total company net profit per calendar month, broken down by signup cohort.

        Returns:
            Pivot DataFrame: rows = calendar_month (Period), columns = signup cohort
            (Period), values = total net profit in USD. Filled with 0 where no
            activity occurred. Sorted chronologically by calendar month.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["calendar_month"] = df["signup_month"] + df["months_since_signup"].astype(int)
        pivot = (
            df.groupby(["calendar_month", "signup_month"])["revenue_usd"]
            .sum()
            .unstack("signup_month")
            .fillna(0.0)
            .sort_index()
        )
        return pivot

    def cohort_retention(self) -> pd.DataFrame:
        """Per-cohort monthly retention rates (% of cohort still active).

        Returns:
            Pivot DataFrame: cohort_month (rows) × months_since_signup (cols),
            values are retention rates 0–1. Month 0 = 1.0 by definition.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        cohort_sizes = df.groupby("signup_month")["user_id"].nunique()
        active = df.groupby(["signup_month", "months_since_signup"])["user_id"].nunique()
        retention = (active / cohort_sizes).unstack("months_since_signup")
        return retention

    def ltv_by_source(self) -> dict[str, pd.DataFrame]:
        """Cohort LTV matrix broken down by acquisition source.

        Returns:
            Dict mapping acquisition_source string → cohort LTV pivot.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return {}
        result: dict[str, pd.DataFrame] = {}
        for source, grp in df.groupby("acquisition_source"):
            agg = grp.groupby(["signup_month", "months_since_signup"])["cum_ltv"].mean()
            result[source] = agg.unstack("months_since_signup")
        return result

    def cac_breakeven(self, cac_usd: float) -> pd.DataFrame:
        """For each acquisition source, compute months to break even at a given CAC.

        Args:
            cac_usd: Customer acquisition cost in USD to evaluate.

        Returns:
            DataFrame with columns: acquisition_source, payback_months
            (None if never), ltv_at_month_12.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame(columns=["acquisition_source", "payback_months", "ltv_at_month_12"])
        agg = (
            df.groupby(["acquisition_source", "months_since_signup"])["cum_ltv"]
            .mean()
            .reset_index()
        )
        rows = []
        for source, grp in agg.groupby("acquisition_source"):
            grp = grp.sort_values("months_since_signup")
            above = grp[grp["cum_ltv"] >= cac_usd]
            payback = int(above["months_since_signup"].iloc[0]) if not above.empty else None
            at12 = grp[grp["months_since_signup"] <= 12]["cum_ltv"].max()
            rows.append(
                {
                    "acquisition_source": source,
                    "payback_months": payback,
                    "ltv_at_month_12": float(at12) if not pd.isna(at12) else None,
                }
            )
        return pd.DataFrame(rows).sort_values("payback_months")

    def revenue_totals(self) -> dict[str, float]:
        """Aggregate revenue columns across all users in the master DataFrame.

        Returns:
            Dict with keys: card_fee_usd, card_tx_fee_usd, net_revenue_usd.
        """
        df = self._master

        def _sum(col: str) -> float:
            return float(df[col].sum()) if col in df.columns else 0.0

        return {
            "card_fee_usd": _sum("card_fee_usd"),
            "card_tx_fee_usd": _sum("card_tx_fee_usd"),
            "net_revenue_usd": _sum("net_revenue_usd"),
        }
