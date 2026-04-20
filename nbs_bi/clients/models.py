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
            "onramp_rev": self._q.onramp_revenue(),
            "card_fees": self._q.card_fees(),
            "card_txs": self._q.card_transactions(),
            "billing": self._q.billing_charges(),
            "cashback": self._q.cashback(),
            "rev_share": self._q.revenue_share(),
            "swaps": self._q.swaps(),
            "payouts": self._q.payouts(),
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
        out["onramp_revenue_usd"] = out.get("onramp_revenue_brl", 0) / fx
        out["net_revenue_usd"] = (
            out["onramp_revenue_usd"]
            + out.get("card_fee_usd", 0)
            + out.get("card_tx_fee_usd", 0)
            + out.get("swap_fee_usd", 0)
            + out.get("payout_fee_usd", 0)
            - out.get("cashback_usd", 0)
            - out.get("revenue_share_paid_usd", 0)
            - out["card_cost_allocated_usd"]
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
            "acquisition_source",
            "referral_code",
            "net_revenue_usd",
            "onramp_revenue_usd",
            "card_fee_usd",
            "card_tx_fee_usd",
            "swap_fee_usd",
            "payout_fee_usd",
            "cashback_usd",
            "card_cost_allocated_usd",
            "n_conversions",
            "n_swaps",
            "tenure_months",
        ]
        present = [c for c in cols if c in self._master.columns]
        df = self._master[present].copy()
        df["user_id"] = df["user_id"].str[:8] + "..."
        return df.nlargest(n, "net_revenue_usd").reset_index(drop=True)

    def product_adoption(self) -> pd.DataFrame:
        """Per-user product activation flags and product count.

        Returns:
            DataFrame with columns: user_id (masked), has_onramp,
            has_card_fee, has_card_tx, has_swap, has_payout, n_products.
        """
        df = self._master[["user_id"]].copy()
        df["user_id"] = df["user_id"].str[:8] + "..."
        df["has_onramp"] = self._master.get("n_conversions", 0) > 0
        df["has_card_fee"] = self._master.get("card_fee_usd", 0) > 0
        df["has_card_tx"] = self._master.get("card_tx_fee_usd", 0) > 0
        df["has_swap"] = self._master.get("n_swaps", 0) > 0
        df["has_payout"] = self._master.get("payout_fee_usd", 0) > 0
        product_cols = ["has_onramp", "has_card_fee", "has_card_tx", "has_swap", "has_payout"]
        df["n_products"] = df[product_cols].sum(axis=1)
        return df

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
        df["user_id"] = df["user_id"].str[:8] + "..."
        cols = [
            "user_id",
            "founder_number",
            "founder_network_size",
            "invites_remaining",
            "net_revenue_usd",
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
        df["user_id"] = df["user_id"].str[:8] + "..."
        cols = [
            "user_id",
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

    def _build_monthly_ltv(self) -> pd.DataFrame:
        """Build (user_id, signup_month, activity_month, monthly_revenue_usd).

        Returns:
            Long-format DataFrame for cohort pivot computation.
        """
        monthly = self._q.onramp_monthly()
        if monthly.empty:
            return pd.DataFrame(
                columns=["user_id", "signup_month", "months_since_signup", "cum_ltv"]
            )
        fx = self._q.fx_rate()
        monthly["revenue_usd"] = monthly["onramp_revenue_brl"] / fx
        monthly["month"] = pd.to_datetime(monthly["month"])
        base = self._master[["user_id", "signup_date", "acquisition_source"]].copy()
        base["signup_month"] = pd.to_datetime(base["signup_date"]).dt.to_period("M")
        df = monthly.merge(base, on="user_id", how="inner")
        df["activity_month"] = df["month"].dt.to_period("M")
        df["months_since_signup"] = df["activity_month"].apply(
            lambda p: p.year * 12 + p.month
        ) - df["signup_month"].apply(lambda p: p.year * 12 + p.month)
        df = df[df["months_since_signup"] >= 0].sort_values(["user_id", "months_since_signup"])
        df["cum_ltv"] = df.groupby("user_id")["revenue_usd"].cumsum()
        return df[
            ["user_id", "signup_month", "acquisition_source", "months_since_signup", "cum_ltv"]
        ]

    def cohort_ltv(self) -> pd.DataFrame:
        """Cohort LTV matrix: cohort_month × months_since_signup → avg cumulative LTV.

        Returns:
            Pivot DataFrame indexed by cohort_month (Period), columns are
            months_since_signup (int). Values are avg cumulative USD LTV.
        """
        df = self._build_monthly_ltv()
        if df.empty:
            return pd.DataFrame()
        agg = df.groupby(["signup_month", "months_since_signup"])["cum_ltv"].mean()
        return agg.unstack("months_since_signup")

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
