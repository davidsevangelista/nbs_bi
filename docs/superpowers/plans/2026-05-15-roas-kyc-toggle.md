# ROAS KYC Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Include KYC cost in ROAS" checkbox to the Marketing Ads tab that adjusts all ROAS displays — the KPI tile, campaign summary table, and channel comparison table — to include a $2.07/user KYC cost in the denominator.

**Architecture:** One new pure function `_apply_kyc_roas_adjustment` rewrites the `roas` column in the `summary` DataFrame using `total_revenue / (total_spend + cohort_users × $2.07)`. It is applied once in `render()` after `summary` is filtered to the latest campaign. `_render_kpis` gets a separate `include_kyc_in_roas` flag because the KPI tile uses the more-accurate DB-queried `kyc_done` count rather than `cohort_users`. Everything else (summary table, channel comparison) flows from the already-adjusted `summary` DataFrame.

**Tech Stack:** Python 3.11, pandas, numpy, Streamlit `st.checkbox`, pytest.

---

## File Map

| File | Change |
|---|---|
| `nbs_bi/reporting/marketing.py` | Add `_apply_kyc_roas_adjustment`; update `_render_kpis` signature; wire checkbox + adjustment in `render()` |
| `tests/reporting/test_marketing.py` | Add import + three unit tests for `_apply_kyc_roas_adjustment` |

---

### Task 1: Add `_apply_kyc_roas_adjustment` pure function

**Files:**
- Modify: `nbs_bi/reporting/marketing.py` (after `_build_channel_comparison`, before `# Figure builders`)
- Test: `tests/reporting/test_marketing.py`

- [ ] **Step 1: Add `_apply_kyc_roas_adjustment` to the test imports and write three failing tests**

In `tests/reporting/test_marketing.py`, change the import block from:

```python
from nbs_bi.reporting.marketing import (
    MetaAdsSection,
    _build_channel_comparison,
    _build_cumulative_spend,
    _fig_cumulative_profit,
    _fig_cumulative_spend,
    _fig_daily_rev_all_vs_cohort,
    _load_acquisition_for_dates,
)
```

to:

```python
from nbs_bi.reporting.marketing import (
    MetaAdsSection,
    _apply_kyc_roas_adjustment,
    _build_channel_comparison,
    _build_cumulative_spend,
    _fig_cumulative_profit,
    _fig_cumulative_spend,
    _fig_daily_rev_all_vs_cohort,
    _load_acquisition_for_dates,
)
```

Then add these three tests at the end of the file:

```python
# ---------------------------------------------------------------------------
# _apply_kyc_roas_adjustment
# ---------------------------------------------------------------------------


def test_apply_kyc_roas_adjustment_recomputes_roas(campaign_summary):
    """roas = total_revenue / (total_spend + cohort_users * _KYC_COST_USD)."""
    from nbs_bi.clients.models import _KYC_COST_USD

    result = _apply_kyc_roas_adjustment(campaign_summary)

    for i, row in campaign_summary.iterrows():
        adj_denom = row["total_spend_usd"] + row["cohort_users"] * _KYC_COST_USD
        expected = round(row["total_revenue_usd"] / adj_denom, 4)
        assert result.loc[i, "roas"] == pytest.approx(expected, abs=1e-4)


def test_apply_kyc_roas_adjustment_does_not_mutate_input(campaign_summary):
    """Original summary DataFrame must not be modified in place."""
    original_roas = campaign_summary["roas"].copy()
    _apply_kyc_roas_adjustment(campaign_summary)
    pd.testing.assert_series_equal(campaign_summary["roas"], original_roas)


def test_apply_kyc_roas_adjustment_zero_denom_is_nan():
    """When total_spend and cohort_users are both 0, roas becomes NaN."""
    summary = pd.DataFrame([{
        "campaign_id": "c1",
        "total_spend_usd": 0.0,
        "cohort_users": 0,
        "total_revenue_usd": 100.0,
        "roas": float("nan"),
    }])
    result = _apply_kyc_roas_adjustment(summary)
    assert pd.isna(result["roas"].iloc[0])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/reporting/test_marketing.py::test_apply_kyc_roas_adjustment_recomputes_roas tests/reporting/test_marketing.py::test_apply_kyc_roas_adjustment_does_not_mutate_input tests/reporting/test_marketing.py::test_apply_kyc_roas_adjustment_zero_denom_is_nan -v
```

Expected: `ImportError` — `_apply_kyc_roas_adjustment` not yet defined.

- [ ] **Step 3: Add `_apply_kyc_roas_adjustment` to `marketing.py`**

In `nbs_bi/reporting/marketing.py`, find the line:

```python
# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------
```

Insert the following block **immediately before** that line:

```python
def _apply_kyc_roas_adjustment(summary: pd.DataFrame) -> pd.DataFrame:
    """Return summary with roas recomputed to include KYC costs in the denominator.

    Uses ``cohort_users`` as the KYC count proxy (conservative upper-bound — not
    every signup completes KYC, but no per-campaign KYC count is available in
    ``roi_summary()`` without an extra DB query).

    Args:
        summary: Output of ``CampaignAnalyzer.roi_summary()``.

    Returns:
        Copy of summary with ``roas`` column set to
        ``total_revenue_usd / (total_spend_usd + cohort_users × _KYC_COST_USD)``.
        Rows where the adjusted denominator is zero have ``roas = NaN``.
    """
    from nbs_bi.clients.models import _KYC_COST_USD

    df = summary.copy()
    adj_spend = df["total_spend_usd"] + df["cohort_users"] * _KYC_COST_USD
    df["roas"] = (df["total_revenue_usd"] / adj_spend.replace(0, np.nan)).round(4)
    return df

```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/reporting/test_marketing.py::test_apply_kyc_roas_adjustment_recomputes_roas tests/reporting/test_marketing.py::test_apply_kyc_roas_adjustment_does_not_mutate_input tests/reporting/test_marketing.py::test_apply_kyc_roas_adjustment_zero_denom_is_nan -v
```

Expected: 3 PASS.

- [ ] **Step 5: Run full marketing test suite**

```bash
pytest tests/reporting/test_marketing.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add nbs_bi/reporting/marketing.py tests/reporting/test_marketing.py
git commit -m "feat(marketing): add _apply_kyc_roas_adjustment pure function"
```

---

### Task 2: Update `_render_kpis` to accept `include_kyc_in_roas`

**Files:**
- Modify: `nbs_bi/reporting/marketing.py` (`_render_kpis` method, currently starting with `def _render_kpis(  # pragma: no cover`)

- [ ] **Step 1: Update `_render_kpis` signature and body**

Find this block in `_render_kpis`:

```python
    def _render_kpis(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        cum_profit_df: pd.DataFrame | None = None,
        kyc_done: int = 0,
        spend_breakdown: dict[str, float] | None = None,
    ) -> None:
        """Render KPI strip including net profit when profit data is available.

        Args:
            summary: Output of ``CampaignAnalyzer.roi_summary()``.
            cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()``
                — when provided, adds a Net Profit KPI tile.
            kyc_done: Count of cohort users who completed KYC (kyc_level >= 1),
                used to compute KYC cost component of CAC.
            spend_breakdown: Per-platform spend totals for the selected window,
                e.g. ``{"meta": 450.0, "google": 310.0}``.
        """
        from nbs_bi.clients.models import _KYC_COST_USD

        total_spend = float(summary["total_spend_usd"].sum())
        total_rev = float(summary["total_revenue_usd"].sum())
        transacting = int(summary["transacting_users"].sum())
        overall_roas = total_rev / total_spend if total_spend > 0 else 0.0

        kyc_cost = kyc_done * _KYC_COST_USD
        cac_active = (total_spend + kyc_cost) / transacting if transacting > 0 else float("nan")
```

Replace it with:

```python
    def _render_kpis(  # pragma: no cover
        self,
        summary: pd.DataFrame,
        cum_profit_df: pd.DataFrame | None = None,
        kyc_done: int = 0,
        spend_breakdown: dict[str, float] | None = None,
        include_kyc_in_roas: bool = False,
    ) -> None:
        """Render KPI strip including net profit when profit data is available.

        Args:
            summary: Output of ``CampaignAnalyzer.roi_summary()``.
            cum_profit_df: Output of ``CampaignAnalyzer.cumulative_profit()``
                — when provided, adds a Net Profit KPI tile.
            kyc_done: Count of cohort users who completed KYC (kyc_level >= 1),
                used to compute KYC cost component of CAC.
            spend_breakdown: Per-platform spend totals for the selected window,
                e.g. ``{"meta": 450.0, "google": 310.0}``.
            include_kyc_in_roas: When True, adds ``kyc_done × $2.07`` to the
                ROAS denominator. Uses the DB-queried ``kyc_done`` count for
                accuracy (vs ``cohort_users`` used in ``_apply_kyc_roas_adjustment``).
        """
        from nbs_bi.clients.models import _KYC_COST_USD

        total_spend = float(summary["total_spend_usd"].sum())
        total_rev = float(summary["total_revenue_usd"].sum())
        transacting = int(summary["transacting_users"].sum())
        kyc_cost = kyc_done * _KYC_COST_USD
        roas_denom = total_spend + (kyc_cost if include_kyc_in_roas else 0.0)
        overall_roas = total_rev / roas_denom if roas_denom > 0 else 0.0
        cac_active = (total_spend + kyc_cost) / transacting if transacting > 0 else float("nan")
```

- [ ] **Step 2: Run full marketing test suite**

```bash
pytest tests/reporting/test_marketing.py -q
```

Expected: all pass (no tests directly call `_render_kpis` — it is `# pragma: no cover`).

- [ ] **Step 3: Commit**

```bash
git add nbs_bi/reporting/marketing.py
git commit -m "feat(marketing): _render_kpis accepts include_kyc_in_roas flag"
```

---

### Task 3: Wire checkbox in `render()` and apply adjustment downstream

**Files:**
- Modify: `nbs_bi/reporting/marketing.py` (`render()` method)

- [ ] **Step 1: Add checkbox after platform/date controls**

In `render()`, find:

```python
        # Raw per-platform spend for chart overlays (date-filtered, platform-scoped).
        spend_df_for_charts = (
            spend_df
            if selected_platform is None
            else spend_df[spend_df["platform"] == selected_platform]
        ) if "platform" in spend_df.columns else spend_df

        # Rebuild analyzer scoped to the selected window/platform.
        analyzer = CampaignAnalyzer(spend_agg, db_url=self._analytics_db_url or self._db_url)
```

Replace with:

```python
        # Raw per-platform spend for chart overlays (date-filtered, platform-scoped).
        spend_df_for_charts = (
            spend_df
            if selected_platform is None
            else spend_df[spend_df["platform"] == selected_platform]
        ) if "platform" in spend_df.columns else spend_df

        include_kyc_in_roas = st.checkbox(
            "Include KYC cost in ROAS",
            value=False,
            key="roas_include_kyc",
            help="Adds $2.07/user KYC verification cost to the ad spend denominator.",
        )

        # Rebuild analyzer scoped to the selected window/platform.
        analyzer = CampaignAnalyzer(spend_agg, db_url=self._analytics_db_url or self._db_url)
```

- [ ] **Step 2: Apply `_apply_kyc_roas_adjustment` to `summary` after latest-campaign filter**

Find this block (immediately after the latest-campaign filter):

```python
        # Focus all campaign-level charts on the most recent campaign only.
        # Cumulative spend (spend_df + campaigns) keeps full history.
        latest_id = summary["campaign_id"].iloc[-1]
        summary = summary[summary["campaign_id"] == latest_id].reset_index(drop=True)
        if not daily.empty and "campaign_id" in daily.columns:
            _d = pd.to_datetime(daily["date"]).dt.date
            daily = daily[(_d >= start_date) & (_d <= end_date)].reset_index(drop=True)

        referral_code = ""
```

Replace with:

```python
        # Focus all campaign-level charts on the most recent campaign only.
        # Cumulative spend (spend_df + campaigns) keeps full history.
        latest_id = summary["campaign_id"].iloc[-1]
        summary = summary[summary["campaign_id"] == latest_id].reset_index(drop=True)
        if include_kyc_in_roas:
            summary = _apply_kyc_roas_adjustment(summary)
        if not daily.empty and "campaign_id" in daily.columns:
            _d = pd.to_datetime(daily["date"]).dt.date
            daily = daily[(_d >= start_date) & (_d <= end_date)].reset_index(drop=True)

        referral_code = ""
```

- [ ] **Step 3: Pass `include_kyc_in_roas` to `_render_kpis`**

Find:

```python
        self._render_kpis(
            summary, cum_profit_df, kyc_done=kyc_done, spend_breakdown=spend_breakdown
        )
```

Replace with:

```python
        self._render_kpis(
            summary,
            cum_profit_df,
            kyc_done=kyc_done,
            spend_breakdown=spend_breakdown,
            include_kyc_in_roas=include_kyc_in_roas,
        )
```

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/reporting/test_marketing.py -q
```

Expected: all pass.

- [ ] **Step 5: Run broader suite**

```bash
pytest tests/ -q --ignore=tests/onramp
```

Expected: same pre-existing failures only (cards simulator + test_referral_code_options_on_db_error), no new failures.

- [ ] **Step 6: Commit**

```bash
git add nbs_bi/reporting/marketing.py
git commit -m "feat(marketing): add KYC cost toggle for ROAS computation"
```

---

## Self-Review

**Spec coverage:**
- ✅ Checkbox "Include KYC cost in ROAS", unchecked by default → Task 3 Step 1
- ✅ KPI tile adjusted via `include_kyc_in_roas` flag using `kyc_done` count → Task 2 + Task 3 Step 3
- ✅ Campaign summary table `roas` adjusted via `_apply_kyc_roas_adjustment` on `summary` → Task 1 + Task 3 Step 2
- ✅ Channel comparison `roas` adjusted (flows from the same `summary` DataFrame passed to `_render_channel`) → Task 3 Step 2
- ✅ `_apply_kyc_roas_adjustment` pure function, tested in isolation → Task 1
- ✅ No changes to `campaigns.py`, `dashboard.py`, or any other module → confirmed

**Placeholder scan:** None found.

**Type consistency:** `include_kyc_in_roas: bool` used consistently across Task 2 and Task 3.
