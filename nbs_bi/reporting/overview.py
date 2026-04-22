"""Overview dashboard section — Tab 1.

Aggregates headline KPIs from the ramp and clients modules into a single
executive summary. All data is sourced from pre-built report dicts so no
additional DB queries are issued when this tab is rendered.

Usage::

    from nbs_bi.reporting.overview import OverviewSection

    section = OverviewSection(ramp_report, client_report)
    section.render()
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nbs_bi.reporting.theme import (
    AMBER,
    BLUE,
    EMERALD,
    TEAL,
    fmt_brl,
    fmt_usd,
    panel,
    rgba,
)
from nbs_bi.reporting.theme import (
    extract_kpi as _kpi,
)
from nbs_bi.reporting.theme import (
    is_empty as _empty,
)
from nbs_bi.reporting.theme import (
    report_get as _get,
)

# ---------------------------------------------------------------------------
# CSS for dark KPI cards
# ---------------------------------------------------------------------------

_CSS = """
<style>
.nbs-kpi-card {
    background:#161B22;border:1px solid #30363D;border-radius:10px;
    padding:18px 20px;margin-bottom:4px;
}
.nbs-kpi-hl {
    background:#0D2818;border:1px solid #1A4731;border-radius:10px;
    padding:18px 20px;margin-bottom:4px;
}
.nbs-kpi-label {
    color:#8B949E;font-size:11px;font-weight:600;
    letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;
}
.nbs-kpi-val   { color:#E6EDF3;font-size:34px;font-weight:700;line-height:1.1; }
.nbs-kpi-hl .nbs-kpi-val { color:#00E676; }
.nbs-kpi-sub   { color:#8B949E;font-size:12px;margin-top:8px; }
.nbs-strip {
    background:#161B22;border:1px solid #30363D;border-radius:10px;
    padding:14px 20px;text-align:center;margin-top:8px;
}
.nbs-strip-label {
    color:#8B949E;font-size:11px;font-weight:600;
    letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px;
}
.nbs-strip-val { color:#E6EDF3;font-size:26px;font-weight:600; }
</style>
"""


# ---------------------------------------------------------------------------
# HTML card builders
# ---------------------------------------------------------------------------


def _kpi_card(label: str, value: str, subtitle: str = "", highlight: bool = False) -> str:
    """Return HTML for a large KPI card.

    Args:
        label: Uppercase label shown above the value.
        value: Formatted metric value string.
        subtitle: Small muted text below the value.
        highlight: If True, applies the green highlight style.

    Returns:
        HTML string suitable for ``st.markdown(..., unsafe_allow_html=True)``.
    """
    cls = "nbs-kpi-hl" if highlight else "nbs-kpi-card"
    return (
        f'<div class="{cls}">'
        f'<div class="nbs-kpi-label">{label}</div>'
        f'<div class="nbs-kpi-val">{value}</div>'
        f'<div class="nbs-kpi-sub">{subtitle}</div>'
        f"</div>"
    )


def _kpi_strip(label: str, value: str) -> str:
    """Return HTML for a small secondary KPI strip card.

    Args:
        label: Uppercase label.
        value: Formatted metric value string.

    Returns:
        HTML string suitable for ``st.markdown(..., unsafe_allow_html=True)``.
    """
    return (
        f'<div class="nbs-strip">'
        f'<div class="nbs-strip-label">{label}</div>'
        f'<div class="nbs-strip-val">{value}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _last_day(df: pd.DataFrame, col: str) -> float:
    """Return the last non-null value for col in df, or 0.0."""
    if _empty(df) or col not in df.columns:
        return 0.0
    vals = df[col].dropna()
    return float(vals.iloc[-1]) if not vals.empty else 0.0


def _window_avg(df: pd.DataFrame, col: str, days: int) -> float:
    """Return the mean of the last N values for col in df, or 0.0."""
    if _empty(df) or col not in df.columns:
        return 0.0
    vals = df[col].dropna()
    return float(vals.iloc[-days:].mean()) if not vals.empty else 0.0


def _mom_annotations(series: pd.Series) -> list[str]:
    """Compute month-over-month percentage change labels for a series.

    Args:
        series: Numeric pandas Series in chronological order.

    Returns:
        List of strings; first element is empty, remainder show e.g. '+12.3%'.
    """
    texts: list[str] = [""]
    for i in range(1, len(series)):
        prev = float(series.iloc[i - 1])
        curr = float(series.iloc[i])
        if prev != 0:
            pct = (curr - prev) / abs(prev) * 100
            sign = "+" if pct >= 0 else ""
            texts.append(f"{sign}{pct:.1f}%")
        else:
            texts.append("")
    return texts


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------


def _fig_monthly_revenue(revenue_monthly: pd.DataFrame) -> go.Figure | None:
    """Stacked area: monthly fee + spread revenue in BRL.

    Args:
        revenue_monthly: DataFrame with columns month, fee_brl, spread_brl.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if _empty(revenue_monthly):
        return None
    fig = go.Figure()
    for col, label, color in [
        ("fee_brl", "Fees", TEAL),
        ("spread_brl", "Spread", BLUE),
    ]:
        if col in revenue_monthly.columns:
            fig.add_trace(
                go.Scatter(
                    x=revenue_monthly["month"],
                    y=revenue_monthly[col],
                    name=label,
                    stackgroup="rev",
                    mode="lines",
                    line=dict(width=1, color=color),
                    fillcolor=rgba(color),
                )
            )
    layout = panel("Monthly Revenue (BRL)")
    layout["yaxis"]["title"] = "BRL"
    fig.update_layout(**layout)
    return fig


def _fig_volume_monthly(conv_daily: pd.DataFrame) -> go.Figure | None:
    """Stacked bar: monthly BRL conversion volume with MoM % annotations.

    Args:
        conv_daily: DataFrame with columns date, onramp, offramp.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if _empty(conv_daily):
        return None
    df = conv_daily.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    for col in ["onramp", "offramp"]:
        if col not in df.columns:
            df[col] = 0.0
    agg = df.groupby("month")[["onramp", "offramp"]].sum().reset_index()
    agg["total"] = agg["onramp"] + agg["offramp"]
    mom_text = _mom_annotations(agg["total"])
    fig = go.Figure()
    for col, label, color in [("onramp", "Onramp", BLUE), ("offramp", "Offramp", AMBER)]:
        fig.add_trace(go.Bar(x=agg["month"], y=agg[col], name=label, marker_color=color))
    fig.add_trace(
        go.Scatter(
            x=agg["month"],
            y=agg["total"],
            mode="text",
            text=mom_text,
            textposition="top center",
            showlegend=False,
            textfont=dict(size=10, color="#64748B"),
        )
    )
    layout = panel("Monthly BRL Volume")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "BRL"
    fig.update_layout(**layout)
    return fig


def _fig_active_users(active_daily: pd.DataFrame) -> go.Figure | None:
    """Area chart: daily unique active users (PIX activity).

    Args:
        active_daily: DataFrame with columns date, active_total.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if _empty(active_daily) or "active_total" not in active_daily.columns:
        return None
    fig = go.Figure(
        go.Scatter(
            x=active_daily["date"],
            y=active_daily["active_total"],
            mode="lines",
            fill="tozeroy",
            line=dict(color=EMERALD, width=2),
            fillcolor=rgba(EMERALD, 0.15),
            name="Usuários ativos",
        )
    )
    layout = panel("Daily Active Users")
    layout["yaxis"]["title"] = "Users"
    fig.update_layout(**layout)
    return fig


def _fig_funnel(funnel: dict) -> go.Figure | None:
    """Funnel chart: All Users → KYC Done → Active (revenue).

    Args:
        funnel: Dict with keys total_users, kyc_done, active_users.

    Returns:
        Plotly Figure or None if funnel is empty.
    """
    if not funnel:
        return None
    total = funnel.get("total_users", 0) or 1
    labels = ["All Users", "KYC Done", "Active (revenue)"]
    values = [
        funnel.get("total_users", 0),
        funnel.get("kyc_done", 0),
        funnel.get("active_users", 0),
    ]
    colors = [BLUE, TEAL, EMERALD]
    texts = [f"{v:,}  ({100 * v / total:.1f}%)" for v in values]
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=values,
            marker_color=colors,
            text=texts,
            textposition="inside",
            textinfo="text",
        )
    )
    layout = panel("User Activation Funnel")
    layout.pop("xaxis", None)
    layout.pop("yaxis", None)
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Section class
# ---------------------------------------------------------------------------


class OverviewSection:
    """Streamlit rendering for the Overview tab (Tab 1).

    Args:
        ramp_report: Dict returned by ``OnrampReport.build()``.
        client_report: Dict returned by ``ClientReport.build()``.
    """

    def __init__(self, ramp_report: dict, client_report: dict) -> None:
        self._r = ramp_report
        self._c = client_report

    def render(self) -> None:
        """Render all overview components."""
        col_left, col_right = st.columns(2)
        with col_left:
            self._render_revenue_trend()
            self._render_active_users()
        with col_right:
            self._render_volume()
            self._render_funnel()

    # ------------------------------------------------------------------
    # Private render methods
    # ------------------------------------------------------------------

    def _render_kpis(self) -> None:
        """Render dark KPI cards (top row) and activity strip (DAU/WAU/MAU/KYC)."""
        funnel = self._c.get("activation_funnel", {})
        activity = self._c.get("activity_kpis", {})
        pix_daily = _get(self._r, "pix_daily")
        card_daily = _get(self._r, "card_daily")
        summary = _get(self._r, "summary")

        total_users = funnel.get("total_users", 0)
        kyc_done = funnel.get("kyc_done", 0)
        active_users = funnel.get("active_users", 0)
        kyc_pct = kyc_done / total_users if total_users else 0.0

        new_users = self._c.get("signups_24h", 0)
        pix_vol = _last_day(pix_daily, "pix_in") + _last_day(pix_daily, "pix_out")
        card_spend = _last_day(card_daily, "amount_usd")
        card_txns = int(_last_day(card_daily, "n_txns"))
        revenue_brl = _kpi(summary, "Total revenue BRL")

        # DAU/WAU/MAU from users.last_active_at — always relative to now,
        # not bounded by the dashboard date range.
        dau = activity.get("dau", 0)
        wau = activity.get("wau", 0)
        mau = activity.get("mau", 0)

        st.markdown(_CSS, unsafe_allow_html=True)

        sub_new = f"{kyc_done:,} KYC'd • {active_users:,} active"
        sub_card = f"{card_txns:,} txns" if card_txns else "—"

        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(
            _kpi_card("NEW USERS 24H", f"{new_users:,}", sub_new, highlight=True),
            unsafe_allow_html=True,
        )
        c2.markdown(
            _kpi_card("PIX VOLUME 24H", fmt_brl(pix_vol), "in + out"),
            unsafe_allow_html=True,
        )
        c3.markdown(
            _kpi_card("CARD SPEND 24H", fmt_usd(card_spend), sub_card),
            unsafe_allow_html=True,
        )
        c4.markdown(
            _kpi_card("REVENUE", fmt_brl(revenue_brl), "period total"),
            unsafe_allow_html=True,
        )

        s1, s2, s3, s4 = st.columns(4)
        s1.markdown(_kpi_strip("DAU", f"{dau:,}"), unsafe_allow_html=True)
        s2.markdown(_kpi_strip("WAU", f"{wau:,}"), unsafe_allow_html=True)
        s3.markdown(_kpi_strip("MAU", f"{mau:,}"), unsafe_allow_html=True)
        s4.markdown(_kpi_strip("KYC %", f"{kyc_pct:.1%}"), unsafe_allow_html=True)

    def _render_revenue_trend(self) -> None:
        """Render the monthly revenue stacked area chart."""
        fig = _fig_monthly_revenue(_get(self._r, "revenue_monthly"))
        if fig is None:
            st.info("No revenue data for this period.")
            return
        st.plotly_chart(fig, width="stretch")

    def _render_volume(self) -> None:
        """Render the monthly BRL volume stacked bar chart."""
        fig = _fig_volume_monthly(_get(self._r, "conv_daily"))
        if fig is None:
            st.info("No volume data for this period.")
            return
        st.plotly_chart(fig, width="stretch")

    def _render_active_users(self) -> None:
        """Render the daily active users area chart."""
        fig = _fig_active_users(_get(self._r, "active_daily"))
        if fig is None:
            st.info("No active user data for this period.")
            return
        st.plotly_chart(fig, width="stretch")

    def _render_funnel(self) -> None:
        """Render the activation funnel horizontal bar chart."""
        funnel = self._c.get("activation_funnel", {})
        fig = _fig_funnel(funnel)
        if fig is None:
            st.info("No funnel data available.")
            return
        st.plotly_chart(fig, width="stretch")
