"""Clients dashboard section — Tab 5.

Wraps the dict returned by ``ClientReport.build()`` into Streamlit + Plotly
components across 5 sub-tabs: LTV & Cohorts, Acquisition, Segments,
Founders Club, Product Adoption.

Usage::

    from nbs_bi.clients.report import ClientReport
    from nbs_bi.reporting.clients import ClientSection

    report = ClientReport("2026-01-01", "2026-04-13").build()
    ClientSection(report).render()
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover

    class _StreamlitShim:
        """Fallback so figure-builder tests can import without Streamlit installed."""

        def __getattr__(self, name):
            def _noop(*args, **kwargs):
                return None

            return _noop

    st = _StreamlitShim()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Shared colour palette (matches cards.py)
# ---------------------------------------------------------------------------

BG = "#FFFFFF"
PLOT_BG = "#F8FAFC"
GRID = "#E2E8F0"
TEXT = "#1E293B"
TEXT_MUTED = "#64748B"
BLUE = "#2563EB"
AMBER = "#D97706"
EMERALD = "#059669"
ROSE = "#E11D48"
VIOLET = "#7C3AED"
TEAL = "#0D9488"

_SOURCE_COLORS = {
    "founder_invite": EMERALD,
    "referral": BLUE,
    "organic": AMBER,
    "unknown": TEXT_MUTED,
}


def _panel(title: str = "") -> dict:
    return dict(
        title=title,
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(color=TEXT, size=12),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
        margin=dict(l=40, r=20, t=40, b=40),
    )


def _empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, pd.DataFrame):
        return v.empty
    return False


def _get(report: dict, key: str) -> pd.DataFrame:
    return report.get(key, pd.DataFrame())


def _fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------


def _fig_ltv_heatmap(cohort_ltv: pd.DataFrame) -> go.Figure | None:
    """Cohort LTV heatmap: cohort_month (rows) × months_since_signup (cols)."""
    if _empty(cohort_ltv):
        return None
    z = cohort_ltv.values.astype(float)
    y = [str(idx) for idx in cohort_ltv.index]
    x = [str(c) for c in cohort_ltv.columns]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=x,
            y=y,
            colorscale="Blues",
            hovertemplate="Cohort: %{y}<br>Month +%{x}: $%{z:.2f}<extra></extra>",
            colorbar=dict(title="Avg LTV (USD)"),
        )
    )
    fig.update_layout(**_panel("Cohort LTV — Avg Cumulative Revenue (USD)"))
    fig.update_xaxes(title="Months Since Signup")
    fig.update_yaxes(title="Cohort Month")
    return fig


def _fig_ltv_curves(ltv_by_source: dict) -> go.Figure | None:
    """LTV curve per acquisition source."""
    if not ltv_by_source:
        return None
    fig = go.Figure()
    for source, pivot in ltv_by_source.items():
        if _empty(pivot):
            continue
        avg_by_month = pivot.mean(axis=0).reset_index()
        avg_by_month.columns = ["month", "avg_ltv"]
        color = _SOURCE_COLORS.get(source, TEXT_MUTED)
        fig.add_trace(
            go.Scatter(
                x=avg_by_month["month"],
                y=avg_by_month["avg_ltv"],
                name=source,
                mode="lines+markers",
                line=dict(color=color, width=2),
            )
        )
    fig.update_layout(**_panel("Avg Cumulative LTV by Acquisition Source"))
    fig.update_xaxes(title="Months Since Signup")
    fig.update_yaxes(title="Avg Cumulative LTV (USD)")
    return fig


def _fig_cac_payback(breakeven: pd.DataFrame) -> go.Figure | None:
    """Bar chart of payback months per acquisition source."""
    if _empty(breakeven):
        return None
    df = breakeven.dropna(subset=["payback_months"]).copy()
    if df.empty:
        return None
    colors = [_SOURCE_COLORS.get(s, BLUE) for s in df["acquisition_source"]]
    fig = go.Figure(
        go.Bar(
            x=df["acquisition_source"],
            y=df["payback_months"],
            marker_color=colors,
            text=df["payback_months"].apply(lambda v: f"M+{int(v)}"),
            textposition="outside",
        )
    )
    fig.update_layout(**_panel("CAC Payback Period by Acquisition Source"))
    fig.update_yaxes(title="Months to Break Even")
    return fig


def _fig_acquisition_bar(acq: pd.DataFrame) -> go.Figure | None:
    """Bar chart: acquisition source → avg net revenue."""
    if _empty(acq):
        return None
    fig = go.Figure(
        go.Bar(
            x=acq["acquisition_source"],
            y=acq["avg_net_revenue_usd"],
            marker_color=[_SOURCE_COLORS.get(s, BLUE) for s in acq["acquisition_source"]],
            text=acq["avg_net_revenue_usd"].apply(lambda v: _fmt_usd(v)),
            textposition="outside",
        )
    )
    fig.update_layout(**_panel("Avg Net Revenue (USD) by Acquisition Source"))
    fig.update_yaxes(title="Avg Net Revenue (USD)")
    return fig


def _fig_funnel(acq: pd.DataFrame) -> go.Figure | None:
    """Conversion rate bar: source → % users with any revenue."""
    if _empty(acq):
        return None
    fig = go.Figure(
        go.Bar(
            x=acq["acquisition_source"],
            y=(acq["conversion_rate"] * 100).round(1),
            marker_color=TEAL,
            text=(acq["conversion_rate"] * 100).round(1).apply(lambda v: f"{v:.1f}%"),
            textposition="outside",
        )
    )
    fig.update_layout(**_panel("Transacting User Rate by Acquisition Source (%)"))
    fig.update_yaxes(title="% Users with Revenue", range=[0, 110])
    return fig


def _fig_segment_donut(summary: pd.DataFrame) -> go.Figure | None:
    """Donut chart of segment distribution."""
    if _empty(summary):
        return None
    colors = {"champion": EMERALD, "active": BLUE, "at_risk": AMBER, "dormant": ROSE}
    fig = go.Figure(
        go.Pie(
            labels=summary["segment"],
            values=summary["n_users"],
            hole=0.5,
            marker_colors=[colors.get(s, BLUE) for s in summary["segment"]],
        )
    )
    fig.update_layout(**_panel("User Segments"))
    return fig


def _fig_founders_scatter(founders: pd.DataFrame) -> go.Figure | None:
    """Network size vs revenue scatter."""
    if _empty(founders) or "founder_network_size" not in founders.columns:
        return None
    df = founders.copy()
    fig = go.Figure(
        go.Scatter(
            x=df["founder_network_size"],
            y=df["net_revenue_usd"],
            mode="markers",
            marker=dict(color=VIOLET, size=8, opacity=0.7),
            text=df["user_id"],
            hovertemplate="User: %{text}<br>Network: %{x}<br>Revenue: $%{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(**_panel("Founder Network Size vs Net Revenue"))
    fig.update_xaxes(title="Founder Network Size")
    fig.update_yaxes(title="Net Revenue (USD)")
    return fig


def _fig_adoption_heatmap(adoption: pd.DataFrame, segments: pd.DataFrame) -> go.Figure | None:
    """Product × segment heatmap — % users active in each combination."""
    if _empty(adoption) or _empty(segments):
        return None
    merged = adoption.merge(
        segments[["user_id", "segment"]].assign(user_id=lambda d: d["user_id"].str[:8] + "..."),
        on="user_id",
        how="left",
    ).dropna(subset=["segment"])
    products = ["has_onramp", "has_card_fee", "has_card_tx", "has_swap", "has_payout"]
    segs = ["champion", "active", "at_risk", "dormant"]
    z = []
    for seg in segs:
        grp = merged[merged["segment"] == seg]
        row = [grp[p].mean() * 100 if p in grp.columns else 0.0 for p in products]
        z.append(row)
    labels = ["Onramp", "Card Fee", "Card Tx", "Swap", "Payout"]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=labels,
            y=segs,
            colorscale="Greens",
            hovertemplate="%{y} × %{x}: %{z:.1f}%<extra></extra>",
            colorbar=dict(title="% Users"),
        )
    )
    fig.update_layout(**_panel("Product Adoption (%) by Segment"))
    return fig


# ---------------------------------------------------------------------------
# Main section class
# ---------------------------------------------------------------------------


class ClientSection:
    """Renders the Clients tab (Tab 5) of the NBS BI dashboard.

    Args:
        report: Dict returned by ``ClientReport.build()``.
    """

    def __init__(self, report: dict) -> None:
        self._r = report

    def render(self) -> None:
        """Render all 5 sub-tabs."""
        tabs = st.tabs(
            [
                "LTV & Cohorts",
                "Acquisition",
                "Segments",
                "Founders Club",
                "Product Adoption",
            ]
        )
        with tabs[0]:
            self._render_ltv()
        with tabs[1]:
            self._render_acquisition()
        with tabs[2]:
            self._render_segments()
        with tabs[3]:
            self._render_founders()
        with tabs[4]:
            self._render_adoption()

    # ------------------------------------------------------------------
    # Tab 1 — LTV & Cohorts
    # ------------------------------------------------------------------

    def _render_ltv(self) -> None:
        cohort_ltv = _get(self._r, "cohort_ltv")
        ltv_by_source = self._r.get("ltv_by_source", {})

        # KPI cards
        leaderboard = _get(self._r, "leaderboard")
        if not _empty(leaderboard):
            avg_ltv = leaderboard["net_revenue_usd"].mean()
            best_src = (
                _get(self._r, "acquisition")
                .sort_values("avg_net_revenue_usd", ascending=False)
                .iloc[0]["acquisition_source"]
                if not _empty(_get(self._r, "acquisition"))
                else "—"
            )
            c1, c2, c3 = st.columns(3)
            c1.metric("Avg Net Revenue (top 50)", _fmt_usd(avg_ltv))
            c2.metric("Best Acquisition Source", best_src)
            c3.metric("FX Rate (BRL/USD)", f"{self._r.get('fx_rate', 0):.4f}")

        st.divider()

        fig = _fig_ltv_heatmap(cohort_ltv)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No cohort LTV data available for the selected period.")

        fig2 = _fig_ltv_curves(ltv_by_source)
        if fig2:
            st.plotly_chart(fig2, use_container_width=True)

        st.divider()
        st.subheader("CAC Breakeven Analysis")
        cac = st.slider(
            "Customer Acquisition Cost (USD)", min_value=0, max_value=200, value=20, step=5
        )
        # CAC breakeven is computed from model — stored in report only if passed
        # We show it from ltv_by_source data directly
        breakeven = self._r.get("cac_breakeven_fn")
        if callable(breakeven):
            df_be = breakeven(cac)
        else:
            df_be = self._r.get("cac_breakeven_20", pd.DataFrame())
        fig3 = _fig_cac_payback(df_be)
        if fig3:
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.caption("CAC breakeven chart requires cohort LTV data. Load data via ClientReport.")

    # ------------------------------------------------------------------
    # Tab 2 — Acquisition
    # ------------------------------------------------------------------

    def _render_acquisition(self) -> None:
        acq = _get(self._r, "acquisition")
        ref = _get(self._r, "referral_codes")

        if _empty(acq):
            st.info("No acquisition data available.")
            return

        st.subheader("Revenue by Acquisition Source")
        col1, col2 = st.columns(2)
        with col1:
            fig = _fig_acquisition_bar(acq)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
        with col2:
            fig2 = _fig_funnel(acq)
            if fig2:
                st.plotly_chart(fig2, use_container_width=True)

        st.subheader("Acquisition Source Summary")
        display = acq.copy()
        for col in ["avg_net_revenue_usd", "median_net_revenue_usd", "total_net_revenue_usd"]:
            if col in display.columns:
                display[col] = display[col].apply(lambda v: f"${v:,.2f}")
        if "conversion_rate" in display.columns:
            display["conversion_rate"] = (display["conversion_rate"] * 100).round(1).astype(
                str
            ) + "%"
        st.dataframe(display, use_container_width=True, hide_index=True)

        if not _empty(ref):
            st.divider()
            st.subheader("Referral Code Performance")
            display_ref = ref.copy()
            for col in [
                "avg_net_revenue_usd",
                "avg_commission_cost_usd",
                "net_arpu_after_commission",
            ]:
                if col in display_ref.columns:
                    display_ref[col] = display_ref[col].apply(lambda v: f"${v:,.2f}")
            st.dataframe(display_ref, use_container_width=True, hide_index=True)

    # ------------------------------------------------------------------
    # Tab 3 — Segments
    # ------------------------------------------------------------------

    def _render_segments(self) -> None:
        summary = _get(self._r, "segment_summary")
        at_risk = _get(self._r, "at_risk")
        leaderboard = _get(self._r, "leaderboard")

        if not _empty(summary):
            champ_row = summary[summary["segment"] == "champion"]
            risk_row = summary[summary["segment"] == "at_risk"]
            n_champ = int(champ_row["n_users"].iloc[0]) if not champ_row.empty else 0
            n_risk = int(risk_row["n_users"].iloc[0]) if not risk_row.empty else 0
            rev_champ = (
                float(champ_row["total_net_revenue_usd"].iloc[0]) if not champ_row.empty else 0.0
            )

            c1, c2, c3 = st.columns(3)
            c1.metric("Champions", n_champ)
            c2.metric("At-Risk Users", n_risk)
            c3.metric("Champion Revenue", _fmt_usd(rev_champ))

        col1, col2 = st.columns([1, 2])
        with col1:
            fig = _fig_segment_donut(summary)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
        with col2:
            if not _empty(summary):
                st.dataframe(summary, use_container_width=True, hide_index=True)

        if not _empty(leaderboard):
            st.subheader("Champion Leaderboard (Top 50 by Net Revenue)")
            st.dataframe(leaderboard, use_container_width=True, hide_index=True)

        if not _empty(at_risk):
            st.divider()
            st.subheader("At-Risk Users — Outreach Targets")
            st.caption("Users inactive 30–90 days with meaningful revenue. user_id masked.")
            st.dataframe(at_risk.head(50), use_container_width=True, hide_index=True)

    # ------------------------------------------------------------------
    # Tab 4 — Founders Club
    # ------------------------------------------------------------------

    def _render_founders(self) -> None:
        founders = _get(self._r, "founders")
        if _empty(founders):
            st.info("No founders data available.")
            return

        f_total = len(founders)
        avg_rev = (
            founders["net_revenue_usd"].mean() if "net_revenue_usd" in founders.columns else 0.0
        )
        total_rev = (
            founders["net_revenue_usd"].sum() if "net_revenue_usd" in founders.columns else 0.0
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Founders with Data", f_total)
        c2.metric("Avg Founder Revenue", _fmt_usd(avg_rev))
        c3.metric("Total Founder Revenue", _fmt_usd(total_rev))

        col1, col2 = st.columns(2)
        with col1:
            fig = _fig_founders_scatter(founders)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
        with col2:
            if "invites_remaining" in founders.columns:
                top_unused = founders[founders["invites_remaining"] > 0].nlargest(
                    20, "invites_remaining"
                )[["user_id", "invites_remaining", "net_revenue_usd"]]
                if not top_unused.empty:
                    st.subheader("Under-leveraged Founders (unused invites)")
                    st.dataframe(top_unused, use_container_width=True, hide_index=True)

        st.subheader("Founders Leaderboard (Top 20 by Revenue)")
        cols = [
            "user_id",
            "founder_number",
            "founder_network_size",
            "net_revenue_usd",
            "n_products",
        ]
        present = [c for c in cols if c in founders.columns]
        st.dataframe(founders[present].head(20), use_container_width=True, hide_index=True)

    # ------------------------------------------------------------------
    # Tab 5 — Product Adoption
    # ------------------------------------------------------------------

    def _render_adoption(self) -> None:
        adoption = _get(self._r, "product_adoption")
        segments = _get(self._r, "segments")

        if _empty(adoption):
            st.info("No product adoption data available.")
            return

        product_cols = ["has_onramp", "has_card_fee", "has_card_tx", "has_swap", "has_payout"]
        present_products = [c for c in product_cols if c in adoption.columns]

        rates = {c: adoption[c].mean() * 100 for c in present_products}
        cols = st.columns(len(rates))
        labels = {
            "has_onramp": "Onramp",
            "has_card_fee": "Card Annual Fee",
            "has_card_tx": "Card Tx",
            "has_swap": "Swap",
            "has_payout": "Payout",
        }
        for i, (col, pct) in enumerate(rates.items()):
            cols[i].metric(f"{labels.get(col, col)} Users", f"{pct:.1f}%")

        fig = _fig_adoption_heatmap(adoption, segments)
        if fig:
            st.plotly_chart(fig, use_container_width=True)

        if "n_products" in adoption.columns:
            st.subheader("Product Combination Distribution")
            combo_counts = adoption["n_products"].value_counts().sort_index()
            fig2 = go.Figure(
                go.Bar(
                    x=[f"{n} product(s)" for n in combo_counts.index],
                    y=combo_counts.values,
                    marker_color=TEAL,
                )
            )
            fig2.update_layout(**_panel("Users by Number of Products Used"))
            fig2.update_yaxes(title="User Count")
            st.plotly_chart(fig2, use_container_width=True)
