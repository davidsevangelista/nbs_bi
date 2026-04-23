"""On/Off Ramp dashboard section — Tab 2.

Wraps the dict returned by ``OnrampReport.build()`` into Streamlit + Plotly
components organised into four subtabs:

* Overview  — volume over time (daily/weekly/monthly toggle)
* Revenue   — revenue by direction + monthly breakdown with MoM deltas
* Users     — top N clients (with attribution) + new vs returning
* FX & Volume — FX rate bands

Usage::

    from nbs_bi.onramp.report import OnrampReport
    from nbs_bi.reporting.ramp import RampSection

    report = OnrampReport(db_url=...).build("2026-01-01", "2026-03-31")
    section = RampSection(report)
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
    fmt_usd,
    mask_user_id,
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
# Helpers
# ---------------------------------------------------------------------------


def _mom_annotations(series: pd.Series) -> list[str]:
    """Compute month-over-month percentage change labels for a numeric series.

    Args:
        series: Numeric pandas Series in chronological order.

    Returns:
        List of annotation strings; first element is always empty.
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


def _resample_conv(conv_daily: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Resample daily conversion data to weekly or monthly buckets.

    Args:
        conv_daily: DataFrame with columns date, onramp, offramp.
        granularity: One of 'Daily', 'Weekly', 'Monthly'.

    Returns:
        Resampled DataFrame with the same column structure.
    """
    df = conv_daily.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if granularity == "Daily":
        return df
    freq = "W-MON" if granularity == "Weekly" else "MS"
    df = df.set_index("date")
    agg_cols = [c for c in ["onramp", "offramp"] if c in df.columns]
    resampled = df[agg_cols].resample(freq).sum().reset_index()
    return resampled


# ---------------------------------------------------------------------------
# Figure builders (pure functions — no Streamlit calls)
# ---------------------------------------------------------------------------


def _fig_volume(conv_daily: pd.DataFrame, granularity: str = "Daily") -> go.Figure:
    """Grouped bar chart: onramp vs offramp BRL volume at the chosen granularity.

    Args:
        conv_daily: DataFrame with columns date, onramp, offramp.
        granularity: Display granularity — 'Daily', 'Weekly', or 'Monthly'.

    Returns:
        Plotly Figure.
    """
    df = _resample_conv(conv_daily, granularity)
    fig = go.Figure()
    for col, label, color in [
        ("onramp", "Onramp (BRL→USDC)", BLUE),
        ("offramp", "Offramp (USDC→BRL)", AMBER),
    ]:
        if col in df.columns:
            fig.add_trace(go.Bar(x=df["date"], y=df[col], name=label, marker_color=color))
    layout = panel()
    layout["barmode"] = "group"
    layout["xaxis"]["title"] = None
    layout["yaxis"]["title"] = "BRL"
    fig.update_layout(**layout)
    return fig


def _fig_revenue_monthly(revenue_monthly: pd.DataFrame) -> go.Figure:
    """Stacked bar: monthly fee revenue vs spread revenue with MoM annotations.

    Args:
        revenue_monthly: DataFrame with columns month, fee_brl, spread_brl.

    Returns:
        Plotly Figure.
    """
    fig = go.Figure()
    totals = pd.Series([0.0] * len(revenue_monthly), dtype=float)
    for col, label, color in [
        ("fee_brl", "Explicit Fees", EMERALD),
        ("spread_brl", "Spread Revenue", TEAL),
    ]:
        if col in revenue_monthly.columns:
            fig.add_trace(
                go.Bar(
                    x=revenue_monthly["month"],
                    y=revenue_monthly[col],
                    name=label,
                    marker_color=color,
                )
            )
            totals = totals + revenue_monthly[col].fillna(0).reset_index(drop=True)

    mom_text = _mom_annotations(totals)
    fig.add_trace(
        go.Scatter(
            x=revenue_monthly["month"],
            y=totals,
            mode="text",
            text=mom_text,
            textposition="top center",
            showlegend=False,
            textfont=dict(size=10, color="#64748B"),
        )
    )
    layout = panel()
    layout["barmode"] = "stack"
    layout["xaxis"]["title"] = None
    layout["yaxis"]["title"] = "BRL"
    fig.update_layout(**layout)
    return fig


def _fig_revenue_by_direction(rev_dir: pd.DataFrame) -> go.Figure:
    """Stacked bar per side: fee + spread revenue by month.

    Renders onramp and offramp as separate grouped bars, each stacked by
    fee_brl (bottom) and spread_brl (top).

    Args:
        rev_dir: DataFrame with columns month, side, fee_brl, spread_brl.

    Returns:
        Plotly Figure.
    """
    colors: dict[tuple[str, str], str] = {
        ("onramp", "fee"): "#1565C0",
        ("onramp", "spread"): BLUE,
        ("offramp", "fee"): "#E65100",
        ("offramp", "spread"): AMBER,
    }
    fig = go.Figure()
    for side in ["onramp", "offramp"]:
        sub = rev_dir[rev_dir["side"] == side].sort_values("month")
        for component, col in [("fee", "fee_brl"), ("spread", "spread_brl")]:
            if col not in sub.columns:
                continue
            fig.add_trace(
                go.Bar(
                    x=sub["month"],
                    y=sub[col],
                    name=f"{side.capitalize()} — {component}",
                    marker_color=colors.get((side, component), "#888"),
                    offsetgroup=side,
                )
            )
    layout = panel()
    layout["barmode"] = "stack"
    layout["xaxis"]["title"] = None
    layout["yaxis"]["title"] = "BRL"
    fig.update_layout(**layout)
    return fig


def _fig_fx_rate(fx_stats: pd.DataFrame) -> go.Figure:
    """Line chart with p10–p90 confidence band per side (onramp / offramp).

    Args:
        fx_stats: DataFrame with columns period, side, fx_mean, fx_p10, fx_p90.

    Returns:
        Plotly Figure.
    """
    colors = {"onramp": BLUE, "offramp": AMBER}
    fig = go.Figure()

    for side in fx_stats["side"].unique():
        sub = fx_stats[fx_stats["side"] == side].sort_values("period")
        c = colors.get(side, "#888")
        fig.add_trace(
            go.Scatter(
                x=sub["period"],
                y=sub["fx_p90"],
                mode="lines",
                line=dict(width=0),
                showlegend=False,
                name=f"{side} p90",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=sub["period"],
                y=sub["fx_p10"],
                mode="lines",
                line=dict(width=0),
                fill="tonexty",
                fillcolor=rgba(c, 0.15),
                showlegend=False,
                name=f"{side} band",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=sub["period"],
                y=sub["fx_mean"],
                mode="lines",
                line=dict(color=c, width=2),
                name=f"{side.capitalize()} avg",
            )
        )

    layout = panel()
    layout["xaxis"]["title"] = None
    layout["yaxis"]["title"] = "BRL / USDC"
    fig.update_layout(**layout)
    return fig


def _fig_new_vs_returning(nvr: pd.DataFrame) -> go.Figure:
    """Stacked bar: new vs returning users per month.

    Args:
        nvr: DataFrame with columns month, new_users, returning_users.

    Returns:
        Plotly Figure.
    """
    fig = go.Figure()
    for col, label, color in [
        ("new_users", "New", TEAL),
        ("returning_users", "Returning", BLUE),
    ]:
        if col in nvr.columns:
            fig.add_trace(go.Bar(x=nvr["month"], y=nvr[col], name=label, marker_color=color))
    layout = panel()
    layout["barmode"] = "stack"
    layout["xaxis"]["title"] = None
    layout["yaxis"]["title"] = "Users"
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Section class
# ---------------------------------------------------------------------------


class RampSection:
    """Streamlit rendering for the On/Off Ramp tab (Tab 2).

    Args:
        report: Dict returned by ``OnrampReport.build()``.
    """

    def __init__(self, report: dict) -> None:
        self._r = report

    def render(self) -> None:
        """Render ramp analytics into four subtabs."""
        self._render_kpis()
        st.divider()
        tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Revenue", "Users", "FX & Volume"])
        with tab1:
            self._render_volume()
        with tab2:
            self._render_revenue_by_direction()
            self._render_revenue_monthly()
        with tab3:
            self._render_top_users()
            self._render_new_vs_returning()
        with tab4:
            self._render_fx_rate()

    # ------------------------------------------------------------------
    # Private render methods
    # ------------------------------------------------------------------

    def _render_kpis(self) -> None:
        """Render the KPI strips at the top of the Ramp tab."""
        summary = _get(self._r, "summary")
        behavior = self._r.get("user_behavior", {})

        cols = st.columns(5)
        cols[0].metric("Conversions", f"{int(_kpi(summary, 'Total conversions')):,}")
        cols[1].metric("Volume (USD)", fmt_usd(_kpi(summary, "Total volume USD")))
        cols[2].metric("Revenue (USD)", fmt_usd(_kpi(summary, "Total revenue USD")))
        cols[3].metric("Unique Users", f"{behavior.get('unique_users', 0):,}")
        cols[4].metric("Repeat Rate", f"{behavior.get('repeat_rate', 0):.1%}")

        st.caption("Last 30 days")
        cols2 = st.columns(5)
        cols2[0].metric("Conversions", f"{int(_kpi(summary, 'Total conversions L30')):,}")
        cols2[1].metric("Volume (USD)", fmt_usd(_kpi(summary, "Total volume USD L30")))
        cols2[2].metric("Revenue (USD)", fmt_usd(_kpi(summary, "Total revenue USD L30")))

    def _render_volume(self) -> None:
        """Render the volume bar chart with granularity toggle."""
        conv_daily = _get(self._r, "conv_daily")
        st.subheader("Volume over time")
        st.caption("Is volume growing? Any day-of-week pattern?")
        if _empty(conv_daily):
            st.info("No conversion data for this period.")
            return
        granularity = st.radio(
            "Granularity",
            ["Daily", "Weekly", "Monthly"],
            horizontal=True,
            key="ramp_vol_gran",
        )
        st.plotly_chart(_fig_volume(conv_daily, granularity), width="stretch")

    def _render_revenue_by_direction(self) -> None:
        """Render the monthly revenue split by direction bar chart."""
        rev = _get(self._r, "revenue_by_direction")
        st.subheader("Revenue by Direction (monthly)")
        st.caption("Do onramp and offramp contribute equally? Is the spread margin compressing?")
        if _empty(rev):
            st.info("No revenue direction data.")
            return
        st.plotly_chart(_fig_revenue_by_direction(rev), width="stretch")

    def _render_revenue_monthly(self) -> None:
        """Render the total monthly revenue stacked bar with MoM deltas."""
        rev = _get(self._r, "revenue_monthly")
        st.subheader("Total Monthly Revenue — fee + spread")
        st.caption("Is the spread margin holding or compressing?")
        if _empty(rev):
            st.info("No revenue data for this period.")
            return
        st.plotly_chart(_fig_revenue_monthly(rev), width="stretch")

    def _render_top_users(self) -> None:
        """Render the top-N clients table with masked user IDs."""
        top = _get(self._r, "top_users")
        n = st.slider("Number of clients", min_value=5, max_value=50, value=10, step=5)
        st.subheader(f"Top {n} clients by BRL volume")
        st.caption("VIP clients? Any concentration risk?")
        if _empty(top):
            st.info("No user data for this period.")
            return
        display = top.head(n).copy()
        display["user_id"] = display["user_id"].apply(mask_user_id)
        cols_order = [
            c
            for c in [
                "user_id",
                "acquisition_source",
                "referral_code_name",
                "volume_brl",
                "revenue_brl",
                "n_conversions",
            ]
            if c in display.columns
        ]
        display = display[cols_order]
        rename_map = {
            "user_id": "Client",
            "acquisition_source": "Source",
            "referral_code_name": "Referral Code",
            "volume_brl": "Volume BRL",
            "revenue_brl": "Revenue BRL",
            "n_conversions": "Conversions",
        }
        display.rename(
            columns={k: v for k, v in rename_map.items() if k in display.columns}, inplace=True
        )
        st.dataframe(display, width="stretch", hide_index=True)

    def _render_new_vs_returning(self) -> None:
        """Render the new vs returning users stacked bar chart."""
        nvr = _get(self._r, "new_vs_returning")
        st.subheader("New vs Returning (monthly)")
        st.caption("Retaining users or relying on new ones?")
        if _empty(nvr):
            st.info("No new/returning data.")
            return
        st.plotly_chart(_fig_new_vs_returning(nvr), width="stretch")

    def _render_fx_rate(self) -> None:
        """Render the implicit FX rate line chart with p10–p90 bands."""
        fx = _get(self._r, "fx_stats")
        st.subheader("Implicit FX Rate")
        st.caption("Is pricing consistent? Any outliers affecting margin?")
        if _empty(fx):
            st.info("No FX data for this period.")
            return
        st.plotly_chart(_fig_fx_rate(fx), width="stretch")
