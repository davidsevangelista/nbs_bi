"""ClientReport — orchestrates all client analytics into a structured output dict.

Usage::

    from nbs_bi.clients.report import ClientReport

    report = ClientReport("2026-01-01", "2026-04-13").build()
    # report["cohort_ltv"]  → pivot DataFrame
    # report["acquisition"] → DataFrame by source_type
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from nbs_bi.clients.campaigns import CampaignAnalyzer, load_ad_spend_from_db
from nbs_bi.clients.models import ClientModel
from nbs_bi.clients.segments import ClientSegments
from nbs_bi.config import ADS_DATABASE_URL

logger = logging.getLogger(__name__)


class ClientReport:
    """Build the full client analytics report dict.

    Args:
        start_date: Inclusive window start (``"2026-01-01"``).
        end_date: Inclusive window end (``"2026-04-13"``).
        card_invoice_total_usd: Rain invoice total for pro-rata card cost.
        db_url: Optional DB URL override.
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        card_invoice_total_usd: float = 6_693.58,
        db_url: str | None = None,
    ) -> None:
        self._model = ClientModel(
            start_date,
            end_date,
            card_invoice_total_usd=card_invoice_total_usd,
            db_url=db_url,
        )
        self._segments = ClientSegments(self._model.master_df)

    def _weighted_cac(self) -> float:
        """Compute weighted incremental CAC across all detected Meta Ads campaigns.

        Spend data comes from ADS_DATABASE_URL (Neon ads DB); revenue queries
        use the main read-only DB — same split as the Marketing-Ads tab.

        Returns:
            Weighted CAC in USD, or ``nan`` if spend data is unavailable.
        """
        spend_df = load_ad_spend_from_db(ADS_DATABASE_URL) if ADS_DATABASE_URL else None
        if spend_df is None or spend_df.empty:
            return float("nan")
        try:
            roi = CampaignAnalyzer(spend_df, db_url=self._model._q._db_url).roi_summary()
            total_spend = float(roi["total_spend_usd"].sum())
            total_incr = float(roi["incremental_users_est"].sum())
            return total_spend / total_incr if total_incr > 0 else float("nan")
        except Exception:
            logger.warning("CAC computation failed — skipping", exc_info=True)
            return float("nan")

    def build(self) -> dict[str, Any]:
        """Run all analyses and return a structured output dict.

        Returns:
            Dict with keys: ``leaderboard``, ``product_adoption``,
            ``segments``, ``segment_summary``, ``acquisition``,
            ``referral_codes``, ``cohort_ltv``, ``ltv_by_source``,
            ``founders``, ``at_risk``, ``fx_rate``, ``cohort_retention``,
            ``cac_breakeven``, ``weighted_cac_usd``.
        """
        logger.info("Building client report...")
        weighted_cac = self._weighted_cac()
        return {
            "leaderboard": self._model.revenue_leaderboard(n=50),
            "product_adoption": self._model.product_adoption(),
            "activation_funnel": self._model.activation_funnel(),
            "segments": self._segments.classify(),
            "segment_summary": self._segments.segment_summary(),
            "acquisition": self._model.acquisition_summary(),
            "profit_by_source_daily": self._model.cumulative_profit_by_source(),
            "referral_codes": self._model.referral_code_summary(),
            "cohort_ltv": self._model.cohort_ltv(),
            "cohort_ltv_gross": self._model.cohort_ltv_gross(),
            "cohort_summary": self._model.cohort_summary(),
            "cohort_retention": self._model.cohort_retention(),
            "cohort_total_profit": self._model.cohort_total_profit(),
            "cohort_active_users": self._model.cohort_active_users(),
            "cohort_avg_dau": self._model.cohort_avg_dau(),
            "cohort_monthly_profit": self._model.cohort_monthly_profit(),
            "ltv_by_source": self._model.ltv_by_source(),
            "founders": self._model.founders_report(),
            "at_risk": self._model.at_risk_users(),
            "fx_rate": self._model.master_df.pipe(lambda _: self._model._q.fx_rate()),
            "signups_daily": self._model.signups_daily(),
            "activity_kpis": self._model._q.activity_kpis(),
            "signups_24h": self._model._q.signups_24h(),
            "revenue_totals": self._model.revenue_totals(),
            "weighted_cac_usd": weighted_cac,
            "cac_breakeven": (
                self._model.cac_breakeven(weighted_cac)
                if not math.isnan(weighted_cac)
                else pd.DataFrame()
            ),
        }

    def to_json_api(self) -> dict[str, Any]:
        """Convert the report to a JSON-serialisable dict for API consumption.

        DataFrames are converted to ``list[dict]`` (``orient="records"``).
        Non-serialisable types (Period index) are stringified.

        Returns:
            Dict where every DataFrame is replaced by a list of records.
        """
        report = self.build()
        out: dict[str, Any] = {}
        for key, val in report.items():
            if isinstance(val, pd.DataFrame):
                df = val.copy()
                # Stringify Period index/columns if present
                if hasattr(df.index, "astype"):
                    try:
                        df.index = df.index.astype(str)
                    except Exception:
                        pass
                if hasattr(df.columns, "astype"):
                    try:
                        df.columns = df.columns.astype(str)
                    except Exception:
                        pass
                out[key] = df.reset_index().to_dict(orient="records")
            elif isinstance(val, dict):
                out[key] = {
                    k: (
                        v.reset_index().to_dict(orient="records")
                        if isinstance(v, pd.DataFrame)
                        else v
                    )
                    for k, v in val.items()
                }
            else:
                out[key] = val
        return out
