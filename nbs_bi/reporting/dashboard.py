"""NBS Business Intelligence — Streamlit dashboard entry point.

Run with::

    streamlit run nbs_bi/reporting/dashboard.py

Six tabs:
    Tab 1 — Overview          (OverviewSection: cross-module KPIs, volume, revenue, funnel)
    Tab 2 — Revenue Analysis  (RevenueAnalysisSection: hour × day-of-week revenue heatmap)
    Tab 3 — Conversions       (OnrampReport → RampSection: 4 subtabs)
    Tab 4 — Cards             (CardAnalyticsSection: Cost Model + Usage Patterns + Tier Pricing)
    Tab 5 — Clients           (ClientReport → ClientSection)
    Tab 6 — Marketing - Ads   (MetaAdsSection: cumulative spend, ROI, channel comparison)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_BRT = timezone(timedelta(hours=-3))  # Brazil Standard Time (UTC-3, permanent since 2019)


def _today_brt() -> date:
    """Return today's date in Brazil Standard Time."""
    return datetime.now(_BRT).date()

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from nbs_bi.clients.report import ClientReport
from nbs_bi.config import ADS_DATABASE_URL, READONLY_DATABASE_URL
from nbs_bi.onramp.queries import OnrampQueries
from nbs_bi.onramp.report import OnrampReport
from nbs_bi.reporting.cards import CardAnalyticsSection
from nbs_bi.reporting.clients import ClientSection
from nbs_bi.reporting.marketing import MetaAdsSection
from nbs_bi.reporting.overview import OverviewSection
from nbs_bi.reporting.ramp import RampSection
from nbs_bi.reporting.revenue_analysis import RevenueAnalysisSection

load_dotenv()


@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_data(ttl=3600, show_spinner=False)
def _load_revenue_7d(db_url: str, today_iso: str) -> pd.DataFrame:
    """Fetch daily revenue by product line for the last 7 days including today.

    Args:
        db_url: Database URL — part of cache key.
        today_iso: Today's date as ISO string — included in the cache key so
            the result is recomputed each calendar day even within the TTL.

    Returns:
        DataFrame with columns date, daily_rev_conversion_usd,
        daily_rev_card_fees_usd, daily_rev_billing_usd, daily_rev_swap_usd,
        daily_rev_usd.
    """
    today = date.fromisoformat(today_iso)
    start = (today - timedelta(days=6)).isoformat()
    # Pass tomorrow: _run applies _to_exclusive_end internally, so end_date in
    # the date spine becomes today (i.e. pd.Timestamp(tomorrow) - 1 day = today).
    end = (today + timedelta(days=1)).isoformat()
    q = OnrampQueries(start_date=start, end_date=end, db_url=db_url)
    return q.daily_revenue_by_product()


@st.cache_data(ttl=3600, show_spinner="Loading revenue analysis…")
def _load_revenue_analysis(start_date: str, end_date: str, db_url: str) -> pd.DataFrame:
    """Load combined revenue DataFrame for the heatmap.

    Args:
        start_date: ISO date string (inclusive).
        end_date: ISO date string passed to OnrampQueries (exclusive-end convention).
        db_url: Database URL — part of cache key.

    Returns:
        DataFrame with columns: created_at, rev_usd, source.
    """
    return RevenueAnalysisSection.load(db_url, start_date, end_date)


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
    """Return a default (start, exclusive_end) date range covering all history.

    Returns:
        Tuple of ISO date strings (inclusive start, exclusive end).
    """
    today = _today_brt()
    start = date(2025, 8, 15)
    exclusive_end = today + timedelta(days=1)
    return start.isoformat(), exclusive_end.isoformat()


def _render_sidebar() -> None:
    """Render sidebar controls including a cache-clear button."""
    with st.sidebar:
        st.caption("Cache")
        if st.button("Clear cache & reload", use_container_width=True):
            st.cache_data.clear()
            st.rerun()


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
        st.exception(exc)
        return
    try:
        revenue_7d = _load_revenue_7d(READONLY_DATABASE_URL, _today_brt().isoformat())
    except Exception as exc:
        st.warning(f"7-day revenue chart unavailable: {exc}", icon="⚠️")
        revenue_7d = pd.DataFrame()
    try:
        OverviewSection(ramp_report, client_report, revenue_7d).render()
    except Exception as exc:
        st.error(f"Overview render error: {exc}", icon="🔴")
        st.exception(exc)


def _tab_revenue_analysis(start_date: str, end_date: str) -> None:
    if not READONLY_DATABASE_URL:
        st.error(
            "Set `READONLY_DATABASE_URL` in your `.env` file to load revenue data.",
            icon="🔴",
        )
        return
    try:
        df = _load_revenue_analysis(start_date, end_date, READONLY_DATABASE_URL)
        RevenueAnalysisSection(df).render()
    except Exception as exc:
        st.error(f"Revenue Analysis failed: {exc}", icon="🔴")
        st.exception(exc)


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
    if not READONLY_DATABASE_URL:
        st.error(
            "Set `READONLY_DATABASE_URL` in your `.env` file to load marketing data.",
            icon="🔴",
        )
        return

    try:
        client_report = _load_client_report(
            start_date, end_date, READONLY_DATABASE_URL, invoice_total
        )
    except Exception as exc:
        st.error(f"Failed to load client data for channel comparison: {exc}", icon="🔴")
        client_report = {}

    MetaAdsSection(
        campaign_data=None,
        acquisition=client_report.get("acquisition"),
        db_url=ADS_DATABASE_URL or None,
        analytics_db_url=READONLY_DATABASE_URL or None,
        profit_by_source_daily=client_report.get("profit_by_source_daily"),
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
    _render_sidebar()

    invoice_total, _invoice_id, _invoice_period = _latest_rain_invoice_total()
    start_date, end_date = _default_date_range()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["Overview", "Revenue Analysis", "Conversions", "Cards", "Clients", "Marketing - Ads"]
    )

    from datetime import date as _date

    _date_from = _date.fromisoformat(start_date) if start_date else None
    _date_to = _date.fromisoformat(end_date) if end_date else None

    with tab1:
        _tab_overview(start_date, end_date, invoice_total)
    with tab2:
        _tab_revenue_analysis(start_date, end_date)
    with tab3:
        _tab_ramp(start_date, end_date)
    with tab4:
        _tab_cards(_date_from, _date_to, invoice_total)
    with tab5:
        _tab_clients(start_date, end_date, invoice_total)
    with tab6:
        _tab_marketing(start_date, end_date, invoice_total)


if __name__ == "__main__":
    main()
