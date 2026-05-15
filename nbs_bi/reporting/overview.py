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

import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nbs_bi.reporting.theme import (
    AMBER,
    BLUE,
    EMERALD,
    TEAL,
    VIOLET,
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

_log = logging.getLogger(__name__)

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


def _resample_revenue(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Resample a daily revenue DataFrame to the chosen granularity.

    Args:
        df: DataFrame with a 'date' column (datetime) and numeric value columns.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.

    Returns:
        Resampled DataFrame with the same column structure.
    """
    if df.empty:
        return df
    value_cols = [c for c in df.columns if c != "date"]
    freq = {"Weekly": "W-MON", "Monthly": "MS", "Yearly": "YS"}.get(granularity)
    if freq is None:
        return df
    return (
        df.set_index("date")[value_cols]
        .resample(freq)
        .sum()
        .reset_index()
    )


def _fig_monthly_revenue(
    revenue: pd.DataFrame,
    card_revenue: pd.DataFrame | None = None,
    title: str = "Revenue (USD)",
) -> go.Figure | None:
    """Stacked bar: conversion + card revenue in USD at any granularity.

    Args:
        revenue: DataFrame with columns date, fee_usd, spread_usd.
        card_revenue: DataFrame with columns date, card_fee_usd, billing_usd.
        title: Chart panel title.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if _empty(revenue):
        return None
    merged = revenue.copy()
    if not _empty(card_revenue):
        merged = merged.merge(card_revenue, on="date", how="outer").fillna(0.0)
    merged = merged.sort_values("date")
    traces = [
        ("fee_usd", "Conv Fees", TEAL),
        ("spread_usd", "Conv Spread", BLUE),
        ("card_fee_usd", "Card Fees", AMBER),
        ("billing_usd", "Card Billing", VIOLET),
    ]
    totals = pd.Series(0.0, index=range(len(merged)))
    for col, _, _ in traces:
        if col in merged.columns:
            totals += merged[col].fillna(0.0).reset_index(drop=True)
    _t = [[v] for v in totals]
    fig = go.Figure()
    for col, label, color in traces:
        if col not in merged.columns:
            continue
        fig.add_trace(
            go.Bar(
                x=merged["date"],
                y=merged[col],
                name=label,
                marker_color=color,
                customdata=_t,
                hovertemplate=f"<b>{label}</b>: $%{{y:,.2f}}<br><b>Total</b>: $%{{customdata[0]:,.2f}}<extra></extra>",
            )
        )
    layout = panel(title)
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "USD"
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
    _t = [[v] for v in agg["total"]]
    for col, label, color in [("onramp", "Onramp", BLUE), ("offramp", "Offramp", AMBER)]:
        fig.add_trace(go.Bar(
            x=agg["month"], y=agg[col], name=label, marker_color=color,
            customdata=_t,
            hovertemplate=f"<b>{label}</b>: R$\xa0%{{y:,.0f}}<br><b>Total</b>: R$\xa0%{{customdata[0]:,.0f}}<extra></extra>",
        ))
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


def _resample_combined(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Resample merged conv+card DataFrame to the chosen granularity.

    Args:
        df: DataFrame with columns date (datetime), conv_usd, card_usd.
        granularity: One of 'Daily', 'Weekly', 'Monthly'.

    Returns:
        Resampled DataFrame with the same column structure. Trailing stub
        buckets (where the bucket start equals the last data point) are
        dropped to avoid single-day partial periods at the end of a range.
    """
    if granularity == "Daily":
        return df
    freq = "7D" if granularity == "Weekly" else "MS"
    result = (
        df.set_index("date")[["conv_usd", "card_usd"]]
        .resample(freq)
        .sum()
        .reset_index()
    )
    # Drop trailing stub: last bucket starts on the very last input date
    if len(result) > 1 and not df.empty:
        last_input = df["date"].max()
        if result["date"].iloc[-1] == last_input:
            result = result.iloc[:-1].reset_index(drop=True)
    return result


def _agg_revenue(
    rev_df: pd.DataFrame,
    card_rev_df: pd.DataFrame,
    granularity: str,
) -> pd.DataFrame:
    """Aggregate conversion + card/billing revenue per period.

    Args:
        rev_df: Daily conversion revenue with columns date, fee_usd, spread_usd.
        card_rev_df: Daily card/billing revenue with columns date, card_fee_usd, billing_usd.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.

    Returns:
        DataFrame with columns: date, total_rev.
    """
    rev = (
        rev_df.copy() if not _empty(rev_df)
        else pd.DataFrame(columns=["date", "fee_usd", "spread_usd"])
    )
    card_rev = (
        card_rev_df.copy() if not _empty(card_rev_df)
        else pd.DataFrame(columns=["date", "card_fee_usd", "billing_usd"])
    )
    rev["date"] = pd.to_datetime(rev["date"], errors="coerce")
    card_rev["date"] = pd.to_datetime(card_rev["date"], errors="coerce")
    if granularity != "Daily":
        rev = _resample_revenue(rev, granularity)
        card_rev = _resample_revenue(card_rev, granularity)
    revenue = rev.merge(card_rev, on="date", how="outer").fillna(0.0)
    for col in ("fee_usd", "spread_usd", "card_fee_usd", "billing_usd"):
        if col not in revenue.columns:
            revenue[col] = 0.0
    revenue["total_rev"] = (
        revenue["fee_usd"] + revenue["spread_usd"]
        + revenue["card_fee_usd"] + revenue["billing_usd"]
    )
    return revenue[["date", "total_rev"]]


def _agg_volume(
    conv_daily: pd.DataFrame,
    card_daily: pd.DataFrame,
    fx_rate: float,
    granularity: str,
) -> pd.DataFrame:
    """Aggregate conversion (BRL→USD) + card spend per period.

    Args:
        conv_daily: Daily conversion volumes with columns date, onramp (BRL), offramp (BRL).
        card_daily: Daily card spend with columns date, amount_usd.
        fx_rate: Period BRL/USD rate. Zero or negative means no FX available.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.

    Returns:
        DataFrame with columns: date, total_vol.
    """
    freq_map = {"Weekly": "W-MON", "Monthly": "MS", "Yearly": "YS"}
    conv = (
        conv_daily.copy() if not _empty(conv_daily)
        else pd.DataFrame(columns=["date", "onramp", "offramp"])
    )
    card = (
        card_daily.copy() if not _empty(card_daily)
        else pd.DataFrame(columns=["date", "amount_usd"])
    )
    conv["date"] = pd.to_datetime(conv["date"], errors="coerce")
    card["date"] = pd.to_datetime(card["date"], errors="coerce")
    for col in ("onramp", "offramp"):
        if col not in conv.columns:
            conv[col] = 0.0
    if "amount_usd" not in card.columns:
        card["amount_usd"] = 0.0
    if granularity != "Daily":
        freq = freq_map.get(granularity)
        if freq:
            conv = conv.set_index("date")[["onramp", "offramp"]].resample(freq).sum().reset_index()
            card = card.set_index("date")[["amount_usd"]].resample(freq).sum().reset_index()
    brl = conv["onramp"].fillna(0.0) + conv["offramp"].fillna(0.0)
    if fx_rate and fx_rate > 0:
        conv["conv_usd"] = brl / fx_rate
    else:
        _log.warning(
            "_agg_volume: fx_rate=%s is zero or invalid; BRL volume zeroed", fx_rate
        )
        conv["conv_usd"] = pd.Series(0.0, index=conv.index)
    vol = (
        conv[["date", "conv_usd"]]
        .merge(card[["date", "amount_usd"]], on="date", how="outer")
        .fillna(0.0)
    )
    vol["total_vol"] = vol["conv_usd"] + vol["amount_usd"]
    return vol[["date", "total_vol"]]


def _compute_take_rate(
    rev_df: pd.DataFrame,
    card_rev_df: pd.DataFrame,
    conv_daily: pd.DataFrame,
    card_daily: pd.DataFrame,
    fx_rate: float,
    granularity: str,
) -> pd.DataFrame:
    """Compute take rate (%) per period: total revenue / total volume * 100.

    Args:
        rev_df: Daily conversion revenue with columns date, fee_usd, spread_usd.
        card_rev_df: Daily card/billing revenue with columns date, card_fee_usd, billing_usd.
        conv_daily: Daily conversion volumes with columns date, onramp (BRL), offramp (BRL).
        card_daily: Daily card spend with columns date, amount_usd.
        fx_rate: Period BRL/USD rate. Zero or negative means no FX available.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.

    Returns:
        DataFrame with columns [date, take_rate_pct]. Periods with zero volume
        are dropped. Returns empty DataFrame if inputs are all empty.
    """
    revenue = _agg_revenue(rev_df, card_rev_df, granularity)
    vol = _agg_volume(conv_daily, card_daily, fx_rate, granularity)
    merged = revenue.merge(vol, on="date", how="outer").fillna(0.0)
    merged = merged[merged["total_vol"] > 0].copy()
    if merged.empty:
        return pd.DataFrame(columns=["date", "take_rate_pct"])
    merged["take_rate_pct"] = merged["total_rev"] / merged["total_vol"] * 100
    return merged[["date", "take_rate_pct"]].sort_values("date").reset_index(drop=True)


def _fig_take_rate(df: pd.DataFrame) -> go.Figure | None:
    """Line chart showing take rate (%) over time.

    Args:
        df: DataFrame with columns date (datetime) and take_rate_pct (float).

    Returns:
        Plotly Figure or None if df is empty or has no non-null values.
    """
    if _empty(df) or "take_rate_pct" not in df.columns or df["take_rate_pct"].dropna().empty:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["take_rate_pct"],
            mode="lines+markers",
            name="Take Rate",
            line=dict(color=EMERALD, width=2),
            marker=dict(size=5),
            hovertemplate="<b>Take Rate</b>: %{y:.2f}%<extra></extra>",
        )
    )
    layout = panel("Take Rate (%)")
    layout["yaxis"]["title"] = "%"
    layout["yaxis"]["ticksuffix"] = "%"
    fig.update_layout(**layout)
    return fig


def _fig_combined_volume(
    conv_daily: pd.DataFrame,
    card_daily: pd.DataFrame,
    fx_rate: float,
    granularity: str = "Monthly",
) -> go.Figure | None:
    """Stacked bar: conversion volume (USD) + card spend (USD) at chosen granularity.

    Args:
        conv_daily: DataFrame with columns date, onramp (BRL), offramp (BRL).
        card_daily: DataFrame with columns date, amount_usd.
        fx_rate: Period median BRL/USDC rate used to convert BRL volume to USD.
            If 0.0, conversion bars are set to 0 to avoid division by zero.
        granularity: One of 'Daily', 'Weekly', 'Monthly'.

    Returns:
        Plotly Figure or None if both inputs are empty.
    """
    conv_empty = _empty(conv_daily)
    card_empty = _empty(card_daily) or "amount_usd" not in card_daily.columns
    if conv_empty and card_empty:
        return None

    safe_rate = fx_rate if fx_rate and fx_rate > 0 else None

    if not conv_empty:
        c = conv_daily[["date"]].copy()
        c["date"] = pd.to_datetime(c["date"], errors="coerce")
        brl = conv_daily.get("onramp", pd.Series(0.0)).fillna(0.0) + conv_daily.get(
            "offramp", pd.Series(0.0)
        ).fillna(0.0)
        c["conv_usd"] = brl / safe_rate if safe_rate else pd.Series(0.0, index=c.index)
    else:
        # conv_empty is True; base the date column on card_daily
        if not card_empty:
            c = card_daily[["date"]].copy()
            c["date"] = pd.to_datetime(c["date"], errors="coerce")
        else:
            c = pd.DataFrame(columns=["date"])
        c["conv_usd"] = 0.0

    if not card_empty:
        k = card_daily[["date", "amount_usd"]].copy()
        k["date"] = pd.to_datetime(k["date"], errors="coerce")
        k = k.rename(columns={"amount_usd": "card_usd"})
    else:
        k = c[["date"]].copy()
        k["card_usd"] = 0.0

    merged = c.merge(k, on="date", how="outer").fillna(0.0).sort_values("date")
    df = _resample_combined(merged, granularity)

    totals = df["conv_usd"].fillna(0.0) + df["card_usd"].fillna(0.0)
    _t = [[v] for v in totals]

    fig = go.Figure()
    for col, label, color in [
        ("conv_usd", "Conversions (USD)", BLUE),
        ("card_usd", "Card Spend (USD)", AMBER),
    ]:
        fig.add_trace(
            go.Bar(
                x=df["date"],
                y=df[col],
                name=label,
                marker_color=color,
                customdata=_t,
                hovertemplate=(
                    f"<b>{label}</b>: $%{{y:,.0f}}"
                    "<br><b>Total</b>: $%{customdata[0]:,.0f}<extra></extra>"
                ),
            )
        )
    layout = panel("Volume (USD)")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "USD"
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


def _fig_revenue_composition_7d(daily_rev: pd.DataFrame) -> go.Figure | None:
    """Stacked bar: daily revenue by product line for the last 7 days (USD).

    Args:
        daily_rev: DataFrame with columns date, daily_rev_conversion_usd,
            daily_rev_card_fees_usd, daily_rev_billing_usd, daily_rev_swap_usd.

    Returns:
        Plotly Figure or None if data is empty.
    """
    if _empty(daily_rev):
        return None
    traces = [
        ("daily_rev_conversion_usd", "Conversions", TEAL),
        ("daily_rev_card_fees_usd", "Card Fees", AMBER),
        ("daily_rev_billing_usd", "Card Billing", VIOLET),
    ]
    fig = go.Figure()
    total_cols = [c for c, _, _ in traces if c in daily_rev.columns]
    totals = daily_rev[total_cols].sum(axis=1)
    _t = [[v] for v in totals]
    for col, label, color in traces:
        if col not in daily_rev.columns:
            continue
        fig.add_trace(
            go.Bar(
                x=daily_rev["date"],
                y=daily_rev[col],
                name=label,
                marker_color=color,
                customdata=_t,
                hovertemplate=f"<b>{label}</b>: $%{{y:,.2f}}<br><b>Total</b>: $%{{customdata[0]:,.2f}}<extra></extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=daily_rev["date"],
            y=totals,
            mode="text",
            text=[f"${v:,.0f}" for v in totals],
            textposition="top center",
            textfont={"size": 11},
            showlegend=False,
            hoverinfo="skip",
        )
    )
    layout = panel("Revenue Composition — Last 7 Days (USD)")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "USD"
    layout["xaxis"]["dtick"] = "D1"
    layout["xaxis"]["tickformat"] = "%b %d"
    fig.update_layout(**layout)
    return fig


class OverviewSection:
    """Streamlit rendering for the Overview tab (Tab 1).

    Args:
        ramp_report: Dict returned by ``OnrampReport.build()``.
        client_report: Dict returned by ``ClientReport.build()``.
        revenue_7d: DataFrame from ``OnrampQueries.daily_revenue_by_product()``
            for the last 7 days. Optional — chart is hidden when absent.
    """

    def __init__(
        self,
        ramp_report: dict,
        client_report: dict,
        revenue_7d: pd.DataFrame | None = None,
    ) -> None:
        self._r = ramp_report
        self._c = client_report
        self._rev7d = revenue_7d if revenue_7d is not None else pd.DataFrame()

    def render(self) -> None:
        """Render all overview components."""
        self._render_volume_kpis()
        col_left, col_right = st.columns(2)
        with col_left:
            self._render_funnel()
        with col_right:
            self._render_active_users()
        self._render_revenue_trend()
        self._render_combined_volume()
        self._render_take_rate()

    # ------------------------------------------------------------------
    # Private render methods
    # ------------------------------------------------------------------

    def _render_volume_kpis(self) -> None:
        """Render two labeled KPI rows: Conversions (3 cards) and Cards (3 cards)."""
        summary = _get(self._r, "summary")
        card_daily = _get(self._r, "card_daily")
        card_revenue = self._r.get("card_revenue", {})

        total_conv = int(_kpi(summary, "Total conversions") or 0)
        volume_brl = float(_kpi(summary, "Onramp volume BRL") or 0) + float(
            _kpi(summary, "Offramp volume BRL") or 0
        )
        conv_revenue_usd = float(_kpi(summary, "Total revenue USD") or 0)
        card_txns = int(card_daily["n_txns"].sum()) if not _empty(card_daily) else 0
        card_vol_usd = float(card_daily["amount_usd"].sum()) if not _empty(card_daily) else 0.0
        card_fee_usd = float(card_revenue.get("card_fee_usd", 0))
        billing_usd = float(card_revenue.get("billing_usd", 0))
        total_card_rev = card_fee_usd + billing_usd

        st.markdown(_CSS, unsafe_allow_html=True)

        st.caption("CONVERSIONS")
        c1, c2, c3 = st.columns(3)
        c1.markdown(
            _kpi_card("Conversions", f"{total_conv:,}", "completed"), unsafe_allow_html=True
        )
        c2.markdown(
            _kpi_card("Volume", fmt_brl(volume_brl), "onramp + offramp"), unsafe_allow_html=True
        )
        c3.markdown(
            _kpi_card("Revenue", fmt_usd(conv_revenue_usd), "fees + spread"),
            unsafe_allow_html=True,
        )

        st.caption("CARDS")
        d1, d2, d3 = st.columns(3)
        d1.markdown(
            _kpi_card("Card Transactions", f"{card_txns:,}", "card spend txns"),
            unsafe_allow_html=True,
        )
        d2.markdown(
            _kpi_card("Card Volume", fmt_usd(card_vol_usd), "total card spend"),
            unsafe_allow_html=True,
        )
        d3.markdown(
            _kpi_card(
                "Revenue",
                fmt_usd(total_card_rev),
                f"Fees {fmt_usd(card_fee_usd)} · Billing {fmt_usd(billing_usd)}",
            ),
            unsafe_allow_html=True,
        )

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

    def _render_revenue_composition_7d(self) -> None:
        """Render full-width stacked bar: revenue by product for the last 7 days."""
        fig = _fig_revenue_composition_7d(self._rev7d)
        if fig is None:
            st.info("No revenue data available for the last 7 days.")
            return
        col = "daily_rev_usd"
        total = float(self._rev7d[col].sum()) if col in self._rev7d.columns else 0.0
        st.metric("Total Revenue — Last 7 Days", fmt_usd(total))
        st.plotly_chart(fig, use_container_width=True)

    def _render_revenue_trend(self) -> None:
        """Render revenue stacked bar with Daily/Weekly/Monthly/Yearly toggle."""
        granularity = st.radio(
            "Granularity",
            ["Daily", "Weekly", "Monthly", "Yearly"],
            index=2,
            horizontal=True,
            key="overview_rev_gran",
        )
        if granularity in ("Daily", "Weekly"):
            rev = _get(self._r, "revenue_daily")
            card_rev = _get(self._r, "card_revenue_daily")
            if granularity == "Weekly":
                rev = _resample_revenue(rev, "Weekly") if not _empty(rev) else rev
                card_rev = _resample_revenue(card_rev, "Weekly") if not _empty(card_rev) else card_rev
        elif granularity == "Monthly":
            rev = _get(self._r, "revenue_monthly")
            if not _empty(rev) and "month" in rev.columns:
                rev = rev.rename(columns={"month": "date"})
            card_rev = _get(self._r, "card_revenue_monthly")
            if not _empty(card_rev) and "month" in card_rev.columns:
                card_rev = card_rev.rename(columns={"month": "date"})
        else:
            rev = _resample_revenue(_get(self._r, "revenue_daily"), "Yearly")
            card_rev = _resample_revenue(_get(self._r, "card_revenue_daily"), "Yearly")
        fig = _fig_monthly_revenue(rev, card_rev)
        if fig is None:
            st.info("No revenue data for this period.")
            return
        st.plotly_chart(fig, use_container_width=True)

    def _render_volume(self) -> None:
        """Render the monthly BRL volume stacked bar chart."""
        fig = _fig_volume_monthly(_get(self._r, "conv_daily"))
        if fig is None:
            st.info("No volume data for this period.")
            return
        st.plotly_chart(fig, width="stretch")

    def _render_combined_volume(self) -> None:
        """Render stacked bar: conversion + card spend volume in USD with granularity toggle."""
        conv_daily = _get(self._r, "conv_daily")
        card_daily = _get(self._r, "card_daily")
        summary = _get(self._r, "summary")

        if _empty(conv_daily) and _empty(card_daily):
            st.info("No volume data for this period.")
            return

        vol_usd = float(_kpi(summary, "Total volume USD") or 0.0)
        brl_onramp = float(_kpi(summary, "Onramp volume BRL") or 0.0)
        brl_offramp = float(_kpi(summary, "Offramp volume BRL") or 0.0)
        brl_total = brl_onramp + brl_offramp
        fx_rate = brl_total / vol_usd if vol_usd > 0 else 1.0

        granularity = st.radio(
            "Granularity",
            ["Daily", "Weekly", "Monthly"],
            horizontal=True,
            key="overview_vol_gran",
        )
        fig = _fig_combined_volume(conv_daily, card_daily, fx_rate=fx_rate, granularity=granularity)
        if fig is None:
            st.info("No volume data for this period.")
            return
        st.plotly_chart(fig, use_container_width=True)

    def _render_take_rate(self) -> None:
        """Render take rate % line chart with Daily/Weekly/Monthly/Yearly toggle."""
        summary = _get(self._r, "summary")
        vol_usd = float(_kpi(summary, "Total volume USD") or 0.0)
        brl_onramp = float(_kpi(summary, "Onramp volume BRL") or 0.0)
        brl_offramp = float(_kpi(summary, "Offramp volume BRL") or 0.0)
        brl_total = brl_onramp + brl_offramp
        fx_rate = brl_total / vol_usd if vol_usd > 0 else 1.0

        granularity = st.radio(
            "Granularity",
            ["Daily", "Weekly", "Monthly", "Yearly"],
            index=2,
            horizontal=True,
            key="overview_tr_gran",
        )

        if granularity in ("Daily", "Weekly"):
            rev = _get(self._r, "revenue_daily")
            card_rev = _get(self._r, "card_revenue_daily")
        elif granularity == "Monthly":
            rev = _get(self._r, "revenue_monthly")
            if not _empty(rev) and "month" in rev.columns:
                rev = rev.rename(columns={"month": "date"})
            card_rev = _get(self._r, "card_revenue_monthly")
            if not _empty(card_rev) and "month" in card_rev.columns:
                card_rev = card_rev.rename(columns={"month": "date"})
        else:  # Yearly
            rev = _get(self._r, "revenue_daily")
            card_rev = _get(self._r, "card_revenue_daily")

        conv_daily = _get(self._r, "conv_daily")
        card_daily = _get(self._r, "card_daily")

        df = _compute_take_rate(
            rev, card_rev, conv_daily, card_daily,
            fx_rate=fx_rate,
            granularity=granularity,
        )
        fig = _fig_take_rate(df)
        if fig is None:
            st.info("No data to compute take rate for this period.")
            return
        st.plotly_chart(fig, use_container_width=True)

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
