"""Card Program dashboard sections.

Two section classes:

* ``CardSection``          — cost-model view (Tab 3: Card Costs).
  Wraps ``CardCostModel`` into Streamlit + Plotly components.
  Each chart answers a specific decision from the CEO's perspective.

* ``CardAnalyticsSection`` — spend analytics view (Tab 4: Card Analytics).
  Interactive 7-tab dashboard driven by live DB data: usage patterns,
  size distribution, fee models, EWMA demand forecast, B2B growth
  scenarios, Model C threshold optimisation, and combination grids.

Usage::

    from nbs_bi.cards.models import CardCostModel
    from nbs_bi.reporting.cards import CardSection

    model = CardCostModel.from_february_2026()
    section = CardSection(model)
    section.render()

For the cost trend chart, pass historical models::

    history = [("2026-01", jan_model), ("2026-02", feb_model)]
    CardSection(model, history=history).render()

For the top card spenders table, pass a DataFrame with columns
``user_id``, ``n_transactions``, ``tx_volume_usd``::

    CardSection(model, top_spenders=df).render()
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from nbs_bi.cards.models import CardCostModel, CostBreakdown
from nbs_bi.reporting.theme import (
    AMBER,
    BG,
    BLUE,
    EMERALD,
    GRID,
    PLOT_BG,
    ROSE,
    TEAL,
    TEXT,
    VIOLET,
    fmt_usd,
    fmt_usd_precise,
    get_streamlit,
    mask_user_id,
)

st = get_streamlit()

# Human-readable labels for CostBreakdown fields.
_LABELS: dict[str, str] = {
    "base_program": "Base Program Fee",
    "virtual_cards": "Virtual Cards",
    "transaction_fee": "Transaction Fee",
    "network_volume": "Network Volume Fee",
    "fee_3ds": "3DS Fee",
    "visa_infinite": "Visa Infinite",
    "visa_platinum": "Visa Platinum",
    "applepay_count": "Apple Pay (count)",
    "applepay_amount": "Apple Pay (amount)",
    "googlepay_count": "Google Pay",
    "share_token": "Share Token",
    "verify_domestic": "Verification (domestic)",
    "verify_intl": "Verification (intl)",
    "chip_auth_intl": "Chip Auth (intl)",
    "network_tx_cost": "Network Tx Cost",
    "network_3ds_cost": "Network 3DS Cost",
    "cross_border": "Cross-border",
}


# ---------------------------------------------------------------------------
# Invoice auto-discovery
# ---------------------------------------------------------------------------

_INVOICES_DIR = Path(__file__).resolve().parents[2] / "data" / "invoices"


def _load_all_invoice_models() -> tuple[CardCostModel, str, str, list[tuple[str, CardCostModel]]]:
    """Load CardCostModels for every actuals JSON in data/invoices/.

    Returns the latest model separately for KPI display, plus the full
    chronological history list for the trend chart.  Falls back to the Feb
    2026 hardcoded reference if no JSON files are found.

    Returns:
        Tuple of (latest_model, invoice_id, period, history).
        ``history`` is a list of (period_label, CardCostModel) sorted by period.
    """
    jsons = sorted(_INVOICES_DIR.glob("Invoice-*-actuals.json"))
    if jsons:
        models = [CardCostModel.from_invoice(p) for p in jsons]
        history = sorted([(m.inputs.period, m) for m in models], key=lambda t: t[0])
        latest_model = history[-1][1]
        return latest_model, latest_model.inputs.invoice_id, latest_model.inputs.period, history
    model = CardCostModel.from_february_2026()
    return model, "NKEMEJLO-0008", "2026-02", [("2026-02", model)]


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------


def _fig_breakdown(breakdown: CostBreakdown) -> go.Figure:
    """Waterfall chart of cost line items sorted descending, with running total.

    Each bar shows the USD contribution of one fee component.  The final bar
    shows the cumulative total so the reader can see both individual weights
    and the aggregate cost at a glance.

    Args:
        breakdown: CostBreakdown from CardCostModel.cost_breakdown().

    Returns:
        Plotly Figure.
    """
    items = [(k, v) for k, v in breakdown.sorted_by_amount() if v > 0]
    labels = [_LABELS.get(k, k.replace("_", " ").title()) for k, _ in items]
    values = [v for _, v in items]

    # Build waterfall: all bars are 'relative', final bar is 'total'.
    measure = ["relative"] * len(values) + ["total"]
    x_labels = labels + ["Total"]
    y_values = values + [sum(values)]

    fig = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=measure,
            x=x_labels,
            y=y_values,
            text=[fmt_usd(v) for v in y_values],
            textposition="outside",
            connector=dict(line=dict(color="#CBD5E1", width=1)),
            increasing=dict(marker_color=BLUE),
            totals=dict(marker_color=EMERALD),
            decreasing=dict(marker_color=AMBER),
        )
    )
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="USD",
        xaxis=dict(tickangle=-35, gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
        margin=dict(t=20, b=80, l=60, r=40),
        height=max(380, len(items) * 22 + 120),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
        showlegend=False,
    )
    return fig


def _fig_trend(history: list[tuple[str, CardCostModel]]) -> go.Figure:
    """Dual-axis line: total cost (left) and cost per transaction (right) over time.

    Args:
        history: List of (period_label, CardCostModel) sorted chronologically.

    Returns:
        Plotly Figure.
    """
    periods = [p for p, _ in history]
    totals = [
        m.inputs.invoice_total_usd
        if getattr(m.inputs, "invoice_total_usd", 0.0) > 0
        else m.cost_breakdown().total
        for _, m in history
    ]
    cpt = []
    for _, m in history:
        try:
            cpt.append(m.cost_per_transaction())
        except ValueError:
            cpt.append(None)

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=periods,
            y=totals,
            mode="lines+markers",
            name="Total cost (USD)",
            line=dict(color="#2196F3", width=2),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=periods,
            y=cpt,
            mode="lines+markers",
            name="Cost / tx (USD)",
            line=dict(color="#FF9800", width=2, dash="dot"),
        ),
        secondary_y=True,
    )
    fig.update_yaxes(title_text="Total cost USD", secondary_y=False)
    fig.update_yaxes(title_text="Cost per tx USD", secondary_y=True)
    fig.update_layout(
        title="Total Cost & Cost per Transaction Trend",
        title_font_size=14,
        xaxis_title=None,
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=40, b=80),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
    )
    return fig


def _fig_cost_driver_stacked(history: list[tuple[str, CardCostModel]]) -> go.Figure:
    """Stacked bar: one column per period, stacked by cost line item.

    Args:
        history: List of (period_label, CardCostModel) sorted chronologically.

    Returns:
        Plotly Figure with barmode='stack'.
    """
    periods = [p for p, _ in history]
    # Collect per-line-item values across periods — exclude 'total'
    line_items = [k for k in _LABELS if k != "total"]
    # Plotly categorical colour cycle — distinct enough for 17 items
    colors = [
        "#2196F3",
        "#FF9800",
        "#4CAF50",
        "#E91E63",
        "#9C27B0",
        "#00BCD4",
        "#FF5722",
        "#8BC34A",
        "#607D8B",
        "#FFC107",
        "#3F51B5",
        "#009688",
        "#795548",
        "#F44336",
        "#CDDC39",
        "#03A9F4",
        "#FF6F00",
    ]
    fig = go.Figure()
    for i, key in enumerate(line_items):
        label = _LABELS.get(key, key.replace("_", " ").title())
        values = [m.cost_breakdown().as_dict().get(key, 0.0) for _, m in history]
        fig.add_trace(
            go.Bar(
                name=label,
                x=periods,
                y=values,
                marker_color=colors[i % len(colors)],
                hovertemplate=f"{label}: $%{{y:,.2f}}<extra></extra>",
            )
        )
    # Add "Outros" trace for fees not captured by the rate model (billed − modelled gap)
    outros = []
    for _, m in history:
        billed = getattr(m.inputs, "invoice_total_usd", 0.0)
        modelled = m.cost_breakdown().total
        outros.append(max(0.0, billed - modelled))
    if any(v > 0 for v in outros):
        fig.add_trace(
            go.Bar(
                name="Other (unmodelled)",
                x=periods,
                y=outros,
                marker_color="#B0BEC5",
                hovertemplate="Other: $%{y:,.2f}<extra></extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        xaxis_title=None,
        yaxis_title="USD",
        legend=dict(orientation="h", y=-0.2, font_size=11),
        margin=dict(t=10, b=60, l=60, r=40),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
    )
    return fig


def _fig_driver_delta(history: list[tuple[str, CardCostModel]]) -> go.Figure | None:
    """Horizontal bar showing how each cost line item changed vs the prior period.

    Bars are ROSE (cost rose) or EMERALD (cost fell). Only renders when
    ``len(history) >= 2``.

    Args:
        history: List of (period_label, CardCostModel) sorted chronologically.

    Returns:
        Plotly Figure or None if insufficient history.
    """
    if len(history) < 2:
        return None
    prev_period, prev_model = history[-2]
    latest_period, latest_model = history[-1]
    prev_d = prev_model.cost_breakdown().as_dict()
    latest_d = latest_model.cost_breakdown().as_dict()
    line_items = [k for k in _LABELS if k != "total"]
    rows = []
    for key in line_items:
        delta = latest_d.get(key, 0.0) - prev_d.get(key, 0.0)
        prev_val = prev_d.get(key, 0.0)
        pct = (delta / prev_val * 100) if prev_val else 0.0
        rows.append((_LABELS.get(key, key), delta, pct))
    rows.sort(key=lambda r: abs(r[1]), reverse=True)
    labels = [r[0] for r in rows]
    deltas = [r[1] for r in rows]
    pcts = [r[2] for r in rows]
    colors = [ROSE if d > 0 else EMERALD for d in deltas]
    texts = [f"{'+' if d >= 0 else ''}{d:,.2f} ({p:+.1f}%)" for d, p in zip(deltas, pcts)]
    fig = go.Figure(
        go.Bar(
            x=deltas,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=texts,
            textposition="outside",
            hovertemplate="%{y}: %{text}<extra></extra>",
        )
    )
    fig.update_layout(
        title=f"Cost Driver Change vs {prev_period}",
        title_font_size=14,
        xaxis_title="Δ USD",
        yaxis_title=None,
        margin=dict(t=40, b=40, l=160, r=120),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
        showlegend=False,
    )
    return fig


def _fig_driver_evolution(history: list[tuple[str, CardCostModel]]) -> go.Figure | None:
    """Line chart: absolute cost per driver over invoice periods (months on X axis).

    One trace per cost driver; drivers with all-zero values across all periods are
    omitted. Useful for spotting which line items are growing or shrinking over time.

    Args:
        history: List of (period_label, CardCostModel) sorted chronologically.

    Returns:
        Plotly Figure or None if insufficient history.
    """
    if len(history) < 2:
        return None

    line_items = [k for k in _LABELS if k != "total"]
    periods = [p for p, _ in history]
    _colors = [BLUE, AMBER, TEAL, VIOLET, EMERALD, ROSE]

    fig = go.Figure()
    for j, key in enumerate(line_items):
        values = [m.cost_breakdown().as_dict().get(key, 0.0) for _, m in history]
        if all(v == 0.0 for v in values):
            continue
        label = _LABELS.get(key, key)
        texts = [fmt_usd(v) for v in values]
        fig.add_trace(
            go.Scatter(
                x=periods,
                y=values,
                name=label,
                mode="lines+markers",
                line=dict(color=_colors[j % len(_colors)], width=2),
                marker=dict(size=7),
                text=texts,
                hovertemplate=f"{label} — %{{x}}: %{{text}}<extra></extra>",
            )
        )

    fig.update_layout(
        title=f"Cost Driver Evolution vs {history[0][0]}",
        title_font_size=14,
        xaxis_title="Period",
        yaxis_title="Cost (USD)",
        margin=dict(t=40, b=60, l=60, r=20),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID, zeroline=True, zerolinecolor=GRID),
        legend=dict(orientation="h", y=-0.25),
    )
    return fig


def _fig_sensitivity(model: CardCostModel) -> go.Figure:
    """Horizontal bar: dollar impact of a 10% increase in each cost driver.

    Args:
        model: CardCostModel for the current period.

    Returns:
        Plotly Figure.
    """
    sens = model.sensitivity_analysis(delta=0.10)
    items = [(k, v) for k, v in sens.items() if v > 0][:10]
    labels = [_LABELS.get(k, k.replace("_", " ").title()) for k, _ in items]
    values = [v for _, v in items]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color="#FF9800",
            text=[f"+{fmt_usd(v)}" for v in values],
            textposition="outside",
        )
    )
    fig.update_layout(
        xaxis_title="Additional cost if input +10% (USD)",
        yaxis=dict(autorange="reversed", gridcolor=GRID),
        xaxis=dict(gridcolor=GRID),
        margin=dict(t=10, b=10, l=180),
        height=max(250, len(items) * 30),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
    )
    return fig


# ---------------------------------------------------------------------------
# Card tier helpers (analytics tab)
# ---------------------------------------------------------------------------

_TIER_CSV = Path(__file__).parents[2] / "data" / "card_fees" / "card_fees_template.csv"
_INF_SENTINEL = 9_999.0  # display sentinel for "No limit" tier boundaries


def _parse_tier_csv() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load card_fees_template.csv into flat and pct tier DataFrames.

    Returns:
        Tuple of (flat_df, pct_df) each with columns:
        flat — Tier, De (USD), Até (USD), Taxa Flat (USD/tx)
        pct  — Tier, De (USD), Até (USD), Taxa % (%)
    """
    df = pd.read_csv(_TIER_CSV, header=None, skiprows=1, dtype=str, keep_default_na=False)

    def _num(s: str) -> float:
        s = s.strip().replace(",", ".")
        return _INF_SENTINEL if s.lower() in ("no limit", "") else float(s)

    flat_rows, pct_rows = [], []
    for _, row in df.iterrows():
        if row[0].strip():
            flat_rows.append(
                {
                    "Tier": f"F{row[0].strip()}",
                    "De (USD)": float(row[1]),
                    "Até (USD)": _num(row[2]),
                    "Taxa Flat (USD/tx)": _num(row[3]),
                }
            )
        if row[4].strip():
            pct_rows.append(
                {
                    "Tier": f"P{row[4].strip()}",
                    "De (USD)": float(row[5]),
                    "Até (USD)": _num(row[6]),
                    "Taxa % (%)": float(str(row[7]).replace("%", "").strip()),
                }
            )
    return pd.DataFrame(flat_rows), pd.DataFrame(pct_rows)


def _tier_breakdown(raw: pd.DataFrame, tiers: pd.DataFrame, mode: str, n_days: int) -> pd.DataFrame:
    """Compute per-tier monthly revenue from raw card transactions.

    Args:
        raw: Transactions DataFrame with ``amount_usd`` column.
        tiers: Edited tier config (from st.data_editor).
        mode: ``"flat"`` charges a fixed USD fee per tx;
              ``"pct"`` charges a percentage of tx amount.
        n_days: Observation window length for mensalisation.

    Returns:
        One row per tier with count, pct_count, revenue_obs_usd, revenue_month_usd.
    """
    factor = 30.0 / max(n_days, 1)
    total = max(len(raw), 1)
    rows = []
    for _, t in tiers.iterrows():
        lo = float(t["De (USD)"])
        hi = float(t["Até (USD)"])
        unbounded = hi >= _INF_SENTINEL
        mask = raw["amount_usd"] >= lo
        if not unbounded:
            mask &= raw["amount_usd"] < hi
        sub = raw[mask]
        n = len(sub)
        if mode == "flat":
            rev_obs = n * float(t["Taxa Flat (USD/tx)"])
        else:
            rev_obs = float(sub["amount_usd"].sum()) * float(t["Taxa % (%)"]) / 100.0
        rows.append(
            {
                "Tier": t["Tier"],
                "De": f"${lo:.0f}",
                "Até": "Sem limite" if unbounded else f"${hi:.0f}",
                "Txs": n,
                "% total": 100.0 * n / total,
                "Receita 30d (USD)": rev_obs * factor,
            }
        )
    return pd.DataFrame(rows)


def _fig_tx_histogram(raw: pd.DataFrame, flat_tiers: pd.DataFrame) -> go.Figure:
    """Histogram of transaction amounts with flat tier boundary overlays.

    Args:
        raw: Transactions with ``amount_usd``.
        flat_tiers: Flat tier config DataFrame (for boundary lines).

    Returns:
        Plotly Figure.
    """
    amounts = raw["amount_usd"].dropna()
    p99 = float(amounts.quantile(0.99))
    fig = go.Figure(
        go.Histogram(
            x=amounts.clip(upper=p99 * 1.1),
            nbinsx=60,
            marker_color="#2563EB",
            opacity=0.75,
        )
    )
    for _, t in flat_tiers.iterrows():
        hi = float(t["Até (USD)"])
        if hi < _INF_SENTINEL and hi <= p99 * 1.2:
            fig.add_vline(
                x=hi,
                line_dash="dot",
                line_color="#D97706",
                line_width=1.5,
                annotation_text=t["Tier"],
                annotation_position="top right",
            )
    fig.update_layout(
        xaxis_title="Transaction Value (USD)",
        yaxis_title="Number of Transactions",
        height=300,
        margin=dict(t=20, b=40, l=60, r=40),
        showlegend=False,
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
    )
    return fig


def _fig_tier_revenue(bkdn: pd.DataFrame, rain_cost: float, model_label: str) -> go.Figure:
    """Bar chart of monthly revenue per tier with invoice target line.

    Args:
        bkdn: Output of :func:`_tier_breakdown`.
        rain_cost: Monthly Rain invoice total (horizontal reference line).
        model_label: Short label for the chart title.

    Returns:
        Plotly Figure.
    """
    total = bkdn["Receita 30d (USD)"].sum()
    fig = go.Figure(
        go.Bar(
            x=bkdn["Tier"],
            y=bkdn["Receita 30d (USD)"],
            marker_color="#2563EB",
            text=bkdn["Receita 30d (USD)"].apply(fmt_usd),
            textposition="outside",
        )
    )
    fig.add_hline(
        y=rain_cost,
        line_dash="dash",
        line_color="#E11D48",
        annotation_text=f"Invoice {fmt_usd(rain_cost)}",
        annotation_position="top right",
    )
    fig.update_layout(
        title=f"{model_label} — Revenue last 30 days by Tier  (total: {fmt_usd(total)})",
        xaxis_title="Tier",
        yaxis_title="USD (30d)",
        height=360,
        margin=dict(t=50, b=40),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
    )
    return fig


def _render_tier_results(
    raw: pd.DataFrame, tiers: pd.DataFrame, mode: str, n_days: int, rain_cost: float, label: str
) -> None:
    """Compute tier breakdown and render metrics + chart + table.

    Args:
        raw: Card transactions.
        tiers: Edited tier config DataFrame.
        mode: ``"flat"`` or ``"pct"``.
        n_days: Observation window length.
        rain_cost: Monthly Rain invoice total.
        label: Display label for chart title.
    """
    import streamlit as st

    clean = tiers.dropna(subset=["De (USD)", "Até (USD)"])
    if clean.empty:
        st.warning("Add at least one pricing tier to calculate.")
        return
    bkdn = _tier_breakdown(raw, clean, mode, n_days)
    total = float(bkdn["Receita 30d (USD)"].sum())
    coverage = total / rain_cost if rain_cost > 0 else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("Revenue (last 30 days)", fmt_usd(total))
    c2.metric("Invoice coverage", f"{coverage * 100:.1f}%")
    c3.metric("Margin vs invoice", fmt_usd(total - rain_cost))
    st.plotly_chart(
        _fig_tier_revenue(bkdn, rain_cost, label), width="stretch", key=f"tier_revenue_{label}"
    )
    disp = bkdn.copy()
    disp["% total"] = disp["% total"].apply(lambda v: f"{v:.1f}%")
    disp["Receita 30d (USD)"] = disp["Receita 30d (USD)"].apply(fmt_usd)
    st.dataframe(disp, width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Section class
# ---------------------------------------------------------------------------


class CardSection:
    """Streamlit rendering for the Card Program tab (Tab 3).

    Args:
        model: CardCostModel for the current period.
        history: Optional list of (period_label, CardCostModel) for trend chart.
            Should be sorted chronologically, e.g. [("2026-01", m1), ("2026-02", m2)].
        top_spenders: Optional DataFrame with columns user_id, n_transactions,
            tx_volume_usd — fetched from card_transactions by the dashboard.
    """

    def __init__(
        self,
        model: CardCostModel,
        history: list[tuple[str, CardCostModel]] | None = None,
        top_spenders: pd.DataFrame | None = None,
    ) -> None:
        self._model = model
        self._history = history
        self._top_spenders = top_spenders

    def render(self) -> None:
        """Render all card cost charts into the current Streamlit context."""
        self._render_kpis()
        st.divider()
        self._render_breakdown()
        self._render_sensitivity()
        self._render_trend()
        self._render_top_spenders()

    # ------------------------------------------------------------------
    # Private render methods
    # ------------------------------------------------------------------

    def _render_kpis(self) -> None:
        bd = self._model.cost_breakdown()
        try:
            cpt = self._model.cost_per_transaction()
        except ValueError:
            cpt = None

        _sorted = bd.sorted_by_amount()
        top_driver_label = _LABELS.get(_sorted[0][0], _sorted[0][0]) if _sorted else "—"

        billed = getattr(self._model.inputs, "invoice_total_usd", 0.0)
        display_total = billed if billed > 0 else bd.total
        gap = billed - bd.total if billed > 0 else None
        gap_str = f"modelled ${bd.total:,.2f}" if gap is not None and abs(gap) > 0.01 else None

        cols = st.columns(5)
        cols[0].metric("Actual Cost (Invoice)", fmt_usd(display_total), delta=gap_str)
        cols[1].metric(
            "Cost / Transaction",
            fmt_usd_precise(cpt) if cpt is not None else "—",
        )
        cols[2].metric("Active Cards", f"{self._model.inputs.n_active_cards:,}")
        cols[3].metric("Card Transactions", f"{self._model.inputs.n_transactions:,}")
        cols[4].metric("Top Cost Driver", top_driver_label)

    def _render_breakdown(self) -> None:
        st.subheader("Cost breakdown")
        bd = self._model.cost_breakdown()
        st.plotly_chart(_fig_breakdown(bd), width="stretch", key="costs_breakdown")

    def _render_sensitivity(self) -> None:
        st.subheader("Cost driver sensitivity (+10%)")
        st.caption("If this volume grows 10%, how much more does it cost?")
        st.plotly_chart(_fig_sensitivity(self._model), width="stretch", key="costs_sensitivity")

    def _render_trend(self) -> None:
        st.subheader("Cost per transaction trend")
        st.caption("Is unit economics improving as volume grows?")
        if not self._history or len(self._history) < 2:
            st.info("Provide at least 2 months of history to display this chart.")
            return
        st.plotly_chart(_fig_trend(self._history), width="stretch", key="costs_trend")

    def _render_top_spenders(self) -> None:
        st.subheader("Top 20 card spenders")
        st.caption("Are my top card users also my top ramp users? (cross-sell signal)")
        if self._top_spenders is None or self._top_spenders.empty:
            st.info("Pass top_spenders=df to CardSection to display this table.")
            return
        display = self._top_spenders.head(20).copy()
        if "user_id" in display.columns:
            display["user_id"] = display["user_id"].apply(mask_user_id)
        display.columns = [c.replace("_", " ").title() for c in display.columns]
        st.dataframe(display, width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Card Analytics Section
# ---------------------------------------------------------------------------


class CardAnalyticsSection:
    """Streamlit rendering for the Card Analytics tab (Tab 4).

    Loads live card spend data from the DB and renders an interactive
    dashboard: Usage Patterns, Distribution & Models, Forecast, Indicators,
    Invoice Coverage, B2B Projection, Model C Threshold, and Combinations.

    Args:
        db_url: Read-only database URL.  Falls back to
            ``nbs_bi.config.READONLY_DATABASE_URL`` if empty.
        date_from: Optional inclusive start filter.
        date_to: Optional inclusive end filter.
        rain_cost_usd: Monthly Rain invoice cost to use as the coverage target.
            Defaults to the February 2026 reference invoice.
    """

    def __init__(
        self,
        db_url: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
        rain_cost_usd: float | None = None,
    ) -> None:
        self._db_url = db_url
        self._date_from = date_from
        self._date_to = date_to
        self._rain_cost_usd = rain_cost_usd

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def render(self) -> None:
        """Render the combined Cards tab: Cost Model, Usage Patterns, Tier Pricing, Evolution."""
        import streamlit as st

        from nbs_bi.cards import analytics as ca

        t_costs, t_uso, t_faixas, t_evo = st.tabs(
            [
                "💰 Program Costs",
                "📊 Usage Patterns",
                "🎚️ Pricing Tiers",
                "📈 Evolution",
            ]
        )

        with t_costs:
            self._render_costs()

        with t_evo:
            self._render_evolution()

        raw = self._load(self._db_url, self._date_from, self._date_to)
        _no_data = "No transactions found for the selected period."
        if raw.empty:
            with t_uso:
                st.warning(_no_data)
            with t_faixas:
                st.warning(_no_data)
            return

        daily = ca.build_daily(raw)
        n_days = len(daily)
        n_tx = int(daily["daily_count"].sum())
        total_vol = float(daily["daily_volume_usd"].sum())
        mean_amt = float(raw["amount_usd"].mean())
        median_amt = float(raw["amount_usd"].median())

        with t_uso:
            st.caption(
                f"{daily.index.min().strftime('%Y-%m-%d')} to "
                f"{daily.index.max().strftime('%Y-%m-%d')}  ·  "
                f"{n_tx:,} transactions  ·  ${total_vol:,.2f} total volume"
            )
            k1, k2, k3 = st.columns(3)
            k1.metric("Total Transactions", f"{n_tx:,}")
            k2.metric("Total Volume", f"${total_vol:,.0f}")
            k3.metric("Avg Ticket", f"${mean_amt:.2f}", delta=f"median ${median_amt:.2f}")
            st.divider()
            c1, c2 = st.columns(2)
            with c1:
                st.plotly_chart(ca.fig_daily_timeline(daily), width="stretch", key="uso_daily")
            with c2:
                st.plotly_chart(ca.fig_weekly_patterns(daily), width="stretch", key="uso_weekly")

        with t_faixas:
            self._render_faixas(raw, n_days, self._rain_cost_usd)

    # ------------------------------------------------------------------
    # Private tab renderers
    # ------------------------------------------------------------------

    def _render_costs(self) -> None:
        """Render the Card Cost Model sub-tab (invoice breakdown + sensitivity)."""
        import streamlit as st

        model, _invoice_id, _period, history = _load_all_invoice_models()

        if len(history) > 1:
            options = [p for p, _ in history]
            selected_period = st.selectbox(
                "Invoice",
                options=options,
                index=len(options) - 1,
                help="Select invoice period for detailed analysis.",
            )
            selected_model = dict(history)[selected_period]
        else:
            selected_period = _period
            selected_model = model

        total = selected_model.cost_breakdown().total
        actual = getattr(selected_model.inputs, "invoice_total_usd", 0.0)
        billed_str = f"  ·  billed ${actual:,.2f}" if actual > 0 else ""
        st.caption(
            f"Invoice {selected_model.inputs.invoice_id} ({selected_period}) — "
            f"modelled ${total:,.2f} USD{billed_str}. "
            "Drop a new PDF in data/invoices/ and run nbs-invoices to update."
        )
        CardSection(selected_model, history=history).render()

    def _render_evolution(self) -> None:
        """Render the Evolução sub-tab: aggregate cross-invoice cost analysis."""
        import streamlit as st

        _, _, _, history = _load_all_invoice_models()

        if len(history) < 2:
            st.info(
                "Only 1 invoice available. Add more PDFs to data/invoices/ "
                "and run nbs-invoices to see trends."
            )
            latest_model = history[0][1]
            st.plotly_chart(
                _fig_breakdown(latest_model.cost_breakdown()),
                width="stretch",
                key="evo_single_breakdown",
            )
            return

        latest_period, latest_model = history[-1]
        prev_period, prev_model = history[-2]
        _billed_latest = getattr(latest_model.inputs, "invoice_total_usd", 0.0)
        _billed_prev = getattr(prev_model.inputs, "invoice_total_usd", 0.0)
        latest_total = _billed_latest if _billed_latest > 0 else latest_model.cost_breakdown().total
        prev_total = _billed_prev if _billed_prev > 0 else prev_model.cost_breakdown().total
        delta_total = latest_total - prev_total
        latest_cpt = latest_model.cost_per_transaction()
        prev_cpt = prev_model.cost_per_transaction()
        delta_cpt = latest_cpt - prev_cpt

        # Avg total cost across all invoice periods
        all_totals = [
            getattr(m.inputs, "invoice_total_usd", 0.0) or m.cost_breakdown().total
            for _, m in history
        ]
        avg_total = sum(all_totals) / len(all_totals)

        # Total transactions across all invoice periods
        total_txns = sum(m.inputs.n_transactions for _, m in history)

        # Revenue from DB (annual fees + billing charges)
        rev: dict = {"annual_fees_usd": 0.0, "billing_usd": 0.0}
        if self._db_url:
            start_str = self._date_from.isoformat() if self._date_from else "2000-01-01"
            end_str = self._date_to.isoformat() if self._date_to else "2099-01-01"
            try:
                rev = self._load_card_revenue(self._db_url, start_str, end_str)
            except Exception:
                pass

        # Row 1 — revenue KPIs
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Total Transactions", f"{total_txns:,}")
        r2.metric("Revenue — Annual Fees", fmt_usd(rev["annual_fees_usd"]))
        r3.metric("Revenue — Billing (Txn)", fmt_usd(rev["billing_usd"]))
        r4.metric(
            "Total Revenue",
            fmt_usd(rev["annual_fees_usd"] + rev["billing_usd"]),
        )
        r5.metric("Active Cards", f"{latest_model.inputs.n_active_cards:,}")

        # Row 2 — cost KPIs
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Avg Total Cost", fmt_usd(avg_total))
        c2.metric(
            "Total Cost (latest)",
            fmt_usd(latest_total),
            delta=f"{delta_total:+.2f} vs {prev_period}",
            delta_color="inverse",
        )
        c3.metric(
            "Cost / Transaction",
            fmt_usd_precise(latest_cpt),
            delta=f"{delta_cpt:+.4f} vs {prev_period}",
            delta_color="inverse",
        )
        c4.metric("Transactions (latest)", f"{latest_model.inputs.n_transactions:,}")
        c5.metric("Active Cards (latest)", f"{latest_model.inputs.n_active_cards:,}")

        st.divider()
        st.plotly_chart(_fig_trend(history), width="stretch", key="evo_trend")

        fig_delta = _fig_driver_delta(history)
        if fig_delta:
            st.plotly_chart(fig_delta, width="stretch", key="evo_delta")

        fig_evo = _fig_driver_evolution(history)
        if fig_evo:
            st.plotly_chart(fig_evo, width="stretch", key="evo_driver_evolution")

        st.subheader("Invoice Detail")
        rows = []
        for p, m in history:
            d = m.cost_breakdown().as_dict()
            billed = getattr(m.inputs, "invoice_total_usd", 0.0)
            d["billed_total"] = billed if billed > 0 else d["total"]
            d["unmodelled"] = max(0.0, billed - d["total"]) if billed > 0 else 0.0
            d["period"] = p
            rows.append(d)
        df_summary = pd.DataFrame(rows).set_index("period")
        # Put billed_total first for clarity
        ordered = ["billed_total", "total", "unmodelled"] + [
            c for c in df_summary.columns if c not in ("billed_total", "total", "unmodelled")
        ]
        st.dataframe(df_summary[ordered].style.format("${:,.2f}"), width="stretch")

    def _render_faixas(
        self,
        raw: pd.DataFrame,
        n_days: int,
        default_rain_cost: float | None,
    ) -> None:
        """Render the Tier Pricing tab: distribution + editable flat and pct tiers."""
        import streamlit as st

        _fallback = _load_all_invoice_models()[0].cost_breakdown().total
        rain_cost_usd = st.number_input(
            "Custo Rain mensal (USD)",
            min_value=0.0,
            value=float(default_rain_cost) if default_rain_cost is not None else _fallback,
            step=100.0,
            format="%.2f",
            key="faixas_rain_cost",
            help="Custo mensal da invoice Rain que a receita de cartão precisa cobrir.",
        )

        flat_def, pct_def = _parse_tier_csv()

        # Rolling 30-day window: use only the last 30 days of actual transactions.
        # Revenue figures reflect the real observed period — no extrapolation factor.
        cutoff = pd.to_datetime(raw["posted_at"]).max() - pd.Timedelta(days=30)
        raw_30d = raw[pd.to_datetime(raw["posted_at"]) >= cutoff]
        n_days_30 = 30

        st.subheader("Transaction Value Distribution")
        st.caption(
            f"Last 30 days · {len(raw_30d):,} transactions · "
            f"${float(raw_30d['amount_usd'].sum()):,.2f} volume"
        )
        st.plotly_chart(
            _fig_tx_histogram(raw_30d, flat_def), width="stretch", key="faixas_histogram"
        )

        st.divider()
        st.subheader("Tier Coverage with Editable Rates")
        st.caption(
            "Revenue calculated over the **last 30 days** of real transactions (no extrapolation). "
            "Edit tier boundaries and rates below. "
            "Use **9999** as 'To (USD)' for no upper limit."
        )

        t_flat, t_pct = st.tabs(["Flat per Tier (USD/tx)", "Percentage per Tier (% of value)"])

        with t_flat:
            flat_edited = st.data_editor(
                flat_def,
                num_rows="dynamic",
                width="stretch",
                key="faixas_flat_editor",
                column_config={
                    "Tier": st.column_config.TextColumn("Tier"),
                    "De (USD)": st.column_config.NumberColumn(
                        "From (USD)", min_value=0.0, format="$%.2f"
                    ),
                    "Até (USD)": st.column_config.NumberColumn(
                        "To (USD)", format="$%.2f", help="9999 = no upper limit"
                    ),
                    "Taxa Flat (USD/tx)": st.column_config.NumberColumn(
                        "Flat Rate (USD/tx)", min_value=0.0, format="$%.2f"
                    ),
                },
            )
            _render_tier_results(raw_30d, flat_edited, "flat", n_days_30, rain_cost_usd, "Flat")

        with t_pct:
            pct_edited = st.data_editor(
                pct_def,
                num_rows="dynamic",
                width="stretch",
                key="faixas_pct_editor",
                column_config={
                    "Tier": st.column_config.TextColumn("Tier"),
                    "De (USD)": st.column_config.NumberColumn(
                        "From (USD)", min_value=0.0, format="$%.2f"
                    ),
                    "Até (USD)": st.column_config.NumberColumn(
                        "To (USD)", format="$%.2f", help="9999 = no upper limit"
                    ),
                    "Taxa % (%)": st.column_config.NumberColumn(
                        "Rate %", min_value=0.0, max_value=100.0, format="%.2f%%"
                    ),
                },
            )
            _render_tier_results(raw_30d, pct_edited, "pct", n_days_30, rain_cost_usd, "Percentage")

        st.divider()
        st.subheader("Combined Total (Flat + Percentage)")
        st.caption("Total revenue if both fee structures were applied simultaneously.")
        flat_clean = flat_edited.dropna(subset=["De (USD)", "Até (USD)"])
        pct_clean = pct_edited.dropna(subset=["De (USD)", "Até (USD)"])
        flat_bkdn = (
            _tier_breakdown(raw_30d, flat_clean, "flat", n_days_30)
            if not flat_clean.empty
            else None
        )
        pct_bkdn = (
            _tier_breakdown(raw_30d, pct_clean, "pct", n_days_30) if not pct_clean.empty else None
        )
        flat_total = float(flat_bkdn["Receita 30d (USD)"].sum()) if flat_bkdn is not None else 0.0
        pct_total = float(pct_bkdn["Receita 30d (USD)"].sum()) if pct_bkdn is not None else 0.0
        combined = flat_total + pct_total
        coverage = combined / rain_cost_usd if rain_cost_usd > 0 else 0.0
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Flat Revenue (30d)", fmt_usd(flat_total))
        c2.metric("Percentage Revenue (30d)", fmt_usd(pct_total))
        c3.metric("Combined Total (30d)", fmt_usd(combined))
        c4.metric(
            "Invoice Coverage",
            f"{coverage * 100:.1f}%",
            delta=fmt_usd(combined - rain_cost_usd),
        )

    @staticmethod
    @st.cache_data(show_spinner="Loading card revenue…")
    def _load_card_revenue(db_url: str, start: str, end: str) -> dict:
        """Query annual fee + billing charge revenue totals for the date range.

        Args:
            db_url: Read-only database URL.
            start: ISO date string (inclusive).
            end: ISO date string (exclusive).

        Returns:
            Dict with keys ``annual_fees_usd`` and ``billing_usd``.
        """
        from sqlalchemy import create_engine
        from sqlalchemy import text as _text

        engine = create_engine(db_url)
        with engine.connect() as conn:
            fees = conn.execute(
                _text(
                    "SELECT COALESCE(SUM(amount_usdc::FLOAT), 0) FROM card_annual_fees "
                    "WHERE status = 'paid' AND created_at >= :start AND created_at < :end"
                ),
                {"start": start, "end": end},
            ).scalar()
            billing = conn.execute(
                _text(
                    "SELECT COALESCE(SUM(amount::FLOAT / 1000000.0), 0) FROM billing_charges "
                    "WHERE status = 'settled' AND created_at >= :start AND created_at < :end"
                ),
                {"start": start, "end": end},
            ).scalar()
        return {"annual_fees_usd": float(fees or 0), "billing_usd": float(billing or 0)}

    @staticmethod
    @st.cache_data(show_spinner="Loading card transactions…")
    def _load(
        db_url: str,
        date_from: date | None,
        date_to: date | None,
    ) -> pd.DataFrame:
        """Cached DB fetch — keyed by (db_url, date_from, date_to).

        Args:
            db_url: Read-only database URL.
            date_from: Start filter.
            date_to: End filter.

        Returns:
            Raw card spend DataFrame.
        """
        from nbs_bi.cards.analytics import load_card_transactions

        return load_card_transactions(date_from=date_from, date_to=date_to, db_url=db_url)
