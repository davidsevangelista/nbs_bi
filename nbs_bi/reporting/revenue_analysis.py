"""Revenue Analysis tab — interactive hour × day-of-week revenue heatmap.

Aggregates revenue from three streams (conversions, card annual fees,
billing charges) and renders a Plotly heatmap with dropdown filters for
month and revenue source.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text

from nbs_bi.onramp.queries import OnrampQueries, _to_exclusive_end
from nbs_bi.reporting.theme import GRID, PLOT_BG, TEXT, TEXT_MUTED, panel

logger = logging.getLogger(__name__)

_SQL_CARD_FEES = """
SELECT paid_at AS created_at, amount_usdc::FLOAT AS rev_usd
FROM   card_annual_fees
WHERE  status = 'paid'
  AND  paid_at >= :start_date AND paid_at < :end_date
"""

_SQL_BILLING = """
SELECT created_at, amount::FLOAT / 1000000.0 AS rev_usd
FROM   billing_charges
WHERE  status = 'settled'
  AND  created_at >= :start_date AND created_at < :end_date
"""

_BOTTOM_ANNOTATIONS: list[dict[str, Any]] = [
    dict(
        text="Month:",
        x=0.0,
        xref="paper",
        y=-0.17,
        yref="paper",
        showarrow=False,
        xanchor="left",
        font=dict(size=12, color=TEXT),
    ),
    dict(
        text="Source:",
        x=0.32,
        xref="paper",
        y=-0.17,
        yref="paper",
        showarrow=False,
        xanchor="left",
        font=dict(size=12, color=TEXT),
    ),
]


class RevenueAnalysisSection:
    """Interactive revenue heatmap tab (hour × day-of-week, BRT timezone).

    Aggregates revenue from conversions, card annual fees, and billing charges.
    Renders a Plotly heatmap with a peak-hour overlay and dropdown filters by
    month and revenue source.
    """

    DOW_ORDER: list[str] = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    HOURS: list[int] = list(range(24))

    def __init__(self, df: pd.DataFrame) -> None:
        """Initialise with a pre-loaded combined revenue DataFrame.

        Args:
            df: DataFrame with columns: created_at (tz-aware UTC), rev_usd
                (float64), source (str). Returned by :meth:`load`.
        """
        self._df = self._enrich(df) if not df.empty else df

    @staticmethod
    def load(db_url: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Query all three revenue streams and return a combined DataFrame.

        Args:
            db_url: Database connection URL.
            start_date: ISO date string (inclusive).
            end_date: ISO date string — passed through OnrampQueries convention
                (one day past intended end; _to_exclusive_end adds another).

        Returns:
            DataFrame with columns: created_at (tz-aware UTC), rev_usd
            (float64), source ('conversion' | 'card_fee' | 'card_transaction').
        """
        oq = OnrampQueries(start_date=start_date, end_date=end_date, db_url=db_url)
        bound = {"start_date": start_date, "end_date": _to_exclusive_end(end_date)}

        conv_raw = oq.conversions()
        if conv_raw.empty:
            conv_df = pd.DataFrame(columns=["created_at", "rev_usd", "source"])
        else:
            rate = pd.to_numeric(conv_raw["exchange_rate"], errors="coerce").replace(
                0, float("nan")
            )
            conv_raw["rev_usd"] = (
                conv_raw["fee_amount_brl"].fillna(0) / rate
                + conv_raw["fee_amount_usdc"].fillna(0)
                + conv_raw["spread_revenue_brl"].fillna(0) / rate
                + conv_raw["spread_revenue_usdc"].fillna(0)
            )
            conv_df = conv_raw[["created_at", "rev_usd"]].assign(source="conversion")

        with oq._engine_lazy.connect() as conn:
            card_df = pd.read_sql(text(_SQL_CARD_FEES), conn, params=bound).assign(
                source="card_fee"
            )
            billing_df = pd.read_sql(text(_SQL_BILLING), conn, params=bound).assign(
                source="card_transaction"
            )

        for partial in (card_df, billing_df):
            partial["created_at"] = pd.to_datetime(partial["created_at"], utc=True)

        all_rev = pd.concat([conv_df, card_df, billing_df], ignore_index=True)
        all_rev["rev_usd"] = all_rev["rev_usd"].astype("float64")
        logger.info("Revenue rows: %d | Total: $%.2f", len(all_rev), all_rev["rev_usd"].sum())
        return all_rev

    def _enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add BRT-local temporal columns: hour, dow, date, month."""
        df = df.copy()
        brt = df["created_at"].dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
        df["hour"] = brt.dt.hour
        df["dow"] = brt.dt.day_name()
        df["date"] = brt.dt.date
        df["month"] = brt.dt.to_period("M").astype(str)
        return df

    def _make_pivot(self, df: pd.DataFrame) -> list[list[float]]:
        """Return a 24×7 nested list of summed rev_usd by (hour, dow)."""
        return (
            df.groupby(["hour", "dow"])["rev_usd"]
            .sum()
            .unstack("dow")
            .reindex(index=self.HOURS, columns=self.DOW_ORDER)
            .fillna(0)
            .values.tolist()
        )

    def _day_avgs(self, df: pd.DataFrame) -> dict[str, float]:
        """Return average total revenue per occurrence of each day-of-week."""
        result: dict[str, float] = {}
        for day in self.DOW_ORDER:
            mask = df["dow"] == day
            n_days = int(df.loc[mask, "date"].nunique())
            total = float(df.loc[mask, "rev_usd"].sum())
            result[day] = total / n_days if n_days > 0 else 0.0
        return result

    def _peak_hours(self, pivot_values: list[list[float]]) -> list[int | None]:
        """Return the peak-revenue hour (0-23) for each day column."""
        arr = np.array(pivot_values, dtype="float64")
        result: list[int | None] = []
        for col_idx in range(arr.shape[1]):
            col = arr[:, col_idx]
            result.append(int(col.argmax()) if col.max() > 0 else None)
        return result

    def _precompute(
        self,
    ) -> tuple[dict, dict, dict, list[str], list[str]]:
        """Precompute pivots, day averages, and peak hours for all filter combos.

        Returns:
            Tuple of (pivots, day_avgs, peak_hrs, month_opts, source_opts)
            keyed by (month, source) tuples.
        """
        month_opts = ["All months"] + sorted(self._df["month"].unique().tolist())
        source_opts = ["All sources"] + sorted(self._df["source"].unique().tolist())
        pivots: dict = {}
        day_avgs: dict = {}
        peak_hrs: dict = {}
        for mo in month_opts:
            df_m = self._df if mo == "All months" else self._df[self._df["month"] == mo]
            for so in source_opts:
                df_s = df_m if so == "All sources" else df_m[df_m["source"] == so]
                p = self._make_pivot(df_s)
                pivots[(mo, so)] = p
                day_avgs[(mo, so)] = self._day_avgs(df_s)
                peak_hrs[(mo, so)] = self._peak_hours(p)
        return pivots, day_avgs, peak_hrs, month_opts, source_opts

    def _top_annotations(self, avgs: dict[str, float]) -> list[dict[str, Any]]:
        """Return per-day average revenue annotations rendered above each column."""
        return [
            dict(
                text=f"<b>${avgs[day]:,.0f}</b>",
                x=day,
                xref="x",
                y=1.0,
                yref="paper",
                yanchor="bottom",
                xanchor="center",
                showarrow=False,
                font=dict(size=10, color=TEXT_MUTED),
            )
            for day in self.DOW_ORDER
        ]

    def _month_buttons(
        self,
        pivots: dict,
        peak_hrs: dict,
        day_avgs: dict,
        month_opts: list[str],
    ) -> list[dict[str, Any]]:
        """Build Plotly dropdown button definitions for the month filter."""
        return [
            dict(
                label=mo,
                method="update",
                args=[
                    {
                        "z": [pivots[(mo, "All sources")], None],
                        "y": [None, peak_hrs[(mo, "All sources")]],
                    },
                    {
                        "title.text": f"Revenue Heatmap — {mo} / All sources (BRT)",
                        "annotations": _BOTTOM_ANNOTATIONS
                        + self._top_annotations(day_avgs[(mo, "All sources")]),
                    },
                ],
            )
            for mo in month_opts
        ]

    def _source_buttons(
        self,
        pivots: dict,
        peak_hrs: dict,
        day_avgs: dict,
        source_opts: list[str],
    ) -> list[dict[str, Any]]:
        """Build Plotly dropdown button definitions for the source filter."""
        return [
            dict(
                label=so,
                method="update",
                args=[
                    {
                        "z": [pivots[("All months", so)], None],
                        "y": [None, peak_hrs[("All months", so)]],
                    },
                    {
                        "title.text": f"Revenue Heatmap — All months / {so} (BRT)",
                        "annotations": _BOTTOM_ANNOTATIONS
                        + self._top_annotations(day_avgs[("All months", so)]),
                    },
                ],
            )
            for so in source_opts
        ]

    def _build_figure(
        self,
        pivots: dict,
        day_avgs: dict,
        peak_hrs: dict,
        month_opts: list[str],
        source_opts: list[str],
    ) -> go.Figure:
        """Assemble the Plotly heatmap with peak-hour overlay and dropdowns."""
        m0, s0 = "All months", "All sources"
        init_ann = _BOTTOM_ANNOTATIONS + self._top_annotations(day_avgs[(m0, s0)])

        fig = go.Figure()
        fig.add_trace(
            go.Heatmap(
                z=pivots[(m0, s0)],
                x=self.DOW_ORDER,
                y=self.HOURS,
                colorscale="Viridis",
                colorbar=dict(title="Revenue (USD)", tickfont=dict(color=TEXT)),
                hovertemplate="%{x}<br>%{y}:00 BRT<br>$%{z:.2f}<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=self.DOW_ORDER,
                y=peak_hrs[(m0, s0)],
                mode="lines+markers",
                line=dict(color="white", width=2),
                marker=dict(size=9, color="white", line=dict(color="#333", width=1.5)),
                name="Peak hour",
                hovertemplate="%{x}<br>Peak: %{y}:00 BRT<extra></extra>",
            )
        )
        # Split into two calls: panel() already sets yaxis; overriding in the same
        # call raises TypeError due to duplicate keyword argument at the Python level.
        fig.update_layout(**panel(f"Revenue Heatmap — {m0} / {s0} (BRT)"))
        fig.update_layout(
            xaxis_title="Day of Week",
            yaxis=dict(tickmode="linear", dtick=1, gridcolor=GRID, title="Hour of Day (BRT)"),
            annotations=init_ann,
            updatemenus=[
                dict(
                    buttons=self._month_buttons(pivots, peak_hrs, day_avgs, month_opts),
                    direction="up",
                    showactive=True,
                    x=0.0,
                    xanchor="left",
                    y=-0.25,
                    yanchor="top",
                    bgcolor=PLOT_BG,
                    font=dict(color=TEXT),
                ),
                dict(
                    buttons=self._source_buttons(pivots, peak_hrs, day_avgs, source_opts),
                    direction="up",
                    showactive=True,
                    x=0.32,
                    xanchor="left",
                    y=-0.25,
                    yanchor="top",
                    bgcolor=PLOT_BG,
                    font=dict(color=TEXT),
                ),
            ],
            margin=dict(t=80, b=180, l=60, r=40),
            height=680,
        )
        return fig

    def _make_bands(self, pivots: dict, selected_months: list[str]) -> list[dict[str, Any]]:
        """Return yellow rect shapes at ±1 h around peak per day for selected months.

        Args:
            pivots: Precomputed pivot dict keyed by (month, source).
            selected_months: Real month strings whose data to combine.

        Returns:
            List of Plotly shape dicts, one per day of week.
        """
        combined = np.zeros((24, len(self.DOW_ORDER)), dtype="float64")
        for mo in selected_months:
            combined += np.array(pivots[(mo, "All sources")], dtype="float64")
        shapes = []
        for di in range(len(self.DOW_ORDER)):
            ph = int(combined[:, di].argmax())
            shapes.append(
                dict(
                    type="rect",
                    x0=di - 0.4,
                    x1=di + 0.4,
                    y0=max(0, ph - 1),
                    y1=min(23, ph + 1),
                    xref="x",
                    yref="y",
                    fillcolor="rgba(255, 200, 0, 0.15)",
                    line=dict(width=0),
                    layer="below",
                )
            )
        return shapes

    def _build_peak_figure(
        self,
        pivots: dict,
        month_opts: list[str],
        selected_months: list[str],
    ) -> go.Figure:
        """Assemble peak revenue hour chart filtered to selected months (BRT).

        All months are always added as traces; visibility is toggled per
        selection so the opacity gradient stays consistent. Yellow bands
        reflect only the selected months' combined peaks.

        Args:
            pivots: Precomputed pivot dict keyed by (month, source).
            month_opts: Full list including 'All months' sentinel.
            selected_months: Subset of real months to show.
        """
        real_months = [mo for mo in month_opts if mo != "All months"]
        n = len(real_months)
        alphas = [0.25 + 0.75 * i / max(n - 1, 1) for i in range(n)]
        active = set(selected_months)

        fig = go.Figure()
        for i, mo in enumerate(real_months):
            arr = np.array(pivots[(mo, "All sources")], dtype="float64")
            peak_h = arr.argmax(axis=0).tolist()
            color = f"rgba(99, 110, 250, {alphas[i]:.2f})"
            fig.add_trace(
                go.Scatter(
                    x=self.DOW_ORDER,
                    y=peak_h,
                    mode="lines+markers",
                    name=mo,
                    visible=mo in active,
                    line=dict(color=color, width=2),
                    marker=dict(size=8, color=color),
                    hovertemplate=f"<b>{mo}</b><br>%{{x}}<br>Peak: %{{y}}:00 BRT<extra></extra>",
                )
            )

        fig.update_layout(**panel("Peak Revenue Hour by Day of Week — by Month (BRT)"))
        fig.update_layout(
            xaxis_title="Day of Week",
            yaxis=dict(
                tickmode="linear",
                dtick=1,
                range=[-0.5, 23.5],
                gridcolor=GRID,
                title="Hour of Day (BRT)",
            ),
            showlegend=False,
            shapes=self._make_bands(pivots, selected_months or real_months),
            height=420,
            margin=dict(t=60, b=60, l=60, r=40),
        )
        return fig

    def render(self) -> None:
        """Render the heatmap and peak-hour chart in the active Streamlit tab."""
        import streamlit as st

        if self._df.empty:
            st.info("No revenue data available for the selected date range.", icon="ℹ️")
            return

        pivots, day_avgs, peak_hrs, month_opts, source_opts = self._precompute()
        st.plotly_chart(
            self._build_figure(pivots, day_avgs, peak_hrs, month_opts, source_opts),
            use_container_width=True,
        )

        real_months = [mo for mo in month_opts if mo != "All months"]
        n = len(real_months)
        alphas = [0.25 + 0.75 * i / max(n - 1, 1) for i in range(n)]

        col_cb, col_chart = st.columns([1, 8])
        selected: list[str] = []
        with col_cb:
            for i, mo in enumerate(real_months):
                a = alphas[i]
                color = f"rgba(99,110,250,{a:.2f})"
                st.markdown(
                    f'<span style="color:{color};font-size:13px;'
                    f'font-family:monospace;">● {mo}</span>',
                    unsafe_allow_html=True,
                )
                if st.checkbox("_", value=True, key=f"peak_{mo}", label_visibility="collapsed"):
                    selected.append(mo)

        with col_chart:
            st.plotly_chart(
                self._build_peak_figure(pivots, month_opts, selected),
                use_container_width=True,
            )
