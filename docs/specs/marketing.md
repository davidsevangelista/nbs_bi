# Spec: nbs_bi.reporting.marketing — Meta Ads ROI Tab

> Status: Draft — 2026-04-21
> Author: prd-architect agent

---

## 1. Purpose

A dedicated 6th dashboard tab that answers one strategic question: **what are the results
of investing in Meta Ads campaigns?** It renders spend history and ROI metrics drawn
exclusively from the Rain corp card CSV (FACEBK charges), and places Meta Ads performance
in context by comparing it directly against the organic, referral, and founder-invite
acquisition channels already computed by `nbs_bi.clients`.

The tab is the canonical place for the CEO to decide whether to increase, hold, or cut
Meta Ads budget.

---

## 2. Scope

**In scope**
- A new Streamlit rendering module: `nbs_bi/reporting/marketing.py`
- A `MetaAdsSection` class that wraps `CampaignAnalyzer` and `ClientReport` public APIs
- 5-KPI strip: total spend, cohort revenue, overall ROAS, best campaign ROAS, full-cohort CAC
- Cumulative spend chart (Rain CSV, daily granularity)
- Per-campaign spend vs revenue grouped bar chart (reuses `_fig_campaign_roi` logic, new
  implementation in `marketing.py`)
- Channel comparison table and chart: Meta Ads cohort metrics vs organic / referral /
  founder_invite from `acquisition_summary()`
- Daily signups vs spend dual-axis chart (reuses `daily_context()` output)
- Campaign summary table with ROAS, CAC full, CAC incremental, conversion rate
- Wiring `MetaAdsSection` into `dashboard.py` as Tab 6

**Out of scope**
- Any data from `data/company_expenses/monthly_pnl.xlsx` — P&L is not used
- Backfill of pre-Feb 2026 spend from any source
- UTM-based attribution (not in DB)
- Budget planning or forecasting
- Changes to `nbs_bi/clients/campaigns.py` or any other existing module
- Export to PDF/Excel

**Included cleanup (same implementation)**
- Remove the "Campaign ROI" sub-tab from `nbs_bi/reporting/clients.py` (`ClientSection`)
  and the corresponding `_render_campaigns()` method. The 6th tab in `ClientSection`
  becomes "Product Adoption" (previously tab 5), collapsing the sub-tab list from 6 to 5.
  No data or logic is lost — `CampaignAnalyzer` is retained and called from `marketing.py`.

---

## 3. Data Sources

| Source | Key Columns | Notes |
|---|---|---|
| Rain corp card CSV | `date`, `amount`, `merchantName`, `currency` | Filtered to `merchantName.startswith("FACEBK")`. All rows are USD. Loaded via `load_ad_spend()` — already implemented. |
| `CampaignAnalyzer.roi_summary()` | `campaign_id`, `start`, `end`, `total_spend_usd`, `cohort_users`, `transacting_users`, `transacting_rate`, `total_revenue_usd`, `roas`, `cac_full`, `cac_incremental` | Public API — no internal imports from `clients/campaigns.py` beyond this method. |
| `CampaignAnalyzer.daily_context()` | `date`, `new_signups`, `daily_spend_usd`, `is_campaign`, `campaign_id` | Used for daily signups vs spend chart. |
| `ClientReport.build()["acquisition"]` | `acquisition_source`, `n_users`, `avg_net_revenue_usd`, `total_net_revenue_usd`, `conversion_rate` | Already computed in `nbs_bi.clients`. Pulled from the report dict passed into `MetaAdsSection`. No DB query issued from `marketing.py`. |

No direct DB queries are issued from `marketing.py`. All data arrives through the public
APIs of `CampaignAnalyzer` and `ClientReport`.

---

## 4. Functional Requirements

### 4.1 CSV Loading and Campaign Analysis

- Input: path to Rain CSV (passed at construction time or via Streamlit file uploader
  if not pre-loaded)
- Logic: call `load_ad_spend(csv_path)` → pass result to `CampaignAnalyzer(spend_df,
  db_url=db_url)` → call `roi_summary()` and `daily_context()`
- Output: `summary` DataFrame and `daily` DataFrame stored on the section instance

If `csv_path` is None and no pre-loaded data is available, render a `st.file_uploader`
and return early (same pattern as current `_render_campaigns()`).

### 4.2 KPI Strip

Five metrics rendered as `st.metric` cards in one row:

| Metric | Formula | Currency |
|---|---|---|
| Total Meta Spend | `summary["total_spend_usd"].sum()` | USD |
| Cohort Revenue | `summary["total_revenue_usd"].sum()` | USD |
| Overall ROAS | cohort_revenue / total_spend | dimensionless (×) |
| Best Campaign ROAS | `summary["roas"].max()` | dimensionless (×) |
| Full-Cohort CAC | `summary["total_spend_usd"].sum() / summary["cohort_users"].sum()` | USD |

ROAS delta label: "above break-even" (green) if ≥ 1.0, "below break-even" (red) if < 1.0.

### 4.3 Cumulative Spend Chart

- Input: `load_ad_spend()` output (daily granularity)
- Logic: compute `cumulative_spend_usd` as `daily_spend_usd.cumsum()` over sorted dates
- Output: line chart — x: date, y: cumulative USD spend, with vertical reference lines at
  each campaign start date (annotated with `campaign_id`)
- Decision supported: "How much have we spent in total, and when did each campaign begin?"

### 4.4 Per-Campaign Spend vs Revenue Chart

Grouped bar: one group per `campaign_id`, bars for `total_spend_usd` (red) and
`total_revenue_usd` (green). Values labeled outside bars in `$X,XXX` format.

### 4.5 Daily Signups vs Ad Spend Chart

Dual-axis chart from `daily_context()`: signups as stacked bars (campaign vs organic),
spend as dotted line on right axis. Identical logic to the existing `_fig_campaign_daily`
in `clients.py` — new implementation in `marketing.py` (no cross-import of figure
builders).

### 4.6 Channel Comparison

Merge Meta Ads aggregate metrics with `acquisition_summary()` rows to produce a single
comparison DataFrame.

**Meta Ads row construction:**
```
acquisition_source  = "meta_ads"
n_users             = summary["cohort_users"].sum()
avg_net_revenue_usd = summary["total_revenue_usd"].sum() / n_users  (0 if n_users == 0)
total_net_revenue_usd = summary["total_revenue_usd"].sum()
conversion_rate     = summary["transacting_users"].sum() / n_users  (0 if n_users == 0)
spend_usd           = summary["total_spend_usd"].sum()              (NaN for non-Meta rows)
roas                = total_revenue / spend_usd                     (NaN for non-Meta rows)
```

The `spend_usd` and `roas` columns are Meta Ads-only; all other channels will have
`NaN` for those columns (they have no attributed spend). The table must make this
caveat visible — annotate non-Meta rows as "no spend data" in those columns.

**Output chart:** horizontal grouped bar — x: `avg_net_revenue_usd`, y: channel name,
color-coded by source. Meta Ads bar labeled with ROAS annotation.

**Output table:** `st.dataframe` showing all channels side-by-side.

### 4.7 Campaign Summary Table

`st.dataframe` of `roi_summary()` output with formatted columns:
- monetary columns → `$X,XXX.XX` (NaN → "n/a")
- `roas` → `X.XX×`
- `transacting_rate` → `XX.X%`
- `incremental_users_est` → integer

---

## 5. Output Specification

### MetaAdsSection.render() — Streamlit components emitted

```
[KPI strip — 5 st.metric cards]
[st.divider]
[Cumulative spend chart — line, Plotly]
[st.divider]
[Per-campaign spend vs revenue — grouped bar, Plotly]
[Daily signups vs spend — dual-axis, Plotly]
[st.divider]
[Channel comparison — grouped bar + st.dataframe]
[st.divider]
[Campaign summary table — st.dataframe]
```

### Channel comparison DataFrame schema

| Column | Type | Notes |
|---|---|---|
| `acquisition_source` | str | "meta_ads", "organic", "referral", "founder_invite", "unknown" |
| `n_users` | int | cohort users (Meta) or all-time users (others) |
| `avg_net_revenue_usd` | float64 | |
| `total_net_revenue_usd` | float64 | |
| `conversion_rate` | float64 | fraction 0–1 |
| `spend_usd` | float64 | Meta Ads only; NaN for others |
| `roas` | float64 | Meta Ads only; NaN for others |

### Cumulative spend DataFrame schema

| Column | Type | Notes |
|---|---|---|
| `date` | date | |
| `daily_spend_usd` | float64 | |
| `cumulative_spend_usd` | float64 | running sum |
| `is_campaign_start` | bool | True on first day of each campaign window |
| `campaign_id` | str | populated on campaign start days; empty string elsewhere |

---

## 6. Non-Functional Requirements

- **Performance:** No DB queries are issued from `marketing.py`. All data is passed in
  at construction time. Render time is bounded by figure construction only — must be < 1s.
- **Caching:** `CampaignAnalyzer` already caches DB results as Parquet via `_run()`.
  `marketing.py` adds no additional caching layer.
- **PII:** No user IDs appear in any output from this module. Campaign IDs (`campaign_1`,
  `campaign_2`, …) are the only identifiers. The channel comparison table uses source
  names only — no per-user data.
- **Currency:** all monetary values are `float64`. Currency is explicit in all variable
  names (`_usd` suffix). No BRL values are introduced in this module.
- **Monetary precision:** `float64` throughout. No `float32`.

---

## 7. Module Structure

```
nbs_bi/reporting/
    marketing.py        — MetaAdsSection: all figure builders + render(); no sub-files needed
```

`marketing.py` is a single file. All figure-building functions are private (`_fig_*`),
following the same pattern as `clients.py` and `cards.py`. No functions exceed 50 lines.

```
tests/reporting/
    test_marketing.py   — unit tests for figure builders and channel comparison logic
```

---

## 8. Public API

```python
class MetaAdsSection:
    """Renders the Meta Ads ROI tab (Tab 6) of the NBS BI dashboard.

    Args:
        campaign_data: Dict with keys ``"summary"`` (DataFrame from
            ``CampaignAnalyzer.roi_summary()``) and ``"daily"`` (DataFrame
            from ``CampaignAnalyzer.daily_context()``).  If None, a CSV
            file uploader is rendered and the section returns early.
        acquisition: DataFrame from ``ClientReport.build()["acquisition"]``.
            Used for the channel comparison view.  If None or empty, the
            channel comparison section is skipped.
        db_url: SQLAlchemy DB URL forwarded to ``CampaignAnalyzer`` when
            a CSV is uploaded at runtime.
    """

    def __init__(
        self,
        campaign_data: dict | None,
        acquisition: pd.DataFrame | None,
        db_url: str | None = None,
    ) -> None: ...

    def render(self) -> None:
        """Render all Meta Ads tab components into the active Streamlit context."""
        ...
```

```python
def _build_channel_comparison(
    summary: pd.DataFrame,
    acquisition: pd.DataFrame,
) -> pd.DataFrame:
    """Merge Meta Ads campaign metrics with acquisition_summary rows.

    Args:
        summary: Output of ``CampaignAnalyzer.roi_summary()``.
        acquisition: Output of ``ClientReport.build()["acquisition"]``.

    Returns:
        DataFrame with schema defined in Section 5 (channel comparison).
        Meta Ads row is prepended; source channels follow in original order.
    """
    ...
```

```python
def _build_cumulative_spend(
    spend_df: pd.DataFrame,
    campaigns: list[dict],
) -> pd.DataFrame:
    """Add cumulative spend and campaign-start flags to a daily spend DataFrame.

    Args:
        spend_df: Output of ``load_ad_spend()`` —
            columns ``date``, ``daily_spend_usd``.
        campaigns: List of campaign dicts from ``CampaignAnalyzer.campaigns``
            — each has ``campaign_id``, ``start``, ``end``.

    Returns:
        DataFrame with columns ``date``, ``daily_spend_usd``,
        ``cumulative_spend_usd``, ``is_campaign_start``, ``campaign_id``.
    """
    ...
```

**dashboard.py change** — one additional tab wired (label "Marketing - Ads"):

```python
# In dashboard.py render loop — add Tab 6
from nbs_bi.reporting.marketing import MetaAdsSection

MetaAdsSection(
    campaign_data=report.get("campaign_roi"),
    acquisition=report.get("acquisition"),
    db_url=db_url,
).render()
```

**clients.py change** — remove Campaign ROI sub-tab:

- Delete `_render_campaigns()` method from `ClientSection`
- Remove `"Campaign ROI"` from the `st.tabs([...])` call in `render()`
- Remove the `with tabs[5]: self._render_campaigns()` block
- `ClientSection.render()` sub-tab list becomes 5 items:
  `["LTV & Cohorts", "Acquisition", "Segments", "Founders Club", "Product Adoption"]`
- The `_fig_campaign_roi`, `_fig_campaign_cac`, `_fig_campaign_daily` figure-builder
  functions in `clients.py` are also removed (their equivalents will live in `marketing.py`)

---

## 9. Test Plan

### Fixtures

- `tests/reporting/fixtures/campaign_summary.parquet` — 3-row DataFrame matching
  `roi_summary()` schema (use values from live DB: campaign_1, campaign_2, campaign_3)
- `tests/reporting/fixtures/daily_spend.parquet` — 47-row DataFrame matching
  `load_ad_spend()` output
- `tests/reporting/fixtures/acquisition_summary.parquet` — 4-row DataFrame matching
  `acquisition_summary()` schema (organic, referral, founder_invite, unknown)

All fixtures are static — no DB, no random values.

### Happy Path

| Test | Assertion |
|---|---|
| `test_build_cumulative_spend_monotonic` | `cumulative_spend_usd` is non-decreasing |
| `test_build_cumulative_spend_campaign_flags` | `is_campaign_start` is True exactly on campaign start dates; False elsewhere |
| `test_build_channel_comparison_row_count` | output has `len(acquisition) + 1` rows |
| `test_build_channel_comparison_meta_row` | meta_ads row `spend_usd` equals `summary["total_spend_usd"].sum()` |
| `test_build_channel_comparison_non_meta_spend_nan` | all non-meta rows have `spend_usd` as NaN |
| `test_kpi_roas_above_breakeven` | overall ROAS ≥ 1 → delta_color logic returns "normal" |
| `test_kpi_cac_formula` | full-cohort CAC = total_spend / total_cohort_users |

### Edge Cases

| Test | Scenario |
|---|---|
| `test_empty_summary` | `campaign_data=None` → `render()` shows file uploader, no exception |
| `test_empty_acquisition` | `acquisition=None` → channel comparison section skipped gracefully |
| `test_single_campaign` | 1 campaign row → cumulative chart and comparison table still render |
| `test_zero_cohort_users` | `cohort_users = 0` for a campaign → CAC returns NaN, no ZeroDivisionError |
| `test_zero_spend` | `total_spend_usd = 0` for a campaign → ROAS returns NaN, no ZeroDivisionError |

### Coverage target

≥ 80% for `marketing.py`. Figure-builder functions that call Streamlit are excluded from
coverage measurement via `# pragma: no cover` on the `render()` method body
(same convention as `clients.py`).

---

## 10. Open Questions

| # | Question | Proposed Default | Status |
|---|---|---|---|
| 1 | Should the CSV file uploader in Tab 6 be separate from the one already present in the Clients → Campaign ROI sub-tab, or should both read from the same session-state key? | Separate uploader with a distinct `key="meta_ads_csv"`. Avoids coupling two tabs to the same widget state. | Open |
| 2 | Should the existing Campaign ROI sub-tab inside Clients (Tab 5) be removed now that a dedicated tab exists, or kept for redundancy? | **Resolved 2026-04-21:** Remove it. `_render_campaigns()` and the "Campaign ROI" sub-tab are deleted from `clients.py` as part of this implementation. | Resolved |
| 3 | What label should Tab 6 carry in the dashboard tab bar? | **Resolved 2026-04-21:** "Marketing - Ads". | Resolved |

---

## 11. Dependencies and Phase

- **Phase:** 6 (Reporting) — incremental addition to existing dashboard
- **Depends on:**
  - `nbs_bi.clients.campaigns.CampaignAnalyzer` (public API only — no changes required)
  - `nbs_bi.clients.report.ClientReport` (public API only — `build()["acquisition"]` key)
  - `nbs_bi.reporting.dashboard` — Tab 6 wiring (one import + one `with tabs[5]:` block)
- **Modifies:**
  - `nbs_bi.reporting.clients` — remove `_render_campaigns()`, three `_fig_campaign_*`
    functions, and the "Campaign ROI" sub-tab entry (resolved open question 2)
  - `nbs_bi.reporting.dashboard` — add "Marketing - Ads" as Tab 6 (resolved open question 3)
- **Blocks:** nothing — this is a leaf feature with no downstream dependents
- **Does not require:** any schema migration, new DB queries, or changes to `clients/campaigns.py`
