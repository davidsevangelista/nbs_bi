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
from types import ModuleType

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from nbs_bi.cards.models import CardCostModel, CostBreakdown

try:
    import streamlit as st
except ModuleNotFoundError:

    class _StreamlitShim:
        """Small fallback so non-UI tests can import this module."""

        @staticmethod
        def cache_data(*args: object, **kwargs: object) -> object:
            def decorator(func: object) -> object:
                return func

            return decorator

    st = _StreamlitShim()

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
# Figure builders
# ---------------------------------------------------------------------------


def _fig_breakdown(breakdown: CostBreakdown) -> go.Figure:
    """Horizontal bar chart of cost line items, sorted descending.

    Args:
        breakdown: CostBreakdown from CardCostModel.cost_breakdown().

    Returns:
        Plotly Figure.
    """
    items = [(k, v) for k, v in breakdown.sorted_by_amount() if v > 0]
    labels = [_LABELS.get(k, k.replace("_", " ").title()) for k, _ in items]
    values = [v for _, v in items]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color="#2196F3",
            text=[f"${v:,.2f}" for v in values],
            textposition="outside",
        )
    )
    fig.update_layout(
        xaxis_title="USD",
        yaxis=dict(autorange="reversed"),
        margin=dict(t=10, b=10, l=180),
        height=max(300, len(items) * 30),
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
    totals = [m.cost_breakdown().total for _, m in history]
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
        xaxis_title=None,
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10),
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
            text=[f"+${v:,.2f}" for v in values],
            textposition="outside",
        )
    )
    fig.update_layout(
        xaxis_title="Additional cost if input +10% (USD)",
        yaxis=dict(autorange="reversed"),
        margin=dict(t=10, b=10, l=180),
        height=max(250, len(items) * 30),
    )
    return fig


def _mask_user_id(uid: str) -> str:
    return str(uid)[:8] + "…"


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

        top_driver_label = _LABELS.get(bd.sorted_by_amount()[0][0], bd.sorted_by_amount()[0][0])

        cols = st.columns(5)
        cols[0].metric("Monthly Cost", f"${bd.total:,.2f}")
        cols[1].metric(
            "Cost / Transaction",
            f"${cpt:.4f}" if cpt is not None else "—",
        )
        cols[2].metric("Active Cards", f"{self._model.inputs.n_active_cards:,}")
        cols[3].metric("Card Transactions", f"{self._model.inputs.n_transactions:,}")
        cols[4].metric("Top Cost Driver", top_driver_label)

    def _render_breakdown(self) -> None:
        st.subheader("Cost breakdown")
        st.caption("Which cost line should I negotiate with Rain first?")
        bd = self._model.cost_breakdown()
        st.plotly_chart(_fig_breakdown(bd), width="stretch")

    def _render_sensitivity(self) -> None:
        st.subheader("Cost driver sensitivity (+10%)")
        st.caption("If this volume grows 10%, how much more does it cost?")
        st.plotly_chart(_fig_sensitivity(self._model), width="stretch")

    def _render_trend(self) -> None:
        st.subheader("Cost per transaction trend")
        st.caption("Is unit economics improving as volume grows?")
        if not self._history or len(self._history) < 2:
            st.info("Provide at least 2 months of history to display this chart.")
            return
        st.plotly_chart(_fig_trend(self._history), width="stretch")

    def _render_top_spenders(self) -> None:
        st.subheader("Top 20 card spenders")
        st.caption("Are my top card users also my top ramp users? (cross-sell signal)")
        if self._top_spenders is None or self._top_spenders.empty:
            st.info("Pass top_spenders=df to CardSection to display this table.")
            return
        display = self._top_spenders.head(20).copy()
        if "user_id" in display.columns:
            display["user_id"] = display["user_id"].apply(_mask_user_id)
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
        """Render the full card analytics dashboard into the current Streamlit context."""
        import streamlit as st

        from nbs_bi.cards import analytics as ca

        # ── Load & derive data ────────────────────────────────────────
        raw = self._load(self._db_url, self._date_from, self._date_to)
        if raw.empty:
            st.warning("Nenhuma transação encontrada no período selecionado.")
            return

        c_threshold = float(ca.MODEL_C_THRESHOLD_DEFAULT)
        daily = ca.build_daily(raw)
        bins = ca.bin_transactions(raw)
        fee_df = ca.fee_comparison(raw, bins, c_threshold=c_threshold)
        monthly_rev = ca.monthly_revenue(raw, c_threshold=c_threshold)
        count_fc = ca.ewma_forecast(daily["daily_count"])
        vol_fc = ca.ewma_forecast(daily["daily_volume_usd"])
        smry = ca.summary_metrics(daily, count_fc, vol_fc)


        # ── Header KPIs ───────────────────────────────────────────────
        n_tx = int(daily["daily_count"].sum())
        total_vol = float(daily["daily_volume_usd"].sum())
        median_amt = float(raw["amount_usd"].median())
        mean_amt = float(raw["amount_usd"].mean())
        best_model = max(monthly_rev, key=lambda m: monthly_rev[m])

        st.caption(
            f"{daily.index.min().strftime('%d/%m/%Y')} a "
            f"{daily.index.max().strftime('%d/%m/%Y')}  ·  "
            f"{n_tx:,} transações  ·  ${total_vol:,.2f} volume total"
        )
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Transações totais", f"{n_tx:,}")
        k2.metric("Volume total", f"${total_vol:,.0f}")
        k3.metric("Ticket médio", f"${mean_amt:.2f}", delta=f"mediana ${median_amt:.2f}")
        k4.metric(
            "Melhor modelo/mês",
            f"${monthly_rev[best_model]:,.2f}",
            delta=best_model.split("—")[0].strip(),
        )
        st.divider()

        # ── Tabs ──────────────────────────────────────────────────────
        (t_uso, t_dist, t_prev, t_ind, t_cob, t_b2b, t_faixas) = st.tabs(
            [
                "📊 Padrões de Uso",
                "📐 Distribuição e Modelos",
                "🔮 Previsão",
                "📋 Indicadores",
                "💵 Cobertura Invoice",
                "📈 Projeção B2B",
                "🎚️ Faixas de Preço",
            ]
        )

        with t_uso:
            c1, c2 = st.columns(2)
            with c1:
                st.plotly_chart(ca.fig_daily_timeline(daily), width="stretch")
            with c2:
                st.plotly_chart(ca.fig_weekly_patterns(daily), width="stretch")

        with t_dist:
            c1, c2 = st.columns(2)
            with c1:
                st.plotly_chart(
                    ca.fig_distribution(bins, median_amt, mean_amt),
                    width="stretch",
                )
            with c2:
                st.plotly_chart(
                    ca.fig_fee_comparison(fee_df, monthly_rev),
                    width="stretch",
                )
            st.subheader("Receita mensal estimada por modelo")
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Modelo": m, "Receita/mês (USD)": f"${monthly_rev[m]:,.2f}"}
                        for m in ca.MODEL_COLORS
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

        with t_prev:
            st.plotly_chart(ca.fig_forecast(daily, count_fc), width="stretch")
            fc_table = pd.DataFrame(
                {
                    "Data": count_fc["forecast"].index.strftime("%d/%m/%Y"),
                    "Transações/dia (proj.)": count_fc["forecast"].round(1).values,
                    "IC inferior (95%)": count_fc["ci_lower"].round(1).values,
                    "IC superior (95%)": count_fc["ci_upper"].round(1).values,
                    "Volume/dia (proj., USD)": vol_fc["forecast"].round(2).values,
                }
            )
            st.subheader("Projeção diária — próximos 5 dias")
            st.dataframe(fc_table, width="stretch", hide_index=True)

        with t_ind:
            st.plotly_chart(ca.fig_summary_table(smry), width="stretch")

        with t_cob:
            self._render_coverage(ca, raw, monthly_rev, self._rain_cost_usd)

        with t_b2b:
            self._render_b2b(ca, raw, daily, n_tx, median_amt)

        with t_faixas:
            self._render_faixas(ca, raw, self._rain_cost_usd)

    # ------------------------------------------------------------------
    # Private tab renderers
    # ------------------------------------------------------------------

    def _render_coverage(
        self,
        ca: ModuleType,
        raw: pd.DataFrame,
        monthly_rev: dict[str, float],
        default_rain_cost: float | None,
    ) -> None:
        import streamlit as st

        _fallback = CardCostModel.from_february_2026().cost_breakdown().total
        _default_rc = float(default_rain_cost) if default_rain_cost is not None else _fallback

        c1, c2, c3 = st.columns([2, 2, 3])
        with c1:
            rain_cost_usd = st.number_input(
                "Custo Rain mensal (USD)",
                min_value=0.0,
                value=_default_rc,
                step=100.0,
                format="%.2f",
                help="Custo mensal da invoice que a receita de cartão precisa cobrir.",
            )
            flat_fee_usd = st.number_input(
                "Taxa fixa testada (USD/tx)",
                min_value=0.0,
                value=0.30,
                step=0.05,
                format="%.2f",
            )
            pct_fee = (
                st.slider(
                    "% variável testado",
                    min_value=0.0,
                    max_value=5.0,
                    value=1.0,
                    step=0.25,
                    format="%.2f%%",
                )
                / 100.0
            )
        with c3:
            compare_flat_fees = st.multiselect(
                "Taxas fixas p/ comparar (linhas)",
                options=ca.COVERAGE_FLAT_OPTIONS,
                default=[0.30, 0.40],
                format_func=lambda v: f"${v:.2f}",
            )

        metrics = ca.flat_pct_coverage_metrics(raw, rain_cost_usd, flat_fee_usd, pct_fee)
        custom_label = f"${flat_fee_usd:.2f} + {pct_fee * 100:.2f}%"
        custom_rev = metrics["revenue_usd"]
        coverage_df = ca.coverage_analysis(
            monthly_rev,
            rain_cost_usd,
            extra_models={custom_label: custom_rev},
        )
        all_flat = sorted(
            set(ca.COVERAGE_FLAT_RANGE + compare_flat_fees + [round(flat_fee_usd, 2)])
        )
        pct_range = sorted(set(ca.COVERAGE_PCT_RANGE + [round(pct_fee, 4)]))
        grid = ca.coverage_grid(raw, rain_cost_usd, all_flat, pct_range)

        st.markdown(
            "Compare a receita mensal projetada do cartão contra o custo da invoice Rain. "
            "A cobrança combinada usa **taxa fixa + percentual do valor** em toda transação."
        )
        k1, k2, k3, k4 = st.columns(4)
        k1.metric(
            custom_label,
            f"${custom_rev:,.2f}/mês",
            delta=f"{metrics['coverage_ratio'] * 100:.1f}% da invoice",
        )
        k2.metric("Margem vs invoice", f"${metrics['margin_usd']:,.2f}")
        k3.metric(
            f"% necessário com ${flat_fee_usd:.2f}",
            f"{metrics['required_pct_with_flat'] * 100:.2f}%",
        )
        k4.metric(
            f"Fixo necessário com {pct_fee * 100:.2f}%",
            f"${metrics['required_flat_with_pct']:.2f}",
        )
        st.caption(
            f"Base mensalizada: {metrics['tx_month']:,.0f} tx/mês · "
            f"${metrics['volume_month_usd']:,.2f} volume/mês · "
            f"{int(metrics['n_days'])} dias observados no filtro atual."
        )

        if compare_flat_fees:
            st.plotly_chart(
                ca.fig_flat_pct_revenue_lines(grid, rain_cost_usd, compare_flat_fees),
                width="stretch",
            )

        st.plotly_chart(ca.fig_coverage_bar(coverage_df), width="stretch")
        st.plotly_chart(ca.fig_coverage_heatmap(grid, rain_cost_usd), width="stretch")

        display = coverage_df.copy()
        display["revenue_usd"] = display["revenue_usd"].apply(lambda v: f"${v:,.2f}")
        display["cost_usd"] = display["cost_usd"].apply(lambda v: f"${v:,.2f}")
        display["coverage_ratio"] = display["coverage_ratio"].apply(lambda v: f"{v:.2f}×")
        display["margin_usd"] = display["margin_usd"].apply(lambda v: f"${v:,.2f}")
        display = display.rename(
            columns={
                "model": "Modelo",
                "revenue_usd": "Receita/mês",
                "cost_usd": "Custo invoice",
                "coverage_ratio": "Cobertura",
                "margin_usd": "Margem",
            }
        )
        st.dataframe(display, width="stretch", hide_index=True)

    def _render_b2b(
        self,
        ca: ModuleType,
        raw: pd.DataFrame,
        daily: pd.DataFrame,
        n_tx: int,
        median_amt: float,
    ) -> None:
        import streamlit as st

        st.caption("Txns× e Ticket× são multiplicadores em relação ao histórico.")
        _defaults = pd.DataFrame(
            {
                "Cenário": ["Atual", "B2B leve", "B2B moderado", "B2B avançado"],
                "Txns×": [1.0, 2.0, 4.0, 8.0],
                "Ticket×": [1.0, 2.0, 4.0, 8.0],
            }
        )
        sc_editor = st.data_editor(
            _defaults,
            num_rows="dynamic",
            width="stretch",
            column_config={
                "Cenário": st.column_config.TextColumn(required=True),
                "Txns×": st.column_config.NumberColumn(
                    min_value=0.1, max_value=100.0, step=0.5, format="%.1f×"
                ),
                "Ticket×": st.column_config.NumberColumn(
                    min_value=0.1, max_value=100.0, step=0.5, format="%.1f×"
                ),
            },
            key="ca_scenario_editor",
        )
        sc_df = sc_editor.dropna(subset=["Cenário", "Txns×", "Ticket×"])
        scenarios = ca.build_scenarios(
            raw,
            sc_df["Txns×"].tolist(),
            sc_df["Ticket×"].tolist(),
            sc_df["Cenário"].tolist(),
        )
        with st.expander("📋 Premissas da projeção", expanded=False):
            p1, p2 = st.columns(2)
            p1.metric("Base histórica", f"{n_tx:,} txns / {len(daily)} dias")
            p2.metric("Ticket mediano", f"${median_amt:.2f}")
            st.info(
                "**Método:** distribuição real de valores preservada. "
                "Cada transação histórica é multiplicada por **Ticket×** antes de calcular "
                "a cobrança. O total diário é multiplicado por **Txns×**."
            )
        st.plotly_chart(ca.fig_b2b_projection(scenarios), width="stretch")
        st.subheader("Tabela de cenários")
        sc_display = scenarios.copy()
        for model in ca.MODEL_COLORS:
            sc_display[model] = sc_display[model].apply(lambda v: f"${v:,.2f}")
        sc_display["avg_ticket_usd"] = sc_display["avg_ticket_usd"].apply(
            lambda v: f"${v:.2f}"
        )
        sc_display = sc_display.rename(
            columns={
                "scenario": "Cenário",
                "tx_mult": "Txns×",
                "size_mult": "Ticket×",
                "avg_ticket_usd": "Ticket med. proj.",
            }
        )
        st.dataframe(sc_display, width="stretch", hide_index=True)

    def _render_faixas(
        self,
        ca: ModuleType,
        raw: pd.DataFrame,
        default_rain_cost: float | None,
    ) -> None:
        import streamlit as st

        _fallback = CardCostModel.from_february_2026().cost_breakdown().total
        rain_cost_usd = float(default_rain_cost) if default_rain_cost is not None else _fallback

        st.markdown(
            "Defina uma **tarifa fixa por faixa de valor** e veja qual estrutura "
            "cobre o custo mensal da invoice Rain. "
            "O heatmap varia as duas faixas selecionadas abaixo, "
            "mantendo as demais constantes."
        )

        col_fees, col_sweep = st.columns([3, 2])
        with col_fees:
            st.caption("Tarifa por faixa (USD/tx)")
            cols = st.columns(len(ca._BIN_LABELS))
            bin_fees: list[float] = []
            for col, lbl, default_fee in zip(cols, ca._BIN_LABELS, ca.BIN_FEE_DEFAULTS):
                with col:
                    bin_fees.append(
                        st.number_input(
                            lbl,
                            min_value=0.0,
                            value=float(default_fee),
                            step=0.05,
                            format="%.2f",
                            key=f"bin_fee_{lbl}",
                        )
                    )
        with col_sweep:
            st.caption("Faixas para o heatmap de sweep")
            sweep_i_label = st.selectbox(
                "Faixa 1 (eixo Y)", options=ca._BIN_LABELS, index=0, key="sweep_i"
            )
            sweep_j_label = st.selectbox(
                "Faixa 2 (eixo X)", options=ca._BIN_LABELS, index=1, key="sweep_j"
            )

        metrics = ca.bin_fee_coverage_metrics(raw, bin_fees, rain_cost_usd)
        breakdown = ca.bin_fee_revenue(raw, bin_fees)

        k1, k2, k3 = st.columns(3)
        k1.metric(
            "Receita total/mês",
            f"${metrics['revenue_usd']:,.2f}",
            delta=f"{metrics['coverage_ratio'] * 100:.1f}% da invoice",
        )
        k2.metric("Margem vs invoice", f"${metrics['margin_usd']:,.2f}")
        k3.metric(
            "Fixo uniforme de equilíbrio",
            f"${metrics['breakeven_uniform_flat']:.2f}/tx",
            help="Taxa única aplicada a todas as transações mensalizadas que cobrira a invoice.",
        )

        st.plotly_chart(
            ca.fig_bin_revenue_breakdown(breakdown, metrics["coverage_ratio"], rain_cost_usd),
            width="stretch",
        )

        i_bin = ca._BIN_LABELS.index(sweep_i_label)
        j_bin = ca._BIN_LABELS.index(sweep_j_label)
        if i_bin == j_bin:
            st.info("Selecione faixas diferentes para gerar o heatmap de sweep.")
            return

        sweep_df = ca.bin_fee_sweep(raw, i_bin, j_bin, ca.BIN_SWEEP_RANGE, bin_fees, rain_cost_usd)
        st.plotly_chart(
            ca.fig_bin_sweep_heatmap(sweep_df, sweep_i_label, sweep_j_label),
            width="stretch",
        )

        st.subheader("Receita por faixa")
        display = breakdown.copy()
        display["fee_usd"] = display["fee_usd"].apply(lambda v: f"${v:.2f}")
        display["revenue_month_usd"] = display["revenue_month_usd"].apply(lambda v: f"${v:,.2f}")
        display["pct_count"] = display["pct_count"].apply(lambda v: f"{v:.1f}%")
        display = display.rename(
            columns={
                "label": "Faixa",
                "count": "Transações (obs.)",
                "pct_count": "% do total",
                "fee_usd": "Tarifa (USD/tx)",
                "revenue_obs_usd": "Receita obs. (USD)",
                "revenue_month_usd": "Receita/mês (USD)",
            }
        )
        st.dataframe(display, width="stretch", hide_index=True)

        # ── Progressão Paramétrica ────────────────────────────────────
        st.divider()
        st.subheader("Progressão Paramétrica de Tarifas")
        st.caption(
            "Tarifas flat crescem e % decresce à medida que o valor da transação sobe. "
            "O gráfico mostra a cobertura da invoice para cada largura de faixa (gap)."
        )

        pr1, pr2, pr3, pr4 = st.columns([2, 1, 2, 2])
        with pr1:
            n_bins = st.slider("Número de faixas", 4, 20, 10, key="prog_n_bins")
        with pr2:
            gap_step = st.number_input(
                "Passo do gap (USD)",
                min_value=1,
                max_value=100,
                value=10,
                step=1,
                key="prog_gap_step",
            )
        with pr3:
            flat_start = st.number_input(
                "Flat inicial (USD)", min_value=0.0, value=float(ca.PROG_FLAT_START_DEFAULT),
                step=0.05, format="%.2f", key="prog_flat_start",
            )
            flat_end = st.number_input(
                "Flat final (USD)", min_value=0.0, value=float(ca.PROG_FLAT_END_DEFAULT),
                step=0.10, format="%.2f", key="prog_flat_end",
            )
        with pr4:
            pct_start = st.number_input(
                "% inicial", min_value=0.0, value=float(ca.PROG_PCT_START_DEFAULT * 100),
                step=0.25, format="%.2f", key="prog_pct_start",
            )
            pct_end = st.number_input(
                "% final", min_value=0.0, value=float(ca.PROG_PCT_END_DEFAULT * 100),
                step=0.05, format="%.2f", key="prog_pct_end",
            )

        if n_bins > 1:
            flat_step = (flat_end - flat_start) / (n_bins - 1)
            pct_step = (pct_start - pct_end) / (n_bins - 1)
            last_start = (n_bins - 1) * gap_step
            st.caption(
                f"Δflat = +${flat_step:.3f}/faixa  ·  "
                f"Δ% = −{pct_step:.3f}%/faixa  ·  "
                f"última faixa: ${last_start:.0f}+"
            )

        # Fee schedule preview (uses actual transaction data for invoice factor)
        bkdn = ca.progressive_fee_breakdown(
            raw, n_bins, float(gap_step),
            flat_start, flat_end, pct_start / 100.0, pct_end / 100.0, rain_cost_usd,
        )

        def _format_to_usd(value: float) -> str:
            if pd.isna(value) or value == float("inf"):
                return "Sem limite"
            return f"${value:.0f}"

        sched_display = pd.DataFrame({
            "Faixa": bkdn["bin"],
            "De (USD)": bkdn["from_usd"].apply(lambda v: f"${v:.0f}"),
            "Até (USD)": bkdn["to_usd"].apply(_format_to_usd),
            "Flat (USD)": bkdn["flat_usd"].apply(lambda v: f"${v:.2f}"),
            "%": bkdn["pct"].apply(lambda v: f"{v * 100:.2f}%"),
            "Programa de Cartão": bkdn["invoice_factor"].apply(lambda v: f"{v:.3f}×"),
        })
        sched_table_col, sched_total_col = st.columns([5, 1])
        with sched_table_col:
            st.dataframe(sched_display, width="content", hide_index=True)
        with sched_total_col:
            st.metric("Σ Programa de Cartão", f"{bkdn['invoice_factor'].sum():.3f}×")

        st.subheader("Cobertura por Faixa com Flat Editável")
        st.caption(
            "Use as mesmas faixas e percentuais acima, alterando o flat de cada faixa "
            "para ver a cobertura total e a contribuição por faixa."
        )

        custom_flat_fees: list[float] = []
        flat_input_cols = st.columns(n_bins)
        for i, (col, row) in enumerate(zip(flat_input_cols, bkdn.itertuples(index=False))):
            tier_end = "Sem limite" if row.to_usd == float("inf") else f"${row.to_usd:.0f}"
            with col:
                custom_flat_fees.append(
                    st.number_input(
                        f"F{i + 1}",
                        min_value=0.0,
                        value=float(row.flat_usd),
                        step=0.05,
                        format="%.2f",
                        key=f"prog_custom_flat_{i}",
                        help=f"Faixa ${row.from_usd:.0f} até {tier_end}",
                    )
                )

        custom_bkdn = ca.progressive_fee_breakdown(
            raw,
            n_bins,
            float(gap_step),
            flat_start,
            flat_end,
            pct_start / 100.0,
            pct_end / 100.0,
            rain_cost_usd,
            flat_fees=custom_flat_fees,
        )
        custom_total_revenue = float(custom_bkdn["revenue_month_usd"].sum())
        custom_total_coverage = float(custom_bkdn["invoice_factor"].sum())
        custom_margin = custom_total_revenue - rain_cost_usd

        ctot1, ctot2, ctot3 = st.columns(3)
        ctot1.metric("Receita total/mês", f"${custom_total_revenue:,.2f}")
        ctot2.metric("Cobertura total", f"{custom_total_coverage:.3f}×")
        ctot3.metric("Margem vs invoice", f"${custom_margin:,.2f}")

        custom_display = pd.DataFrame({
            "Faixa": custom_bkdn["bin"],
            "De (USD)": custom_bkdn["from_usd"].apply(lambda v: f"${v:.0f}"),
            "Até (USD)": custom_bkdn["to_usd"].apply(_format_to_usd),
            "Flat (USD)": custom_bkdn["flat_usd"].apply(lambda v: f"${v:.2f}"),
            "%": custom_bkdn["pct"].apply(lambda v: f"{v * 100:.2f}%"),
            "Transações (obs.)": custom_bkdn["count"],
            "% do total": custom_bkdn["pct_count"].apply(lambda v: f"{v:.1f}%"),
            "Receita/mês (USD)": custom_bkdn["revenue_month_usd"].apply(
                lambda v: f"${v:,.2f}"
            ),
            "Cobertura da invoice": custom_bkdn["invoice_factor"].apply(lambda v: f"{v:.3f}×"),
        })
        st.dataframe(custom_display, width="stretch", hide_index=True)

        gap_values = [float(gap_step * i) for i in range(1, 21)]
        prog_sweep = ca.progressive_fee_sweep(
            raw, gap_values, n_bins,
            flat_start, flat_end, pct_start / 100.0, pct_end / 100.0, rain_cost_usd,
        )
        st.plotly_chart(
            ca.fig_progressive_coverage(prog_sweep, rain_cost_usd),
            width="stretch",
        )

    @staticmethod
    @st.cache_data(show_spinner="Carregando transações do cartão…")
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
