"""Meta Ads ROI dashboard tab — Tab 6 (Marketing - Ads).

Wraps ``CampaignAnalyzer`` and ``ClientReport`` public APIs into Streamlit
components.  No DB queries are issued from this module — all data arrives
through the caller.

Usage::

    from nbs_bi.reporting.marketing import MetaAdsSection

    MetaAdsSection(
        campaign_data=report.get("campaign_roi"),
        acquisition=report.get("acquisition"),
        db_url=db_url,
    ).render()
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from nbs_bi.reporting.theme import (
    AMBER,
    BLUE,
    EMERALD,
    ROSE,
    TEAL,
    TEXT_MUTED,
    VIOLET,
    fmt_usd,
    get_streamlit,
    panel,
)

st = get_streamlit()


def _fmt_usd_safe(v: object) -> str:
    """Format USD value, returning '—' for NaN or None."""
    if v is None:
        return "—"
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return fmt_usd(f) if pd.notna(f) else "—"


# Earliest date for which ad spend tracking is considered reliable.
# Spend before this date is excluded from campaign detection and ROI analysis.
_TRACKING_START = "2026-04-12"

# Meta Ads row colour in channel comparison chart.
_META_COLOR = ROSE

# Channel colour map (extends theme SOURCE_COLORS with meta_ads).
_CHANNEL_COLORS: dict[str, str] = {
    "meta_ads": _META_COLOR,
    "founder_invite": EMERALD,
    "referral": BLUE,
    "organic": AMBER,
    "unknown": TEXT_MUTED,
}


# ---------------------------------------------------------------------------
# Pure data-transform functions
# ---------------------------------------------------------------------------


def _build_cumulative_spend(
    spend_df: pd.DataFrame,
    campaigns: list[dict],
) -> pd.DataFrame:
    """Add cumulative spend and campaign-start flags to a daily spend DataFrame.

    Args:
        spend_df: Output of ``load_ad_spend()`` — columns ``date``,
            ``daily_spend_usd``.
        campaigns: List of campaign dicts from ``CampaignAnalyzer.campaigns``
            — each has ``campaign_id``, ``start``, ``end``.

    Returns:
        DataFrame with columns ``date``, ``daily_spend_usd``,
        ``cumulative_spend_usd``, ``is_campaign_start``, ``campaign_id``.
    """
    if spend_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "daily_spend_usd",
                "cumulative_spend_usd",
                "is_campaign_start",
                "campaign_id",
            ]
        )
    df = spend_df.copy().sort_values("date").reset_index(drop=True)
    df["cumulative_spend_usd"] = df["daily_spend_usd"].cumsum()

    start_dates = {str(c["start"]): c["campaign_id"] for c in campaigns}
    df["date_str"] = df["date"].astype(str)
    df["is_campaign_start"] = df["date_str"].isin(start_dates)
    df["campaign_id"] = df["date_str"].map(start_dates).fillna("")
    return df.drop(columns=["date_str"])


def _build_channel_comparison(
    summary: pd.DataFrame,
    acquisition: pd.DataFrame,
    cum_profit_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Merge Meta Ads campaign metrics with acquisition_summary rows.

    Args:
        summary: Output of ``CampaignAnalyzer.roi_summary()``.
        acquisition: Output of ``ClientReport.build()["acquisition"]``.
        cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()`` —
            when provided, the Meta Ads row uses ``cum_profit_usd`` (revenue
            minus card COGS and KYC cost) instead of gross revenue, making
            the metric comparable with other channels' ``net_revenue_usd``.

    Returns:
        DataFrame with schema: ``acquisition_source``, ``n_users``,
        ``avg_operational_profit_usd``, ``total_operational_profit_usd``,
        ``conversion_rate``, ``spend_usd``, ``roas``.
        Meta Ads row is prepended; other channels have NaN for
        ``spend_usd`` and ``roas``.
    """
    total_spend = float(summary["total_spend_usd"].sum()) if not summary.empty else 0.0
    n_users = int(summary["cohort_users"].sum()) if not summary.empty else 0
    transacting = int(summary["transacting_users"].sum()) if not summary.empty else 0

    _has_profit = (
        cum_profit_df is not None
        and not cum_profit_df.empty
        and "cum_profit_usd" in cum_profit_df.columns
    )
    if _has_profit:
        total_op_profit = float(cum_profit_df["cum_profit_usd"].iloc[-1])  # type: ignore[index]
    else:
        total_op_profit = float(summary["total_revenue_usd"].sum()) if not summary.empty else 0.0

    meta_row = {
        "acquisition_source": "meta_ads",
        "n_users": n_users,
        "avg_operational_profit_usd": total_op_profit / n_users if n_users > 0 else 0.0,
        "total_operational_profit_usd": total_op_profit,
        "conversion_rate": transacting / n_users if n_users > 0 else 0.0,
        "spend_usd": total_spend,
        "roas": total_op_profit / total_spend if total_spend > 0 else np.nan,
    }

    if acquisition is not None and not acquisition.empty:
        acq = acquisition.rename(
            columns={
                "avg_net_revenue_usd": "avg_operational_profit_usd",
                "total_net_revenue_usd": "total_operational_profit_usd",
            }
        ).copy()
    else:
        acq = pd.DataFrame()
    acq["spend_usd"] = np.nan
    acq["roas"] = np.nan

    return pd.concat([pd.DataFrame([meta_row]), acq], ignore_index=True)


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------


def _fig_cumulative_spend(
    cum_df: pd.DataFrame,
    campaigns: list[dict],
    cum_rev_df: pd.DataFrame | None = None,
    cum_profit_df: pd.DataFrame | None = None,
) -> go.Figure | None:
    """Line chart of cumulative spend, cohort revenue, and profit with campaign markers.

    Args:
        cum_df: Output of :func:`_build_cumulative_spend`.
        campaigns: Campaign dicts from ``CampaignAnalyzer.campaigns``.
        cum_rev_df: Output of ``CampaignAnalyzer.cumulative_revenue()`` — optional
            revenue overlay for the most recent campaign's cohort.
        cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()`` — optional
            profit overlay (revenue − COGS − ad spend) for the latest cohort.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if cum_df.empty:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=cum_df["date"].astype(str),
            y=cum_df["cumulative_spend_usd"],
            mode="lines",
            line=dict(color=ROSE, width=2),
            name="Cumulative Spend (USD)",
            hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
        )
    )
    if cum_rev_df is not None and not cum_rev_df.empty and "cum_rev_usd" in cum_rev_df.columns:
        fig.add_trace(
            go.Scatter(
                x=cum_rev_df["date"].astype(str),
                y=cum_rev_df["cum_rev_usd"],
                mode="lines",
                line=dict(color=EMERALD, width=2),
                name="Cumulative Revenue — latest cohort (USD)",
                hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
            )
        )
    _has_profit = (
        cum_profit_df is not None
        and not cum_profit_df.empty
        and "cum_contribution_margin_usd" in cum_profit_df.columns
    )
    if _has_profit:
        fig.add_trace(
            go.Scatter(
                x=cum_profit_df["date"].astype(str),  # type: ignore[union-attr]
                y=cum_profit_df["cum_contribution_margin_usd"],  # type: ignore[index]
                mode="lines",
                line=dict(color=VIOLET, width=2, dash="dot"),
                name="Cumulative Contribution Margin — latest cohort (USD)",
                hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
            )
        )
        fig.add_hline(
            y=0,
            line_dash="dash",
            line_color=TEXT_MUTED,
            line_width=1,
            opacity=0.6,
        )
    for c in campaigns:
        x_str = str(c["start"])
        fig.add_shape(
            type="line",
            x0=x_str,
            x1=x_str,
            y0=0,
            y1=1,
            xref="x",
            yref="paper",
            line=dict(color=TEXT_MUTED, width=1, dash="dash"),
        )
        fig.add_annotation(
            x=x_str,
            y=1,
            xref="x",
            yref="paper",
            text=c["campaign_id"],
            showarrow=False,
            yanchor="bottom",
            font=dict(size=10, color=TEXT_MUTED),
        )
    spend_days = cum_df[cum_df["daily_spend_usd"] > 0]
    for _, row in spend_days.iterrows():
        x_str = str(row["date"])
        fig.add_vline(
            x=x_str,
            line_dash="dash",
            line_color=ROSE,
            line_width=1,
            opacity=0.35,
        )
        fig.add_annotation(
            x=x_str,
            y=1.0,
            yref="paper",
            text=_fmt_usd_safe(row["daily_spend_usd"]),
            showarrow=False,
            textangle=-90,
            font=dict(size=9, color=ROSE),
            xanchor="left",
            yanchor="top",
        )
    layout = panel("Cumulative Ad Spend vs Cohort Revenue (USD)")
    layout["xaxis"]["title"] = "Date"
    layout["yaxis"]["title"] = "USD"
    fig.update_layout(**layout)
    return fig


def _fig_campaign_roi(
    summary: pd.DataFrame,
    cum_profit_df: pd.DataFrame | None = None,
) -> go.Figure | None:
    """Grouped bar: ad spend vs cohort revenue per campaign, with profit line overlay.

    Args:
        summary: Output of ``CampaignAnalyzer.roi_summary()``.
        cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()`` — used
            to compute total net profit for the latest campaign overlay.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if summary.empty:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=summary["campaign_id"],
            y=summary["total_spend_usd"],
            name="Ad Spend",
            marker_color=ROSE,
            text=summary["total_spend_usd"].apply(_fmt_usd_safe),
            textposition="outside",
        )
    )
    fig.add_trace(
        go.Bar(
            x=summary["campaign_id"],
            y=summary["total_revenue_usd"],
            name="Cohort Revenue",
            marker_color=EMERALD,
            text=summary["total_revenue_usd"].apply(_fmt_usd_safe),
            textposition="outside",
        )
    )
    _has_profit = (
        cum_profit_df is not None
        and not cum_profit_df.empty
        and "cum_contribution_margin_usd" in cum_profit_df.columns
    )
    if _has_profit:
        net_profit = float(cum_profit_df["cum_contribution_margin_usd"].iloc[-1])  # type: ignore[union-attr]
        latest_id = summary["campaign_id"].iloc[-1]
        fig.add_trace(
            go.Scatter(
                x=[latest_id],
                y=[net_profit],
                mode="markers+text",
                marker=dict(color=VIOLET, size=12, symbol="diamond"),
                text=[_fmt_usd_safe(net_profit)],
                textposition="top center",
                name="Net Profit (latest cohort)",
            )
        )
        fig.add_hline(
            y=0,
            line_dash="dash",
            line_color=TEXT_MUTED,
            line_width=1,
            opacity=0.6,
        )
    layout = panel("Ad Spend vs Cohort Revenue by Campaign")
    layout["barmode"] = "group"
    layout["yaxis"]["title"] = "USD"
    fig.update_layout(**layout)
    return fig


def _fig_cumulative_profit(cum_profit_df: pd.DataFrame) -> go.Figure | None:
    """Line chart of cumulative revenue, card program cost, and net profit.

    Shows three USD lines on the left axis and cumulative card transaction
    count on a secondary right axis, so the driver of card-program COGS is
    immediately visible.

    Args:
        cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()`` —
            must contain ``date``, ``cum_rev_usd``, ``cum_card_cogs_usd``,
            ``cum_profit_usd``, and ``cum_txn_count`` columns.

    Returns:
        Plotly Figure or None if data is empty or missing required columns.
    """
    required = {
        "date",
        "cum_rev_usd",
        "cum_card_cogs_usd",
        "cum_contribution_margin_usd",
        "cum_profit_usd",
        "cum_txn_count",
        "cum_conversion_count",
    }
    if cum_profit_df.empty or not required.issubset(cum_profit_df.columns):
        return None

    x = cum_profit_df["date"].astype(str)
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=x,
            y=cum_profit_df["cum_rev_usd"],
            mode="lines",
            line=dict(color=EMERALD, width=2),
            name="Cumulative Revenue (USD)",
            yaxis="y1",
            hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=cum_profit_df["cum_card_cogs_usd"],
            mode="lines",
            line=dict(color=ROSE, width=2, dash="dot"),
            name="Cumulative Card Program Cost (USD)",
            yaxis="y1",
            hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=cum_profit_df["cum_contribution_margin_usd"],
            mode="lines",
            line=dict(color=VIOLET, width=1.5, dash="dot"),
            name="Cumulative Contribution Margin (USD)",
            yaxis="y1",
            hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=cum_profit_df["cum_profit_usd"],
            mode="lines",
            line=dict(color=VIOLET, width=2),
            name="Cumulative Operational Profit (USD)",
            yaxis="y1",
            hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=cum_profit_df["cum_txn_count"],
            mode="lines",
            line=dict(color=TEAL, width=1, dash="dot"),
            name="Cumulative Card Transactions",
            yaxis="y2",
            hovertemplate="%{x}: %{y:,.0f} txns<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=cum_profit_df["cum_conversion_count"],
            mode="lines",
            line=dict(color=AMBER, width=1, dash="dot"),
            name="Cumulative Conversions (BRL↔USDC)",
            yaxis="y2",
            hovertemplate="%{x}: %{y:,.0f} conversions<extra></extra>",
        )
    )
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color=TEXT_MUTED,
        line_width=1,
        opacity=0.6,
        annotation_text="breakeven",
        annotation_position="bottom right",
        annotation_font_size=10,
        annotation_font_color=TEXT_MUTED,
    )
    layout = panel("Operational Profit & Contribution Margin — Latest Cohort (USD)")
    layout["xaxis"]["title"] = "Date"
    layout["yaxis"]["title"] = "USD"
    layout["yaxis2"] = {
        "title": "Transactions",
        "overlaying": "y",
        "side": "right",
        "showgrid": False,
        "tickformat": ",.0f",
    }
    fig.update_layout(**layout)
    return fig


def _fig_revenue_breakdown(cum_profit_df: pd.DataFrame) -> go.Figure | None:
    """Stacked area chart of cumulative revenue by source for the latest cohort.

    Shows four stacked positive revenue streams plus two cost deduction lines
    (cashback, rev share) so the composition of cohort revenue is visible.

    Args:
        cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()`` —
            must contain ``date``, ``cum_rev_conversion_usd``,
            ``cum_rev_card_fees_usd``, ``cum_rev_billing_usd``,
            ``cum_cost_cashback_usd``, ``cum_cost_rev_share_usd``.

    Returns:
        Plotly Figure or None if data is empty or missing required columns.
    """
    required = {
        "date",
        "cum_rev_conversion_usd",
        "cum_rev_card_fees_usd",
        "cum_rev_billing_usd",
        "cum_cost_cashback_usd",
        "cum_cost_rev_share_usd",
    }
    if cum_profit_df.empty or not required.issubset(cum_profit_df.columns):
        return None

    x = cum_profit_df["date"].astype(str)
    fig = go.Figure()

    for col, color, label in [
        ("cum_rev_conversion_usd", EMERALD, "Conversion Spread"),
        ("cum_rev_card_fees_usd", TEAL, "Card Annual Fees"),
        ("cum_rev_billing_usd", BLUE, "Billing Charges"),
    ]:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=cum_profit_df[col],
                mode="lines",
                stackgroup="revenue",
                line=dict(color=color, width=1),
                name=label,
                hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
            )
        )

    for col, color, label in [
        ("cum_cost_cashback_usd", ROSE, "Cashback Cost"),
        ("cum_cost_rev_share_usd", VIOLET, "Rev Share Cost"),
    ]:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=-cum_profit_df[col],
                mode="lines",
                line=dict(color=color, width=1, dash="dot"),
                name=label,
                hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
            )
        )

    layout = panel("Cumulative Revenue Breakdown — Latest Cohort (USD)")
    layout["xaxis"]["title"] = "Date"
    layout["yaxis"]["title"] = "USD"
    fig.update_layout(**layout)
    return fig


def _fig_campaign_cac(summary: pd.DataFrame) -> go.Figure | None:
    """Bar chart: full-cohort CAC vs incremental CAC per campaign."""
    if summary.empty or "cac_full" not in summary.columns:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=summary["campaign_id"],
            y=summary["cac_full"],
            name="CAC (active users)",
            marker_color=AMBER,
            text=summary["cac_full"].apply(lambda v: f"${v:.2f}" if pd.notna(v) else "n/a"),
            textposition="outside",
        )
    )
    valid = summary["cac_incremental"].notna()
    if valid.any():
        fig.add_trace(
            go.Bar(
                x=summary.loc[valid, "campaign_id"],
                y=summary.loc[valid, "cac_incremental"],
                name="CAC (incremental est.)",
                marker_color=VIOLET,
                text=summary.loc[valid, "cac_incremental"].apply(lambda v: f"${v:.2f}"),
                textposition="outside",
            )
        )
    layout = panel("Customer Acquisition Cost (USD)")
    layout["barmode"] = "group"
    layout["yaxis"]["title"] = "CAC (USD / User)"
    fig.update_layout(**layout)
    return fig


def _fig_campaign_daily(daily: pd.DataFrame) -> go.Figure | None:
    """Dual-axis chart: daily signups (stacked bar) + ad spend (line)."""
    if daily.empty:
        return None
    fig = go.Figure()
    colors = [ROSE, VIOLET, AMBER, TEAL]
    for i, cid in enumerate(daily["campaign_id"].unique()):
        if not cid:
            continue
        mask = daily["campaign_id"] == cid
        fig.add_trace(
            go.Bar(
                x=daily.loc[mask, "date"].astype(str),
                y=daily.loc[mask, "new_signups"],
                name=f"{cid} signups",
                marker_color=colors[i % len(colors)],
                opacity=0.6,
            )
        )
    organic = ~daily["is_campaign"]
    fig.add_trace(
        go.Bar(
            x=daily.loc[organic, "date"].astype(str),
            y=daily.loc[organic, "new_signups"],
            name="Organic signups",
            marker_color=BLUE,
            opacity=0.4,
        )
    )
    spending = daily["daily_spend_usd"] > 0
    fig.add_trace(
        go.Scatter(
            x=daily.loc[spending, "date"].astype(str),
            y=daily.loc[spending, "daily_spend_usd"],
            name="Ad spend (USD)",
            mode="lines+markers",
            line=dict(color=ROSE, width=2, dash="dot"),
            yaxis="y2",
        )
    )
    layout = panel("Daily Signups vs Ad Spend")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "New Signups"
    layout["xaxis"]["title"] = "Date"
    layout["yaxis2"] = dict(
        title="Daily Spend (USD)",
        overlaying="y",
        side="right",
        gridcolor="rgba(0,0,0,0)",
    )
    fig.update_layout(**layout)
    return fig


def _fig_daily_revenue_vs_spend(
    cum_rev_df: pd.DataFrame,
    spend_agg: pd.DataFrame,
) -> go.Figure | None:
    """Stacked bars: daily revenue by product line + total ad spend on right axis."""
    rev_cols = [
        ("daily_rev_conversion_usd", "Conversion", TEAL),
        ("daily_rev_card_fees_usd", "Card Fees", AMBER),
        ("daily_rev_billing_usd", "Billing", VIOLET),
        ("daily_rev_swap_usd", "Swap Fees", BLUE),
    ]
    available = [(c, label, color) for c, label, color in rev_cols if c in cum_rev_df.columns]
    if not available:
        return None

    rev = cum_rev_df[["date"] + [c for c, _, _ in available]].copy()
    rev["date"] = pd.to_datetime(rev["date"]).dt.normalize()

    spend = spend_agg[["date", "daily_spend_usd"]].copy()
    spend["date"] = pd.to_datetime(spend["date"]).dt.normalize()

    merged = rev.merge(spend, on="date", how="outer").sort_values("date").fillna(0.0)

    fig = go.Figure()
    for col, lbl, color in available:
        fig.add_trace(
            go.Bar(
                x=merged["date"].astype(str),
                y=merged[col],
                name=lbl,
                marker_color=color,
            )
        )

    has_spend = merged["daily_spend_usd"].gt(0).any()
    if has_spend:
        fig.add_trace(
            go.Scatter(
                x=merged["date"].astype(str),
                y=merged["daily_spend_usd"],
                name="Total Ad Spend (USD)",
                mode="lines+markers",
                line=dict(color=ROSE, width=2, dash="dot"),
                yaxis="y2",
            )
        )

    layout = panel("Daily Revenue vs Ad Spend")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "Revenue (USD)"
    layout["xaxis"]["title"] = "Date"
    if has_spend:
        layout["yaxis2"] = dict(
            title="Ad Spend (USD)",
            overlaying="y",
            side="right",
            gridcolor="rgba(0,0,0,0)",
        )
    fig.update_layout(**layout)
    return fig


def _fig_daily_rev_all_vs_cohort(
    all_users_df: pd.DataFrame,
    cohort_df: pd.DataFrame,
    spend_agg: pd.DataFrame,
) -> go.Figure | None:
    """Stacked bars: non-cohort platform revenue (muted) + cohort revenue piled on top.

    Args:
        all_users_df: Output of ``CampaignAnalyzer.all_users_daily_revenue()``.
        cohort_df: Output of ``CampaignAnalyzer.cumulative_revenue()`` — cohort only.
        spend_agg: Aggregated spend DataFrame with columns ``date``, ``daily_spend_usd``.

    Returns:
        Plotly Figure or None if all_users_df is empty.
    """
    rev_cols = [
        ("daily_rev_conversion_usd", TEAL, "Conversion"),
        ("daily_rev_card_fees_usd", AMBER, "Card Fees"),
        ("daily_rev_billing_usd", VIOLET, "Billing"),
        ("daily_rev_swap_usd", BLUE, "Swap Fees"),
    ]
    available = [(c, color, lbl) for c, color, lbl in rev_cols if c in all_users_df.columns]
    if not available or all_users_df.empty:
        return None

    all_rev = all_users_df[["date"] + [c for c, _, _ in available]].copy()
    all_rev["date"] = pd.to_datetime(all_rev["date"]).dt.normalize()

    spend = spend_agg[["date", "daily_spend_usd"]].copy()
    spend["date"] = pd.to_datetime(spend["date"]).dt.normalize()

    merged = all_rev.merge(spend, on="date", how="outer").sort_values("date").fillna(0.0)

    # Align cohort to merged date index
    coh = pd.DataFrame({"date": merged["date"]})
    if not cohort_df.empty:
        _coh_cols = ["date"] + [col for col, _, _ in available if col in cohort_df.columns]
        c = cohort_df[_coh_cols].copy()
        c["date"] = pd.to_datetime(c["date"]).dt.normalize()
        coh = coh.merge(c, on="date", how="left").fillna(0.0)
    for col, _, _ in available:
        if col not in coh.columns:
            coh[col] = 0.0

    dates_str = merged["date"].astype(str)
    fig = go.Figure()

    # Layer 1: non-cohort portion (all_users minus cohort), muted
    for col, color, lbl in available:
        rest = (merged[col] - coh[col]).clip(lower=0)
        fig.add_trace(
            go.Bar(
                x=dates_str,
                y=rest,
                name=f"{lbl}",
                marker_color=color,
                opacity=0.35,
                marker_line_width=0,
                legendgroup="other",
                legendgrouptitle_text="Other Users",
            )
        )

    # Layer 2: cohort revenue piled on top, full opacity
    for col, color, lbl in available:
        fig.add_trace(
            go.Bar(
                x=dates_str,
                y=coh[col],
                name=f"{lbl}",
                marker_color=color,
                opacity=0.9,
                marker_line_width=0,
                legendgroup="cohort",
                legendgrouptitle_text="Cohort",
            )
        )

    # Layer 3: ad spend on right axis
    has_spend = merged["daily_spend_usd"].gt(0).any()
    if has_spend:
        fig.add_trace(
            go.Scatter(
                x=dates_str,
                y=merged["daily_spend_usd"],
                name="Total Ad Spend (USD)",
                mode="lines+markers",
                line=dict(color=ROSE, width=2, dash="dot"),
                yaxis="y2",
            )
        )

    layout = panel("Daily Platform Revenue: Other Users + Cohort Contribution")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "Revenue (USD)"
    layout["xaxis"]["title"] = "Date"
    if has_spend:
        layout["yaxis2"] = dict(
            title="Ad Spend (USD)",
            overlaying="y",
            side="right",
            gridcolor="rgba(0,0,0,0)",
        )
    fig.update_layout(**layout)
    return fig


def _fig_channel_comparison(comparison: pd.DataFrame) -> go.Figure | None:
    """Horizontal bar: avg operational profit per acquisition channel."""
    if comparison.empty:
        return None
    col = "avg_operational_profit_usd"
    if col not in comparison.columns:
        return None
    colors = [_CHANNEL_COLORS.get(s, TEXT_MUTED) for s in comparison["acquisition_source"]]
    texts = []
    for _, row in comparison.iterrows():
        label = _fmt_usd_safe(row[col])
        if pd.notna(row.get("roas")) and row["acquisition_source"] == "meta_ads":
            label += f"  ROAS {row['roas']:.2f}×"
        texts.append(label)
    fig = go.Figure(
        go.Bar(
            x=comparison[col],
            y=comparison["acquisition_source"],
            orientation="h",
            marker_color=colors,
            text=texts,
            textposition="outside",
        )
    )
    layout = panel("Avg Operational Profit (USD) by Acquisition Channel")
    layout["xaxis"]["title"] = "Avg Operational Profit (USD)"
    fig.update_layout(**layout)
    return fig


def _fig_channel_daily(daily: pd.DataFrame) -> go.Figure | None:
    """Multi-line chart of cumulative operational profit by acquisition source.

    Args:
        daily: Output of ``ClientModel.cumulative_profit_by_source()`` —
            columns ``signup_date``, ``acquisition_source``,
            ``cumulative_net_revenue_usd``.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if daily is None or daily.empty:
        return None
    fig = go.Figure()
    for source, grp in daily.groupby("acquisition_source"):
        color = _CHANNEL_COLORS.get(str(source), TEXT_MUTED)
        fig.add_trace(
            go.Scatter(
                x=grp["signup_date"].astype(str),
                y=grp["cumulative_net_revenue_usd"],
                mode="lines",
                name=str(source),
                line=dict(color=color, width=2),
                hovertemplate="%{x}: %{y:$,.2f}<extra>" + str(source) + "</extra>",
            )
        )
    layout = panel("Cumulative Operational Profit by Acquisition Channel")
    layout["xaxis"]["title"] = "Signup Date"
    layout["yaxis"]["title"] = "Cumulative Profit (USD)"
    fig.update_layout(**layout)
    return fig


def _fig_campaign_funnel(funnel: dict) -> go.Figure | None:
    """Funnel chart: Sign-ups → KYC Done → Activated for a campaign cohort.

    Args:
        funnel: Dict with keys ``signups``, ``kyc_done``, ``activated``.

    Returns:
        Plotly Figure or None if funnel is empty.
    """
    if not funnel or funnel.get("signups", 0) == 0:
        return None
    total = funnel["signups"] or 1
    labels = ["Sign-ups", "KYC Done", "Activated"]
    values = [funnel["signups"], funnel["kyc_done"], funnel["activated"]]
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
    layout = panel("Cohort Activation Funnel")
    layout.pop("xaxis", None)
    layout.pop("yaxis", None)
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Section class
# ---------------------------------------------------------------------------


class MetaAdsSection:
    """Renders the Meta Ads ROI tab (Tab 6 — Marketing - Ads).

    Args:
        campaign_data: Dict with keys ``"summary"`` (DataFrame from
            ``CampaignAnalyzer.roi_summary()``) and ``"daily"`` (DataFrame
            from ``CampaignAnalyzer.daily_context()``).  If None, a CSV
            file uploader is rendered and the section returns early.
        acquisition: DataFrame from ``ClientReport.build()["acquisition"]``.
            If None or empty, the channel comparison section is skipped.
        db_url: SQLAlchemy DB URL forwarded to ``CampaignAnalyzer`` when
            a CSV is uploaded at runtime.
    """

    def __init__(
        self,
        campaign_data: dict | None,
        acquisition: pd.DataFrame | None,
        db_url: str | None = None,
        analytics_db_url: str | None = None,
        profit_by_source_daily: pd.DataFrame | None = None,
    ) -> None:
        self._data = campaign_data
        self._acquisition = acquisition
        self._db_url = db_url
        self._analytics_db_url = analytics_db_url
        self._profit_by_source_daily = profit_by_source_daily

    def render(self) -> None:  # pragma: no cover
        """Render all Marketing - Ads tab components into the active Streamlit context."""
        campaign_data = self._data

        if campaign_data is None:
            campaign_data = self._try_upload()
            if campaign_data is None:
                return

        spend_df: pd.DataFrame = campaign_data.get("spend_df", pd.DataFrame())
        invoice_history: list = campaign_data.get("invoice_history", [])

        if spend_df.empty:
            st.warning("No ad spend rows found in the uploaded CSV.")
            return

        # --- Platform + date filters --------------------------------------------
        from nbs_bi.clients.campaigns import CampaignAnalyzer, aggregate_spend

        # Platform selector
        has_platform = "platform" in spend_df.columns
        available_platforms = sorted(spend_df["platform"].unique()) if has_platform else []
        platform_options = ["All"] + [p.capitalize() for p in available_platforms]
        selected_platform_label = st.radio(
            "Platform",
            platform_options,
            horizontal=True,
            key="ads_platform_filter",
        )
        selected_platform = (
            None if selected_platform_label == "All" else selected_platform_label.lower()
        )

        spend_dates = pd.to_datetime(spend_df["date"])
        min_date = spend_dates.min().date()
        max_date = spend_dates.max().date()

        import datetime as _dt

        _default_start = max(min_date, _dt.date(2026, 4, 25))

        # Reset date inputs when the data range grows (e.g. new platform rows added).
        _max_key = "ads_data_max_date"
        if st.session_state.get(_max_key) != str(max_date):
            st.session_state["ads_start_date"] = _default_start
            st.session_state["ads_end_date"] = max_date
            st.session_state[_max_key] = str(max_date)

        col_start, col_end = st.columns(2)
        with col_start:
            start_date = st.date_input(
                "Analysis start",
                value=_default_start,
                min_value=min_date,
                max_value=max_date,
                key="ads_start_date",
            )
        with col_end:
            end_date = st.date_input(
                "Analysis end",
                value=max_date,
                min_value=min_date,
                max_value=max_date,
                key="ads_end_date",
            )

        if start_date > end_date:
            st.error("Start date must be before end date.")
            return

        date_mask = (spend_dates.dt.date >= start_date) & (spend_dates.dt.date <= end_date)
        spend_df = spend_df[date_mask].reset_index(drop=True)
        if spend_df.empty:
            st.warning("No spend data in the selected date range.")
            return

        # Aggregate to date-level for CampaignAnalyzer (filter by platform if selected).
        spend_agg = aggregate_spend(spend_df, platform=selected_platform)
        if spend_agg.empty:
            st.warning("No spend data for the selected platform in this date range.")
            return

        # Per-platform breakdown for KPI tile.
        spend_breakdown: dict[str, float] = (
            spend_df.groupby("platform")["daily_spend_usd"].sum().to_dict()
            if "platform" in spend_df.columns
            else {}
        )

        # Rebuild analyzer scoped to the selected window/platform.
        analyzer = CampaignAnalyzer(spend_agg, db_url=self._analytics_db_url or self._db_url)
        campaigns: list[dict] = analyzer.campaigns
        summary: pd.DataFrame = analyzer.roi_summary()
        daily: pd.DataFrame = analyzer.daily_context()

        if summary.empty:
            st.warning("No campaign detected in the selected date range.")
            return

        # Focus all campaign-level charts on the most recent campaign only.
        # Cumulative spend (spend_df + campaigns) keeps full history.
        latest_id = summary["campaign_id"].iloc[-1]
        summary = summary[summary["campaign_id"] == latest_id].reset_index(drop=True)
        if not daily.empty and "campaign_id" in daily.columns:
            latest_start = pd.to_datetime(summary["start"].iloc[0])
            cutoff = latest_start - pd.Timedelta(days=14)
            daily = daily[pd.to_datetime(daily["date"]) >= cutoff].reset_index(drop=True)

        referral_code = ""
        if analyzer is not None:
            referral_options = analyzer.referral_code_options()
            if referral_options:
                options = ["All"] + referral_options
                selected = st.selectbox(
                    "Cohort filter — referral source",
                    options,
                    key="referral_filter",
                    help="Filter the cohort to users attributed to a specific referral source.",
                )
                referral_code = "" if selected == "All" else selected

        cum_rev_df = (
            analyzer.cumulative_revenue(latest_id, referral_code=referral_code)
            if analyzer is not None
            else pd.DataFrame()
        )
        cum_profit_df = (
            analyzer.cumulative_profit(latest_id, invoice_history, referral_code=referral_code)
            if analyzer is not None
            else pd.DataFrame()
        )

        all_users_rev_df = pd.DataFrame()
        if analyzer is not None and cum_rev_df is not None and not cum_rev_df.empty:
            _au_start = str(cum_rev_df["date"].min().date())
            _au_end = str((pd.Timestamp.today().normalize() + pd.Timedelta(days=1)).date())
            try:
                all_users_rev_df = analyzer.all_users_daily_revenue(_au_start, _au_end)
            except Exception:
                logging.getLogger(__name__).warning("all_users_daily_revenue failed", exc_info=True)

        kyc_done = (
            analyzer.cohort_kyc_count(latest_id, referral_code=referral_code)
            if analyzer is not None
            else 0
        )

        # --- Export button (top of analysis, before KPIs) ----------------------
        signups = int(summary["cohort_users"].sum()) if not summary.empty else 0
        activated = int(summary["transacting_users"].sum()) if not summary.empty else 0
        funnel = {"signups": signups, "kyc_done": kyc_done, "activated": activated}
        self._render_export_button(
            summary,
            cum_profit_df,
            cum_rev_df,
            daily,
            spend_agg,
            campaigns,
            funnel,
            kyc_done,
            spend_breakdown=spend_breakdown,
            spend_df_raw=spend_df,
            all_users_rev_df=all_users_rev_df,
        )

        self._render_kpis(
            summary, cum_profit_df, kyc_done=kyc_done, spend_breakdown=spend_breakdown
        )
        if not summary.empty:
            fig_funnel = _fig_campaign_funnel(funnel)
            if fig_funnel:
                st.plotly_chart(fig_funnel, width="stretch")
        st.divider()
        self._render_spend_charts(
            summary,
            daily,
            spend_agg,
            campaigns,
            cum_rev_df,
            cum_profit_df,
            all_users_rev_df=all_users_rev_df,
        )
        st.divider()
        self._render_channel(summary, cum_profit_df)
        st.divider()
        self._render_summary_table(summary)

    def _build_pdf_bytes(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        cum_profit_df: pd.DataFrame | None,
        cum_rev_df: pd.DataFrame | None,
        daily: pd.DataFrame,
        spend_df: pd.DataFrame,
        campaigns: list[dict],
        funnel: dict,
        kyc_done: int,
        spend_breakdown: dict[str, float] | None = None,
        spend_df_raw: pd.DataFrame | None = None,
        all_users_rev_df: pd.DataFrame | None = None,
    ) -> tuple[bytes, list[str]]:
        """Build the marketing PDF and return raw bytes plus chart error list.

        Args:
            summary: ``roi_summary()`` DataFrame (latest campaign rows).
            cum_profit_df: ``cumulative_profit()`` DataFrame (may be None).
            cum_rev_df: ``cumulative_revenue()`` DataFrame (may be None).
            daily: ``daily_context()`` DataFrame.
            spend_df: Aggregated ad spend DataFrame (date + daily_spend_usd).
            campaigns: List of campaign dicts.
            funnel: Dict with ``signups``, ``kyc_done``, ``activated`` counts.
            kyc_done: KYC-completed count for CAC calculation.
            spend_breakdown: Per-platform spend totals.
            spend_df_raw: Raw spend DataFrame with platform column for per-platform chart lines.
            all_users_rev_df: All-users daily revenue DataFrame from
                ``CampaignAnalyzer.all_users_daily_revenue()`` (may be None).

        Returns:
            Tuple of (pdf_bytes, chart_errors).
        """
        from nbs_bi.reporting.export import build_marketing_pdf

        pdf_bytes, chart_errors = build_marketing_pdf(
            summary=summary,
            cum_profit_df=cum_profit_df,
            cum_rev_df=cum_rev_df,
            daily=daily,
            spend_df=spend_df,
            campaigns=campaigns,
            funnel=funnel,
            kyc_done=kyc_done,
            spend_breakdown=spend_breakdown,
            spend_df_raw=spend_df_raw,
            all_users_rev_df=all_users_rev_df,
        )
        return pdf_bytes, chart_errors

    def _render_export_button(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        cum_profit_df: pd.DataFrame | None,
        cum_rev_df: pd.DataFrame | None,
        daily: pd.DataFrame,
        spend_df: pd.DataFrame,
        campaigns: list[dict],
        funnel: dict,
        kyc_done: int,
        spend_breakdown: dict[str, float] | None = None,
        spend_df_raw: pd.DataFrame | None = None,
        all_users_rev_df: pd.DataFrame | None = None,
    ) -> None:
        """Render a PDF download button for the marketing briefing.

        Uses matplotlib (no external binaries) — renders in ~0.5 s with a
        spinner, then shows a download button immediately.

        Args:
            summary: ``roi_summary()`` DataFrame (latest campaign rows).
            cum_profit_df: ``cumulative_profit()`` DataFrame (may be None).
            cum_rev_df: ``cumulative_revenue()`` DataFrame (may be None).
            daily: ``daily_context()`` DataFrame.
            spend_df: Aggregated ad spend DataFrame.
            campaigns: List of campaign dicts.
            funnel: Dict with ``signups``, ``kyc_done``, ``activated`` counts.
            kyc_done: KYC-completed count for CAC calculation.
            spend_breakdown: Per-platform spend totals.
            spend_df_raw: Raw spend DataFrame with platform column for per-platform chart lines.
            all_users_rev_df: All-users daily revenue from
                ``CampaignAnalyzer.all_users_daily_revenue()`` (may be None).
        """
        _log = logging.getLogger(__name__)

        try:
            with st.spinner("Generating PDF…"):
                pdf_bytes, chart_errors = self._build_pdf_bytes(
                    summary=summary,
                    cum_profit_df=cum_profit_df,
                    cum_rev_df=cum_rev_df,
                    daily=daily,
                    spend_df=spend_df,
                    campaigns=campaigns,
                    funnel=funnel,
                    kyc_done=kyc_done,
                    spend_breakdown=spend_breakdown,
                    spend_df_raw=spend_df_raw,
                    all_users_rev_df=all_users_rev_df,
                )
        except Exception as exc:
            _log.exception("PDF export failed")
            st.error(f"PDF generation failed: {exc}")
            return

        chart_count = pdf_bytes.count(b"\x89PNG")
        col_dl, col_info, _ = st.columns([1, 2, 3])
        with col_dl:
            st.download_button(
                label="Download PDF",
                data=pdf_bytes,
                file_name="nbs_marketing_report.pdf",
                mime="application/pdf",
                key="ads_export_pdf_download",
            )
        with col_info:
            st.caption(f"{len(pdf_bytes):,} bytes · {chart_count} chart(s)")
        if chart_errors:
            with st.expander(f"⚠ {len(chart_errors)} chart issue(s)"):
                for err in chart_errors:
                    st.warning(err)

    def _try_upload(self) -> dict | None:  # pragma: no cover
        """Load campaign data: DB first, then local CSV, then file uploader.

        Priority:
        1. ``meta_ads_spend`` DB table (populated by ``nbs-ads-upload`` CLI).
        2. Most-recent CSV in ``data/nbs_corp_card/`` (local dev).
        3. Streamlit file uploader (manual fallback).
        """
        from nbs_bi.clients.campaigns import (
            CampaignAnalyzer,
            aggregate_spend,
            load_ad_spend,
            load_ad_spend_from_db,
        )
        from nbs_bi.config import DATA_DIR
        from nbs_bi.reporting.cards import _load_all_invoice_models

        spend = None

        # 1. Try DB
        if self._db_url:
            spend = load_ad_spend_from_db(self._db_url)
            if spend is not None:
                st.caption("Spend data loaded from database.")

        # 2. Try local CSV (nbs_corp_card/ first, then data/ root for rain exports)
        if spend is None:
            corp_card_dir = DATA_DIR / "nbs_corp_card"
            local_csvs = sorted(corp_card_dir.glob("*.csv")) if corp_card_dir.exists() else []
            if not local_csvs:
                local_csvs = sorted(DATA_DIR.glob("rain-transactions-export-*.csv"))
            if local_csvs:
                csv_path = local_csvs[-1]
                st.caption(f"Loaded spend data from `{csv_path.name}`")
                spend = load_ad_spend(csv_path)

        if spend is None:
            st.info(
                "No ad spend data found in the database. "
                "Run `nbs-ads-upload <rain-export.csv>` to populate it."
            )
            return None

        cutoff = pd.Timestamp(_TRACKING_START)
        spend = spend[pd.to_datetime(spend["date"]) >= cutoff].reset_index(drop=True)
        analyzer = CampaignAnalyzer(
            aggregate_spend(spend), db_url=self._analytics_db_url or self._db_url
        )

        _, _, _, history = _load_all_invoice_models()
        invoice_history = [
            (
                period,
                m.inputs.invoice_total_usd or float(m.cost_breakdown().total),
                m.inputs.n_transactions,
            )
            for period, m in history
        ]

        summary = analyzer.roi_summary()

        return {
            "summary": summary,
            "daily": analyzer.daily_context(),
            "spend_df": spend,
            "campaigns": analyzer.campaigns,
            "analyzer": analyzer,
            "invoice_history": invoice_history,
        }

    def _render_kpis(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        cum_profit_df: pd.DataFrame | None = None,
        kyc_done: int = 0,
        spend_breakdown: dict[str, float] | None = None,
    ) -> None:
        """Render KPI strip including net profit when profit data is available.

        Args:
            summary: Output of ``CampaignAnalyzer.roi_summary()``.
            cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()``
                — when provided, adds a Net Profit KPI tile.
            kyc_done: Count of cohort users who completed KYC (kyc_level >= 1),
                used to compute KYC cost component of CAC.
            spend_breakdown: Per-platform spend totals for the selected window,
                e.g. ``{"meta": 450.0, "google": 310.0}``.
        """
        from nbs_bi.clients.models import _KYC_COST_USD

        total_spend = float(summary["total_spend_usd"].sum())
        total_rev = float(summary["total_revenue_usd"].sum())
        transacting = int(summary["transacting_users"].sum())
        overall_roas = total_rev / total_spend if total_spend > 0 else 0.0

        kyc_cost = kyc_done * _KYC_COST_USD
        cac_active = (total_spend + kyc_cost) / transacting if transacting > 0 else float("nan")

        has_profit = (
            cum_profit_df is not None
            and not cum_profit_df.empty
            and "cum_contribution_margin_usd" in cum_profit_df.columns
        )
        multi_platform = bool(spend_breakdown and len(spend_breakdown) > 1)
        n_spend_cols = len(spend_breakdown) if multi_platform else 1
        n_cols = n_spend_cols + 3 + (1 if has_profit else 0)
        cols = st.columns(n_cols)

        if multi_platform:
            for i, (plat, amt) in enumerate(sorted(spend_breakdown.items())):  # type: ignore[union-attr]
                cols[i].metric(f"{plat.capitalize()} Spend", fmt_usd(amt))
        else:
            cols[0].metric("Total Spend", fmt_usd(total_spend))

        base = n_spend_cols
        cols[base].metric("Cohort Revenue", fmt_usd(total_rev))
        cols[base + 1].metric(
            "Overall ROAS",
            f"{overall_roas:.2f}×",
            delta=f"{'above' if overall_roas >= 1 else 'below'} break-even",
            delta_color="normal" if overall_roas >= 1 else "inverse",
        )
        cols[base + 2].metric(
            "CAC",
            fmt_usd(cac_active) if not np.isnan(cac_active) else "n/a",
        )
        if has_profit:
            net_profit = float(cum_profit_df["cum_contribution_margin_usd"].iloc[-1])  # type: ignore[union-attr]
            cols[base + 3].metric(
                "Net Profit (latest cohort)",
                fmt_usd(net_profit),
                delta="profitable" if net_profit >= 0 else "loss",
                delta_color="normal" if net_profit >= 0 else "inverse",
            )

    def _render_spend_charts(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        daily: pd.DataFrame,
        spend_df: pd.DataFrame,
        campaigns: list[dict],
        cum_rev_df: pd.DataFrame | None = None,
        cum_profit_df: pd.DataFrame | None = None,
        all_users_rev_df: pd.DataFrame | None = None,
    ) -> None:
        """Render cumulative spend, ROI, CAC, and daily signups charts."""
        if not spend_df.empty:
            cum_df = _build_cumulative_spend(spend_df, campaigns)
            fig = _fig_cumulative_spend(cum_df, campaigns, cum_rev_df, cum_profit_df)
            if fig:
                st.plotly_chart(fig, width="stretch")

        if cum_profit_df is not None and not cum_profit_df.empty:
            fig_profit = _fig_cumulative_profit(cum_profit_df)
            if fig_profit:
                st.plotly_chart(fig_profit, width="stretch")
            fig_breakdown = _fig_revenue_breakdown(cum_profit_df)
            if fig_breakdown:
                st.plotly_chart(fig_breakdown, width="stretch")

        if not daily.empty:
            fig4 = _fig_campaign_daily(daily)
            if fig4:
                st.plotly_chart(fig4, width="stretch")

        if cum_rev_df is not None and not cum_rev_df.empty and not spend_df.empty:
            fig_rev_spend = _fig_daily_revenue_vs_spend(cum_rev_df, spend_df)
            if fig_rev_spend:
                st.plotly_chart(fig_rev_spend, width="stretch")

        if all_users_rev_df is not None and not all_users_rev_df.empty and not spend_df.empty:
            fig_all = _fig_daily_rev_all_vs_cohort(
                all_users_rev_df, cum_rev_df if cum_rev_df is not None else pd.DataFrame(), spend_df
            )
            if fig_all:
                st.plotly_chart(fig_all, width="stretch")

        col1, col2 = st.columns(2)
        with col1:
            fig2 = _fig_campaign_roi(summary, cum_profit_df)
            if fig2:
                st.plotly_chart(fig2, width="stretch")
        with col2:
            fig3 = _fig_campaign_cac(summary)
            if fig3:
                st.plotly_chart(fig3, width="stretch")

    def _render_channel(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        cum_profit_df: pd.DataFrame | None = None,
    ) -> None:
        """Render channel comparison chart, daily evolution, and summary table."""
        acq = self._acquisition
        if acq is None or (isinstance(acq, pd.DataFrame) and acq.empty):
            st.info("Channel comparison unavailable — load ClientReport to compare sources.")
            return

        comparison = _build_channel_comparison(summary, acq, cum_profit_df)
        st.subheader("Acquisition Channel Comparison")
        st.caption(
            "Operational profit per user (revenue − card COGS − KYC cost) by acquisition channel. "
            "For Meta Ads the figure uses campaign-cohort costs; other channels use the all-time "
            "client base. Spend and ROAS columns are Meta Ads only."
        )
        fig = _fig_channel_comparison(comparison)
        if fig:
            st.plotly_chart(fig, width="stretch")

        fig_daily = _fig_channel_daily(self._profit_by_source_daily)
        if fig_daily:
            st.plotly_chart(fig_daily, width="stretch")

        display = comparison.copy()
        for col in ["avg_operational_profit_usd", "total_operational_profit_usd", "spend_usd"]:
            if col in display.columns:
                display[col] = display[col].apply(
                    lambda v: fmt_usd(v) if pd.notna(v) else "no spend data"
                )
        if "roas" in display.columns:
            display["roas"] = display["roas"].apply(
                lambda v: f"{v:.2f}×" if pd.notna(v) else "no spend data"
            )
        if "conversion_rate" in display.columns:
            display["conversion_rate"] = display["conversion_rate"].apply(
                lambda v: f"{v * 100:.1f}%"
            )
        st.dataframe(display, width="stretch", hide_index=True)

    def _render_summary_table(self, summary: pd.DataFrame) -> None:  # pragma: no cover
        """Render formatted campaign summary table."""
        st.subheader("Campaign Summary")
        display = summary.copy()
        for col in [
            "total_spend_usd",
            "total_revenue_usd",
            "cac_full",
            "cac_incremental",
            "avg_rev_per_transacting_user",
        ]:
            if col in display.columns:
                display[col] = display[col].apply(lambda v: fmt_usd(v) if pd.notna(v) else "n/a")
        if "roas" in display.columns:
            display["roas"] = display["roas"].apply(lambda v: f"{v:.2f}×" if pd.notna(v) else "n/a")
        if "transacting_rate" in display.columns:
            display["transacting_rate"] = display["transacting_rate"].apply(
                lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "n/a"
            )
        if "incremental_users_est" in display.columns:
            display["incremental_users_est"] = display["incremental_users_est"].apply(
                lambda v: int(v) if pd.notna(v) else "n/a"
            )
        st.dataframe(display, width="stretch", hide_index=True)
