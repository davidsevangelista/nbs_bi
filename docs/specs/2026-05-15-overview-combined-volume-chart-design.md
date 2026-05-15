# Design: Overview Tab — Combined Volume Chart (USD)

**Date:** 2026-05-15
**Status:** Approved

---

## Goal

Replace the fixed "Monthly BRL Volume" chart in the Overview tab's right column with a new stacked bar chart showing card spend volume and conversion volume together in USD, with a Daily / Weekly / Monthly granularity toggle.

---

## Data Sources

Both DataFrames are already available from `ramp_report` — no new queries or report keys required.

| Key | Columns used |
|---|---|
| `ramp_report["conv_daily"]` | `date`, `onramp` (BRL), `offramp` (BRL) |
| `ramp_report["card_daily"]` | `date`, `amount_usd` |
| `ramp_report["summary"]` | `"Total volume USD"`, `"Onramp volume BRL"`, `"Offramp volume BRL"` |

**FX rate:** derived as `Total volume USD / (Onramp volume BRL + Offramp volume BRL)` from the existing summary KPI. This is the period median rate — adequate for a volume comparison chart. If the BRL denominator is zero (no conversions), the chart renders card volume only with conversion bars at zero.

---

## Chart Specification

**Type:** Stacked bar (`barmode = "stack"`)

**X axis:** date bucket (day / week-starting-Monday / month-start)

**Y axis:** USD volume

**Series:**

| Series | Source column | Color |
|---|---|---|
| Conversions (USD) | `(onramp + offramp) / fx_rate` | `BLUE` |
| Card Spend (USD) | `amount_usd` | `AMBER` |

**Hover:** each bar segment shows its own value and the combined total for that period, using the `customdata` pattern already standard in this codebase.

**Granularity toggle:** `st.radio("Granularity", ["Daily", "Weekly", "Monthly"], horizontal=True, key="overview_vol_gran")` rendered above the chart. Weekly resamples with `W-MON`, monthly with `MS`.

---

## Implementation Scope

All changes are confined to `nbs_bi/reporting/overview.py`. No other file is modified.

### New functions

**`_resample_combined(df, granularity)`**
- Input: merged DataFrame with columns `date`, `conv_usd`, `card_usd`
- Resamples to the requested granularity using `pd.Grouper` or `resample`
- Returns aggregated DataFrame with same column structure

**`_fig_combined_volume(conv_daily, card_daily, fx_rate, granularity)`**
- Pure function — no Streamlit calls
- Merges `conv_daily` and `card_daily` on `date` (outer join, fillna 0)
- Computes `conv_usd = (onramp + offramp) / fx_rate`
- Calls `_resample_combined()` for aggregation
- Builds stacked bar figure with two `go.Bar` traces
- Returns `go.Figure` or `None` if both inputs are empty

### Modified methods

**`_render_combined_volume()`** — replaces `_render_volume()`
- Reads `conv_daily`, `card_daily`, and derives `fx_rate` from `summary`
- Renders `st.radio` granularity toggle (`key="overview_vol_gran"`)
- Calls `_fig_combined_volume()` and renders via `st.plotly_chart(..., width="stretch")`
- Shows `st.info(...)` if both DataFrames are empty

**`render()`** — change `self._render_volume()` call to `self._render_combined_volume()`

---

## Edge Cases

| Condition | Behaviour |
|---|---|
| No conversions, cards present | `fx_rate` fallback to 1.0 (or omit conversion bar); card volume shown |
| No cards, conversions present | Card bar absent or zero; conversion volume shown |
| Both empty | `st.info("No volume data for this period.")` |
| Zero BRL volume (fx_rate denominator = 0) | Guard with `if brl_total > 0 else 1.0` |

---

## What is NOT changing

- The existing "Monthly BRL Volume" chart (`_fig_volume_monthly`) is removed from the render path but the function is kept (it is not used elsewhere, but deletion is a separate cleanup task)
- No changes to `ramp_report` keys or `OnrampReport.build()`
- No changes to `dashboard.py`
- The granularity toggle does NOT affect any other chart in the Overview tab
