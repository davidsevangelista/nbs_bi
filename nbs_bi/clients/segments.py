"""Client segmentation: RFM-style segments, founder analysis, referral performance."""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_CHAMPION_REVENUE_PERCENTILE = 80
_ACTIVE_DAYS = 30
_AT_RISK_DAYS_MAX = 90


class ClientSegments:
    """Classify users into segments and produce segment-level summaries.

    Args:
        master_df: Full master DataFrame from ``ClientModel.master_df``.
    """

    def __init__(self, master_df: pd.DataFrame) -> None:
        self._df = master_df.copy()

    def classify(self) -> pd.DataFrame:
        """Add a ``segment`` column to the master DataFrame.

        Segments:
          - **champion**: active ≤ 30 days AND revenue ≥ 80th percentile
          - **active**: active ≤ 30 days AND revenue < 80th percentile
          - **at_risk**: inactive 31–90 days
          - **dormant**: inactive > 90 days

        Returns:
            DataFrame identical to master_df with ``segment`` column added.
        """
        df = self._df.copy()
        rev_threshold = df["net_revenue_usd"].quantile(_CHAMPION_REVENUE_PERCENTILE / 100)
        days = df["days_since_last_active"]

        conditions = [
            (days <= _ACTIVE_DAYS) & (df["net_revenue_usd"] >= rev_threshold),
            (days <= _ACTIVE_DAYS) & (df["net_revenue_usd"] < rev_threshold),
            (days > _ACTIVE_DAYS) & (days <= _AT_RISK_DAYS_MAX),
        ]
        choices = ["champion", "active", "at_risk"]
        df["segment"] = pd.Series(
            pd.Categorical(
                pd.Series(conditions[0])
                .map({True: "champion", False: None})
                .combine_first(pd.Series(conditions[1]).map({True: "active", False: None}))
                .combine_first(pd.Series(conditions[2]).map({True: "at_risk", False: None}))
                .fillna("dormant"),
                categories=["champion", "active", "at_risk", "dormant"],
                ordered=True,
            ),
            index=df.index,
        )
        # Override with numpy select for cleanliness
        import numpy as np

        df["segment"] = np.select(conditions, choices, default="dormant")
        return df

    def segment_summary(self) -> pd.DataFrame:
        """Aggregate counts and revenue per segment.

        Returns:
            DataFrame with columns: segment, n_users, avg_net_revenue_usd,
            total_net_revenue_usd, pct_users.
        """
        classified = self.classify()
        grp = classified.groupby("segment")
        out = pd.DataFrame(
            {
                "n_users": grp["user_id"].count(),
                "avg_net_revenue_usd": grp["net_revenue_usd"].mean(),
                "total_net_revenue_usd": grp["net_revenue_usd"].sum(),
            }
        ).reset_index()
        out["pct_users"] = out["n_users"] / out["n_users"].sum()
        return out.sort_values("segment")

    def founders_vs_non_founders(self) -> pd.DataFrame:
        """Compare revenue and conversion metrics between founders and non-founders.

        Returns:
            DataFrame with columns: is_founder, n_users, avg_net_revenue_usd,
            median_net_revenue_usd, pct_transacting.
        """
        df = self._df.copy()
        df["is_transacting"] = df["net_revenue_usd"] > 0
        grp = df.groupby("is_founder")
        return pd.DataFrame(
            {
                "n_users": grp["user_id"].count(),
                "avg_net_revenue_usd": grp["net_revenue_usd"].mean(),
                "median_net_revenue_usd": grp["net_revenue_usd"].median(),
                "pct_transacting": grp["is_transacting"].mean(),
            }
        ).reset_index()

    def referral_performance(self) -> pd.DataFrame:
        """Per referral-code segment mix and net economic value.

        Returns:
            DataFrame with columns: referral_code, n_users, avg_revenue,
            commission_rate_bps, commission_cost_usd, net_value_usd,
            pct_champion, pct_active, pct_at_risk, pct_dormant.
        """
        classified = self.classify()
        df = classified[
            classified["referral_code"].notna()
            & (classified["referral_code"] != "")
            & (classified["referral_code"] != 0)
        ].copy()
        if df.empty:
            return pd.DataFrame()

        grp = df.groupby("referral_code")
        out = pd.DataFrame(
            {
                "n_users": grp["user_id"].count(),
                "avg_revenue": grp["net_revenue_usd"].mean(),
                "commission_rate_bps": grp["commission_rate_bps"].first(),
            }
        ).reset_index()

        for seg in ["champion", "active", "at_risk", "dormant"]:
            out[f"pct_{seg}"] = (
                (
                    df[df["segment"] == seg].groupby("referral_code")["user_id"].count()
                    / out.set_index("referral_code")["n_users"]
                )
                .fillna(0)
                .values
            )

        out["commission_cost_usd"] = (
            out["avg_revenue"] * out["commission_rate_bps"] / 10_000
        ).clip(lower=0)
        out["net_value_usd"] = out["avg_revenue"] - out["commission_cost_usd"]
        return out.sort_values("net_value_usd", ascending=False).reset_index(drop=True)
