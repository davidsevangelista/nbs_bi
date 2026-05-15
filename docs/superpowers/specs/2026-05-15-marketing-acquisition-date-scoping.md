# Marketing Ads — Acquisition Date Scoping & Default Start Date

**Date:** 2026-05-15
**Status:** Approved for implementation

---

## Problem

The "Acquisition by Channel" section in the Marketing Ads tab uses a frozen
`acquisition` DataFrame loaded by `_tab_marketing` with the **global dashboard
date pickers**. The ads-specific date pickers inside `MetaAdsSection` have no
effect on it, so the channel comparison always reflects company-wide history
rather than the selected analysis window.

Additionally, the ads analysis start date defaults to the earliest date in the
spend CSV, rather than the campaign start date of interest (2026-04-14).

---

## Goals

1. Acquisition by channel reflects the ads analysis date range chosen inside
   the marketing tab.
2. Default ads analysis start date is 2026-04-14 (clamped to available data).
3. Error message shown when the DB is unavailable instead of silent fallback.

---

## Data Flow — Before

```
_tab_marketing(global_start, global_end)
  → _load_client_report(global_start, global_end)   # @st.cache_data
  → MetaAdsSection(acquisition=frozen_df).render()
      → ads date pickers resolve (ads_start, ads_end)
      → _render_channel(acquisition=frozen_df)       # stale dates
```

## Data Flow — After

```
_tab_marketing(global_start, global_end)
  → MetaAdsSection(acquisition=None, invoice_total=invoice_total).render()
      → ads date pickers resolve (ads_start, ads_end)
      → _render_channel(start=ads_start, end=ads_end)
          → _load_acquisition_for_dates(ads_start, ads_end, db_url, invoice_total)
              # @st.cache_data ttl=3600
              → ClientReport(...).build()["acquisition"]
```

`_tab_marketing` no longer calls `_load_client_report`. The `acquisition=`
constructor param is kept for callers that supply pre-loaded data (e.g. tests
or non-dashboard usage with no DB URL).

---

## Component Changes

### `nbs_bi/reporting/marketing.py`

**New cached loader (module level):**
```python
@st.cache_data(ttl=3600, show_spinner="Loading acquisition data…")
def _load_acquisition_for_dates(
    start_date: str,
    end_date: str,
    db_url: str,
    invoice_total: float,
) -> pd.DataFrame:
    from nbs_bi.clients.report import ClientReport
    report = ClientReport(start_date, end_date, invoice_total, db_url).build()
    return report.get("acquisition", pd.DataFrame())
```

**`MetaAdsSection.__init__`:** add `invoice_total: float = 0.0`, stored as
`self._invoice_total`.

**`MetaAdsSection.render()`:** after date pickers resolve, pass `start_date`
and `end_date` as arguments to `_render_channel`.

**`_render_channel(self, summary, cum_profit_df, start_date, end_date)`:**
- If `self._analytics_db_url` is set: call `_load_acquisition_for_dates` with
  ads dates. Wrap in try/except — on failure show `st.error(...)`.
- If `self._analytics_db_url` is None: fall back to `self._acquisition`
  (pre-loaded DataFrame from constructor).

### `nbs_bi/reporting/dashboard.py`

**`_tab_marketing`:** remove the `_load_client_report` call and the
`acquisition=client_report.get("acquisition")` argument. Add
`invoice_total=invoice_total` to the `MetaAdsSection(...)` call.

### Default start date

Add module constant to `marketing.py`:
```python
import datetime as _dt
_DEFAULT_ADS_START = _dt.date(2026, 4, 14)
```

Change in `render()`:
```python
# before
_default_start = min_date

# after
_default_start = max(min_date, _DEFAULT_ADS_START)
```

---

## Error Handling

| Condition | Behaviour |
|---|---|
| `analytics_db_url` is None | Fall back to `self._acquisition` (may be None → existing placeholder info message) |
| DB query raises exception | `st.error("Failed to load acquisition data: {exc}")` — channel section aborts |
| DB returns empty DataFrame | Existing placeholder info message unchanged |

---

## Caching

`_load_acquisition_for_dates` is `@st.cache_data(ttl=3600)`. Cache key is
`(start_date, end_date, db_url, invoice_total)` — same as `_load_client_report`
in dashboard.py. Switching date pickers invalidates the cache and triggers a
fresh `ClientReport` load; navigating back to the same dates reuses the cached
result.

---

## Testing

- Update existing tests that call `_render_channel` with the new signature
  `(summary, cum_profit_df, start_date, end_date)`.
- Add unit test for `_load_acquisition_for_dates` using a mock `ClientReport`.
- Existing `MetaAdsSection` instantiation tests updated to optionally pass
  `invoice_total`.

---

## Out of Scope

- `profit_by_source_daily` (the cumulative channel chart) is not re-scoped in
  this change — it already extends to today via the sentinel-row fix.
- No changes to `campaigns.py`, `clients/`, or any other module.
