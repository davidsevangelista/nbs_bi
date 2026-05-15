# Take Rate Chart — Overview Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a full-width take rate % line chart as a fourth row in the Overview tab, with Daily / Weekly / Monthly / Yearly granularity toggle.

**Architecture:** Two pure functions (`_compute_take_rate`, `_fig_take_rate`) are added as module-level helpers in `nbs_bi/reporting/overview.py`, following the existing pattern of `_resample_revenue` / `_fig_monthly_revenue`. A `_render_take_rate()` method wires them to Streamlit. No new DB queries are issued — all inputs come from the report dict already built by `OnrampReport.build()`.

**Tech Stack:** Python 3.11, pandas, Plotly `go.Scatter`, Streamlit.

---

## File Map

| File | Change |
|---|---|
| `nbs_bi/reporting/overview.py` | Add `_compute_take_rate`, `_fig_take_rate`, `_render_take_rate`; update `render()` |
| `tests/reporting/test_overview_take_rate.py` | New — unit tests for `_compute_take_rate` and `_fig_take_rate` |

---

## Context: existing data available in the report dict

`OverviewSection.__init__` stores the ramp report dict as `self._r`. The following keys are already populated by `OnrampReport.build()`:

| Key | Columns |
|---|---|
| `revenue_daily` | `date` (datetime), `fee_usd` (float), `spread_usd` (float) |
| `card_revenue_daily` | `date` (datetime), `card_fee_usd` (float), `billing_usd` (float) |
| `revenue_monthly` | `month` (datetime), `fee_usd`, `spread_usd` |
| `card_revenue_monthly` | `month` (datetime), `card_fee_usd`, `billing_usd` |
| `conv_daily` | `date` (datetime), `onramp` (BRL float), `offramp` (BRL float) |
| `card_daily` | `date` (datetime), `amount_usd` (float), `n_txns` (int) |
| `summary` | KPI rows — used to derive `fx_rate` (BRL/USD) |

The fx_rate is computed identically to `_render_combined_volume`:
```python
vol_usd = float(_kpi(summary, "Total volume USD") or 0.0)
brl_onramp = float(_kpi(summary, "Onramp volume BRL") or 0.0)
brl_offramp = float(_kpi(summary, "Offramp volume BRL") or 0.0)
brl_total = brl_onramp + brl_offramp
fx_rate = brl_total / vol_usd if vol_usd > 0 else 1.0
```

Helpers already importable from `nbs_bi.reporting.theme`:
- `EMERALD` — the green color constant to use for the take rate line
- `panel(title)` — returns a base layout dict for consistent styling
- `_empty(df)` — returns True if df is None or empty
- `_get(report_dict, key)` — safely fetches a key, returning an empty DataFrame if missing
- `_kpi(summary_df, label)` — extracts a scalar KPI value from the summary DataFrame

Existing resample helpers to reuse:
- `_resample_revenue(df, granularity)` — resamples a `date`-indexed DataFrame to Weekly/Monthly/Yearly
- `_resample_combined(df, granularity)` — resamples the `conv_usd`/`card_usd` merged DataFrame

---

## Task 1: Pure computation function `_compute_take_rate`

**Files:**
- Modify: `nbs_bi/reporting/overview.py` (insert after `_resample_combined`, before `_fig_combined_volume`, around line 319)
- Test: `tests/reporting/test_overview_take_rate.py` (create)

- [ ] **Step 1: Create the test file with a failing test**

```python
# tests/reporting/test_overview_take_rate.py
"""Unit tests for the take rate computation and figure helpers."""

import math

import pandas as pd
import pytest

from nbs_bi.reporting.overview import _compute_take_rate


@pytest.fixture()
def daily_rev() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "fee_usd": [10.0, 20.0],
            "spread_usd": [5.0, 10.0],
        }
    )


@pytest.fixture()
def daily_card_rev() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "card_fee_usd": [2.0, 4.0],
            "billing_usd": [1.0, 2.0],
        }
    )


@pytest.fixture()
def daily_conv() -> pd.DataFrame:
    # BRL volumes; fx_rate=5.0 → USD = BRL/5
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "onramp": [500.0, 1000.0],
            "offramp": [0.0, 0.0],
        }
    )


@pytest.fixture()
def daily_card() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "amount_usd": [50.0, 100.0],
        }
    )


def test_compute_take_rate_basic(daily_rev, daily_card_rev, daily_conv, daily_card):
    # Day 1: rev = 10+5+2+1 = 18; vol = 500/5 + 50 = 150; rate = 18/150 = 12%
    # Day 2: rev = 20+10+4+2 = 36; vol = 1000/5 + 100 = 300; rate = 36/300 = 12%
    result = _compute_take_rate(
        daily_rev, daily_card_rev, daily_conv, daily_card,
        fx_rate=5.0,
        granularity="Daily",
    )
    assert list(result.columns) == ["date", "take_rate_pct"]
    assert len(result) == 2
    assert abs(result.loc[0, "take_rate_pct"] - 12.0) < 0.01
    assert abs(result.loc[1, "take_rate_pct"] - 12.0) < 0.01
```

- [ ] **Step 2: Run the test — confirm it fails with ImportError**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_compute_take_rate_basic -v
```

Expected: `ImportError: cannot import name '_compute_take_rate' from 'nbs_bi.reporting.overview'`

- [ ] **Step 3: Implement `_compute_take_rate` in `overview.py`**

Insert this block immediately after `_resample_combined` (around line 319) and before `_fig_combined_volume`:

```python
def _compute_take_rate(
    rev_df: pd.DataFrame,
    card_rev_df: pd.DataFrame,
    conv_daily: pd.DataFrame,
    card_daily: pd.DataFrame,
    fx_rate: float,
    granularity: str,
) -> pd.DataFrame:
    """Compute take rate (%) per period: total revenue / total volume * 100.

    Args:
        rev_df: Daily conversion revenue with columns date, fee_usd, spread_usd.
        card_rev_df: Daily card/billing revenue with columns date, card_fee_usd, billing_usd.
        conv_daily: Daily conversion volumes with columns date, onramp (BRL), offramp (BRL).
        card_daily: Daily card spend with columns date, amount_usd.
        fx_rate: Period BRL/USD rate used to convert BRL volumes to USD.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.

    Returns:
        DataFrame with columns [date, take_rate_pct]. Periods with zero volume
        are dropped. Returns empty DataFrame if inputs are all empty.
    """
    safe_rate = fx_rate if fx_rate and fx_rate > 0 else None

    # --- Revenue side ---
    rev = rev_df.copy() if not _empty(rev_df) else pd.DataFrame(columns=["date", "fee_usd", "spread_usd"])
    card_rev = card_rev_df.copy() if not _empty(card_rev_df) else pd.DataFrame(columns=["date", "card_fee_usd", "billing_usd"])

    rev["date"] = pd.to_datetime(rev["date"], errors="coerce")
    card_rev["date"] = pd.to_datetime(card_rev["date"], errors="coerce")

    if granularity != "Daily":
        rev = _resample_revenue(rev, granularity)
        card_rev = _resample_revenue(card_rev, granularity)

    revenue = rev.merge(card_rev, on="date", how="outer").fillna(0.0)
    for col in ("fee_usd", "spread_usd", "card_fee_usd", "billing_usd"):
        if col not in revenue.columns:
            revenue[col] = 0.0
    revenue["total_rev"] = (
        revenue["fee_usd"] + revenue["spread_usd"]
        + revenue["card_fee_usd"] + revenue["billing_usd"]
    )

    # --- Volume side ---
    conv = conv_daily.copy() if not _empty(conv_daily) else pd.DataFrame(columns=["date", "onramp", "offramp"])
    card = card_daily.copy() if not _empty(card_daily) else pd.DataFrame(columns=["date", "amount_usd"])

    conv["date"] = pd.to_datetime(conv["date"], errors="coerce")
    card["date"] = pd.to_datetime(card["date"], errors="coerce")

    for col in ("onramp", "offramp"):
        if col not in conv.columns:
            conv[col] = 0.0
    if "amount_usd" not in card.columns:
        card["amount_usd"] = 0.0

    if granularity != "Daily":
        freq = {"Weekly": "7D", "Monthly": "MS", "Yearly": "YS"}.get(granularity)
        if freq:
            conv = conv.set_index("date")[["onramp", "offramp"]].resample(freq).sum().reset_index()
            card = card.set_index("date")[["amount_usd"]].resample(freq).sum().reset_index()

    brl = conv["onramp"].fillna(0.0) + conv["offramp"].fillna(0.0)
    conv["conv_usd"] = brl / safe_rate if safe_rate else pd.Series(0.0, index=conv.index)

    vol = conv[["date", "conv_usd"]].merge(card[["date", "amount_usd"]], on="date", how="outer").fillna(0.0)
    vol["total_vol"] = vol["conv_usd"] + vol["amount_usd"]

    # --- Merge and compute rate ---
    merged = revenue[["date", "total_rev"]].merge(vol[["date", "total_vol"]], on="date", how="outer").fillna(0.0)
    merged = merged[merged["total_vol"] > 0].copy()
    if merged.empty:
        return pd.DataFrame(columns=["date", "take_rate_pct"])
    merged["take_rate_pct"] = merged["total_rev"] / merged["total_vol"] * 100
    return merged[["date", "take_rate_pct"]].sort_values("date").reset_index(drop=True)
```

- [ ] **Step 4: Run the test — confirm it passes**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_compute_take_rate_basic -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add nbs_bi/reporting/overview.py tests/reporting/test_overview_take_rate.py
git commit -m "feat(overview): add _compute_take_rate pure function"
```

---

## Task 2: Edge case tests for `_compute_take_rate`

**Files:**
- Test: `tests/reporting/test_overview_take_rate.py`

- [ ] **Step 1: Add three edge-case tests**

Append to `tests/reporting/test_overview_take_rate.py`:

```python
def test_compute_take_rate_zero_volume_row_dropped(daily_rev, daily_card_rev, daily_card):
    # conv_daily has zero BRL and card_daily has zero USD for day 1
    conv = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "onramp": [0.0, 1000.0],
            "offramp": [0.0, 0.0],
        }
    )
    card_zero = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "amount_usd": [0.0, 100.0],
        }
    )
    result = _compute_take_rate(
        daily_rev, daily_card_rev, conv, card_zero,
        fx_rate=5.0,
        granularity="Daily",
    )
    # Day 1 has zero volume → must be absent
    assert len(result) == 1
    assert pd.Timestamp("2026-01-01") not in result["date"].values


def test_compute_take_rate_all_empty():
    result = _compute_take_rate(
        pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        fx_rate=5.0,
        granularity="Daily",
    )
    assert result.empty
    assert list(result.columns) == ["date", "take_rate_pct"]


def test_compute_take_rate_weekly(daily_rev, daily_card_rev, daily_conv, daily_card):
    # Both dates fall in the same W-7D bucket → one output row
    result = _compute_take_rate(
        daily_rev, daily_card_rev, daily_conv, daily_card,
        fx_rate=5.0,
        granularity="Weekly",
    )
    assert len(result) == 1
    assert "take_rate_pct" in result.columns
    assert result.loc[0, "take_rate_pct"] > 0
```

- [ ] **Step 2: Run all three new tests — confirm they fail**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_compute_take_rate_zero_volume_row_dropped tests/reporting/test_overview_take_rate.py::test_compute_take_rate_all_empty tests/reporting/test_overview_take_rate.py::test_compute_take_rate_weekly -v
```

Expected: all three FAIL (function missing or wrong output)

- [ ] **Step 3: Run all tests in the file — confirm all pass (implementation already exists)**

```bash
pytest tests/reporting/test_overview_take_rate.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/reporting/test_overview_take_rate.py
git commit -m "test(overview): edge cases for _compute_take_rate"
```

---

## Task 3: `_fig_take_rate` figure builder

**Files:**
- Modify: `nbs_bi/reporting/overview.py` (insert after `_compute_take_rate`, before `_fig_combined_volume`)
- Test: `tests/reporting/test_overview_take_rate.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/reporting/test_overview_take_rate.py`:

```python
import plotly.graph_objects as go

from nbs_bi.reporting.overview import _fig_take_rate


def test_fig_take_rate_returns_none_on_empty():
    assert _fig_take_rate(pd.DataFrame()) is None
    assert _fig_take_rate(pd.DataFrame(columns=["date", "take_rate_pct"])) is None


def test_fig_take_rate_has_one_scatter_trace():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "take_rate_pct": [5.0, 6.0],
        }
    )
    fig = _fig_take_rate(df)
    assert fig is not None
    assert len(fig.data) == 1
    assert isinstance(fig.data[0], go.Scatter)


def test_fig_take_rate_y_axis_labelled_pct():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"]),
            "take_rate_pct": [5.0],
        }
    )
    fig = _fig_take_rate(df)
    assert fig.layout.yaxis.title.text == "%"
```

- [ ] **Step 2: Run — confirm ImportError on `_fig_take_rate`**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_fig_take_rate_returns_none_on_empty -v
```

Expected: `ImportError: cannot import name '_fig_take_rate'`

- [ ] **Step 3: Implement `_fig_take_rate` in `overview.py`**

Insert immediately after `_compute_take_rate` and before `_fig_combined_volume`:

```python
def _fig_take_rate(df: pd.DataFrame) -> go.Figure | None:
    """Line chart showing take rate (%) over time.

    Args:
        df: DataFrame with columns date (datetime) and take_rate_pct (float).

    Returns:
        Plotly Figure or None if df is empty.
    """
    if _empty(df) or "take_rate_pct" not in df.columns or df["take_rate_pct"].dropna().empty:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["take_rate_pct"],
            mode="lines+markers",
            name="Take Rate",
            line=dict(color=EMERALD, width=2),
            marker=dict(size=5),
            hovertemplate="<b>Take Rate</b>: %{y:.2f}%<extra></extra>",
        )
    )
    layout = panel("Take Rate (%)")
    layout["yaxis"]["title"] = "%"
    layout["yaxis"]["ticksuffix"] = "%"
    fig.update_layout(**layout)
    return fig
```

- [ ] **Step 4: Run all tests in file — confirm all pass**

```bash
pytest tests/reporting/test_overview_take_rate.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add nbs_bi/reporting/overview.py tests/reporting/test_overview_take_rate.py
git commit -m "feat(overview): add _fig_take_rate line chart builder"
```

---

## Task 4: Wire `_render_take_rate` into `OverviewSection.render()`

**Files:**
- Modify: `nbs_bi/reporting/overview.py` — add `_render_take_rate` method; update `render()`

- [ ] **Step 1: Add `_render_take_rate` method to `OverviewSection`**

Insert after `_render_combined_volume` (around line 742 in the current file):

```python
def _render_take_rate(self) -> None:
    """Render take rate % line chart with Daily/Weekly/Monthly/Yearly toggle."""
    summary = _get(self._r, "summary")
    vol_usd = float(_kpi(summary, "Total volume USD") or 0.0)
    brl_onramp = float(_kpi(summary, "Onramp volume BRL") or 0.0)
    brl_offramp = float(_kpi(summary, "Offramp volume BRL") or 0.0)
    brl_total = brl_onramp + brl_offramp
    fx_rate = brl_total / vol_usd if vol_usd > 0 else 1.0

    granularity = st.radio(
        "Granularity",
        ["Daily", "Weekly", "Monthly", "Yearly"],
        index=2,
        horizontal=True,
        key="overview_tr_gran",
    )

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

    df = _compute_take_rate(rev, card_rev, conv_daily, card_daily, fx_rate=fx_rate, granularity=granularity)
    fig = _fig_take_rate(df)
    if fig is None:
        st.info("No data to compute take rate for this period.")
        return
    st.plotly_chart(fig, use_container_width=True)
```

- [ ] **Step 2: Update `render()` to call `_render_take_rate`**

Find the `render` method (around line 546). Its current body:

```python
def render(self) -> None:
    """Render all overview components."""
    self._render_volume_kpis()
    col_left, col_right = st.columns(2)
    with col_left:
        self._render_funnel()
    with col_right:
        self._render_active_users()
    self._render_revenue_trend()
    self._render_combined_volume()
```

Replace with:

```python
def render(self) -> None:
    """Render all overview components."""
    self._render_volume_kpis()
    col_left, col_right = st.columns(2)
    with col_left:
        self._render_funnel()
    with col_right:
        self._render_active_users()
    self._render_revenue_trend()
    self._render_combined_volume()
    self._render_take_rate()
```

- [ ] **Step 3: Run the full test suite — confirm 229+ pass, no regressions**

```bash
pytest tests/ -q --tb=short
```

Expected: same count of pre-existing failures (5 in `tests/cards/test_simulator.py` and `tests/clients/test_campaigns.py`), all new tests pass.

- [ ] **Step 4: Commit**

```bash
git add nbs_bi/reporting/overview.py
git commit -m "feat(overview): wire _render_take_rate into Overview tab as fourth full-width row"
```

---

## Task 5: CHANGELOG and PROGRESS update

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/PROGRESS.md`

- [ ] **Step 1: Prepend entry to `CHANGELOG.md`**

Add at the top of `CHANGELOG.md` (after any existing header):

```markdown
## [Unreleased] - 2026-05-15
### Added
- Overview tab: take rate % line chart as fourth full-width row, with Daily/Weekly/Monthly/Yearly granularity toggle
- `_compute_take_rate` pure function: computes total revenue ÷ total volume per period, drops zero-volume periods
- `_fig_take_rate` pure function: single `go.Scatter` line chart with % y-axis
```

- [ ] **Step 2: Update `docs/PROGRESS.md`**

In the reporting section, note the new chart was added.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/PROGRESS.md
git commit -m "docs: log take rate chart addition in CHANGELOG and PROGRESS"
```
