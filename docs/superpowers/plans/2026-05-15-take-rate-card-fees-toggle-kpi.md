# Take Rate — Card Fees Toggle & KPI Annotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a card-annual-fees toggle and a volume-weighted avg/L30 KPI annotation to the take rate line chart on the Overview tab.

**Architecture:** Three focused changes to `nbs_bi/reporting/overview.py`: (1) thread `include_card_fees: bool` through `_agg_revenue` → `_compute_take_rate`, exposing `total_rev`/`total_vol` columns in the return; (2) add pure `_take_rate_kpis(df)` to compute the two KPIs; (3) update `_fig_take_rate` to accept and render them as a Plotly annotation, and update `_render_take_rate` with the checkbox and the new calling convention.

**Tech Stack:** Python 3.11, pandas, Plotly graph_objects, Streamlit, pytest

---

## File Map

| File | Role |
|---|---|
| `nbs_bi/reporting/overview.py` | All production changes — four functions modified, one added |
| `tests/reporting/test_overview_take_rate.py` | Existing tests updated + 6 new tests added |

---

### Task 1: `_agg_revenue` toggle + `_compute_take_rate` column exposure

**Files:**
- Modify: `nbs_bi/reporting/overview.py` — `_agg_revenue` (lines ~337–373), `_compute_take_rate` (lines ~450–479)
- Test: `tests/reporting/test_overview_take_rate.py`

---

- [ ] **Step 1: Write the failing test for `_agg_revenue` exclusion**

Add to `tests/reporting/test_overview_take_rate.py`:

```python
from nbs_bi.reporting.overview import _agg_revenue


def test_agg_revenue_excludes_card_fees(daily_rev, daily_card_rev):
    # fee_usd=10+20=30, spread_usd=5+10=15, billing_usd=1+2=3; card_fee_usd excluded
    result = _agg_revenue(daily_rev, daily_card_rev, "Daily", include_card_fees=False)
    expected_total = 30.0 + 15.0 + 3.0  # no card_fee_usd (2+4=6)
    assert abs(result["total_rev"].sum() - expected_total) < 0.01
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_agg_revenue_excludes_card_fees -v
```

Expected: `FAILED` — `_agg_revenue() got an unexpected keyword argument 'include_card_fees'`

- [ ] **Step 3: Update `_agg_revenue` to accept `include_card_fees`**

In `nbs_bi/reporting/overview.py`, replace the `_agg_revenue` signature and `total_rev` line:

```python
def _agg_revenue(
    rev_df: pd.DataFrame,
    card_rev_df: pd.DataFrame,
    granularity: str,
    include_card_fees: bool = True,
) -> pd.DataFrame:
    """Aggregate conversion + card/billing revenue per period.

    Args:
        rev_df: Daily conversion revenue with columns date, fee_usd, spread_usd.
        card_rev_df: Daily card/billing revenue with columns date, card_fee_usd, billing_usd.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.
        include_card_fees: When False, card_fee_usd is excluded from total_rev.

    Returns:
        DataFrame with columns: date, total_rev.
    """
```

Replace the `total_rev` line (keep everything else intact):

```python
    card_fee = revenue["card_fee_usd"] if include_card_fees else 0.0
    revenue["total_rev"] = (
        revenue["fee_usd"] + revenue["spread_usd"]
        + card_fee + revenue["billing_usd"]
    )
    return revenue[["date", "total_rev"]]
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_agg_revenue_excludes_card_fees -v
```

Expected: `PASSED`

- [ ] **Step 5: Write the failing tests for `_compute_take_rate` column exposure**

Update the existing `test_compute_take_rate_basic` column assertion and `test_compute_take_rate_all_empty`, and add a new test to `tests/reporting/test_overview_take_rate.py`.

Replace the two existing assertions:

```python
# In test_compute_take_rate_basic — change:
assert list(result.columns) == ["date", "take_rate_pct"]
# to:
assert {"date", "take_rate_pct", "total_rev", "total_vol"}.issubset(result.columns)
```

```python
# In test_compute_take_rate_all_empty — change:
assert list(result.columns) == ["date", "take_rate_pct"]
# to:
assert list(result.columns) == ["date", "take_rate_pct", "total_rev", "total_vol"]
```

Add a new test:

```python
def test_compute_take_rate_exposes_rev_vol(daily_rev, daily_card_rev, daily_conv, daily_card):
    result = _compute_take_rate(
        daily_rev, daily_card_rev, daily_conv, daily_card,
        fx_rate=5.0,
        granularity="Daily",
    )
    assert "total_rev" in result.columns
    assert "total_vol" in result.columns
    # Day 1: rev=18, vol=150
    assert abs(result.loc[0, "total_rev"] - 18.0) < 0.01
    assert abs(result.loc[0, "total_vol"] - 150.0) < 0.01
```

- [ ] **Step 6: Run these tests to confirm they fail**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_compute_take_rate_basic \
       tests/reporting/test_overview_take_rate.py::test_compute_take_rate_all_empty \
       tests/reporting/test_overview_take_rate.py::test_compute_take_rate_exposes_rev_vol -v
```

Expected: all three `FAILED`

- [ ] **Step 7: Update `_compute_take_rate` to expose columns and pass `include_card_fees`**

Replace the entire `_compute_take_rate` function:

```python
def _compute_take_rate(
    rev_df: pd.DataFrame,
    card_rev_df: pd.DataFrame,
    conv_daily: pd.DataFrame,
    card_daily: pd.DataFrame,
    fx_rate: float,
    granularity: str,
    include_card_fees: bool = True,
) -> pd.DataFrame:
    """Compute take rate (%) per period: total revenue / total volume * 100.

    Args:
        rev_df: Daily conversion revenue with columns date, fee_usd, spread_usd.
        card_rev_df: Daily card/billing revenue with columns date, card_fee_usd, billing_usd.
        conv_daily: Daily conversion volumes with columns date, onramp (BRL), offramp (BRL).
        card_daily: Daily card spend with columns date, amount_usd.
        fx_rate: Period BRL/USD rate. Zero or negative means no FX available.
        granularity: One of 'Daily', 'Weekly', 'Monthly', 'Yearly'.
        include_card_fees: When False, card_fee_usd is excluded from revenue.

    Returns:
        DataFrame with columns [date, take_rate_pct, total_rev, total_vol]. Periods
        with zero volume are dropped. Returns empty DataFrame if inputs are all empty.
    """
    _COLS = ["date", "take_rate_pct", "total_rev", "total_vol"]
    revenue = _agg_revenue(rev_df, card_rev_df, granularity, include_card_fees)
    vol = _agg_volume(conv_daily, card_daily, fx_rate, granularity)
    merged = revenue.merge(vol, on="date", how="outer").fillna(0.0)
    merged = merged[merged["total_vol"] > 0].copy()
    if merged.empty:
        return pd.DataFrame(columns=_COLS)
    merged["take_rate_pct"] = merged["total_rev"] / merged["total_vol"] * 100
    return merged[_COLS].sort_values("date").reset_index(drop=True)
```

- [ ] **Step 8: Run all take rate tests to confirm they pass**

```bash
pytest tests/reporting/test_overview_take_rate.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 9: Commit**

```bash
git add nbs_bi/reporting/overview.py tests/reporting/test_overview_take_rate.py
git commit -m "feat(overview): add include_card_fees param to take rate; expose total_rev/vol"
```

---

### Task 2: `_take_rate_kpis` + `_fig_take_rate` annotation

**Files:**
- Modify: `nbs_bi/reporting/overview.py` — add `_take_rate_kpis` after `_compute_take_rate`; update `_fig_take_rate`
- Test: `tests/reporting/test_overview_take_rate.py`

---

- [ ] **Step 1: Write the failing tests for `_take_rate_kpis`**

Add to `tests/reporting/test_overview_take_rate.py`:

```python
from nbs_bi.reporting.overview import _take_rate_kpis


def test_take_rate_kpis_avg():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        "take_rate_pct": [10.0, 20.0, 15.0],
        "total_rev": [10.0, 40.0, 30.0],
        "total_vol": [100.0, 200.0, 200.0],
    })
    avg_pct, _ = _take_rate_kpis(df)
    # sum(rev)=80, sum(vol)=500 → 16.0%
    assert abs(avg_pct - 16.0) < 0.01


def test_take_rate_kpis_l30():
    today = pd.Timestamp("2026-03-31")
    df = pd.DataFrame({
        "date": [today - pd.Timedelta(days=60), today - pd.Timedelta(days=5)],
        "take_rate_pct": [5.0, 10.0],
        "total_rev": [5.0, 20.0],
        "total_vol": [100.0, 200.0],
    })
    _, l30_pct = _take_rate_kpis(df)
    # only the 5-day-ago row is within 30 days: 20/200*100 = 10.0%
    assert abs(l30_pct - 10.0) < 0.01


def test_take_rate_kpis_empty():
    avg_pct, l30_pct = _take_rate_kpis(pd.DataFrame())
    assert avg_pct is None
    assert l30_pct is None
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_take_rate_kpis_avg \
       tests/reporting/test_overview_take_rate.py::test_take_rate_kpis_l30 \
       tests/reporting/test_overview_take_rate.py::test_take_rate_kpis_empty -v
```

Expected: all `FAILED` — `cannot import name '_take_rate_kpis'`

- [ ] **Step 3: Implement `_take_rate_kpis`**

Insert the following function in `nbs_bi/reporting/overview.py` immediately after `_compute_take_rate` (before `_fig_take_rate`):

```python
def _take_rate_kpis(
    df: pd.DataFrame,
) -> tuple[float | None, float | None]:
    """Compute overall and last-30-day volume-weighted average take rate.

    Args:
        df: DataFrame with columns date, take_rate_pct, total_rev, total_vol.

    Returns:
        Tuple (avg_pct, l30_pct). Either value is None when the relevant slice
        has zero total volume or is absent.
    """
    if _empty(df) or "total_rev" not in df.columns or "total_vol" not in df.columns:
        return None, None

    def _wavg(rows: pd.DataFrame) -> float | None:
        total_vol = rows["total_vol"].sum()
        if total_vol == 0:
            return None
        return float(rows["total_rev"].sum() / total_vol * 100)

    avg_pct = _wavg(df)
    max_date = df["date"].max()
    l30 = df[df["date"] >= max_date - pd.Timedelta(days=30)]
    l30_pct = _wavg(l30) if not l30.empty else None
    return avg_pct, l30_pct
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_take_rate_kpis_avg \
       tests/reporting/test_overview_take_rate.py::test_take_rate_kpis_l30 \
       tests/reporting/test_overview_take_rate.py::test_take_rate_kpis_empty -v
```

Expected: all `PASSED`

- [ ] **Step 5: Write the failing tests for `_fig_take_rate` annotation**

Add to `tests/reporting/test_overview_take_rate.py`:

```python
def test_fig_take_rate_annotation_present():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        "take_rate_pct": [5.0, 6.0],
    })
    fig = _fig_take_rate(df, avg_pct=5.5, l30_pct=6.0)
    assert fig is not None
    assert len(fig.layout.annotations) == 1
    assert "5.50%" in fig.layout.annotations[0].text
    assert "6.00%" in fig.layout.annotations[0].text


def test_fig_take_rate_no_annotation_when_none():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"]),
        "take_rate_pct": [5.0],
    })
    fig = _fig_take_rate(df, avg_pct=None, l30_pct=None)
    assert fig is not None
    assert len(fig.layout.annotations) == 0
```

- [ ] **Step 6: Run the tests to confirm they fail**

```bash
pytest tests/reporting/test_overview_take_rate.py::test_fig_take_rate_annotation_present \
       tests/reporting/test_overview_take_rate.py::test_fig_take_rate_no_annotation_when_none -v
```

Expected: `FAILED` — `_fig_take_rate() got an unexpected keyword argument 'avg_pct'`

- [ ] **Step 7: Update `_fig_take_rate` to accept KPI params and render annotation**

Replace the entire `_fig_take_rate` function:

```python
def _fig_take_rate(
    df: pd.DataFrame,
    avg_pct: float | None = None,
    l30_pct: float | None = None,
) -> go.Figure | None:
    """Line chart showing take rate (%) over time.

    Args:
        df: DataFrame with columns date (datetime) and take_rate_pct (float).
        avg_pct: Overall volume-weighted avg take rate for the annotation box.
        l30_pct: Last-30-day volume-weighted avg take rate for the annotation box.

    Returns:
        Plotly Figure or None if df is empty or has no non-null values.
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
    lines = []
    if avg_pct is not None:
        lines.append(f"Avg  {avg_pct:.2f}%")
    if l30_pct is not None:
        lines.append(f"L30  {l30_pct:.2f}%")
    if lines:
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
    layout = panel("Take Rate (%)")
    layout["yaxis"]["title"] = "%"
    layout["yaxis"]["ticksuffix"] = "%"
    fig.update_layout(**layout)
    return fig
```

- [ ] **Step 8: Run all take rate tests**

```bash
pytest tests/reporting/test_overview_take_rate.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 9: Commit**

```bash
git add nbs_bi/reporting/overview.py tests/reporting/test_overview_take_rate.py
git commit -m "feat(overview): add _take_rate_kpis and KPI annotation to take rate chart"
```

---

### Task 3: `_render_take_rate` — checkbox + KPI wiring

**Files:**
- Modify: `nbs_bi/reporting/overview.py` — `_render_take_rate` (lines ~937–974)
- Modify: `CHANGELOG.md`

---

- [ ] **Step 1: Update `_render_take_rate`**

Replace the entire `_render_take_rate` method with:

```python
def _render_take_rate(self, granularity: str) -> None:
    """Render take rate % line chart with Daily/Weekly/Monthly/Yearly toggle."""
    include_card_fees = st.checkbox(
        "Include card annual fees",
        value=True,
        key="tr_incl_card_fees",
    )
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
        include_card_fees=include_card_fees,
    )
    avg_pct, l30_pct = _take_rate_kpis(df) if not _empty(df) else (None, None)
    fig = _fig_take_rate(df, avg_pct=avg_pct, l30_pct=l30_pct)
    if fig is None:
        st.info("No data to compute take rate for this period.")
        return
    st.plotly_chart(fig, use_container_width=True)
```

- [ ] **Step 2: Run the full reporting test suite**

```bash
pytest tests/reporting/ -v
```

Expected: all tests `PASSED` (71 + 9 new = 80 total)

- [ ] **Step 3: Update `CHANGELOG.md`**

Prepend to the `## [Unreleased]` section:

```markdown
- Overview take rate chart: checkbox to include/exclude card annual fees from the revenue numerator (default: included)
- Overview take rate chart: volume-weighted Avg and L30 KPI annotation in top-right corner of the chart
```

- [ ] **Step 4: Commit**

```bash
git add nbs_bi/reporting/overview.py CHANGELOG.md
git commit -m "feat(overview): take rate card fees toggle and avg/L30 KPI annotation"
```
