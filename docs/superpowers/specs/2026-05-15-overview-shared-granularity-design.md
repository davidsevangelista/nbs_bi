# Overview Tab — Shared Granularity Selector & Revenue Bug Fix

## Goal

Fix the revenue (USD) chart showing zeros for all granularities except Monthly, then unify the granularity selector across the three bottom charts (Revenue, Volume, Take Rate) into a single radio above all three.

---

## Part 1 — Revenue Granularity Bug Fix

### Root cause

`_render_revenue_trend` uses two different data sources:
- **Monthly**: `revenue_monthly` (column: `month`, type: `datetime64[ns]` period-bucketed)
- **Daily / Weekly / Yearly**: `revenue_daily` (column: `date`, type: `datetime64[ns]` tz-naive normalized)

`card_revenue_daily` is built from a PostgreSQL `DATE(...)` column, which SQLAlchemy returns as Python `datetime.date` objects. After `pd.to_datetime()` in `card_revenue_daily()`, the `date` column becomes `datetime64[ns]`. However, `revenue_daily`'s `date` column comes from `.dt.tz_convert(None).dt.normalize()` — identical type but potentially different timezone treatment. If the outer merge in `_fig_monthly_revenue` fails to match rows (due to a microsecond or timezone difference), all `fee_usd`/`spread_usd` columns become 0.0 from `fillna(0.0)` on the merge.

A secondary candidate: if `_build_revenue_daily` is missing the `@staticmethod` decorator, `self._build_revenue_daily(conv_df)` silently fails and returns an empty DataFrame, which `_get` returns as empty — `_fig_monthly_revenue` receives a non-empty merged frame from an outer merge with `card_revenue_daily` but with all conversion revenue columns absent.

### Fix

1. **Diagnose**: Read dtypes of `revenue_daily["date"]` and `card_revenue_daily["date"]` to confirm which hypothesis is correct.
2. **Normalize dates** in `_fig_monthly_revenue`: before merging, normalize both `revenue["date"]` and `card_revenue["date"]` to `datetime64[ns]` via `pd.to_datetime(...).dt.normalize()`. This makes the merge date-type agnostic and timezone-safe.
3. **Verify `@staticmethod`** on `_build_revenue_daily` in `report.py`.
4. **Add a regression test** in `tests/reporting/test_overview_take_rate.py` asserting that Daily granularity produces non-zero `fee_usd` values when given non-zero input data.

---

## Part 2 — Shared Granularity Selector

### Architecture

The three render methods `_render_revenue_trend`, `_render_combined_volume`, `_render_take_rate` each currently own their own `st.radio`. The new design:

- `render()` creates a single radio **once** with `key="overview_charts_gran"`, `["Daily", "Weekly", "Monthly", "Yearly"]`, default index 2 (Monthly).
- Each of the three methods gains a `granularity: str` parameter and no longer creates a radio.
- `render()` passes `granularity` to all three: `self._render_revenue_trend(granularity)`, `self._render_combined_volume(granularity)`, `self._render_take_rate(granularity)`.
- The volume chart gains **Yearly** as a valid option (currently handles only Daily/Weekly/Monthly in `_resample_combined`). `_resample_combined` gets `"Yearly": "YS"` added to its frequency map.

### Resulting `render()` body

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

### Signature changes

| Method | Before | After |
|---|---|---|
| `_render_revenue_trend` | `(self) -> None` | `(self, granularity: str) -> None` |
| `_render_combined_volume` | `(self) -> None` | `(self, granularity: str) -> None` |
| `_render_take_rate` | `(self) -> None` | `(self, granularity: str) -> None` |

### `_resample_combined` change

Add `"Yearly": "YS"` to the frequency map so Yearly volume aggregation works.

---

## File Changes

| File | Change |
|---|---|
| `nbs_bi/reporting/overview.py` | Fix `_fig_monthly_revenue` date normalization; update `render()` with shared radio; refactor 3 render methods to accept `granularity` param; update `_resample_combined` with Yearly freq |
| `nbs_bi/onramp/report.py` | Verify/add `@staticmethod` on `_build_revenue_daily` |
| `tests/reporting/test_overview_take_rate.py` | Add regression test for Daily granularity non-zero revenue |

---

## Tests

1. **`test_fig_monthly_revenue_daily_granularity`** — pass `revenue_daily`-like DataFrame (datetime64 dates) and `card_revenue_daily`-like DataFrame (date object dates after `pd.to_datetime`) to `_fig_monthly_revenue`; assert figure is not None and has non-zero y-values.
2. **`test_resample_combined_yearly`** — pass a multi-month `conv_usd`/`card_usd` DataFrame; assert Yearly granularity collapses to 1 row.
3. Update existing tests that call `_render_revenue_trend`, `_render_combined_volume`, `_render_take_rate` with no arguments — add `granularity="Monthly"` argument (none of these methods are directly tested via Streamlit so this only affects unit tests that may call them directly).
