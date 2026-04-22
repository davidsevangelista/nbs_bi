"""Shared visual theme for the NBS BI Streamlit dashboard.

Centralises colour constants, layout helpers, monetary formatters, and PII
masking so every reporting module stays consistent.

Usage::

    from nbs_bi.reporting.theme import (
        BLUE, EMERALD, AMBER, ROSE, TEAL, VIOLET,
        panel, fmt_brl, fmt_usd, fmt_usd_precise, mask_user_id,
    )
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BLUE: str = "#2563EB"
EMERALD: str = "#059669"
AMBER: str = "#D97706"
ROSE: str = "#E11D48"
TEAL: str = "#0D9488"
VIOLET: str = "#7C3AED"

PLOT_BG: str = "#F8FAFC"
GRID: str = "#E2E8F0"
TEXT: str = "#1E293B"
TEXT_MUTED: str = "#64748B"
BG: str = "#FFFFFF"

# Acquisition source → colour mapping used across clients tab.
SOURCE_COLORS: dict[str, str] = {
    "founder_invite": EMERALD,
    "referral": BLUE,
    "organic": AMBER,
    "unknown": TEXT_MUTED,
}


# ---------------------------------------------------------------------------
# Layout helper
# ---------------------------------------------------------------------------


def panel(title: str = "") -> dict:
    """Return shared Plotly ``update_layout`` kwargs for all dashboard charts.

    Produces a consistent background, grid colour, font, and margin so every
    chart in the dashboard shares the same visual language.

    Args:
        title: Optional chart title string.

    Returns:
        Dict suitable for unpacking into ``fig.update_layout(**panel(...))``.
    """
    return dict(
        title=title,
        title_font_size=14,
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(color=TEXT, size=12),
        xaxis=dict(gridcolor=GRID, showgrid=True),
        yaxis=dict(gridcolor=GRID, showgrid=True),
        margin=dict(t=40, b=60, l=10, r=10),
        legend=dict(orientation="h", y=-0.2),
    )


# ---------------------------------------------------------------------------
# Monetary formatters
# ---------------------------------------------------------------------------


def fmt_brl(value: float) -> str:
    """Format a BRL monetary value with the R$ prefix and zero decimal places.

    Suitable for headline KPI cards and chart axis labels where amounts are
    typically in the thousands.

    Args:
        value: Monetary amount in Brazilian Reais.

    Returns:
        Formatted string, e.g. ``'R$ 12.345'``.
    """
    return f"R$ {value:,.0f}"


def fmt_usd(value: float) -> str:
    """Format a USD monetary value with the $ prefix and two decimal places.

    Suitable for invoice totals, campaign spend, and revenue figures.

    Args:
        value: Monetary amount in US Dollars.

    Returns:
        Formatted string, e.g. ``'$6,693.58'``.
    """
    return f"${value:,.2f}"


def fmt_usd_precise(value: float) -> str:
    """Format a USD cost-per-transaction value with four decimal places.

    Suitable for unit economics metrics where rounding to two places would
    mask meaningful differences in per-transaction cost.

    Args:
        value: Cost per transaction in US Dollars.

    Returns:
        Formatted string, e.g. ``'$0.3247'``.
    """
    return f"${value:.4f}"


# ---------------------------------------------------------------------------
# PII helpers
# ---------------------------------------------------------------------------


def mask_user_id(uid: str) -> str:
    """Truncate a UUID to its first 8 characters to prevent PII exposure.

    Never expose full user UUIDs in dashboard outputs, table displays, or
    chart hover text.  Apply this function to every ``user_id`` column before
    rendering.

    Args:
        uid: Full UUID string (or any user identifier).

    Returns:
        First 8 characters followed by an ellipsis, e.g. ``'a1b2c3d4…'``.
    """
    return str(uid)[:8] + "…"


# ---------------------------------------------------------------------------
# Colour utility
# ---------------------------------------------------------------------------


def rgba(hex_color: str, alpha: float = 0.4) -> str:
    """Convert a 6-digit hex colour to an ``rgba()`` CSS string.

    Used for fill colours on area charts and confidence-interval bands.

    Args:
        hex_color: Hex colour string with or without leading ``#``.
        alpha: Opacity, 0.0 (transparent) to 1.0 (opaque).

    Returns:
        String of the form ``'rgba(R, G, B, alpha)'``.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ---------------------------------------------------------------------------
# Report data helpers (shared across all reporting modules)
# ---------------------------------------------------------------------------


def report_get(report: dict, key: str) -> pd.DataFrame:
    """Return a DataFrame from a report dict, or an empty DataFrame.

    Args:
        report: Dict returned by any ``*.build()`` method.
        key: Key to look up.

    Returns:
        The value at *key* if it is a DataFrame, else an empty DataFrame.
    """
    return report.get(key, pd.DataFrame())


def is_empty(v: object) -> bool:
    """Return True if v is None or an empty DataFrame.

    Args:
        v: Any value — typically a DataFrame or None from a report dict.

    Returns:
        True when v cannot be used for charting or display.
    """
    if v is None:
        return True
    if isinstance(v, pd.DataFrame):
        return v.empty
    return False


def extract_kpi(summary: pd.DataFrame, metric: str, default: float = 0.0) -> float:
    """Extract a named scalar from a flat KPI summary DataFrame.

    Args:
        summary: DataFrame with at least ``metric`` and ``value`` columns.
        metric: The metric name to look up.
        default: Fallback value when the metric is not found.

    Returns:
        Float value for the metric, or *default* if not present.
    """
    if summary.empty or "metric" not in summary.columns:
        return default
    row = summary.loc[summary["metric"] == metric, "value"]
    return float(row.iloc[0]) if not row.empty else default


# ---------------------------------------------------------------------------
# Streamlit shim for test environments
# ---------------------------------------------------------------------------


def get_streamlit() -> object:
    """Return the real ``streamlit`` module, or a no-op shim.

    Allows figure-builder functions to be imported and tested without
    a running Streamlit server.

    Returns:
        The ``streamlit`` module, or a shim whose every attribute returns
        a callable that does nothing.
    """
    try:
        import streamlit as _st  # noqa: PLC0415

        return _st
    except ModuleNotFoundError:

        class _Shim:
            def __getattr__(self, name: str):  # type: ignore[override]
                def _noop(*args: object, **kwargs: object) -> None:
                    return None

                return _noop

        return _Shim()
