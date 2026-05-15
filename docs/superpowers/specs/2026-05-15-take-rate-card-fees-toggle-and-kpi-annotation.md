# Take Rate — Card Fees Toggle & KPI Annotation

## Goal

Add two enhancements to the take rate chart on the Overview tab:

1. A checkbox to include/exclude `card_annual_fees` (the `card_fee_usd` column) from the revenue numerator.
2. An in-chart KPI annotation showing the volume-weighted average take rate for the full period and the last 30 days.

---

## Feature 1 — Card Annual Fees Toggle

### UI

A `st.checkbox` rendered at the top of `_render_take_rate`, above the chart:

```python
include_card_fees = st.checkbox(
    "Include card annual fees",
    value=True,
    key="tr_incl_card_fees",
)
```

Default: checked (current behaviour unchanged).

### Data path

`include_card_fees` is passed down through:

- `_render_take_rate(self, granularity, include_card_fees)` → not needed; the checkbox lives inside `_render_take_rate`, so it reads the local variable directly.
- `_compute_take_rate(..., include_card_fees: bool = True)`
- `_agg_revenue(..., include_card_fees: bool = True)`

In `_agg_revenue`, the only change is in the `total_rev` summation:

```python
card_fee = revenue["card_fee_usd"] if include_card_fees else 0.0
revenue["total_rev"] = (
    revenue["fee_usd"] + revenue["spread_usd"]
    + card_fee + revenue["billing_usd"]
)
```

`billing_usd` (Rain billing charges) is always included — it is a separate revenue line unrelated to the annual fee product.

---

## Feature 2 — KPI Annotation

### Computed values

A new pure function:

```python
def _take_rate_kpis(
    df: pd.DataFrame,
) -> tuple[float | None, float | None]:
```

**Input:** DataFrame with columns `date`, `take_rate_pct`, `total_rev`, `total_vol` (as returned by the updated `_compute_take_rate`).

**Returns:** `(avg_pct, l30_pct)` where:
- `avg_pct`: volume-weighted average over all rows = `sum(total_rev) / sum(total_vol) * 100`. Returns `None` if `sum(total_vol) == 0`.
- `l30_pct`: same, but rows filtered to `date >= df["date"].max() - pd.Timedelta(days=30)`. Returns `None` if no rows or zero volume in that window.

### `_compute_take_rate` return value change

Currently returns `["date", "take_rate_pct"]`. Updated to return `["date", "take_rate_pct", "total_rev", "total_vol"]`. The extra columns are already computed internally; they are simply exposed. `_fig_take_rate` ignores them (uses only `date` and `take_rate_pct`).

### In-chart annotation

`_fig_take_rate` signature change:

```python
def _fig_take_rate(
    df: pd.DataFrame,
    avg_pct: float | None = None,
    l30_pct: float | None = None,
) -> go.Figure | None:
```

When at least one KPI is not None, a single `go.layout.Annotation` is added:

```python
lines = []
if avg_pct is not None:
    lines.append(f"Avg  {avg_pct:.2f}%")
if l30_pct is not None:
    lines.append(f"L30  {l30_pct:.2f}%")

fig.add_annotation(
    text="<br>".join(lines),
    xref="paper", yref="paper",
    x=0.99, y=0.97,
    xanchor="right", yanchor="top",
    showarrow=False,
    align="right",
    font=dict(size=13),
    bgcolor="rgba(30,30,30,0.6)",
    bordercolor="rgba(255,255,255,0.15)",
    borderwidth=1,
    borderpad=6,
)
```

### Caller (`_render_take_rate`) update

```python
df = _compute_take_rate(...)
avg_pct, l30_pct = _take_rate_kpis(df) if not _empty(df) else (None, None)
fig = _fig_take_rate(df, avg_pct=avg_pct, l30_pct=l30_pct)
```

---

## File Changes

| File | Change |
|---|---|
| `nbs_bi/reporting/overview.py` | `_agg_revenue`: add `include_card_fees` param; `_compute_take_rate`: add `include_card_fees` param, expose `total_rev`/`total_vol`; add `_take_rate_kpis`; `_fig_take_rate`: add `avg_pct`/`l30_pct` params + annotation; `_render_take_rate`: add checkbox, compute KPIs, pass to figure |
| `tests/reporting/test_overview_take_rate.py` | Add tests for `_take_rate_kpis` and `_agg_revenue` with `include_card_fees=False`; update `_fig_take_rate` call sites to match new signature |

---

## Tests

1. `test_agg_revenue_excludes_card_fees` — pass `include_card_fees=False`; assert `total_rev` equals only `fee_usd + spread_usd + billing_usd`.
2. `test_take_rate_kpis_avg` — pass a 3-row DataFrame with known rev/vol; assert `avg_pct` matches `sum(rev)/sum(vol)*100`.
3. `test_take_rate_kpis_l30` — include one row outside the 30-day window; assert `l30_pct` uses only the in-window rows.
4. `test_take_rate_kpis_empty` — pass empty DataFrame; assert both return values are `None`.
5. `test_fig_take_rate_annotation_present` — pass non-None `avg_pct`; assert figure has one annotation.
6. `test_fig_take_rate_no_annotation_when_none` — pass `avg_pct=None, l30_pct=None`; assert figure has zero annotations.
