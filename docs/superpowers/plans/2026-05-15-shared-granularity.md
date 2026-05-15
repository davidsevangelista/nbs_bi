# Overview Shared Granularity + Revenue Bug Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the revenue (USD) chart showing zeros for Daily/Weekly/Yearly, then replace three individual granularity radios with one shared radio above all three bottom charts.

**Architecture:** Two self-contained changes to `nbs_bi/reporting/overview.py`: (1) defensive date normalization in `_fig_monthly_revenue` so revenue + card_revenue always merge correctly regardless of date column dtype; (2) granularity lifted out of the three `_render_*` methods into `render()`, passed down as a `str` parameter. `_resample_combined` gains Yearly support in the same pass.

**Tech Stack:** Python 3.11, pandas, Plotly, Streamlit.

---

## File Map

| File | Change |
|---|---|
| `nbs_bi/reporting/overview.py` | Fix `_fig_monthly_revenue` date normalization; add Yearly to `_resample_combined`; lift granularity radio to `render()`; update 3 render method signatures |
| `tests/reporting/test_overview_revenue.py` | New — regression tests for `_fig_monthly_revenue` and `_resample_combined` |

---

## Background: existing data keys

`OverviewSection.__init__` stores `self._r = ramp_report` (from `OnrampReport.build()`). Relevant keys:

| Key | Columns | Date col type |
|---|---|---|
| `revenue_daily` | `date`, `fee_usd`, `spread_usd` | `datetime64[ns]` tz-naive |
| `card_revenue_daily` | `date`, `card_fee_usd`, `billing_usd` | `datetime64[ns]` (from `pd.to_datetime` on SQL `DATE`) |
| `revenue_monthly` | `month`, `fee_usd`, `spread_usd` | `datetime64[ns]` (period→timestamp) |
| `card_revenue_monthly` | `month`, `card_fee_usd`, `billing_usd` | `datetime64[ns]` |
| `conv_daily` | `date`, `onramp` (BRL), `offramp` (BRL) | `datetime64[ns]` |
| `card_daily` | `date`, `amount_usd`, `n_txns` | `datetime.date` objects |

**Root cause of zeros**: `card_revenue_daily` dates may arrive as `datetime.date` Python objects (not properly promoted to `datetime64`) before the outer merge in `_fig_monthly_revenue`. When pandas merges `datetime64[ns]` with `object`-typed dates, no rows match — all revenue columns become NaN, then `fillna(0.0)` zeroes them. The fix: normalize both date columns to `datetime64[ns]` inside `_fig_monthly_revenue` before merging.

---

## Task 1: Fix `_fig_monthly_revenue` date normalization

**Files:**
- Modify: `nbs_bi/reporting/overview.py` (around line 197–250)
- Create: `tests/reporting/test_overview_revenue.py`

- [ ] **Step 1: Write failing regression test**

Create `tests/reporting/test_overview_revenue.py`:

```python
"""Regression tests for _fig_monthly_revenue and _resample_combined."""

import pandas as pd
import pytest

from nbs_bi.reporting.overview import _fig_monthly_revenue


def _rev_df(dates):
    """Helper: daily revenue DataFrame with datetime64 dates."""
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "fee_usd": [10.0] * len(dates),
            "spread_usd": [5.0] * len(dates),
        }
    )


def _card_rev_df_date_objects(dates):
    """Helper: card revenue DataFrame with Python datetime.date objects (as from SQL)."""
    import datetime
    return pd.DataFrame(
        {
            "date": [datetime.date(int(d[:4]), int(d[5:7]), int(d[8:10])) for d in dates],
            "card_fee_usd": [2.0] * len(dates),
            "billing_usd": [1.0] * len(dates),
        }
    )


def test_fig_monthly_revenue_non_zero_with_date_object_card_rev():
    """revenue rows must be non-zero even when card_revenue dates are datetime.date objects."""
    dates = ["2026-01-01", "2026-01-02"]
    rev = _rev_df(dates)
    card_rev = _card_rev_df_date_objects(dates)

    fig = _fig_monthly_revenue(rev, card_rev)
    assert fig is not None

    # fee_usd trace must have non-zero y values
    fee_trace = next(t for t in fig.data if t.name == "Conv Fees")
    assert all(y > 0 for y in fee_trace.y), f"fee_usd is zero: {fee_trace.y}"


def test_fig_monthly_revenue_non_zero_without_card_rev():
    """revenue rows must be non-zero when card_revenue is None."""
    dates = ["2026-01-01", "2026-01-02"]
    rev = _rev_df(dates)

    fig = _fig_monthly_revenue(rev, None)
    assert fig is not None

    fee_trace = next(t for t in fig.data if t.name == "Conv Fees")
    assert all(y > 0 for y in fee_trace.y), f"fee_usd is zero: {fee_trace.y}"
```

- [ ] **Step 2: Run — confirm first test FAILS (dates don't merge)**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/reporting/test_overview_revenue.py::test_fig_monthly_revenue_non_zero_with_date_object_card_rev -v
```

Expected: FAIL — `AssertionError: fee_usd is zero` (the merge silently zeroes the revenue columns)

The second test should PASS already (no card_rev, so no merge issue). Confirm:

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/reporting/test_overview_revenue.py::test_fig_monthly_revenue_non_zero_without_card_rev -v
```

Expected: PASS

- [ ] **Step 3: Fix `_fig_monthly_revenue` in `overview.py`**

Read the current implementation (around line 197–250). The current merge block is:

```python
merged = revenue.copy()
if not _empty(card_revenue):
    merged = merged.merge(card_revenue, on="date", how="outer").fillna(0.0)
merged = merged.sort_values("date")
```

Replace with:

```python
merged = revenue.copy()
merged["date"] = pd.to_datetime(merged["date"]).dt.normalize()
if not _empty(card_revenue):
    cr = card_revenue.copy()
    cr["date"] = pd.to_datetime(cr["date"]).dt.normalize()
    merged = merged.merge(cr, on="date", how="outer").fillna(0.0)
merged = merged.sort_values("date")
```

- [ ] **Step 4: Run both tests — confirm both PASS**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/reporting/test_overview_revenue.py -v
```

Expected: both PASS

- [ ] **Step 5: Run full suite — no regressions**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/ -q --tb=no 2>&1 | tail -5
```

Expected: same 5 pre-existing failures, all others pass.

- [ ] **Step 6: Commit**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && git add nbs_bi/reporting/overview.py tests/reporting/test_overview_revenue.py && git commit -m "fix(overview): normalize dates in _fig_monthly_revenue before merge"
```

---

## Task 2: Add Yearly to `_resample_combined`

**Files:**
- Modify: `nbs_bi/reporting/overview.py` (around line 302–328)
- Test: `tests/reporting/test_overview_revenue.py`

- [ ] **Step 1: Write failing test**

Append to `tests/reporting/test_overview_revenue.py`:

```python
from nbs_bi.reporting.overview import _resample_combined


def test_resample_combined_yearly_collapses_to_one_row():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([
                "2026-01-15", "2026-02-15", "2026-03-15",
                "2026-04-15", "2026-05-15",
            ]),
            "conv_usd": [100.0, 200.0, 150.0, 300.0, 250.0],
            "card_usd": [50.0, 60.0, 70.0, 80.0, 90.0],
        }
    )
    result = _resample_combined(df, "Yearly")
    assert len(result) == 1
    assert abs(result["conv_usd"].iloc[0] - 1000.0) < 0.01
    assert abs(result["card_usd"].iloc[0] - 350.0) < 0.01
```

- [ ] **Step 2: Run — confirm FAIL**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/reporting/test_overview_revenue.py::test_resample_combined_yearly_collapses_to_one_row -v
```

Expected: FAIL — `_resample_combined` falls through to `"MS"` for any granularity that isn't Weekly, giving monthly rows instead of one yearly row.

- [ ] **Step 3: Fix `_resample_combined`**

Read the current implementation (around line 302–328). The current freq line is:

```python
freq = "7D" if granularity == "Weekly" else "MS"
```

Replace the entire function body with:

```python
def _resample_combined(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Resample merged conv+card DataFrame to the chosen granularity.

    Args:
        df: DataFrame with columns date (datetime), conv_usd, card_usd.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.

    Returns:
        Resampled DataFrame with the same column structure. Trailing stub
        buckets (where the bucket start equals the last data point) are
        dropped to avoid single-day partial periods at the end of a range.
    """
    if granularity == "Daily":
        return df
    freq_map = {"Weekly": "W-MON", "Monthly": "MS", "Yearly": "YS"}
    freq = freq_map.get(granularity)
    if freq is None:
        return df
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
```

Note: Weekly changed from `"7D"` to `"W-MON"` to align with the revenue/take-rate resampling.

- [ ] **Step 4: Run test — confirm PASS**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/reporting/test_overview_revenue.py::test_resample_combined_yearly_collapses_to_one_row -v
```

Expected: PASS

- [ ] **Step 5: Run full suite — no regressions**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/ -q --tb=no 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && git add nbs_bi/reporting/overview.py tests/reporting/test_overview_revenue.py && git commit -m "feat(overview): add Yearly granularity to _resample_combined; align Weekly to W-MON"
```

---

## Task 3: Shared granularity selector

**Files:**
- Modify: `nbs_bi/reporting/overview.py` — `render()`, `_render_revenue_trend`, `_render_combined_volume`, `_render_take_rate`

- [ ] **Step 1: Update `_render_revenue_trend` signature and body**

Read the current `_render_revenue_trend` (around line 866–899). The current body starts with:

```python
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
        ...
```

Replace the method with (remove the radio, accept `granularity` as parameter):

```python
def _render_revenue_trend(self, granularity: str) -> None:
    """Render revenue stacked bar at the chosen granularity.

    Args:
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.
    """
    if granularity in ("Daily", "Weekly"):
        rev = _get(self._r, "revenue_daily")
        card_rev = _get(self._r, "card_revenue_daily")
        if granularity == "Weekly":
            rev = _resample_revenue(rev, "Weekly") if not _empty(rev) else rev
            card_rev = (
                _resample_revenue(card_rev, "Weekly")
                if not _empty(card_rev)
                else card_rev
            )
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
```

- [ ] **Step 2: Update `_render_combined_volume` signature and body**

Read the current `_render_combined_volume` (around line 909–902). Replace:

```python
def _render_combined_volume(self, granularity: str) -> None:
    """Render stacked bar: conversion + card spend volume in USD at chosen granularity.

    Args:
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.
    """
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

    fig = _fig_combined_volume(conv_daily, card_daily, fx_rate=fx_rate, granularity=granularity)
    if fig is None:
        st.info("No volume data for this period.")
        return
    st.plotly_chart(fig, use_container_width=True)
```

Note: the radio block (`granularity = st.radio(...)`) is removed; `granularity` is now a parameter.

- [ ] **Step 3: Update `_render_take_rate` signature and body**

Read the current `_render_take_rate` (around line 904). Replace with:

```python
def _render_take_rate(self, granularity: str) -> None:
    """Render take rate % line chart at the chosen granularity.

    Args:
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.
    """
    summary = _get(self._r, "summary")
    vol_usd = float(_kpi(summary, "Total volume USD") or 0.0)
    brl_onramp = float(_kpi(summary, "Onramp volume BRL") or 0.0)
    brl_offramp = float(_kpi(summary, "Offramp volume BRL") or 0.0)
    brl_total = brl_onramp + brl_offramp
    fx_rate = brl_total / vol_usd if vol_usd > 0 else 0.0

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
```

- [ ] **Step 4: Update `render()` to add shared radio and pass granularity**

Read the current `render()` (around line 734). Replace:

```python
def render(self) -> None:
    """Render all overview components."""
    self._render_volume_kpis()
    col_left, col_right = st.columns(2)
    with col_left:
        self._render_funnel()
    with col_right:
        self._render_active_users()
    granularity = st.radio(
        "Granularity",
        ["Daily", "Weekly", "Monthly", "Yearly"],
        index=2,
        horizontal=True,
        key="overview_charts_gran",
    )
    self._render_revenue_trend(granularity)
    self._render_combined_volume(granularity)
    self._render_take_rate(granularity)
```

- [ ] **Step 5: Run full suite — no regressions**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && python -m pytest tests/ -q --tb=no 2>&1 | tail -5
```

Expected: same 5 pre-existing failures, all others pass.

- [ ] **Step 6: Lint check**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && ruff check nbs_bi/reporting/overview.py
```

Fix any new violations (ignore pre-existing E501 on hovertemplate lines).

- [ ] **Step 7: Commit**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && git add nbs_bi/reporting/overview.py && git commit -m "feat(overview): shared granularity radio for revenue, volume, and take rate charts"
```

---

## Task 4: CHANGELOG update

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Prepend to `[Unreleased]` section in `CHANGELOG.md`**

Add under the existing `## [Unreleased]` bullet points:

```markdown
- Overview tab: fix revenue (USD) chart showing zeros for Daily/Weekly/Yearly (date-type mismatch in merge)
- Overview tab: single shared Granularity radio (Daily/Weekly/Monthly/Yearly) controls all three bottom charts
- `_resample_combined`: added Yearly granularity; aligned Weekly anchor to W-MON
```

- [ ] **Step 2: Commit**

```bash
cd /home/david/Documents/nbs/repos/nbs_bi && git add CHANGELOG.md && git commit -m "docs: log revenue fix and shared granularity in CHANGELOG"
```
