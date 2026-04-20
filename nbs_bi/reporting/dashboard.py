"""NBS Business Intelligence — Streamlit dashboard entry point.

Run with::

    streamlit run nbs_bi/reporting/dashboard.py

Five tabs:
    Tab 1 — Overview        (placeholder until all modules are built)
    Tab 2 — On/Off Ramp     (OnrampReport → RampSection)
    Tab 3 — Card Costs      (CardCostModel → CardSection)
    Tab 4 — Card Analytics  (live DB spend data → CardAnalyticsSection)
    Tab 5 — Clients         (placeholder until nbs_bi.clients is built)
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta

import streamlit as st
from dotenv import load_dotenv

from nbs_bi.cards.models import CardCostModel
from nbs_bi.config import READONLY_DATABASE_URL
from nbs_bi.onramp.report import OnrampReport
from nbs_bi.reporting.cards import CardAnalyticsSection, CardSection
from nbs_bi.reporting.ramp import RampSection

load_dotenv()

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


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _sidebar() -> tuple[str, str]:
    """Render the sidebar and return the selected (start_date, end_date).

    Returns:
        Tuple of ISO date strings (start, end).
    """
    with st.sidebar:
        st.header("Filters")

        today = date.today()
        default_start = today.replace(day=1) - timedelta(days=60)  # ~last 3 months
        default_start = default_start.replace(day=1)

        date_range = st.date_input(
            "Date range",
            value=(default_start, today),
            max_value=today,
        )

        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            start, end = date_range[0], date_range[1]
        else:
            start, end = default_start, today

        st.divider()
        st.caption("Database")
        if READONLY_DATABASE_URL:
            st.success("Connected", icon="✅")
        else:
            st.error("READONLY_DATABASE_URL not set", icon="🔴")

    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------


def _tab_overview() -> None:
    st.info(
        "**Overview tab** is coming soon.\n\n"
        "It will aggregate KPIs from all modules: total revenue BRL, "
        "active users, new users, total BRL volume, card spend, and swap volume.",
        icon="🚧",
    )


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


def _tab_cards() -> None:
    st.caption(
        "Card costs are modelled from Rain invoice inputs. "
        "Upload a new invoice JSON to update — or use the Feb 2026 reference."
    )

    uploaded = st.file_uploader(
        "Upload invoice JSON (optional)",
        type="json",
        key="card_invoice_upload",
    )

    if uploaded is not None:
        try:
            data = json.load(uploaded)
            with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as tmp:
                json.dump(data, tmp)
                tmp_path = tmp.name
            model = CardCostModel.from_invoice(tmp_path)
        except Exception as exc:
            st.error(f"Could not parse invoice file: {exc}", icon="🔴")
            return
    else:
        model = CardCostModel.from_february_2026()
        st.info(
            "Showing Feb 2026 reference invoice (NKEMEJLO-0008 — $6,693.58 USD).",
            icon="ℹ️",
        )

    CardSection(model).render()


def _tab_card_analytics(date_from: date | None, date_to: date | None) -> None:
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
        ).render()
    except Exception as exc:
        st.error(f"Failed to load card analytics: {exc}", icon="🔴")


def _tab_clients() -> None:
    st.info(
        "**Clients tab** is coming soon.\n\n"
        "It will show: revenue leaderboard, product adoption matrix, "
        "client segments (Champion / Active / At-Risk / Dormant), "
        "income band analysis, and cohort LTV.",
        icon="🚧",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Streamlit app entry point."""
    st.set_page_config(
        page_title="NBS Business Intelligence",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("NBS Business Intelligence")
    st.caption("Internal dashboard — Neobankless Brasil LTDA")

    start_date, end_date = _sidebar()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Overview",
            "On/Off Ramp",
            "Card Costs",
            "Card Analytics",
            "Clients",
        ]
    )

    # Parse date strings back to date objects for CardAnalyticsSection
    from datetime import date as _date

    _date_from = _date.fromisoformat(start_date) if start_date else None
    _date_to = _date.fromisoformat(end_date) if end_date else None

    with tab1:
        _tab_overview()
    with tab2:
        _tab_ramp(start_date, end_date)
    with tab3:
        _tab_cards()
    with tab4:
        _tab_card_analytics(_date_from, _date_to)
    with tab5:
        _tab_clients()


if __name__ == "__main__":
    main()
