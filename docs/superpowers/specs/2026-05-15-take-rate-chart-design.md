# Take Rate Chart — Overview Tab Design

## Goal

Add a third full-width chart to the Overview tab showing the **take rate** (total revenue ÷ total volume, expressed as %) over time, with Daily / Weekly / Monthly / Yearly granularity.

## Architecture

The chart follows the same rendering pattern as `_render_revenue_trend` and `_render_combined_volume` in `nbs_bi/reporting/overview.py`. No new data is fetched from the DB — all inputs are already present in the report dict built by `OnrampReport.build()`.

## Data Flow

**Inputs (from report dict):**

| Key | Description |
|---|---|
| `revenue_daily` | Daily conversion revenue: columns `date`, `fee_usd`, `spread_usd` |
| `card_revenue_daily` | Daily card/billing revenue: columns `date`, `card_fee_usd`, `billing_usd` |
| `revenue_monthly` | Monthly conversion revenue: columns `month`, `fee_usd`, `spread_usd` |
| `card_revenue_monthly` | Monthly card/billing revenue: columns `month`, `card_fee_usd`, `billing_usd` |
| `conv_daily` | Daily conversion volume: columns `date`, `onramp` (BRL), `offramp` (BRL) |
| `card_daily` | Daily card spend: columns `date`, `amount_usd`, `n_txns` |
| `summary` | KPI summary DataFrame — used to derive the period fx_rate |

**Formula per period:**

```
total_revenue_usd = fee_usd + spread_usd + card_fee_usd + billing_usd
total_volume_usd  = (onramp_brl + offramp_brl) / fx_rate + card_amount_usd
take_rate_pct     = (total_revenue_usd / total_volume_usd) * 100
```

Periods where `total_volume_usd == 0` produce NaN and are excluded from the line.

**Granularity handling:**

- **Daily:** use `revenue_daily` + `card_revenue_daily` as-is; resample `conv_daily` / `card_daily` to day
- **Weekly:** resample all daily DataFrames to `W-MON` using existing `_resample_revenue` / `_resample_combined` patterns
- **Monthly:** use `revenue_monthly` (rename `month` → `date`) + `card_revenue_monthly`; resample `conv_daily` / `card_daily` to `MS`
- **Yearly:** resample all daily DataFrames to `YS`

## Components

### `_compute_take_rate(rev_df, card_rev_df, conv_daily, card_daily, fx_rate, granularity) → pd.DataFrame`

Pure function. Returns DataFrame with columns `[date, take_rate_pct]`. Handles:
- Missing/empty inputs (returns empty DataFrame)
- Outer-merge of revenue and volume on `date`, filling NaN with 0
- Zero-volume rows → NaN take rate → dropped before return

### `_fig_take_rate(df: pd.DataFrame) → go.Figure | None`

Pure function. Returns None if df is empty. Builds a single `go.Scatter` line:
- x: `date`, y: `take_rate_pct`
- y-axis formatted as `%` with 2 decimal places
- Hover: `{date}: {take_rate_pct:.2f}%`
- Consistent with existing theme colors (TEAL or EMERALD line)

### `_render_take_rate(self) → None`

Method on `OverviewSection`. Pattern:
1. Radio: `["Daily", "Weekly", "Monthly", "Yearly"]`, default Monthly, key `"overview_tr_gran"`
2. Build resampled revenue + volume DataFrames for the chosen granularity
3. Call `_compute_take_rate(...)` → call `_fig_take_rate(...)` → `st.plotly_chart(..., use_container_width=True)`
4. Show `st.info("No data for this period.")` if figure is None

### `render()` update

Add `self._render_take_rate()` as a fourth full-width row, below `self._render_combined_volume()`.

## Tests

**`tests/reporting/test_overview_take_rate.py`** (new file):

1. `test_compute_take_rate_basic` — fixed fixture with known revenue and volume, asserts correct `take_rate_pct`
2. `test_compute_take_rate_zero_volume` — volume = 0 for one period → that row is absent from output (no NaN crash)
3. `test_compute_take_rate_empty_inputs` — all inputs empty → returns empty DataFrame
4. `test_fig_take_rate_returns_none_on_empty` — `_fig_take_rate(pd.DataFrame())` returns None
5. `test_fig_take_rate_has_scatter_trace` — non-empty df → figure has one Scatter trace

## File Changes

| File | Action |
|---|---|
| `nbs_bi/reporting/overview.py` | Add `_compute_take_rate`, `_fig_take_rate`, `_render_take_rate`; update `render()` |
| `tests/reporting/test_overview_take_rate.py` | New test file |
