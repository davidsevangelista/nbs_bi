"""ClientReport — orchestrates all client analytics into a structured output dict.

Usage::

    from nbs_bi.clients.report import ClientReport

    report = ClientReport("2026-01-01", "2026-04-13").build()
    # report["cohort_ltv"]  → pivot DataFrame
    # report["acquisition"] → DataFrame by source_type
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from nbs_bi.clients.models import ClientModel
from nbs_bi.clients.segments import ClientSegments

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

    def build(self) -> dict[str, Any]:
        """Run all analyses and return a structured output dict.

        Returns:
            Dict with keys: ``leaderboard``, ``product_adoption``,
            ``segments``, ``segment_summary``, ``acquisition``,
            ``referral_codes``, ``cohort_ltv``, ``ltv_by_source``,
            ``founders``, ``at_risk``, ``fx_rate``.
        """
        logger.info("Building client report...")
        return {
            "leaderboard": self._model.revenue_leaderboard(n=50),
            "product_adoption": self._model.product_adoption(),
            "activation_funnel": self._model.activation_funnel(),
            "segments": self._segments.classify(),
            "segment_summary": self._segments.segment_summary(),
            "acquisition": self._model.acquisition_summary(),
            "referral_codes": self._model.referral_code_summary(),
            "cohort_ltv": self._model.cohort_ltv(),
            "ltv_by_source": self._model.ltv_by_source(),
            "founders": self._model.founders_report(),
            "at_risk": self._model.at_risk_users(),
            "fx_rate": self._model.master_df.pipe(lambda _: self._model._q.fx_rate()),
            "signups_daily": self._model.signups_daily(),
            "activity_kpis": self._model._q.activity_kpis(),
            "signups_24h": self._model._q.signups_24h(),
            "revenue_totals": self._model.revenue_totals(),
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
