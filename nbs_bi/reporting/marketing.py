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

import io

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
            columns=["date", "daily_spend_usd", "cumulative_spend_usd",
                     "is_campaign_start", "campaign_id"]
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
) -> pd.DataFrame:
    """Merge Meta Ads campaign metrics with acquisition_summary rows.

    Args:
        summary: Output of ``CampaignAnalyzer.roi_summary()``.
        acquisition: Output of ``ClientReport.build()["acquisition"]``.

    Returns:
        DataFrame with schema: ``acquisition_source``, ``n_users``,
        ``avg_net_revenue_usd``, ``total_net_revenue_usd``,
        ``conversion_rate``, ``spend_usd``, ``roas``.
        Meta Ads row is prepended; other channels have NaN for
        ``spend_usd`` and ``roas``.
    """
    total_spend = float(summary["total_spend_usd"].sum()) if not summary.empty else 0.0
    total_rev = float(summary["total_revenue_usd"].sum()) if not summary.empty else 0.0
    n_users = int(summary["cohort_users"].sum()) if not summary.empty else 0
    transacting = int(summary["transacting_users"].sum()) if not summary.empty else 0

    meta_row = {
        "acquisition_source": "meta_ads",
        "n_users": n_users,
        "avg_net_revenue_usd": total_rev / n_users if n_users > 0 else 0.0,
        "total_net_revenue_usd": total_rev,
        "conversion_rate": transacting / n_users if n_users > 0 else 0.0,
        "spend_usd": total_spend,
        "roas": total_rev / total_spend if total_spend > 0 else np.nan,
    }

    if acquisition is not None and not acquisition.empty:
        acq = acquisition.copy()
    else:
        acq = pd.DataFrame()
    acq["spend_usd"] = np.nan
    acq["roas"] = np.nan

    return pd.concat(
        [pd.DataFrame([meta_row]), acq], ignore_index=True
    )


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------


def _fig_cumulative_spend(
    cum_df: pd.DataFrame,
    campaigns: list[dict],
    cum_rev_df: pd.DataFrame | None = None,
) -> go.Figure | None:
    """Line chart of cumulative spend and cohort revenue with campaign markers.

    Args:
        cum_df: Output of :func:`_build_cumulative_spend`.
        campaigns: Campaign dicts from ``CampaignAnalyzer.campaigns``.
        cum_rev_df: Output of ``CampaignAnalyzer.cumulative_revenue()`` — optional
            revenue overlay for the most recent campaign's cohort.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if cum_df.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=cum_df["date"].astype(str),
        y=cum_df["cumulative_spend_usd"],
        mode="lines",
        line=dict(color=ROSE, width=2),
        name="Cumulative Spend (USD)",
        hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
    ))
    if cum_rev_df is not None and not cum_rev_df.empty and "cum_rev_usd" in cum_rev_df.columns:
        fig.add_trace(go.Scatter(
            x=cum_rev_df["date"].astype(str),
            y=cum_rev_df["cum_rev_usd"],
            mode="lines",
            line=dict(color=EMERALD, width=2),
            name="Cumulative Revenue — latest cohort (USD)",
            hovertemplate="%{x}: %{y:$,.2f}<extra></extra>",
        ))
    for c in campaigns:
        x_str = str(c["start"])
        fig.add_shape(
            type="line", x0=x_str, x1=x_str, y0=0, y1=1,
            xref="x", yref="paper",
            line=dict(color=TEXT_MUTED, width=1, dash="dash"),
        )
        fig.add_annotation(
            x=x_str, y=1, xref="x", yref="paper",
            text=c["campaign_id"], showarrow=False,
            yanchor="bottom", font=dict(size=10, color=TEXT_MUTED),
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
    layout = panel("Cumulative Meta Ads Spend vs Cohort Revenue (USD)")
    layout["xaxis"]["title"] = "Date"
    layout["yaxis"]["title"] = "USD"
    fig.update_layout(**layout)
    return fig


def _fig_campaign_roi(summary: pd.DataFrame) -> go.Figure | None:
    """Grouped bar: ad spend vs cohort revenue per campaign."""
    if summary.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=summary["campaign_id"], y=summary["total_spend_usd"],
        name="Ad Spend", marker_color=ROSE,
        text=summary["total_spend_usd"].apply(_fmt_usd_safe), textposition="outside",
    ))
    fig.add_trace(go.Bar(
        x=summary["campaign_id"], y=summary["total_revenue_usd"],
        name="Cohort Revenue", marker_color=EMERALD,
        text=summary["total_revenue_usd"].apply(_fmt_usd_safe), textposition="outside",
    ))
    layout = panel("Ad Spend vs Cohort Revenue by Campaign")
    layout["barmode"] = "group"
    layout["yaxis"]["title"] = "USD"
    fig.update_layout(**layout)
    return fig


def _fig_campaign_cac(summary: pd.DataFrame) -> go.Figure | None:
    """Bar chart: full-cohort CAC vs incremental CAC per campaign."""
    if summary.empty or "cac_full" not in summary.columns:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=summary["campaign_id"], y=summary["cac_full"],
        name="CAC (all cohort users)", marker_color=AMBER,
        text=summary["cac_full"].apply(lambda v: f"${v:.2f}" if pd.notna(v) else "n/a"),
        textposition="outside",
    ))
    valid = summary["cac_incremental"].notna()
    if valid.any():
        fig.add_trace(go.Bar(
            x=summary.loc[valid, "campaign_id"], y=summary.loc[valid, "cac_incremental"],
            name="CAC (incremental est.)", marker_color=VIOLET,
            text=summary.loc[valid, "cac_incremental"].apply(lambda v: f"${v:.2f}"),
            textposition="outside",
        ))
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
        fig.add_trace(go.Bar(
            x=daily.loc[mask, "date"].astype(str),
            y=daily.loc[mask, "new_signups"],
            name=f"{cid} signups",
            marker_color=colors[i % len(colors)],
            opacity=0.6,
        ))
    organic = ~daily["is_campaign"]
    fig.add_trace(go.Bar(
        x=daily.loc[organic, "date"].astype(str),
        y=daily.loc[organic, "new_signups"],
        name="Organic signups", marker_color=BLUE, opacity=0.4,
    ))
    spending = daily["daily_spend_usd"] > 0
    fig.add_trace(go.Scatter(
        x=daily.loc[spending, "date"].astype(str),
        y=daily.loc[spending, "daily_spend_usd"],
        name="Ad spend (USD)", mode="lines+markers",
        line=dict(color=ROSE, width=2, dash="dot"), yaxis="y2",
    ))
    layout = panel("Daily Signups vs Meta Ad Spend")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "New Signups"
    layout["xaxis"]["title"] = "Date"
    layout["yaxis2"] = dict(
        title="Daily Spend (USD)", overlaying="y", side="right",
        gridcolor="rgba(0,0,0,0)",
    )
    fig.update_layout(**layout)
    return fig


def _fig_channel_comparison(comparison: pd.DataFrame) -> go.Figure | None:
    """Horizontal bar: avg net revenue per acquisition channel."""
    if comparison.empty:
        return None
    colors = [_CHANNEL_COLORS.get(s, TEXT_MUTED) for s in comparison["acquisition_source"]]
    texts = []
    for _, row in comparison.iterrows():
        label = _fmt_usd_safe(row["avg_net_revenue_usd"])
        if pd.notna(row.get("roas")) and row["acquisition_source"] == "meta_ads":
            label += f"  ROAS {row['roas']:.2f}×"
        texts.append(label)
    fig = go.Figure(go.Bar(
        x=comparison["avg_net_revenue_usd"],
        y=comparison["acquisition_source"],
        orientation="h",
        marker_color=colors,
        text=texts,
        textposition="outside",
    ))
    layout = panel("Avg Net Revenue (USD) by Acquisition Channel")
    layout["xaxis"]["title"] = "Avg Net Revenue (USD)"
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
    ) -> None:
        self._data = campaign_data
        self._acquisition = acquisition
        self._db_url = db_url

    def render(self) -> None:  # pragma: no cover
        """Render all Marketing - Ads tab components into the active Streamlit context."""
        campaign_data = self._data

        if campaign_data is None:
            campaign_data = self._try_upload()
            if campaign_data is None:
                return

        summary: pd.DataFrame = campaign_data.get("summary", pd.DataFrame())
        daily: pd.DataFrame = campaign_data.get("daily", pd.DataFrame())
        spend_df: pd.DataFrame = campaign_data.get("spend_df", pd.DataFrame())
        campaigns: list[dict] = campaign_data.get("campaigns", [])

        if summary.empty:
            st.warning("No FACEBK spend rows found in the uploaded CSV.")
            return

        # Focus all campaign-level charts on the most recent campaign only.
        # Cumulative spend (spend_df + campaigns) keeps full history.
        latest_id = summary["campaign_id"].iloc[-1]
        summary = summary[summary["campaign_id"] == latest_id].reset_index(drop=True)
        if not daily.empty and "campaign_id" in daily.columns:
            latest_start = pd.to_datetime(summary["start"].iloc[0])
            cutoff = latest_start - pd.Timedelta(days=14)
            daily = daily[pd.to_datetime(daily["date"]) >= cutoff].reset_index(drop=True)

        analyzer = campaign_data.get("analyzer")
        cum_rev_df = (
            analyzer.cumulative_revenue(latest_id)
            if analyzer is not None
            else pd.DataFrame()
        )

        self._render_kpis(summary)
        st.divider()
        self._render_spend_charts(summary, daily, spend_df, campaigns, cum_rev_df)
        st.divider()
        self._render_channel(summary)
        st.divider()
        self._render_summary_table(summary)

    def _try_upload(self) -> dict | None:  # pragma: no cover
        """Load campaign data from local CSV or file uploader.

        Checks ``data/nbs_corp_card/`` for CSV files first (most recent by
        name).  Falls back to a Streamlit file uploader if none are found.
        """
        from nbs_bi.clients.campaigns import CampaignAnalyzer, load_ad_spend
        from nbs_bi.config import DATA_DIR

        corp_card_dir = DATA_DIR / "nbs_corp_card"
        local_csvs = sorted(corp_card_dir.glob("*.csv")) if corp_card_dir.exists() else []

        if local_csvs:
            csv_path = local_csvs[-1]  # most recent by filename sort
            st.caption(f"Loaded spend data from `{csv_path.name}`")
            spend = load_ad_spend(csv_path)
        else:
            uploaded = st.file_uploader(
                "Rain Card CSV export", type=["csv"], key="meta_ads_csv"
            )
            if uploaded is None:
                st.info(
                    "No Rain CSV found in data/nbs_corp_card/. "
                    "Upload rain-transactions-export-YYYY-MM-DD.csv to load spend data."
                )
                return None
            raw = uploaded.read()
            spend = load_ad_spend(io.StringIO(raw.decode("utf-8")))

        cutoff = pd.Timestamp(_TRACKING_START)
        spend = spend[pd.to_datetime(spend["date"]) >= cutoff].reset_index(drop=True)
        analyzer = CampaignAnalyzer(spend, db_url=self._db_url)
        return {
            "summary": analyzer.roi_summary(),
            "daily": analyzer.daily_context(),
            "spend_df": spend,
            "campaigns": analyzer.campaigns,
            "analyzer": analyzer,
        }

    def _render_kpis(self, summary: pd.DataFrame) -> None:  # pragma: no cover
        """Render 5-metric KPI strip."""
        total_spend = float(summary["total_spend_usd"].sum())
        total_rev = float(summary["total_revenue_usd"].sum())
        n_users = int(summary["cohort_users"].sum())
        overall_roas = total_rev / total_spend if total_spend > 0 else 0.0
        best_roas = float(summary["roas"].max()) if "roas" in summary.columns else 0.0
        cac_full = total_spend / n_users if n_users > 0 else float("nan")

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Meta Spend", fmt_usd(total_spend))
        c2.metric("Cohort Revenue", fmt_usd(total_rev))
        c3.metric(
            "Overall ROAS", f"{overall_roas:.2f}×",
            delta=f"{'above' if overall_roas >= 1 else 'below'} break-even",
            delta_color="normal" if overall_roas >= 1 else "inverse",
        )
        c4.metric("Best Campaign ROAS", f"{best_roas:.2f}×")
        c5.metric("Full-Cohort CAC", fmt_usd(cac_full) if not np.isnan(cac_full) else "n/a")

        st.caption(
            "Revenue is from ALL users who signed up during campaign windows — "
            "includes organic signups. CAC (incremental) in the table below "
            "estimates only Meta-attributed uplift."
        )

    def _render_spend_charts(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        daily: pd.DataFrame,
        spend_df: pd.DataFrame,
        campaigns: list[dict],
        cum_rev_df: pd.DataFrame | None = None,
    ) -> None:
        """Render cumulative spend, ROI, CAC, and daily signups charts."""
        if not spend_df.empty:
            cum_df = _build_cumulative_spend(spend_df, campaigns)
            fig = _fig_cumulative_spend(cum_df, campaigns, cum_rev_df)
            if fig:
                st.plotly_chart(fig, width="stretch")

        if not daily.empty:
            fig4 = _fig_campaign_daily(daily)
            if fig4:
                st.plotly_chart(fig4, width="stretch")

        col1, col2 = st.columns(2)
        with col1:
            fig2 = _fig_campaign_roi(summary)
            if fig2:
                st.plotly_chart(fig2, width="stretch")
        with col2:
            fig3 = _fig_campaign_cac(summary)
            if fig3:
                st.plotly_chart(fig3, width="stretch")

    def _render_channel(self, summary: pd.DataFrame) -> None:  # pragma: no cover
        """Render channel comparison chart and table."""
        acq = self._acquisition
        if acq is None or (isinstance(acq, pd.DataFrame) and acq.empty):
            st.info("Channel comparison unavailable — load ClientReport to compare sources.")
            return

        comparison = _build_channel_comparison(summary, acq)
        st.subheader("Acquisition Channel Comparison")
        st.caption(
            "Meta Ads cohort metrics vs other acquisition channels. "
            "Spend and ROAS columns are Meta Ads only — other channels have no attributed spend."
        )
        fig = _fig_channel_comparison(comparison)
        if fig:
            st.plotly_chart(fig, width="stretch")

        display = comparison.copy()
        for col in ["avg_net_revenue_usd", "total_net_revenue_usd", "spend_usd"]:
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
        for col in ["total_spend_usd", "total_revenue_usd", "cac_full",
                    "cac_incremental", "avg_rev_per_transacting_user"]:
            if col in display.columns:
                display[col] = display[col].apply(
                    lambda v: fmt_usd(v) if pd.notna(v) else "n/a"
                )
        if "roas" in display.columns:
            display["roas"] = display["roas"].apply(
                lambda v: f"{v:.2f}×" if pd.notna(v) else "n/a"
            )
        if "transacting_rate" in display.columns:
            display["transacting_rate"] = display["transacting_rate"].apply(
                lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "n/a"
            )
        if "incremental_users_est" in display.columns:
            display["incremental_users_est"] = display["incremental_users_est"].apply(
                lambda v: int(v) if pd.notna(v) else "n/a"
            )
        st.dataframe(display, width="stretch", hide_index=True)
