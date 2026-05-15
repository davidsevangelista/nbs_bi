# Overview Combined Volume Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed "Monthly BRL Volume" chart in the Overview tab's right column with a stacked bar chart showing conversion volume + card spend volume, both in USD, with a Daily / Weekly / Monthly granularity toggle.

**Architecture:** All changes are confined to `nbs_bi/reporting/overview.py`. Two new pure functions handle resampling and figure-building; one new render method replaces `_render_volume()`. The FX rate is derived from the existing `summary` KPI (period median), so no new DB queries or report keys are needed.

**Tech Stack:** Python 3.11, pandas, Plotly `graph_objects`, Streamlit

---

## File Map

| Action | Path |
|---|---|
| Modify | `nbs_bi/reporting/overview.py` |
| Create | `tests/reporting/test_overview.py` |

---

### Task 1: Write failing tests for `_resample_combined`

**Files:**
- Create: `tests/reporting/test_overview.py`

- [ ] **Step 1: Create the test file with failing tests**

```python
"""Tests for nbs_bi.reporting.overview — combined volume figure helpers."""

import pandas as pd
import pytest
import plotly.graph_objects as go

from nbs_bi.reporting.overview import _resample_combined, _fig_combined_volume


def _make_combined(n: int = 6) -> pd.DataFrame:
    """n days of merged conv+card data starting 2026-01-01."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "conv_usd": [10.0] * n,
        "card_usd": [5.0] * n,
    })


def test_resample_combined_daily_unchanged() -> None:
    df = _make_combined(6)
    result = _resample_combined(df, "Daily")
    assert len(result) == 6


def test_resample_combined_weekly_reduces_rows() -> None:
    # 14 days → 2 weeks
    df = _make_combined(14)
    result = _resample_combined(df, "Weekly")
    assert len(result) == 2


def test_resample_combined_monthly_reduces_rows() -> None:
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "conv_usd": [10.0] * 60,
        "card_usd": [5.0] * 60,
    })
    result = _resample_combined(df, "Monthly")
    assert len(result) == 2


def test_resample_combined_weekly_sums_correctly() -> None:
    # 7 days, conv_usd=10 each → weekly sum = 70
    df = _make_combined(7)
    result = _resample_combined(df, "Weekly")
    assert result["conv_usd"].iloc[0] == pytest.approx(70.0)
    assert result["card_usd"].iloc[0] == pytest.approx(35.0)


def test_resample_combined_daily_preserves_values() -> None:
    df = _make_combined(3)
    result = _resample_combined(df, "Daily")
    assert result["conv_usd"].iloc[0] == pytest.approx(10.0)
    assert result["card_usd"].iloc[0] == pytest.approx(5.0)
```

- [ ] **Step 2: Run to confirm they fail (import error expected)**

```bash
pytest tests/reporting/test_overview.py -v 2>&1 | tail -15
```

Expected: `ImportError: cannot import name '_resample_combined'`

---

### Task 2: Write failing tests for `_fig_combined_volume`

**Files:**
- Modify: `tests/reporting/test_overview.py`

- [ ] **Step 1: Append figure builder tests to the file**

```python
def _make_conv_daily(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "onramp": [1000.0] * n,
        "offramp": [500.0] * n,
    })


def _make_card_daily(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "amount_usd": [200.0] * n,
    })


def test_fig_combined_volume_returns_figure() -> None:
    fig = _fig_combined_volume(_make_conv_daily(), _make_card_daily(), fx_rate=5.0, granularity="Monthly")
    assert isinstance(fig, go.Figure)


def test_fig_combined_volume_has_two_bar_traces() -> None:
    fig = _fig_combined_volume(_make_conv_daily(), _make_card_daily(), fx_rate=5.0, granularity="Monthly")
    bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
    assert len(bar_traces) == 2


def test_fig_combined_volume_returns_none_when_both_empty() -> None:
    fig = _fig_combined_volume(pd.DataFrame(), pd.DataFrame(), fx_rate=5.0, granularity="Monthly")
    assert fig is None


def test_fig_combined_volume_conv_usd_uses_fx_rate() -> None:
    # onramp=1000 BRL, offramp=0, fx_rate=5.0 → conv_usd=200 per day
    conv = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=1), "onramp": [1000.0], "offramp": [0.0]})
    card = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=1), "amount_usd": [0.0]})
    fig = _fig_combined_volume(conv, card, fx_rate=5.0, granularity="Daily")
    conv_trace = fig.data[0]
    assert float(conv_trace.y[0]) == pytest.approx(200.0)


def test_fig_combined_volume_card_only_when_no_conv() -> None:
    # Empty conv_daily → conv bar is zero, card bar has value
    card = _make_card_daily(1)
    fig = _fig_combined_volume(pd.DataFrame(), card, fx_rate=5.0, granularity="Daily")
    assert isinstance(fig, go.Figure)
    bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
    assert len(bar_traces) == 2
    # card trace has non-zero value
    card_trace = bar_traces[1]
    assert float(card_trace.y[0]) == pytest.approx(200.0)


def test_fig_combined_volume_zero_fx_rate_does_not_raise() -> None:
    fig = _fig_combined_volume(_make_conv_daily(), _make_card_daily(), fx_rate=0.0, granularity="Monthly")
    # Should not raise; conversion bars will be NaN/inf → handled gracefully
    assert fig is not None
```

- [ ] **Step 2: Run to confirm they still fail**

```bash
pytest tests/reporting/test_overview.py -v 2>&1 | tail -10
```

Expected: `ImportError: cannot import name '_resample_combined'` (same as before — nothing implemented yet)

---

### Task 3: Implement `_resample_combined` and `_fig_combined_volume`

**Files:**
- Modify: `nbs_bi/reporting/overview.py`

- [ ] **Step 1: Add `_resample_combined` after `_fig_volume_monthly` (around line 264)**

Insert this block after the closing of `_fig_volume_monthly`:

```python
def _resample_combined(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Resample merged conv+card DataFrame to the chosen granularity.

    Args:
        df: DataFrame with columns date (datetime), conv_usd, card_usd.
        granularity: One of 'Daily', 'Weekly', 'Monthly'.

    Returns:
        Resampled DataFrame with the same column structure.
    """
    if granularity == "Daily":
        return df
    freq = "W-MON" if granularity == "Weekly" else "MS"
    return (
        df.set_index("date")[["conv_usd", "card_usd"]]
        .resample(freq)
        .sum()
        .reset_index()
    )


def _fig_combined_volume(
    conv_daily: pd.DataFrame,
    card_daily: pd.DataFrame,
    fx_rate: float,
    granularity: str = "Monthly",
) -> go.Figure | None:
    """Stacked bar: conversion volume (USD) + card spend (USD) at chosen granularity.

    Args:
        conv_daily: DataFrame with columns date, onramp (BRL), offramp (BRL).
        card_daily: DataFrame with columns date, amount_usd.
        fx_rate: Period median BRL/USDC rate used to convert BRL volume to USD.
            If 0.0, conversion bars are set to 0 to avoid division by zero.
        granularity: One of 'Daily', 'Weekly', 'Monthly'.

    Returns:
        Plotly Figure or None if both inputs are empty.
    """
    conv_empty = _empty(conv_daily)
    card_empty = _empty(card_daily) or "amount_usd" not in card_daily.columns
    if conv_empty and card_empty:
        return None

    safe_rate = fx_rate if fx_rate and fx_rate > 0 else None

    if not conv_empty:
        c = conv_daily[["date"]].copy()
        c["date"] = pd.to_datetime(c["date"], errors="coerce")
        brl = conv_daily.get("onramp", pd.Series(0.0)).fillna(0.0) + \
              conv_daily.get("offramp", pd.Series(0.0)).fillna(0.0)
        c["conv_usd"] = brl / safe_rate if safe_rate else pd.Series(0.0, index=c.index)
    else:
        if not card_empty:
            c = card_daily[["date"]].copy()
            c["date"] = pd.to_datetime(c["date"], errors="coerce")
        c["conv_usd"] = 0.0

    if not card_empty:
        k = card_daily[["date", "amount_usd"]].copy()
        k["date"] = pd.to_datetime(k["date"], errors="coerce")
        k = k.rename(columns={"amount_usd": "card_usd"})
    else:
        k = c[["date"]].copy()
        k["card_usd"] = 0.0

    merged = c.merge(k, on="date", how="outer").fillna(0.0).sort_values("date")
    df = _resample_combined(merged, granularity)

    totals = (df["conv_usd"].fillna(0.0) + df["card_usd"].fillna(0.0))
    _t = [[v] for v in totals]

    fig = go.Figure()
    for col, label, color in [
        ("conv_usd", "Conversions (USD)", BLUE),
        ("card_usd", "Card Spend (USD)", AMBER),
    ]:
        fig.add_trace(
            go.Bar(
                x=df["date"],
                y=df[col],
                name=label,
                marker_color=color,
                customdata=_t,
                hovertemplate=f"<b>{label}</b>: $%{{y:,.0f}}<br><b>Total</b>: $%{{customdata[0]:,.0f}}<extra></extra>",
            )
        )
    layout = panel("Volume (USD)")
    layout["barmode"] = "stack"
    layout["yaxis"]["title"] = "USD"
    fig.update_layout(**layout)
    return fig
```

- [ ] **Step 2: Run the tests — expect them to pass**

```bash
pytest tests/reporting/test_overview.py -v 2>&1 | tail -20
```

Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add nbs_bi/reporting/overview.py tests/reporting/test_overview.py
git commit -m "Add _resample_combined and _fig_combined_volume to overview"
```

---

### Task 4: Replace `_render_volume` with `_render_combined_volume`

**Files:**
- Modify: `nbs_bi/reporting/overview.py`

- [ ] **Step 1: Add `_render_combined_volume` method to `OverviewSection`**

Add the new method right after `_render_volume` (around line 555):

```python
def _render_combined_volume(self) -> None:
    """Render stacked bar: conversion + card spend volume in USD with granularity toggle."""
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
    fx_rate = vol_usd / brl_total if brl_total > 0 else 1.0

    granularity = st.radio(
        "Granularity",
        ["Daily", "Weekly", "Monthly"],
        horizontal=True,
        key="overview_vol_gran",
    )
    fig = _fig_combined_volume(conv_daily, card_daily, fx_rate=fx_rate, granularity=granularity)
    if fig is None:
        st.info("No volume data for this period.")
        return
    st.plotly_chart(fig, width="stretch")
```

- [ ] **Step 2: Update `render()` to call the new method**

Find this line in `render()`:

```python
            self._render_volume()
```

Replace it with:

```python
            self._render_combined_volume()
```

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```bash
pytest tests/ -v 2>&1 | tail -15
```

Expected: same pass/fail count as before this task (5 pre-existing failures, all others green)

- [ ] **Step 4: Commit**

```bash
git add nbs_bi/reporting/overview.py
git commit -m "Replace monthly BRL volume chart with combined USD volume chart + granularity toggle"
```

---

### Task 5: Update CHANGELOG and PROGRESS

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/PROGRESS.md`

- [ ] **Step 1: Prepend to `CHANGELOG.md` under `[Unreleased]`**

```markdown
## [2.5.3] — 2026-05-15

Overview tab: combined volume chart (USD) with granularity toggle.

### Added
- `nbs_bi/reporting/overview.py` — `_resample_combined(df, granularity)`: resamples merged conv+card DataFrame to Daily / Weekly / Monthly buckets
- `nbs_bi/reporting/overview.py` — `_fig_combined_volume(conv_daily, card_daily, fx_rate, granularity)`: stacked bar chart showing conversion volume (BRL→USD at period median rate) + card spend (USD)
- `tests/reporting/test_overview.py` — 11 unit tests covering resampling, figure structure, FX conversion, and empty-data edge cases

### Changed
- `nbs_bi/reporting/overview.py` — `_render_volume()` replaced by `_render_combined_volume()`: adds dedicated Daily/Weekly/Monthly granularity radio (key `"overview_vol_gran"`) and renders the new combined chart
```

- [ ] **Step 2: Update `docs/PROGRESS.md`** — in the Phase 6 reporting section, add:

```
- [x] `reporting/overview.py` — combined volume chart (USD): conversion + card spend stacked bar with Daily/Weekly/Monthly toggle; replaces fixed monthly BRL chart
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/PROGRESS.md
git commit -m "Update CHANGELOG and PROGRESS for combined volume chart (v2.5.3)"
```
