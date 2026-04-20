"""On/Off Ramp dashboard section — Tab 2.

Wraps the dict returned by ``OnrampReport.build()`` into Streamlit + Plotly
components. Each decision the CEO needs to make has a dedicated chart.

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
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(report: dict[str, pd.DataFrame], key: str) -> pd.DataFrame:
    """Return a DataFrame from the report dict, or an empty DataFrame."""
    return report.get(key, pd.DataFrame())


def _kpi(summary: pd.DataFrame, metric: str, default: float = 0.0) -> float:
    """Extract a named value from the flat summary KPI table."""
    row = summary.loc[summary["metric"] == metric, "value"]
    return float(row.iloc[0]) if not row.empty else default


def _empty(df: pd.DataFrame) -> bool:
    return df is None or df.empty


def _mask_user_id(uid: str) -> str:
    """Show only first 8 chars of a UUID for display — never expose full UUIDs."""
    return str(uid)[:8] + "…"


# ---------------------------------------------------------------------------
# Figure builders (pure functions — no Streamlit calls)
# ---------------------------------------------------------------------------


def _fig_volume(conv_daily: pd.DataFrame) -> go.Figure:
    """Grouped bar chart: daily onramp vs offramp BRL volume.

    Args:
        conv_daily: DataFrame with columns date, onramp, offramp.

    Returns:
        Plotly Figure.
    """
    fig = go.Figure()
    for col, label, color in [
        ("onramp", "Onramp (BRL→USDC)", "#2196F3"),
        ("offramp", "Offramp (USDC→BRL)", "#FF9800"),
    ]:
        if col in conv_daily.columns:
            fig.add_trace(
                go.Bar(
                    x=conv_daily["date"],
                    y=conv_daily[col],
                    name=label,
                    marker_color=color,
                )
            )
    fig.update_layout(
        barmode="group",
        xaxis_title=None,
        yaxis_title="BRL",
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10),
    )
    return fig


def _fig_revenue_monthly(revenue_monthly: pd.DataFrame) -> go.Figure:
    """Stacked bar: monthly fee revenue vs spread revenue.

    Args:
        revenue_monthly: DataFrame with columns month, fee_brl, spread_brl.

    Returns:
        Plotly Figure.
    """
    fig = go.Figure()
    for col, label, color in [
        ("fee_brl", "Explicit Fees", "#4CAF50"),
        ("spread_brl", "Spread Revenue", "#8BC34A"),
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
    fig.update_layout(
        barmode="stack",
        xaxis_title=None,
        yaxis_title="BRL",
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10),
    )
    return fig


def _fig_fx_rate(fx_stats: pd.DataFrame) -> go.Figure:
    """Line chart with p10–p90 band per side (onramp / offramp).

    Args:
        fx_stats: DataFrame with columns period, side, fx_mean, fx_p10, fx_p90.

    Returns:
        Plotly Figure.
    """
    colors = {"onramp": "#2196F3", "offramp": "#FF9800"}
    fig = go.Figure()

    for side in fx_stats["side"].unique():
        sub = fx_stats[fx_stats["side"] == side].sort_values("period")
        c = colors.get(side, "#888")
        # Band: p90 (top) then p10 with fill back to p90
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
                fillcolor=f"rgba({_hex_to_rgb(c)}, 0.15)",
                showlegend=False,
                name=f"{side} band",
            )
        )
        # Mean line
        fig.add_trace(
            go.Scatter(
                x=sub["period"],
                y=sub["fx_mean"],
                mode="lines",
                line=dict(color=c, width=2),
                name=f"{side.capitalize()} avg",
            )
        )

    fig.update_layout(
        xaxis_title=None,
        yaxis_title="BRL / USDC",
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10),
    )
    return fig


def _fig_position(position: pd.DataFrame) -> go.Figure:
    """Dual-axis: USDC inventory (bar) + weighted avg cost BRL/USDC (line).

    Args:
        position: DataFrame from OnrampModel.position().

    Returns:
        Plotly Figure.
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=position["created_at"],
            y=position["position_qty_usdc"],
            name="USDC position",
            marker_color="#2196F3",
            opacity=0.6,
        ),
        secondary_y=False,
    )
    if "avg_price_brl_per_usdc" in position.columns:
        fig.add_trace(
            go.Scatter(
                x=position["created_at"],
                y=position["avg_price_brl_per_usdc"],
                name="Avg cost (BRL/USDC)",
                line=dict(color="#E91E63", width=2),
            ),
            secondary_y=True,
        )
    fig.update_yaxes(title_text="USDC", secondary_y=False)
    fig.update_yaxes(title_text="BRL / USDC", secondary_y=True)
    fig.update_layout(
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10),
    )
    return fig


def _fig_pnl(position: pd.DataFrame) -> go.Figure:
    """Cumulative PnL line chart in BRL.

    Args:
        position: DataFrame from OnrampModel.position().

    Returns:
        Plotly Figure.
    """
    fig = go.Figure(
        go.Scatter(
            x=position["created_at"],
            y=position["pnl_cum_brl"],
            mode="lines",
            fill="tozeroy",
            line=dict(color="#4CAF50", width=2),
            name="Cumulative PnL",
        )
    )
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="BRL",
        margin=dict(t=10, b=10),
    )
    return fig


def _fig_pix(pix_daily: pd.DataFrame) -> go.Figure:
    """Line chart: PIX IN vs PIX OUT daily BRL flows.

    Args:
        pix_daily: DataFrame with columns date, pix_in, pix_out.

    Returns:
        Plotly Figure.
    """
    fig = go.Figure()
    for col, label, color in [
        ("pix_in", "PIX IN", "#4CAF50"),
        ("pix_out", "PIX OUT", "#F44336"),
    ]:
        if col in pix_daily.columns:
            fig.add_trace(
                go.Scatter(
                    x=pix_daily["date"],
                    y=pix_daily[col],
                    mode="lines",
                    name=label,
                    line=dict(color=color, width=2),
                )
            )
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="BRL",
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10),
    )
    return fig


def _hex_to_rgb(hex_color: str) -> str:
    """Convert a 6-digit hex color to an 'R, G, B' string for rgba()."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"{r}, {g}, {b}"


# ---------------------------------------------------------------------------
# Section class
# ---------------------------------------------------------------------------


class RampSection:
    """Streamlit rendering for the On/Off Ramp tab (Tab 2).

    Args:
        report: Dict returned by ``OnrampReport.build()``.
    """

    def __init__(self, report: dict[str, pd.DataFrame]) -> None:
        self._r = report

    def render(self) -> None:
        """Render all ramp charts into the current Streamlit context."""
        self._render_kpis()
        st.divider()
        self._render_volume()
        self._render_revenue_monthly()
        self._render_fx_rate()
        self._render_position_pnl()
        self._render_top_users()
        self._render_pix_flows()

    # ------------------------------------------------------------------
    # Private render methods
    # ------------------------------------------------------------------

    def _render_kpis(self) -> None:
        summary = _get(self._r, "summary")
        position = _get(self._r, "position")

        usdc_pos = (
            float(position["position_qty_usdc"].iloc[-1])
            if not _empty(position) and "position_qty_usdc" in position.columns
            else 0.0
        )
        margin = (
            float(position["margin_sell_pct"].dropna().mean())
            if not _empty(position) and "margin_sell_pct" in position.columns
            else None
        )

        cols = st.columns(6)
        cols[0].metric("Conversions", f"{int(_kpi(summary, 'Total conversions')):,}")
        cols[1].metric("Onramp BRL", f"R$ {_kpi(summary, 'Onramp volume BRL'):,.0f}")
        cols[2].metric("Offramp BRL", f"R$ {_kpi(summary, 'Offramp volume BRL'):,.0f}")
        cols[3].metric("Revenue BRL", f"R$ {_kpi(summary, 'Total revenue BRL'):,.2f}")
        cols[4].metric("USDC Position", f"{usdc_pos:,.0f} USDC")
        cols[5].metric(
            "Avg Sell Margin",
            f"{margin:.2%}" if margin is not None else "—",
        )

    def _render_volume(self) -> None:
        conv_daily = _get(self._r, "conv_daily")
        st.subheader("Volume over time")
        st.caption("Is volume growing? Is there a day-of-week pattern I can act on?")
        if _empty(conv_daily):
            st.info("No conversion data for this period.")
            return
        st.plotly_chart(_fig_volume(conv_daily), width="stretch")

    def _render_revenue_monthly(self) -> None:
        rev = _get(self._r, "revenue_monthly")
        st.subheader("Revenue breakdown (monthly)")
        st.caption("Is the spread margin holding or compressing?")
        if _empty(rev):
            st.info("No revenue data for this period.")
            return
        st.plotly_chart(_fig_revenue_monthly(rev), width="stretch")

    def _render_fx_rate(self) -> None:
        fx = _get(self._r, "fx_stats")
        st.subheader("Implicit FX rate")
        st.caption("Am I pricing consistently? Are outliers hurting me?")
        if _empty(fx):
            st.info("No FX data for this period.")
            return
        st.plotly_chart(_fig_fx_rate(fx), width="stretch")

    def _render_position_pnl(self) -> None:
        position = _get(self._r, "position")
        if _empty(position):
            st.info("No position data for this period.")
            return
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("USDC Position")
            st.caption("Do I need to buy more USDC? Is my position too large?")
            st.plotly_chart(_fig_position(position), width="stretch")
        with col2:
            st.subheader("Cumulative PnL")
            st.caption("Is the ramp business making money overall?")
            st.plotly_chart(_fig_pnl(position), width="stretch")

    def _render_top_users(self) -> None:
        top = _get(self._r, "top_users")
        st.subheader("Top 10 clients by BRL volume")
        st.caption("Are there VIP clients I should call? Any concentration risk?")
        if _empty(top):
            st.info("No user data for this period.")
            return
        display = top.head(10).copy()
        display["user_id"] = display["user_id"].apply(_mask_user_id)
        display.columns = [c.replace("_", " ").title() for c in display.columns]
        st.dataframe(display, width="stretch", hide_index=True)

    def _render_pix_flows(self) -> None:
        pix = _get(self._r, "pix_daily")
        st.subheader("PIX IN vs PIX OUT (daily)")
        st.caption("Is my BRL liquidity net positive or am I draining reserves?")
        if _empty(pix):
            st.info("No PIX data for this period.")
            return
        st.plotly_chart(_fig_pix(pix), width="stretch")
