"""NBS Business Intelligence — Streamlit dashboard entry point.

Run with::

    streamlit run nbs_bi/reporting/dashboard.py

Five tabs:
    Tab 1 — Overview        (OverviewSection: cross-module KPIs, volume, revenue, funnel)
    Tab 2 — Conversions     (OnrampReport → RampSection: 4 subtabs)
    Tab 3 — Cards           (CardAnalyticsSection: Cost Model + Usage Patterns + Tier Pricing)
    Tab 4 — Clients         (ClientReport → ClientSection)
    Tab 5 — Marketing - Ads (MetaAdsSection: cumulative spend, ROI, channel comparison)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from nbs_bi.clients.report import ClientReport
from nbs_bi.config import ADS_DATABASE_URL, READONLY_DATABASE_URL
from nbs_bi.onramp.report import OnrampReport
from nbs_bi.reporting.cards import CardAnalyticsSection
from nbs_bi.reporting.clients import ClientSection
from nbs_bi.reporting.marketing import MetaAdsSection
from nbs_bi.reporting.overview import OverviewSection
from nbs_bi.reporting.ramp import RampSection

load_dotenv()


def _latest_rain_invoice_total() -> tuple[float, str, str]:
    """Return (invoice_total_usd, invoice_id, period) from the latest parsed invoice JSON.

    Uses the actual Rain-billed total when available (``invoice_total_usd`` field).
    Falls back to the computed model total if the field is absent or zero
    (e.g. JSONs produced before the invoice_total_usd field was added).

    Returns:
        Tuple of (total_usd, invoice_id, period).
    """
    from nbs_bi.reporting.cards import _load_all_invoice_models

    model, invoice_id, period, _ = _load_all_invoice_models()
    actual = getattr(model.inputs, "invoice_total_usd", 0.0)
    total = actual if actual > 0 else model.cost_breakdown().total
    return total, invoice_id, period


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600, show_spinner="Loading ramp data…")
def _load_ramp_report(start_date: str, end_date: str, db_url: str) -> dict:
    """Fetch and cache the OnrampReport result dict.

    Args:
        start_date: ISO date string.
        end_date: ISO date string.
        db_url: Database URL — included in cache key so different DBs don't collide.

    Returns:
        Dict of DataFrames as returned by OnrampReport.build().
    """
    return OnrampReport(db_url=db_url).build(start_date, end_date)


@st.cache_data(ttl=3600, show_spinner="Loading client data…")
def _load_client_report(start_date: str, end_date: str, db_url: str, invoice_total: float) -> dict:
    """Fetch and cache the ClientReport result dict.

    Args:
        start_date: ISO date string.
        end_date: ISO date string.
        db_url: Database URL — part of cache key.
        invoice_total: Rain invoice total for pro-rata card cost allocation.

    Returns:
        Dict of DataFrames as returned by ClientReport.build().
    """
    return ClientReport(start_date, end_date, invoice_total, db_url).build()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _default_date_range() -> tuple[str, str]:
    """Return a default (start, exclusive_end) date range covering ~3 months.

    Returns:
        Tuple of ISO date strings (inclusive start, exclusive end).
    """
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=60)).replace(day=1)
    exclusive_end = today + timedelta(days=1)
    return start.isoformat(), exclusive_end.isoformat()


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------


def _tab_overview(start_date: str, end_date: str, invoice_total: float) -> None:
    if not READONLY_DATABASE_URL:
        st.error(
            "Set `READONLY_DATABASE_URL` in your `.env` file to load overview data.",
            icon="🔴",
        )
        return
    try:
        ramp_report = _load_ramp_report(start_date, end_date, READONLY_DATABASE_URL)
        client_report = _load_client_report(
            start_date, end_date, READONLY_DATABASE_URL, invoice_total
        )
    except Exception as exc:
        st.error(f"Failed to load overview data: {exc}", icon="🔴")
        return
    OverviewSection(ramp_report, client_report).render()


def _tab_ramp(start_date: str, end_date: str) -> None:
    if not READONLY_DATABASE_URL:
        st.error(
            "Set `READONLY_DATABASE_URL` in your `.env` file to load ramp data.",
            icon="🔴",
        )
        return

    try:
        report = _load_ramp_report(start_date, end_date, READONLY_DATABASE_URL)
    except Exception as exc:
        st.error(f"Failed to load ramp data: {exc}", icon="🔴")
        return

    RampSection(report).render()


def _tab_cards(date_from: date | None, date_to: date | None, rain_cost_usd: float) -> None:
    if not READONLY_DATABASE_URL:
        st.error(
            "Set `READONLY_DATABASE_URL` in your `.env` file to load card data.",
            icon="🔴",
        )
        return
    try:
        CardAnalyticsSection(
            db_url=READONLY_DATABASE_URL,
            date_from=date_from,
            date_to=date_to,
            rain_cost_usd=rain_cost_usd,
        ).render()
    except Exception as exc:
        st.error(f"Failed to load card analytics: {exc}", icon="🔴")


def _tab_clients(start_date: str, end_date: str, invoice_total: float) -> None:
    if not READONLY_DATABASE_URL:
        st.error(
            "Set `READONLY_DATABASE_URL` in your `.env` file to load client data.",
            icon="🔴",
        )
        return

    try:
        report = _load_client_report(start_date, end_date, READONLY_DATABASE_URL, invoice_total)
    except Exception as exc:
        st.error(f"Failed to load client data: {exc}", icon="🔴")
        return

    ClientSection(report).render()


def _tab_marketing(start_date: str, end_date: str, invoice_total: float) -> None:
    campaign_data = None
    acquisition = None

    if READONLY_DATABASE_URL:
        try:
            report = _load_client_report(start_date, end_date, READONLY_DATABASE_URL, invoice_total)
            campaign_data = report.get("campaign_roi")
            acquisition = report.get("acquisition")
        except Exception as exc:
            st.warning(f"Could not pre-load client data: {exc}", icon="⚠️")

    MetaAdsSection(
        campaign_data=campaign_data,
        acquisition=acquisition,
        db_url=ADS_DATABASE_URL or None,
    ).render()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Streamlit app entry point."""
    _logo = Path(__file__).parent.parent.parent / "data" / "logo" / "Logo.png"
    st.set_page_config(
        page_title="NBS Data Analytics",
        page_icon=str(_logo) if _logo.exists() else "📊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.title("NBS Data Analytics")

    invoice_total, _invoice_id, _invoice_period = _latest_rain_invoice_total()
    start_date, end_date = _default_date_range()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["Overview", "Conversions", "Cards", "Clients", "Marketing - Ads"]
    )

    from datetime import date as _date

    _date_from = _date.fromisoformat(start_date) if start_date else None
    _date_to = _date.fromisoformat(end_date) if end_date else None

    with tab1:
        _tab_overview(start_date, end_date, invoice_total)
    with tab2:
        _tab_ramp(start_date, end_date)
    with tab3:
        _tab_cards(_date_from, _date_to, invoice_total)
    with tab4:
        _tab_clients(start_date, end_date, invoice_total)
    with tab5:
        _tab_marketing(start_date, end_date, invoice_total)


if __name__ == "__main__":
    main()
