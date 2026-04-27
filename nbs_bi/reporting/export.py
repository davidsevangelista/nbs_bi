"""PDF export for the Marketing - Ads tab.

Generates a professional A4 marketing briefing document from the live
analysis data using ReportLab for layout and Plotly/kaleido for chart
image rendering.

Usage::

    from nbs_bi.reporting.export import build_marketing_pdf

    pdf_bytes = build_marketing_pdf(
        summary=summary,
        cum_profit_df=cum_profit_df,
        cum_rev_df=cum_rev_df,
        daily=daily,
        spend_df=spend_df,
        campaigns=campaigns,
        funnel={"signups": 120, "kyc_done": 80, "activated": 40},
        kyc_done=80,
    )
"""

from __future__ import annotations

import copy
import io
import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
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

from nbs_bi.reporting.marketing import (
    _build_cumulative_spend,
    _fig_campaign_cac,
    _fig_campaign_daily,
    _fig_campaign_roi,
    _fig_cumulative_profit,
    _fig_cumulative_spend,
    _fig_revenue_breakdown,
)
from nbs_bi.reporting.theme import fmt_usd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_PAGE_W, _PAGE_H = A4  # 595.27 x 841.89 pt
_MARGIN = 18 * mm
_CONTENT_W = _PAGE_W - 2 * _MARGIN

# Light-theme colours for print
_DARK_TEXT = colors.HexColor("#111827")
_ACCENT = colors.HexColor("#1D4ED8")
_MUTED = colors.HexColor("#6B7280")
_WHITE = colors.white
_LIGHT_BG = colors.HexColor("#F3F4F6")
_BORDER = colors.HexColor("#D1D5DB")
_HEADER_BG = colors.HexColor("#1E3A5F")
_ROW_ALT = colors.HexColor("#EFF6FF")


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    """Build and return named paragraph styles for the report.

    Returns:
        Dict mapping style name to ``ParagraphStyle`` instance.
    """
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
            textColor=_MUTED,
            fontName="Helvetica",
            alignment=1,
        ),
        "kpi_value": ParagraphStyle(
            "nbs_kpi_value",
            parent=base["Normal"],
            fontSize=14,
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
# Figure → PNG bytes
# ---------------------------------------------------------------------------


def _strip_string_axis_shapes(fig_dict: dict) -> None:
    """Remove per-data-point vline shapes that use string x-axis references.

    ``add_vline`` on a string (categorical) x-axis produces shapes with
    ``xref="x"`` and a string ``x0``/``x1``. These can cause kaleido's static
    renderer to fail silently. Campaign-start marker shapes (also ``xref="x"``)
    are kept if there are ≤ 5 of them (typically one per campaign).

    Args:
        fig_dict: Mutable Plotly figure dict (modified in-place).
    """
    shapes = fig_dict.get("layout", {}).get("shapes", []) or []
    x_shapes = [s for s in shapes if s.get("xref") == "x"]
    non_x_shapes = [s for s in shapes if s.get("xref") != "x"]
    # Keep campaign markers (few) but drop per-day spend markers (many)
    keep_x = x_shapes if len(x_shapes) <= 6 else []
    fig_dict["layout"]["shapes"] = non_x_shapes + keep_x

    # Also trim annotations to avoid per-day label clutter
    annotations = fig_dict.get("layout", {}).get("annotations", []) or []
    x_annots = [a for a in annotations if a.get("xref") == "x"]
    non_x_annots = [a for a in annotations if a.get("xref") != "x"]
    keep_annots = x_annots if len(x_annots) <= 6 else []
    fig_dict["layout"]["annotations"] = non_x_annots + keep_annots


def _apply_light_theme(fig_dict: dict) -> None:
    """Apply a print-friendly white theme to a Plotly figure dict in-place.

    Args:
        fig_dict: Mutable Plotly figure dict (modified in-place).
    """
    layout = fig_dict.setdefault("layout", {})
    layout.update(
        paper_bgcolor="white",
        plot_bgcolor="#F8FAFC",
        font={"color": "#111827", "family": "Helvetica, Arial, sans-serif"},
    )
    for ax in ("xaxis", "yaxis", "yaxis2"):
        axis = layout.setdefault(ax, {})
        axis.update(
            gridcolor="#E5E7EB",
            linecolor="#9CA3AF",
            tickfont={"color": "#111827"},
            title_font={"color": "#374151"},
            zerolinecolor="#D1D5DB",
        )
    legend = layout.setdefault("legend", {})
    legend.update(font={"color": "#111827"}, bgcolor="white", bordercolor="#D1D5DB")

    # Lighten annotation text so it reads on white
    for ann in layout.get("annotations", []) or []:
        if ann.get("font", {}).get("color") in ("#8B949E", "#6B7280", "rgba(139,148,158,1)"):
            ann["font"]["color"] = "#374151"


def _render_light_fig(fig: go.Figure, px_w: int, px_h: int) -> bytes:
    """Render a light-themed Plotly figure to PNG bytes via kaleido.

    Tries ``plotly.io.to_image`` first (more robust in multi-threaded
    contexts such as Streamlit) and falls back to ``fig.to_image``.
    Raises the last exception if both attempts fail so callers can surface
    the error rather than silently producing a chart-free PDF.

    Args:
        fig: Light-themed Plotly figure (already themed and shape-stripped).
        px_w: Output width in pixels.
        px_h: Output height in pixels.

    Returns:
        PNG bytes.

    Raises:
        Exception: If both kaleido invocations fail.
    """
    import plotly.io as pio

    last_exc: Exception | None = None

    try:
        png = pio.to_image(fig, format="png", width=px_w, height=px_h, engine="kaleido")
        if png:
            return png
    except Exception as exc:  # noqa: BLE001
        log.warning("pio.to_image failed (%s), retrying via fig.to_image", exc)
        last_exc = exc

    try:
        png = fig.to_image(format="png", width=px_w, height=px_h, engine="kaleido")
        if png:
            return png
    except Exception as exc:  # noqa: BLE001
        last_exc = exc

    raise RuntimeError("kaleido could not render figure") from last_exc


def _fig_to_image(fig: go.Figure, width_pt: float, height_pt: float) -> Image | None:
    """Render a Plotly figure to a ReportLab Image at the given dimensions.

    Converts to a white-background, print-friendly variant by operating on
    the figure's dict representation (safe deep copy). Strips per-day vlines
    that break kaleido's static renderer on categorical x-axes.

    Args:
        fig: Plotly figure to render.
        width_pt: Target width in PDF points.
        height_pt: Target height in PDF points.

    Returns:
        ReportLab ``Image`` flowable, or None if rendering fails.

    Raises:
        RuntimeError: Propagates kaleido failure so ``_render_export_button``
            can display it via ``st.error``.
    """
    px_w = int(width_pt * 2)  # 2× for crisp output
    px_h = int(height_pt * 2)

    fig_dict = copy.deepcopy(fig.to_dict())
    _strip_string_axis_shapes(fig_dict)
    _apply_light_theme(fig_dict)
    light_fig = go.Figure(fig_dict)

    png_bytes = _render_light_fig(light_fig, px_w, px_h)

    if not png_bytes:
        log.warning("kaleido returned empty bytes for figure")
        return None

    buf = io.BytesIO(png_bytes)
    return Image(buf, width=width_pt, height=height_pt)


# ---------------------------------------------------------------------------
# KPI tile table
# ---------------------------------------------------------------------------


def _build_kpi_table(
    kpis: list[tuple[str, str]],
    styles: dict[str, ParagraphStyle],
    content_w: float,
) -> Table:
    """Build a horizontal KPI strip table.

    Args:
        kpis: List of (label, value) tuples.
        styles: Style dict from :func:`_styles`.
        content_w: Available width in PDF points.

    Returns:
        ReportLab ``Table`` flowable styled as light KPI tiles.
    """
    n = len(kpis)
    col_w = content_w / n
    header_row = [Paragraph(label, styles["kpi_label"]) for label, _ in kpis]
    value_row = [Paragraph(value, styles["kpi_value"]) for _, value in kpis]

    tbl = Table(
        [header_row, value_row],
        colWidths=[col_w] * n,
        rowHeights=[14, 22],
    )
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
    """Build a formatted campaign summary table flowable.

    Args:
        summary: Output of ``CampaignAnalyzer.roi_summary()``.
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

    rows = [headers]
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
    """Render funnel numbers as a compact text paragraph.

    Args:
        funnel: Dict with ``signups``, ``kyc_done``, ``activated`` keys.
        styles: Style dict from :func:`_styles`.

    Returns:
        ReportLab ``Paragraph`` flowable.
    """
    signups = funnel.get("signups", 0)
    kyc = funnel.get("kyc_done", 0)
    activated = funnel.get("activated", 0)
    total = signups or 1
    kyc_pct = 100 * kyc / total
    act_pct = 100 * activated / total
    text = (
        f"Sign-ups: <b>{signups:,}</b> "
        f"&rarr; KYC Done: <b>{kyc:,}</b> ({kyc_pct:.1f}%) "
        f"&rarr; Activated: <b>{activated:,}</b> ({act_pct:.1f}%)"
    )
    return Paragraph(text, styles["body"])


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
) -> bytes:
    """Generate an A4 PDF marketing briefing from live analysis data.

    Renders KPIs, cohort activation funnel, key Plotly charts (converted to
    PNG via kaleido), and the campaign summary table into a single A4 document
    using ReportLab's Platypus layout engine.

    Args:
        summary: Output of ``CampaignAnalyzer.roi_summary()`` (latest campaign
            rows only — same slice used in the dashboard).
        cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()``.
        cum_rev_df: Output of ``CampaignAnalyzer.cumulative_revenue()``.
        daily: Output of ``CampaignAnalyzer.daily_context()``.
        spend_df: Date-filtered ad spend DataFrame (columns: ``date``,
            ``daily_spend_usd``).
        campaigns: List of campaign dicts from ``CampaignAnalyzer.campaigns``.
        funnel: Dict with ``signups``, ``kyc_done``, ``activated`` counts.
        kyc_done: Count of cohort users who completed KYC (kyc_level >= 1).

    Returns:
        Raw PDF file as bytes, ready for ``st.download_button``.
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

    _add_header(story, s)
    _add_kpi_strip(story, s, summary, cum_profit_df, kyc_done)
    _add_funnel(story, s, funnel)
    _add_charts(story, s, summary, cum_profit_df, cum_rev_df, daily, spend_df, campaigns)
    _add_summary_table(story, s, summary)

    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Story section builders (each ≤ 50 lines)
# ---------------------------------------------------------------------------


def _add_header(story: list[Any], s: dict[str, ParagraphStyle]) -> None:
    """Append report title and generation timestamp to story.

    Args:
        story: ReportLab story list to append flowables to.
        s: Style dict from :func:`_styles`.
    """
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph("NBS SPSAV LTDA — Marketing Analysis", s["title"]))
    subtitle = f"Meta Ads · Cohort ROI Briefing &nbsp;&nbsp;|&nbsp;&nbsp; Generated {now}"
    story.append(Paragraph(subtitle, s["subtitle"]))
    story.append(HRFlowable(width=_CONTENT_W, thickness=1.5, color=_ACCENT, spaceAfter=8))


def _add_kpi_strip(
    story: list[Any],
    s: dict[str, ParagraphStyle],
    summary: pd.DataFrame,
    cum_profit_df: pd.DataFrame | None,
    kyc_done: int,
) -> None:
    """Append the KPI strip (spend, revenue, ROAS, CAC, net profit) to story.

    Args:
        story: ReportLab story list.
        s: Style dict.
        summary: ``roi_summary()`` DataFrame.
        cum_profit_df: ``cumulative_profit()`` DataFrame (may be None).
        kyc_done: KYC-completed count for CAC calculation.
    """
    from nbs_bi.clients.models import _KYC_COST_USD

    total_spend = float(summary["total_spend_usd"].sum())
    total_rev = float(summary["total_revenue_usd"].sum())
    transacting = int(summary["transacting_users"].sum())
    overall_roas = total_rev / total_spend if total_spend > 0 else 0.0
    kyc_cost = kyc_done * _KYC_COST_USD
    cac = (total_spend + kyc_cost) / transacting if transacting > 0 else float("nan")

    has_profit = (
        cum_profit_df is not None
        and not cum_profit_df.empty
        and "cum_contribution_margin_usd" in cum_profit_df.columns
    )

    kpis: list[tuple[str, str]] = [
        ("Total Meta Spend", fmt_usd(total_spend)),
        ("Cohort Revenue", fmt_usd(total_rev)),
        ("Overall ROAS", f"{overall_roas:.2f}×"),
        ("CAC (spend + KYC)", fmt_usd(cac) if not np.isnan(cac) else "n/a"),
    ]
    if has_profit:
        net = float(cum_profit_df["cum_contribution_margin_usd"].iloc[-1])  # type: ignore[index]
        kpis.append(("Net Profit", fmt_usd(net)))

    story.append(Paragraph("Key Performance Indicators", s["section"]))
    story.append(_build_kpi_table(kpis, s, _CONTENT_W))
    story.append(Spacer(1, 6))


def _add_funnel(
    story: list[Any],
    s: dict[str, ParagraphStyle],
    funnel: dict,
) -> None:
    """Append cohort activation funnel numbers to story.

    Args:
        story: ReportLab story list.
        s: Style dict.
        funnel: Dict with ``signups``, ``kyc_done``, ``activated``.
    """
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
) -> None:
    """Render Plotly figures to PNG and append as images to story.

    Args:
        story: ReportLab story list.
        s: Style dict.
        summary: ``roi_summary()`` DataFrame.
        cum_profit_df: ``cumulative_profit()`` DataFrame.
        cum_rev_df: ``cumulative_revenue()`` DataFrame.
        daily: ``daily_context()`` DataFrame.
        spend_df: Date-filtered spend DataFrame.
        campaigns: List of campaign dicts.
    """
    story.append(Paragraph("Campaign Charts", s["section"]))

    full_w = _CONTENT_W
    half_w = _CONTENT_W / 2 - 3
    chart_h_full = 160.0
    chart_h_half = 140.0

    figs_full: list[tuple[go.Figure, float]] = []

    if not spend_df.empty:
        cum_df = _build_cumulative_spend(spend_df, campaigns)
        fig_spend = _fig_cumulative_spend(cum_df, campaigns, cum_rev_df, cum_profit_df)
        if fig_spend is not None:
            figs_full.append((fig_spend, chart_h_full))

    if cum_profit_df is not None and not cum_profit_df.empty:
        fig_profit = _fig_cumulative_profit(cum_profit_df)
        if fig_profit is not None:
            figs_full.append((fig_profit, chart_h_full))
        fig_breakdown = _fig_revenue_breakdown(cum_profit_df)
        if fig_breakdown is not None:
            figs_full.append((fig_breakdown, chart_h_full))

    if not daily.empty:
        fig_daily = _fig_campaign_daily(daily)
        if fig_daily is not None:
            figs_full.append((fig_daily, chart_h_full))

    for fig, h in figs_full:
        img = _fig_to_image(fig, full_w, h)
        if img:
            story.append(img)
            story.append(Spacer(1, 4))

    _add_paired_charts(
        story,
        [_fig_campaign_roi(summary, cum_profit_df), _fig_campaign_cac(summary)],
        half_w,
        chart_h_half,
    )


def _add_paired_charts(
    story: list[Any],
    figs: list[go.Figure | None],
    half_w: float,
    h: float,
) -> None:
    """Render two Plotly figures side-by-side as a two-column ReportLab table.

    Args:
        story: ReportLab story list.
        figs: Exactly two figures (either may be None to leave a blank cell).
        half_w: Width per column in PDF points.
        h: Height per figure in PDF points.
    """
    cells: list[Any] = []
    for fig in figs:
        if fig is None:
            cells.append("")
        else:
            img = _fig_to_image(fig, half_w, h)
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
    """Append the campaign summary table to story.

    Args:
        story: ReportLab story list.
        s: Style dict.
        summary: ``roi_summary()`` DataFrame.
    """
    tbl = _build_summary_table(summary, s, _CONTENT_W)
    if tbl is None:
        return
    story.append(Paragraph("Campaign Summary", s["section"]))
    story.append(tbl)
