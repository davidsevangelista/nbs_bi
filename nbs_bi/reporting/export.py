"""PDF export for the Marketing - Ads tab.

Generates an A4 marketing briefing using ReportLab for layout and
matplotlib for chart rendering.  No external binaries (Chrome / kaleido)
are required — matplotlib is pure Python and works on every platform.

Usage::

    from nbs_bi.reporting.export import build_marketing_pdf

    pdf_bytes, errors = build_marketing_pdf(
        summary=summary,
        cum_profit_df=cum_profit_df,
        cum_rev_df=cum_rev_df,
        daily=daily,
        spend_df=spend_df,
        campaigns=campaigns,
        funnel={"signups": 120, "kyc_done": 80, "activated": 40},
        kyc_done=80,
        spend_breakdown={"meta": 1200.0, "google": 340.0},
    )
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from typing import Any

import matplotlib

matplotlib.use("Agg")  # non-interactive backend — safe for threads and servers
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from nbs_bi.reporting.theme import fmt_usd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_PAGE_W, _PAGE_H = A4  # 595.27 x 841.89 pt
_MARGIN = 18 * mm
_CONTENT_W = _PAGE_W - 2 * _MARGIN

# ReportLab colours
_DARK_TEXT = colors.HexColor("#111827")
_ACCENT = colors.HexColor("#1D4ED8")
_MUTED_RL = colors.HexColor("#6B7280")
_WHITE = colors.white
_LIGHT_BG = colors.HexColor("#F3F4F6")
_BORDER = colors.HexColor("#D1D5DB")
_HEADER_BG = colors.HexColor("#1E3A5F")
_ROW_ALT = colors.HexColor("#EFF6FF")

# Matplotlib colours (match dashboard theme)
_ROSE = "#f43f5e"
_EMERALD = "#10b981"
_VIOLET = "#8b5cf6"
_TEAL = "#14b8a6"
_AMBER = "#f59e0b"
_BLUE = "#3b82f6"
_MUTED = "#94a3b8"
_CAMPAIGN_COLORS = [_ROSE, _VIOLET, _AMBER, _TEAL]

_PDF_DPI = 150


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    """Build and return named paragraph styles for the report."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "nbs_title",
            parent=base["Title"],
            fontSize=20,
            textColor=_WHITE,
            backColor=_HEADER_BG,
            spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "nbs_subtitle",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#BFDBFE"),
            backColor=_HEADER_BG,
            spaceAfter=8,
            fontName="Helvetica",
        ),
        "section": ParagraphStyle(
            "nbs_section",
            parent=base["Heading2"],
            fontSize=12,
            textColor=_ACCENT,
            spaceBefore=10,
            spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "nbs_body",
            parent=base["Normal"],
            fontSize=9,
            textColor=_DARK_TEXT,
            spaceAfter=4,
            fontName="Helvetica",
        ),
        "kpi_label": ParagraphStyle(
            "nbs_kpi_label",
            parent=base["Normal"],
            fontSize=8,
            textColor=_MUTED_RL,
            fontName="Helvetica",
            alignment=1,
        ),
        "kpi_value": ParagraphStyle(
            "nbs_kpi_value",
            parent=base["Normal"],
            fontSize=13,
            textColor=_ACCENT,
            fontName="Helvetica-Bold",
            alignment=1,
        ),
        "table_header": ParagraphStyle(
            "nbs_th",
            parent=base["Normal"],
            fontSize=8,
            textColor=_WHITE,
            fontName="Helvetica-Bold",
            alignment=1,
        ),
        "table_cell": ParagraphStyle(
            "nbs_td",
            parent=base["Normal"],
            fontSize=8,
            textColor=_DARK_TEXT,
            fontName="Helvetica",
            alignment=1,
        ),
    }


# ---------------------------------------------------------------------------
# Matplotlib chart helpers
# ---------------------------------------------------------------------------


def _mpl_style(ax: plt.Axes, title: str) -> None:
    """Apply a clean, print-friendly style to a matplotlib Axes.

    Args:
        ax: Axes to style.
        title: Chart title text.
    """
    ax.set_title(title, fontsize=9, fontweight="bold", color="#111827", pad=6)
    ax.set_facecolor("#F8FAFC")
    ax.figure.patch.set_facecolor("white")  # type: ignore[union-attr]
    ax.grid(True, color="#E5E7EB", linewidth=0.5, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#D1D5DB")
    ax.spines["bottom"].set_color("#D1D5DB")
    ax.tick_params(colors="#6B7280", labelsize=7)
    ax.xaxis.label.set_color("#374151")
    ax.yaxis.label.set_color("#374151")


def _fig_to_rl_image(
    fig: plt.Figure,
    width_pt: float,
    height_pt: float,
    errors: list[str],
    title: str = "chart",
) -> Image | None:
    """Save a matplotlib Figure to a ReportLab Image flowable.

    Args:
        fig: matplotlib Figure to render.
        width_pt: Target width in PDF points.
        height_pt: Target height in PDF points.
        errors: Mutable list; failure messages are appended here.
        title: Chart name for error messages.

    Returns:
        ReportLab ``Image`` flowable, or None on failure.
    """
    try:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=_PDF_DPI, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return Image(buf, width=width_pt, height=height_pt)
    except Exception as exc:
        errors.append(f"{title}: {exc}")
        plt.close(fig)
        return None


# ---------------------------------------------------------------------------
# Matplotlib chart builders
# ---------------------------------------------------------------------------


def _mpl_cumulative_spend(
    spend_df: pd.DataFrame,
    campaigns: list[dict],
    cum_rev_df: pd.DataFrame | None,
) -> plt.Figure | None:
    """Cumulative spend line with optional cumulative revenue overlay.

    Args:
        spend_df: Aggregated daily spend (columns: ``date``, ``daily_spend_usd``).
        campaigns: Campaign dicts with ``start`` and ``campaign_id``.
        cum_rev_df: Optional cumulative revenue DataFrame with ``cum_rev_usd``.

    Returns:
        matplotlib Figure or None if spend_df is empty.
    """
    if spend_df.empty:
        return None
    df = spend_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    df["cum_spend"] = df["daily_spend_usd"].cumsum()

    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    _mpl_style(ax, "Cumulative Ad Spend vs Cohort Revenue (USD)")
    ax.plot(df["date"], df["cum_spend"], color=_ROSE, lw=2, label="Cumulative Spend")

    if cum_rev_df is not None and not cum_rev_df.empty and "cum_rev_usd" in cum_rev_df.columns:
        rev = cum_rev_df.copy()
        rev["date"] = pd.to_datetime(rev["date"])
        ax.plot(rev["date"], rev["cum_rev_usd"], color=_EMERALD, lw=2, label="Cohort Revenue")

    ymax = ax.get_ylim()[1]
    for c in campaigns:
        x_ts = pd.Timestamp(c["start"])
        ax.axvline(x_ts, color=_MUTED, lw=0.8, ls="--", alpha=0.7)
        ax.text(
            x_ts,
            ymax * 0.95,
            c["campaign_id"],
            fontsize=6,
            color=_MUTED,
            rotation=90,
            va="top",
            ha="right",
        )

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_xlabel("Date", fontsize=7)
    ax.set_ylabel("USD", fontsize=7)
    ax.legend(fontsize=7)
    fig.tight_layout()
    return fig


def _mpl_roas_over_time(
    cum_profit_df: pd.DataFrame,
    spend_df: pd.DataFrame,
) -> plt.Figure | None:
    """Running ROAS trajectory: cumulative revenue / cumulative spend.

    Args:
        cum_profit_df: Cumulative profit DataFrame with ``cum_rev_usd``.
        spend_df: Aggregated daily spend with ``daily_spend_usd``.

    Returns:
        matplotlib Figure or None if data is insufficient.
    """
    if (
        cum_profit_df is None
        or cum_profit_df.empty
        or spend_df.empty
        or "cum_rev_usd" not in cum_profit_df.columns
    ):
        return None

    spend = spend_df.copy()
    spend["date"] = pd.to_datetime(spend["date"]).dt.normalize()
    spend = spend.sort_values("date")
    spend["cum_spend"] = spend["daily_spend_usd"].cumsum()

    rev = cum_profit_df[["date", "cum_rev_usd"]].copy()
    rev["date"] = pd.to_datetime(rev["date"]).dt.normalize()

    merged = rev.merge(spend[["date", "cum_spend"]], on="date", how="left")
    merged["cum_spend"] = merged["cum_spend"].ffill().fillna(0)
    merged = merged[merged["cum_spend"] > 0].copy()
    if merged.empty:
        return None

    merged["roas"] = merged["cum_rev_usd"] / merged["cum_spend"]

    fig, ax = plt.subplots(figsize=(7.5, 2.8))
    _mpl_style(ax, "Running ROAS — Latest Cohort")
    ax.plot(pd.to_datetime(merged["date"]), merged["roas"], color=_VIOLET, lw=2)
    ax.axhline(1.0, color=_MUTED, lw=1, ls="--", label="Break-even (1×)")

    last_roas = float(merged["roas"].iloc[-1])
    last_date = pd.to_datetime(merged["date"].iloc[-1])
    ax.annotate(
        f"  {last_roas:.2f}×",
        xy=(last_date, last_roas),
        fontsize=8,
        color=_VIOLET,
        fontweight="bold",
    )

    ax.legend(fontsize=7)
    ax.set_xlabel("Date", fontsize=7)
    ax.set_ylabel("ROAS", fontsize=7)
    fig.tight_layout()
    return fig


_CHARCOAL = "#1C1C1C"
_ORANGE = "#F97316"


def _mpl_revenue_breakdown(cum_profit_df: pd.DataFrame) -> plt.Figure | None:
    """Stacked-area cumulative revenue breakdown with operational and overall profit lines.

    Args:
        cum_profit_df: Cumulative profit DataFrame with per-source columns.

    Returns:
        matplotlib Figure or None if required columns are missing.
    """
    required = {
        "date",
        "cum_rev_conversion_usd",
        "cum_rev_card_fees_usd",
        "cum_rev_billing_usd",
        "cum_rev_swap_usd",
        "cum_cost_cashback_usd",
        "cum_cost_rev_share_usd",
    }
    if cum_profit_df is None or cum_profit_df.empty or not required.issubset(cum_profit_df.columns):
        return None

    df = cum_profit_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    x = df["date"]

    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    _mpl_style(ax, "Cumulative Revenue & Profit Breakdown — Latest Cohort (USD)")

    revenue_layers = [
        ("cum_rev_conversion_usd", _EMERALD, "Conversion Spread"),
        ("cum_rev_card_fees_usd", _TEAL, "Card Fees"),
        ("cum_rev_billing_usd", _BLUE, "Billing"),
        ("cum_rev_swap_usd", _AMBER, "Swap Fees"),
    ]
    baseline = np.zeros(len(df))
    for col, color, label in revenue_layers:
        y = df[col].fillna(0).values
        ax.fill_between(x, baseline, baseline + y, alpha=0.55, color=color, label=label)
        baseline = baseline + y

    for col, color, label in [
        ("cum_cost_cashback_usd", _ROSE, "Cashback"),
        ("cum_cost_rev_share_usd", _VIOLET, "Rev Share"),
    ]:
        ax.plot(x, -df[col].fillna(0), color=color, lw=1.2, ls="--", label=label)

    # Breakeven reference
    ax.axhline(0, color="#6B7280", lw=0.8, ls=":", zorder=1)

    # Profit lines
    if "cum_profit_usd" in df.columns:
        ax.plot(
            x,
            df["cum_profit_usd"].fillna(0),
            color=_CHARCOAL,
            lw=2.0,
            label="Operational Profit",
            zorder=5,
        )
    if "cum_contribution_margin_usd" in df.columns:
        ax.plot(
            x,
            df["cum_contribution_margin_usd"].fillna(0),
            color=_ORANGE,
            lw=2.0,
            label="Overall Profit (incl. Mkt)",
            zorder=5,
        )

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=6, ncol=3)
    ax.set_xlabel("Date", fontsize=7)
    ax.set_ylabel("USD", fontsize=7)
    fig.tight_layout()
    return fig


_PLATFORM_COLORS: dict[str, str] = {"meta": _ROSE, "google": _BLUE}
_FALLBACK_PLATFORM_COLORS = [_AMBER, _TEAL, _VIOLET]


def _mpl_campaign_daily(
    daily: pd.DataFrame,
    spend_df_raw: pd.DataFrame | None = None,
) -> plt.Figure | None:
    """Daily signups (stacked bars per campaign) with per-platform spend lines.

    Args:
        daily: ``CampaignAnalyzer.daily_context()`` output.
        spend_df_raw: Raw spend DataFrame with a ``platform`` column; used to
            draw separate spend lines per platform on the secondary axis.
            Falls back to the aggregated ``daily_spend_usd`` if not provided.

    Returns:
        matplotlib Figure or None if daily is empty.
    """
    if daily is None or daily.empty:
        return None

    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"])

    fig, ax1 = plt.subplots(figsize=(7.5, 3.2))
    ax2 = ax1.twinx()
    _mpl_style(ax1, "Daily Signups vs Ad Spend")

    bar_w = np.timedelta64(16, "h")
    campaign_ids = [c for c in df["campaign_id"].unique() if c]
    for i, cid in enumerate(campaign_ids):
        mask = df["campaign_id"] == cid
        ax1.bar(
            df.loc[mask, "date"],
            df.loc[mask, "new_signups"],
            width=bar_w,
            color=_CAMPAIGN_COLORS[i % len(_CAMPAIGN_COLORS)],
            alpha=0.7,
            label=cid,
        )

    organic = df["campaign_id"] == ""
    if organic.any():
        ax1.bar(
            df.loc[organic, "date"],
            df.loc[organic, "new_signups"],
            width=bar_w,
            color=_BLUE,
            alpha=0.4,
            label="Organic",
        )

    # Build per-platform daily series from raw spend data if available
    platform_spend: dict[str, pd.Series] = {}
    if spend_df_raw is not None and not spend_df_raw.empty and "platform" in spend_df_raw.columns:
        grp = spend_df_raw.copy()
        grp["date"] = pd.to_datetime(grp["date"])
        for plat, sub in grp.groupby("platform"):
            series = sub.groupby("date")["daily_spend_usd"].sum()
            platform_spend[str(plat)] = series

    any_spend = False

    # Total spend line (thin, muted) — always from daily aggregate
    total_spending = df["daily_spend_usd"] > 0
    if total_spending.any():
        ax2.plot(
            df.loc[total_spending, "date"],
            df.loc[total_spending, "daily_spend_usd"],
            color=_MUTED,
            lw=1.0,
            ls="--",
            label="Total Spend",
            alpha=0.7,
        )
        any_spend = True

    # Per-platform lines (thicker, colored)
    for i, (plat, series) in enumerate(sorted(platform_spend.items())):
        series = series[series > 0]
        if series.empty:
            continue
        color = _PLATFORM_COLORS.get(
            plat.lower(), _FALLBACK_PLATFORM_COLORS[i % len(_FALLBACK_PLATFORM_COLORS)]
        )
        ax2.plot(
            series.index,
            series.values,
            color=color,
            lw=1.8,
            ls="--",
            marker="o",
            markersize=3,
            label=f"{plat.capitalize()} Spend",
        )
        any_spend = True

    if any_spend:
        ax2.set_ylabel("Daily Spend (USD)", fontsize=7, color="#6B7280")
        ax2.tick_params(colors="#6B7280", labelsize=6)
        ax2.spines["right"].set_color("#D1D5DB")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax2.legend(fontsize=6, loc="upper right")

    ax1.set_xlabel("Date", fontsize=7)
    ax1.set_ylabel("New Signups", fontsize=7)
    ax1.legend(fontsize=6, loc="upper left")
    fig.tight_layout()
    return fig


def _mpl_campaign_roi(summary: pd.DataFrame) -> plt.Figure | None:
    """Grouped bar: ad spend vs cohort revenue per campaign.

    Args:
        summary: ``CampaignAnalyzer.roi_summary()`` output.

    Returns:
        matplotlib Figure or None if summary is empty.
    """
    if summary is None or summary.empty:
        return None

    campaign_ids = summary["campaign_id"].tolist()
    x = np.arange(len(campaign_ids))
    w = 0.35

    fig, ax = plt.subplots(figsize=(4.0, 3.2))
    _mpl_style(ax, "Spend vs Revenue by Campaign")
    ax.bar(x - w / 2, summary["total_spend_usd"], w, label="Ad Spend", color=_ROSE, alpha=0.85)
    ax.bar(
        x + w / 2,
        summary["total_revenue_usd"],
        w,
        label="Cohort Revenue",
        color=_EMERALD,
        alpha=0.85,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(campaign_ids, fontsize=7)
    ax.legend(fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylabel("USD", fontsize=7)
    fig.tight_layout()
    return fig


def _mpl_campaign_cac(summary: pd.DataFrame) -> plt.Figure | None:
    """Grouped bar: CAC (active users) vs CAC (incremental) per campaign.

    Args:
        summary: ``CampaignAnalyzer.roi_summary()`` output.

    Returns:
        matplotlib Figure or None if summary is empty or CAC column missing.
    """
    if summary is None or summary.empty or "cac_full" not in summary.columns:
        return None

    campaign_ids = summary["campaign_id"].tolist()
    x = np.arange(len(campaign_ids))
    has_incr = summary["cac_incremental"].notna().any()
    w = 0.35 if has_incr else 0.5

    fig, ax = plt.subplots(figsize=(4.0, 3.2))
    _mpl_style(ax, "Customer Acquisition Cost (USD)")
    offset = -w / 2 if has_incr else 0
    ax.bar(
        x + offset,
        summary["cac_full"].fillna(0),
        w,
        label="CAC (active users)",
        color=_AMBER,
        alpha=0.85,
    )
    if has_incr:
        valid = summary["cac_incremental"].notna()
        ax.bar(
            x[valid.values] + w / 2,
            summary.loc[valid, "cac_incremental"],
            w,
            label="CAC (incremental)",
            color=_VIOLET,
            alpha=0.85,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(campaign_ids, fontsize=7)
    ax.legend(fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.2f}"))
    ax.set_ylabel("USD / User", fontsize=7)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# KPI table
# ---------------------------------------------------------------------------


def _build_kpi_table(
    kpis: list[tuple[str, str]],
    styles: dict[str, ParagraphStyle],
    content_w: float,
) -> Table:
    """Horizontal KPI strip table.

    Args:
        kpis: List of ``(label, value)`` tuples.
        styles: Style dict from :func:`_styles`.
        content_w: Available width in PDF points.

    Returns:
        ReportLab ``Table`` flowable.
    """
    n = len(kpis)
    col_w = content_w / n
    header_row = [Paragraph(label, styles["kpi_label"]) for label, _ in kpis]
    value_row = [Paragraph(value, styles["kpi_value"]) for _, value in kpis]
    tbl = Table([header_row, value_row], colWidths=[col_w] * n, rowHeights=[14, 22])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _LIGHT_BG),
                ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return tbl


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------


def _build_summary_table(
    summary: pd.DataFrame,
    styles: dict[str, ParagraphStyle],
    content_w: float,
) -> Table | None:
    """Formatted campaign summary table.

    Args:
        summary: ``CampaignAnalyzer.roi_summary()`` output.
        styles: Style dict from :func:`_styles`.
        content_w: Available width in PDF points.

    Returns:
        ReportLab ``Table`` flowable, or None if summary is empty.
    """
    if summary.empty:
        return None

    display_cols = {
        "campaign_id": "Campaign",
        "cohort_users": "Sign-ups",
        "transacting_users": "Activated",
        "total_spend_usd": "Spend (USD)",
        "total_revenue_usd": "Revenue (USD)",
        "roas": "ROAS",
        "cac_full": "CAC (USD)",
    }
    present = [c for c in display_cols if c in summary.columns]
    headers = [Paragraph(display_cols[c], styles["table_header"]) for c in present]

    def _fmt_cell(col: str, val: Any) -> str:
        if pd.isna(val):
            return "n/a"
        if col in ("total_spend_usd", "total_revenue_usd", "cac_full"):
            return fmt_usd(float(val))
        if col == "roas":
            return f"{float(val):.2f}×"
        return str(val)

    rows: list[list] = [headers]
    for _, row in summary[present].iterrows():
        rows.append([Paragraph(_fmt_cell(c, row[c]), styles["table_cell"]) for c in present])

    col_w = content_w / len(present)
    tbl = Table(rows, colWidths=[col_w] * len(present))
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("BACKGROUND", (0, 1), (-1, -1), _WHITE),
                ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_WHITE, _ROW_ALT]),
            ]
        )
    )
    return tbl


# ---------------------------------------------------------------------------
# Funnel text block
# ---------------------------------------------------------------------------


def _funnel_paragraph(funnel: dict, styles: dict[str, ParagraphStyle]) -> Paragraph:
    """Render funnel counts and conversion rates as a text paragraph.

    Args:
        funnel: Dict with ``signups``, ``kyc_done``, ``activated``.
        styles: Style dict from :func:`_styles`.

    Returns:
        ReportLab ``Paragraph`` flowable.
    """
    signups = funnel.get("signups", 0)
    kyc = funnel.get("kyc_done", 0)
    activated = funnel.get("activated", 0)
    kyc_pct = 100 * kyc / max(signups, 1)
    act_pct = 100 * activated / max(kyc, 1)
    text = (
        f"Sign-ups: <b>{signups:,}</b>"
        f" &rarr; KYC Done: <b>{kyc:,}</b> ({kyc_pct:.1f}% of sign-ups)"
        f" &rarr; Activated: <b>{activated:,}</b> ({act_pct:.1f}% of KYC done)"
    )
    return Paragraph(text, styles["body"])


# ---------------------------------------------------------------------------
# Payback period helper
# ---------------------------------------------------------------------------


def _payback_days(cum_profit_df: pd.DataFrame | None) -> int | None:
    """Return days from cohort start until cumulative operational profit turns positive.

    Args:
        cum_profit_df: ``CampaignAnalyzer.cumulative_profit()`` output.

    Returns:
        Number of days to payback, or None if still negative / data unavailable.
    """
    if (
        cum_profit_df is None
        or cum_profit_df.empty
        or "cum_profit_usd" not in cum_profit_df.columns
    ):
        return None
    positive = cum_profit_df[cum_profit_df["cum_profit_usd"] > 0]
    if positive.empty:
        return None
    first = pd.to_datetime(cum_profit_df["date"].iloc[0])
    breakeven = pd.to_datetime(positive["date"].iloc[0])
    return int((breakeven - first).days)


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_marketing_pdf(
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
) -> tuple[bytes, list[str]]:
    """Generate an A4 PDF marketing briefing from live analysis data.

    Uses matplotlib for chart rendering — no external binaries required.

    Args:
        summary: ``CampaignAnalyzer.roi_summary()`` (latest campaign rows).
        cum_profit_df: ``CampaignAnalyzer.cumulative_profit()`` output.
        cum_rev_df: ``CampaignAnalyzer.cumulative_revenue()`` output.
        daily: ``CampaignAnalyzer.daily_context()`` output.
        spend_df: Date-aggregated spend DataFrame (``date``, ``daily_spend_usd``).
        campaigns: List of campaign dicts from ``CampaignAnalyzer.campaigns``.
        funnel: Dict with ``signups``, ``kyc_done``, ``activated`` counts.
        kyc_done: Count of cohort users who completed KYC (kyc_level >= 1).
        spend_breakdown: Optional per-platform spend totals,
            e.g. ``{"meta": 1200.0, "google": 340.0}``.
        spend_df_raw: Optional raw spend DataFrame with a ``platform`` column;
            used to draw per-platform lines in the Daily Signups chart.

    Returns:
        Tuple of ``(pdf_bytes, chart_errors)`` where ``chart_errors`` lists
        human-readable strings for any charts that failed to render.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
    )

    s = _styles()
    story: list[Any] = []
    chart_errors: list[str] = []

    _add_header(story, s)
    _add_kpi_strip(story, s, summary, cum_profit_df, kyc_done, spend_breakdown)
    _add_funnel(story, s, funnel)
    _add_charts(
        story,
        s,
        summary,
        cum_profit_df,
        cum_rev_df,
        daily,
        spend_df,
        campaigns,
        chart_errors,
        spend_df_raw=spend_df_raw,
    )
    _add_summary_table(story, s, summary)

    doc.build(story)
    return buf.getvalue(), chart_errors


# ---------------------------------------------------------------------------
# Story section builders
# ---------------------------------------------------------------------------


def _add_header(story: list[Any], s: dict[str, ParagraphStyle]) -> None:
    """Append report title and generation timestamp."""
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph("NBS — Marketing Analysis", s["title"]))
    story.append(
        Paragraph(
            f"Meta Ads · Google Ads · Cohort ROI Briefing &nbsp;|&nbsp; Generated {now}",
            s["subtitle"],
        )
    )
    story.append(HRFlowable(width=_CONTENT_W, thickness=1.5, color=_ACCENT, spaceAfter=8))


def _add_kpi_strip(
    story: list[Any],
    s: dict[str, ParagraphStyle],
    summary: pd.DataFrame,
    cum_profit_df: pd.DataFrame | None,
    kyc_done: int,
    spend_breakdown: dict[str, float] | None = None,
) -> None:
    """Append KPI strip (spend, revenue, ROAS, CAC, net profit + secondary metrics)."""
    from nbs_bi.clients.models import _KYC_COST_USD

    total_spend = float(summary["total_spend_usd"].sum())
    total_rev = float(summary["total_revenue_usd"].sum())
    transacting = int(summary["transacting_users"].sum())
    cohort_users = int(summary["cohort_users"].sum()) if "cohort_users" in summary.columns else 0
    overall_roas = total_rev / total_spend if total_spend > 0 else 0.0
    kyc_cost = kyc_done * _KYC_COST_USD
    cac = (total_spend + kyc_cost) / transacting if transacting > 0 else float("nan")

    has_profit = (
        cum_profit_df is not None
        and not cum_profit_df.empty
        and "cum_contribution_margin_usd" in cum_profit_df.columns
    )

    story.append(Paragraph("Key Performance Indicators", s["section"]))

    # --- Primary KPI row: per-platform spend tiles OR total spend ---
    multi = bool(spend_breakdown and len(spend_breakdown) > 1)
    if multi:
        primary: list[tuple[str, str]] = [
            (f"{plat.capitalize()} Spend", fmt_usd(amt))
            for plat, amt in sorted(spend_breakdown.items())  # type: ignore[union-attr]
        ]
    else:
        primary = [("Total Spend", fmt_usd(total_spend))]

    primary += [
        ("Cohort Revenue", fmt_usd(total_rev)),
        ("Overall ROAS", f"{overall_roas:.2f}×"),
        ("CAC (spend + KYC)", fmt_usd(cac) if not np.isnan(cac) else "n/a"),
    ]
    if has_profit:
        net = float(cum_profit_df["cum_contribution_margin_usd"].iloc[-1])  # type: ignore[index]
        primary.append(("Net Profit", fmt_usd(net)))

    story.append(_build_kpi_table(primary, s, _CONTENT_W))
    story.append(Spacer(1, 4))

    # --- Secondary KPI row: operational metrics ---
    secondary: list[tuple[str, str]] = []

    if cohort_users > 0 and transacting > 0:
        tx_rate = 100.0 * transacting / cohort_users
        secondary.append(("Transacting Rate", f"{tx_rate:.1f}%"))

    if kyc_done > 0 and total_spend > 0:
        cost_per_kyc = total_spend / kyc_done
        secondary.append(("Cost per KYC", fmt_usd(cost_per_kyc)))

    payback = _payback_days(cum_profit_df)
    secondary.append(("Payback Period", f"{payback}d" if payback is not None else "not yet"))

    if secondary:
        story.append(_build_kpi_table(secondary, s, _CONTENT_W))
        story.append(Spacer(1, 4))


def _add_funnel(story: list[Any], s: dict[str, ParagraphStyle], funnel: dict) -> None:
    """Append cohort activation funnel with conversion rates."""
    if not funnel or funnel.get("signups", 0) == 0:
        return
    story.append(Paragraph("Cohort Activation Funnel", s["section"]))
    story.append(_funnel_paragraph(funnel, s))
    story.append(Spacer(1, 6))


def _add_charts(
    story: list[Any],
    s: dict[str, ParagraphStyle],
    summary: pd.DataFrame,
    cum_profit_df: pd.DataFrame | None,
    cum_rev_df: pd.DataFrame | None,
    daily: pd.DataFrame,
    spend_df: pd.DataFrame,
    campaigns: list[dict],
    errors: list[str],
    spend_df_raw: pd.DataFrame | None = None,
) -> None:
    """Render all matplotlib charts and append as ReportLab Image flowables."""
    story.append(Paragraph("Campaign Charts", s["section"]))

    full_w = _CONTENT_W
    half_w = _CONTENT_W / 2 - 3
    h_full = 155.0
    h_half = 140.0

    # Full-width charts
    full_charts: list[tuple[plt.Figure | None, str]] = [
        (_mpl_cumulative_spend(spend_df, campaigns, cum_rev_df), "Cumulative Spend"),
        (
            _mpl_roas_over_time(cum_profit_df, spend_df) if cum_profit_df is not None else None,
            "ROAS Over Time",
        ),
        (_mpl_revenue_breakdown(cum_profit_df), "Revenue Breakdown"),
        (_mpl_campaign_daily(daily, spend_df_raw=spend_df_raw), "Daily Signups"),
    ]
    for fig, title in full_charts:
        if fig is None:
            errors.append(f"{title}: no data")
            continue
        img = _fig_to_rl_image(fig, full_w, h_full, errors, title)
        if img:
            story.append(img)
            story.append(Spacer(1, 4))

    # Side-by-side: ROI bar + CAC bar
    pairs = [
        (_mpl_campaign_roi(summary), "Spend vs Revenue"),
        (_mpl_campaign_cac(summary), "CAC"),
    ]
    cells: list[Any] = []
    for fig, title in pairs:
        if fig is None:
            cells.append("")
            errors.append(f"{title}: no data")
        else:
            img = _fig_to_rl_image(fig, half_w, h_half, errors, title)
            cells.append(img if img else "")

    if any(c != "" for c in cells):
        tbl = Table([cells], colWidths=[half_w, half_w])
        tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(tbl)
        story.append(Spacer(1, 4))


def _add_summary_table(
    story: list[Any],
    s: dict[str, ParagraphStyle],
    summary: pd.DataFrame,
) -> None:
    """Append the campaign summary table."""
    tbl = _build_summary_table(summary, s, _CONTENT_W)
    if tbl is None:
        return
    story.append(Paragraph("Campaign Summary", s["section"]))
    story.append(tbl)
