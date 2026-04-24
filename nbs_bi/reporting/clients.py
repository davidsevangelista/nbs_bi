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

import math

import pandas as pd
import plotly.graph_objects as go

from nbs_bi.reporting.theme import (
    AMBER,
    BLUE,
    EMERALD,
    ROSE,
    SOURCE_COLORS,
    TEAL,
    TEXT_MUTED,
    VIOLET,
    fmt_usd,
    get_streamlit,
    mask_user_id,
    panel,
)
from nbs_bi.reporting.theme import (
    is_empty as _empty,
)
from nbs_bi.reporting.theme import (
    report_get as _get,
)

st = get_streamlit()

_SOURCE_COLORS = SOURCE_COLORS
_panel = panel
_fmt_usd = fmt_usd


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------


def _fig_ltv_heatmap(
    cohort_ltv: pd.DataFrame,
    title: str = "Cohort LTV — Avg Cumulative Net Profit (USD)",
    colorbar_title: str = "Avg LTV (USD)",
    zmin: float | None = 0,
    value_fmt: str = "${v:.0f}",
) -> go.Figure | None:
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
            colorscale="YlGn",
            zmin=zmin,
            hovertemplate="Cohort: %{y}<br>Month +%{x}: %{z:.2f}<extra></extra>",
            colorbar=dict(title=colorbar_title),
            text=[[value_fmt.format(v=v) if not (v != v) else "" for v in row] for row in z],
            texttemplate="%{text}",
            textfont=dict(size=9, color="black"),
        )
    )
    fig.update_layout(**_panel(title))
    fig.update_xaxes(title="Months Since Signup")
    fig.update_yaxes(title="Cohort Month")
    return fig


def _fig_cohort_totals(summary: pd.DataFrame) -> go.Figure | None:
    """Stacked bar of gross revenue by product per cohort, with cost load % line.

    Stacks: Conversion (on/off ramp), Card fees, Billing, Swap fees.
    Secondary y-axis shows cost load % (deductions as fraction of gross).

    Args:
        summary: Output of ``ClientModel.cohort_summary()``.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if _empty(summary):
        return None
    fig = go.Figure()
    components = [
        ("total_conversion_revenue_usd", "Conversion (On/Off Ramp)", BLUE),
        ("total_card_fee_usd", "Card Fees", AMBER),
        ("total_billing_usd", "Billing", TEAL),
        ("total_swap_fee_usd", "Swap", VIOLET),
    ]
    for col, label, color in components:
        if col not in summary.columns:
            continue
        fig.add_trace(
            go.Bar(
                x=summary["cohort_month"],
                y=summary[col],
                name=label,
                marker_color=color,
                offsetgroup="revenue",
            )
        )
    fig.add_trace(
        go.Bar(
            x=summary["cohort_month"],
            y=summary["total_net_revenue_usd"],
            name="Profit (net)",
            marker_color=EMERALD,
            offsetgroup="net",
            text=[f"${v:,.0f}" for v in summary["total_net_revenue_usd"]],
            textposition="outside",
        )
    )
    cost_load = (
        (summary["total_gross_revenue_usd"] - summary["total_net_revenue_usd"])
        / summary["total_gross_revenue_usd"].replace(0, float("nan"))
        * 100
    )
    fig.add_trace(
        go.Scatter(
            x=summary["cohort_month"],
            y=cost_load,
            name="Cost Load %",
            mode="lines+markers",
            line=dict(color=ROSE, width=2),
            yaxis="y2",
        )
    )
    layout = _panel("Revenue by Product per Cohort (USD)")
    layout["barmode"] = "stack"
    layout["yaxis2"] = dict(
        title="Cost Load %",
        overlaying="y",
        side="right",
        range=[0, 100],
        showgrid=False,
    )
    fig.update_layout(**layout)
    fig.update_xaxes(title="Cohort Month")
    fig.update_yaxes(title="USD")
    return fig


def _fig_retention_curves(retention: pd.DataFrame) -> go.Figure | None:
    """Line chart of monthly retention rate per cohort with avg + 30% threshold.

    Args:
        retention: Pivot DataFrame from ``ClientModel.cohort_retention()``.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if _empty(retention):
        return None
    fig = go.Figure()
    for idx in retention.index:
        row = retention.loc[idx].dropna()
        if row.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=row.index.tolist(),
                y=(row.values * 100).tolist(),
                name=str(idx),
                mode="lines",
                line=dict(width=1.5),
                opacity=0.5,
            )
        )
    avg = retention.mean(axis=0).dropna()
    if not avg.empty:
        fig.add_trace(
            go.Scatter(
                x=avg.index.tolist(),
                y=(avg.values * 100).tolist(),
                name="Average",
                mode="lines+markers",
                line=dict(color=EMERALD, width=3),
            )
        )
    fig.add_hline(
        y=30,
        line_dash="dash",
        line_color=ROSE,
        annotation_text="30% threshold",
        annotation_position="bottom right",
    )
    layout = _panel("Cohort Retention Rate (%)")
    fig.update_layout(**layout)
    fig.update_xaxes(title="Months Since Signup")
    fig.update_yaxes(title="% Active", range=[0, 105])
    return fig


def _fig_lorenz(segments: pd.DataFrame) -> go.Figure | None:
    """Lorenz curve: cumulative % of users vs cumulative % of revenue.

    Args:
        segments: Per-user DataFrame with ``net_revenue_usd`` column.

    Returns:
        Plotly Figure or None if no positive-revenue users.
    """
    if _empty(segments) or "net_revenue_usd" not in segments.columns:
        return None
    rev_pos = (
        segments["net_revenue_usd"]
        .dropna()
        .pipe(lambda s: s[s > 0])
        .sort_values(ascending=False)
        .reset_index(drop=True)
    )
    if rev_pos.empty:
        return None
    n = len(rev_pos)
    cum_rev = rev_pos.cumsum() / rev_pos.sum() * 100
    cum_users = (rev_pos.index + 1) / n * 100
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=cum_users.tolist(),
            y=cum_rev.tolist(),
            mode="lines",
            name="Revenue concentration",
            line=dict(color=BLUE, width=2),
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.1)",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[0, 100],
            y=[0, 100],
            mode="lines",
            name="Perfect equality",
            line=dict(color=TEXT_MUTED, width=1, dash="dash"),
        )
    )
    layout = _panel("Revenue Concentration (Lorenz Curve)")
    fig.update_layout(**layout)
    fig.update_xaxes(title="Cumulative Users (%)", range=[0, 100])
    fig.update_yaxes(title="Cumulative Revenue (%)", range=[0, 105])
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
    """Network size vs revenue scatter with masked user IDs in hover text.

    Args:
        founders: DataFrame with columns user_id, founder_network_size,
            net_revenue_usd.

    Returns:
        Plotly Figure or None if data is empty or missing required column.
    """
    if _empty(founders) or "founder_network_size" not in founders.columns:
        return None
    df = founders.copy()
    name_col = (
        df["full_name"]
        if "full_name" in df.columns
        else (df["user_id"].apply(mask_user_id) if "user_id" in df.columns else None)
    )
    fig = go.Figure(
        go.Scatter(
            x=df["founder_network_size"],
            y=df["net_revenue_usd"],
            mode="markers",
            marker=dict(color=VIOLET, size=8, opacity=0.7),
            text=name_col,
            hovertemplate="User: %{text}<br>Network: %{x}<br>Revenue: $%{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(**_panel("Founder Network Size vs Net Revenue"))
    fig.update_xaxes(title="Founder Network Size")
    fig.update_yaxes(title="Net Revenue (USD)")
    return fig


def _fig_activation_funnel(funnel: dict) -> go.Figure | None:
    """Funnel chart: All Users → KYC Done → Active (any txn)."""
    if not funnel:
        return None
    labels = ["All Users", "KYC Done", "Active (any txn)"]
    values = [
        funnel.get("total_users", 0),
        funnel.get("kyc_done", 0),
        funnel.get("active_users", 0),
    ]
    colors = [BLUE, EMERALD, AMBER]
    total = values[0] or 1
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
    layout = _panel("User Activation Funnel")
    layout.pop("xaxis", None)
    layout.pop("yaxis", None)
    fig.update_layout(**layout)
    return fig


def _fig_product_adoption_bars(adoption: pd.DataFrame) -> go.Figure | None:
    """Horizontal bar chart: product → % of all users who used it."""
    if _empty(adoption):
        return None
    products = {
        "has_conversion": ("Conversion (On/Off Ramp)", TEAL),
        "has_card": ("Card", BLUE),
        "has_swap": ("Swap", VIOLET),
        "has_crossborder": ("Cross-border (Unblock)", AMBER),
    }
    labels, rates, colors = [], [], []
    for col, (label, color) in products.items():
        if col in adoption.columns:
            labels.append(label)
            rates.append(float(adoption[col].mean() * 100))
            colors.append(color)
    if not labels:
        return None
    fig = go.Figure(
        go.Bar(
            x=rates,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=[f"{r:.1f}%" for r in rates],
            textposition="outside",
        )
    )
    layout = _panel("Product Adoption Rate (% of all users)")
    layout["xaxis"]["title"] = "% Users"
    layout["xaxis"]["range"] = [0, max(rates) * 1.25] if rates else [0, 100]
    fig.update_layout(**layout)
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
    products = ["has_conversion", "has_card", "has_swap", "has_crossborder"]
    segs = ["champion", "active", "at_risk", "dormant"]
    z = []
    for seg in segs:
        grp = merged[merged["segment"] == seg]
        row = [grp[p].mean() * 100 if p in grp.columns else 0.0 for p in products]
        z.append(row)
    labels = ["Conversion", "Card", "Swap", "Cross-border"]
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
        cohort_active_users = _get(self._r, "cohort_active_users")
        cohort_retention = _get(self._r, "cohort_retention")
        cohort_total_profit = _get(self._r, "cohort_total_profit")
        ltv_by_source = self._r.get("ltv_by_source", {})
        segments = _get(self._r, "segments")
        cac_be = _get(self._r, "cac_breakeven")

        # --- KPI computations ---
        # CAC (incremental)
        cac_usd = self._r.get("weighted_cac_usd", float("nan"))
        if cac_usd is None:
            cac_usd = float("nan")

        # LTV at M+6
        ltv_m6 = float("nan")
        if not _empty(cohort_ltv) and 6 in cohort_ltv.columns:
            ltv_m6 = float(cohort_ltv[6].mean(skipna=True))

        # LTV:CAC
        if not math.isnan(ltv_m6) and not math.isnan(cac_usd) and cac_usd > 0:
            ltv_cac = ltv_m6 / cac_usd
        else:
            ltv_cac = float("nan")

        # Payback period (best-case across sources)
        payback_mo = None
        if not _empty(cac_be) and "payback_months" in cac_be.columns:
            valid = cac_be["payback_months"].dropna()
            if not valid.empty:
                payback_mo = int(valid.iloc[0])

        # M+1 retention
        ret_m1 = float("nan")
        if not _empty(cohort_retention) and 1 in cohort_retention.columns:
            ret_m1 = float(cohort_retention[1].mean(skipna=True)) * 100

        # Top 10% revenue concentration
        top10_pct = 0.0
        if not _empty(segments) and "net_revenue_usd" in segments.columns:
            rev_all = segments["net_revenue_usd"].dropna()
            rev_pos = rev_all[rev_all > 0]
            if len(rev_pos) > 0:
                n_top = max(1, len(rev_pos) // 10)
                top10_pct = float(rev_pos.nlargest(n_top).sum() / rev_pos.sum() * 100)

        # DAU from activity_kpis
        activity = self._r.get("activity_kpis") or {}
        dau = int(activity.get("dau", 0))

        # KPI strip
        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        c1.metric("Avg Daily Active Users", f"{dau:,}")
        c2.metric(
            "CAC (incremental)",
            _fmt_usd(cac_usd) if not math.isnan(cac_usd) else "n/a",
        )
        c3.metric(
            "LTV at M+6",
            _fmt_usd(ltv_m6) if not math.isnan(ltv_m6) else "n/a",
        )
        c4.metric(
            "LTV:CAC",
            f"{ltv_cac:.1f}×" if not math.isnan(ltv_cac) else "n/a",
            delta="target ≥ 3×",
        )
        c5.metric(
            "Payback Period",
            f"M+{payback_mo}" if payback_mo is not None else "n/a",
            delta="target ≤ M+6",
        )
        c6.metric(
            "M+1 Retention",
            f"{ret_m1:.1f}%" if not math.isnan(ret_m1) else "n/a",
            delta="target ≥ 30%",
        )
        c7.metric("Top 10% Revenue Share", f"{top10_pct:.1f}%")

        st.divider()

        # Row 1 — avg net heatmap
        st.caption(
            "Average cumulative net profit per active user, by cohort and months since signup."
        )
        fig_net = _fig_ltv_heatmap(cohort_ltv, title="Cohort Profit — Avg Net (USD)")
        if fig_net:
            st.plotly_chart(fig_net, width="stretch")
        else:
            st.info("No cohort LTV data available for the selected period.")

        # Row 2 — cohort total profit heatmap
        st.caption(
            "Total cumulative net profit for the whole cohort (not per-user average). "
            "Larger values mean that cohort has generated more absolute profit for the company."
        )
        fig_total_profit = _fig_ltv_heatmap(
            cohort_total_profit,
            title="Cohort Profit — Total Cumulative Net (USD)",
            colorbar_title="Total Net (USD)",
            zmin=None,
        )
        if fig_total_profit:
            st.plotly_chart(fig_total_profit, width="stretch")

        # Row 2b — active users per cohort heatmap
        st.caption(
            "How many users in each cohort are still active at each month since signup? "
            "A steep drop in the first 1–2 months signals an onboarding or activation problem."
        )
        fig_active_users = _fig_ltv_heatmap(
            cohort_active_users,
            title="Active Users by Cohort (Monthly)",
            colorbar_title="Users",
            zmin=0,
            value_fmt="{v:.0f}",
        )
        if fig_active_users:
            st.plotly_chart(fig_active_users, width="stretch")

        # Row 2c — Avg DAU per cohort heatmap
        cohort_avg_dau = _get(self._r, "cohort_avg_dau")
        st.caption(
            "Average number of distinct users active per day within each cohort-month. "
            "Low DAU relative to monthly actives means sporadic usage — "
            "a signal to improve engagement loops."
        )
        fig_avg_dau = _fig_ltv_heatmap(
            cohort_avg_dau,
            title="Avg Daily Active Users by Cohort (Monthly)",
            colorbar_title="Avg DAU",
            zmin=0,
            value_fmt="{v:.1f}",
        )
        if fig_avg_dau:
            st.plotly_chart(fig_avg_dau, width="stretch")

        # Row 3 — LTV curves by acquisition source
        st.caption(
            "Which channel brings the highest-value users over time? "
            "A steeper slope means faster revenue ramp — prioritise those channels."
        )
        fig_ltv = _fig_ltv_curves(ltv_by_source)
        if fig_ltv:
            st.plotly_chart(fig_ltv, width="stretch")

        # Row 4 — Retention curves
        st.subheader("Cohort Retention")
        st.caption("Are users staying? M+1 retention below 30% = stop scaling acquisition.")
        fig_ret = _fig_retention_curves(cohort_retention)
        if fig_ret:
            st.plotly_chart(fig_ret, width="stretch")
        elif _empty(cohort_retention):
            st.info("No retention data for this period.")

        # Row 5 — CAC payback + Lorenz concentration side by side
        st.caption(
            "Payback > M+6 means we wait too long to recoup acquisition cost. "
            "A steep Lorenz curve means revenue is concentrated in a few users — "
            "healthy if those users are sticky, dangerous if they churn."
        )
        col_x, col_y = st.columns(2)
        fig_payback = _fig_cac_payback(cac_be)
        fig_lorenz = _fig_lorenz(segments)
        if fig_payback:
            col_x.plotly_chart(fig_payback, width="stretch")
        if fig_lorenz:
            col_y.plotly_chart(fig_lorenz, width="stretch")

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
        st.caption(
            "Which channels bring users who actually spend? "
            "High conversion rate + high avg revenue = the channel to double-down on."
        )
        col1, col2 = st.columns(2)
        with col1:
            fig = _fig_acquisition_bar(acq)
            if fig:
                st.plotly_chart(fig, width="stretch")
        with col2:
            fig2 = _fig_funnel(acq)
            if fig2:
                st.plotly_chart(fig2, width="stretch")

        st.subheader("Acquisition Source Summary")
        display = acq.copy()
        for col in ["avg_net_revenue_usd", "median_net_revenue_usd", "total_net_revenue_usd"]:
            if col in display.columns:
                display[col] = display[col].apply(lambda v: f"${v:,.2f}")
        if "conversion_rate" in display.columns:
            display["conversion_rate"] = (display["conversion_rate"] * 100).round(1).astype(
                str
            ) + "%"
        st.dataframe(display, width="stretch", hide_index=True)

        if not _empty(ref):
            st.divider()
            st.subheader("Referral Code Performance")
            st.caption(
                "Net ARPU after commission shows whether the referral cost is justified. "
                "Negative net ARPU = we're paying more to acquire the user than they generate."
            )
            display_ref = ref.copy()
            for col in [
                "avg_net_revenue_usd",
                "avg_commission_cost_usd",
                "net_arpu_after_commission",
            ]:
                if col in display_ref.columns:
                    display_ref[col] = display_ref[col].apply(lambda v: f"${v:,.2f}")
            st.dataframe(display_ref, width="stretch", hide_index=True)

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

        st.caption(
            "Champions are your revenue engine — protect them. "
            "At-risk users are lapsing but still valuable; outreach costs less than re-acquisition."
        )
        col1, col2 = st.columns([1, 2])
        with col1:
            fig = _fig_segment_donut(summary)
            if fig:
                st.plotly_chart(fig, width="stretch")
        with col2:
            if not _empty(summary):
                st.dataframe(summary, width="stretch", hide_index=True)

        if not _empty(leaderboard):
            st.subheader("Champion Leaderboard (Top 50 by Net Revenue)")
            st.caption(
                "Your highest-value users. Know who they are and ensure they're being served well."
            )
            st.dataframe(leaderboard, width="stretch", hide_index=True)

        if not _empty(at_risk):
            st.divider()
            st.subheader("At-Risk Users — Outreach Targets")
            st.caption("Users inactive 30–90 days with meaningful revenue. user_id masked.")
            st.dataframe(at_risk.head(50), width="stretch", hide_index=True)

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

        st.caption(
            "Founders with large networks but low revenue are untapped — "
            "they recruited users who never transacted. Target them for activation."
        )
        col1, col2 = st.columns(2)
        with col1:
            fig = _fig_founders_scatter(founders)
            if fig:
                st.plotly_chart(fig, width="stretch")
        with col2:
            if "invites_remaining" in founders.columns:
                unused_cols = [
                    "user_id",
                    "referral_code",
                    "referral_code_name",
                    "invites_remaining",
                    "net_revenue_usd",
                ]
                unused_present = [c for c in unused_cols if c in founders.columns]
                top_unused = founders[founders["invites_remaining"] > 0].nlargest(
                    20, "invites_remaining"
                )[unused_present]
                if not top_unused.empty:
                    st.subheader("Under-leveraged Founders (unused invites)")
                    st.dataframe(top_unused, width="stretch", hide_index=True)

        st.subheader("Founders Leaderboard (Top 20 by Revenue)")
        st.caption("Revenue breakdown per founder shows which product drives their value.")
        cols = [
            "user_id",
            "referral_code",
            "referral_code_name",
            "founder_number",
            "founder_network_size",
            "net_revenue_usd",
            # --- revenue sources ---
            "onramp_revenue_usd",
            "offramp_revenue_usd",
            "card_fee_usd",
            "card_tx_fee_usd",
            "swap_fee_usd",
            # --- deductions ---
            "cashback_usd",
            "revenue_share_paid_usd",
            "card_cost_allocated_usd",
            # --- other ---
            "n_products",
        ]
        present = [c for c in cols if c in founders.columns]
        st.dataframe(founders[present].head(20), width="stretch", hide_index=True)

    # ------------------------------------------------------------------
    # Tab 5 — Product Adoption
    # ------------------------------------------------------------------

    def _render_adoption(self) -> None:
        adoption = _get(self._r, "product_adoption")
        segments = _get(self._r, "segments")
        funnel = self._r.get("activation_funnel", {})

        # -- Activation funnel --
        st.subheader("User Activation Funnel")
        st.caption(
            "Signed up but not KYC'd = lost before they started. "
            "KYC'd but never transacted = activation failure. Fix the leakiest stage first."
        )
        fig_funnel = _fig_activation_funnel(funnel)
        if fig_funnel:
            st.plotly_chart(fig_funnel, width="stretch")
            if funnel:
                total = funnel.get("total_users", 1) or 1
                kyc = funnel.get("kyc_done", 0)
                active = funnel.get("active_users", 0)
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Users", f"{total:,}")
                c2.metric("KYC Done", f"{kyc:,}", delta=f"{100 * kyc / total:.1f}% of total")
                c3.metric(
                    "Active (any txn)",
                    f"{active:,}",
                    delta=f"{100 * active / total:.1f}% of total",
                )
        else:
            st.info("No activation funnel data — run ClientReport.build() with DB access.")

        if _empty(adoption):
            st.info("No product adoption data available.")
            return

        st.divider()

        # -- Product adoption rates --
        st.subheader("Product Adoption")
        st.caption(
            "Low adoption on a product means users don't know it exists or see no value in it. "
            "Multi-product users have higher retention — cross-sell is a retention lever."
        )
        fig_bars = _fig_product_adoption_bars(adoption)
        if fig_bars:
            st.plotly_chart(fig_bars, width="stretch")

        # KPI row
        kpi_map = {
            "has_conversion": "Conversion (On/Off Ramp)",
            "has_card": "Card",
            "has_swap": "Swap",
            "has_crossborder": "Cross-border",
        }
        present_kpis = [(col, label) for col, label in kpi_map.items() if col in adoption.columns]
        if present_kpis:
            cols = st.columns(len(present_kpis))
            for i, (col, label) in enumerate(present_kpis):
                cols[i].metric(f"{label}", f"{adoption[col].mean() * 100:.1f}%")

        st.divider()

        # -- Segment heatmap --
        st.caption(
            "Champions using only one product are your best cross-sell targets. "
            "Dormant users with high adoption had the intent — find out why they stopped."
        )
        fig_heat = _fig_adoption_heatmap(adoption, segments)
        if fig_heat:
            st.plotly_chart(fig_heat, width="stretch")

        if "n_products" in adoption.columns:
            st.subheader("Product Combination Distribution")
            combo_counts = adoption["n_products"].value_counts().sort_index()
            fig_combo = go.Figure(
                go.Bar(
                    x=[f"{n} product(s)" for n in combo_counts.index],
                    y=combo_counts.values,
                    marker_color=TEAL,
                )
            )
            fig_combo.update_layout(**_panel("Users by Number of Products Used"))
            fig_combo.update_yaxes(title="User Count")
            st.plotly_chart(fig_combo, width="stretch")
