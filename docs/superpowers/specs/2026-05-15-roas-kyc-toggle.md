# ROAS KYC Cost Toggle — Design Spec

**Date:** 2026-05-15
**Status:** Approved for implementation

---

## Problem

All ROAS displays in the Marketing Ads tab use `total_revenue / total_ad_spend` as the formula,
excluding the one-time KYC verification cost ($2.07 per user). This understates the true cost
of acquiring each customer. The analyst needs to switch between the two definitions to reason
about both the pure ad-channel efficiency (no KYC) and the true all-in acquisition cost (with KYC).

---

## Goal

Add a checkbox **"Include KYC cost in ROAS"** (unchecked by default) that, when checked, adjusts
every ROAS figure in the tab:

| Display | Adjusted denominator |
|---|---|
| KPI tile "Overall ROAS" | `total_spend + kyc_done × $2.07` |
| Campaign summary table `roas` column | `total_spend_usd + cohort_users × $2.07` |
| Channel comparison table `roas` column | same per-campaign adjustment |

KYC cost constant: `_KYC_COST_USD = 2.07` (already defined in `nbs_bi/clients/models.py`).

---

## Why Two Different KYC Count Sources?

- **KPI tile**: uses the already-queried `kyc_done` value (DB-verified users with `kyc_level >= 1`
  for the latest campaign) — most accurate.
- **Campaign summary & channel comparison**: `roi_summary()` does not return a per-campaign KYC
  count, so `cohort_users` (signup count) is used as the proxy. This is a slight overcount since
  not every signup completes KYC, but it is the conservative upper-bound and requires no extra
  DB query.

---

## Component Changes

### `nbs_bi/reporting/marketing.py`

**New pure function (module level):**

```python
def _apply_kyc_roas_adjustment(summary: pd.DataFrame) -> pd.DataFrame:
    """Return summary with roas recomputed to include KYC costs in the denominator.

    Uses cohort_users as the KYC count proxy (conservative upper-bound).

    Args:
        summary: Output of CampaignAnalyzer.roi_summary().

    Returns:
        Copy of summary with roas column adjusted.
    """
    from nbs_bi.clients.models import _KYC_COST_USD
    df = summary.copy()
    adj_spend = df["total_spend_usd"] + df["cohort_users"] * _KYC_COST_USD
    df["roas"] = (df["total_revenue_usd"] / adj_spend.replace(0, np.nan)).round(4)
    return df
```

**`render()` change:**

After the platform/date selectors (before `analyzer = CampaignAnalyzer(...)`), add:

```python
include_kyc_in_roas = st.checkbox(
    "Include KYC cost in ROAS",
    value=False,
    key="roas_include_kyc",
    help="Adds $2.07/user KYC verification cost to the ad spend denominator.",
)
```

After `summary` is computed (post `analyzer.roi_summary()`), apply:

```python
if include_kyc_in_roas:
    summary = _apply_kyc_roas_adjustment(summary)
```

**`_render_kpis()` change:**

Add `include_kyc_in_roas: bool = False` parameter. When `True`:

```python
roas_denom = total_spend + (kyc_done * _KYC_COST_USD if include_kyc_in_roas else 0.0)
overall_roas = total_rev / roas_denom if roas_denom > 0 else 0.0
```

Update the call site in `render()` to pass `include_kyc_in_roas=include_kyc_in_roas`.

### No changes to `campaigns.py`, `dashboard.py`, or any other module.

---

## Data Flow

```
render()
  → include_kyc_in_roas = st.checkbox(...)
  → summary = analyzer.roi_summary()         # raw, KYC-free roas
  → if include_kyc_in_roas:
        summary = _apply_kyc_roas_adjustment(summary)   # rewrites roas column
  → _render_kpis(summary, ..., include_kyc_in_roas=include_kyc_in_roas)
      # uses kyc_done (DB count) for the KPI tile
  → _render_channel(summary, ...)             # summary["roas"] already adjusted
  → _render_summary_table(summary)            # summary["roas"] already adjusted
```

---

## Default Behaviour

Checkbox is **unchecked** by default. All ROAS values are identical to the current output.
Checking the box adjusts every ROAS figure simultaneously.

---

## Testing

- Unit test for `_apply_kyc_roas_adjustment`: verify adjusted roas = `rev / (spend + users × 2.07)`.
- Unit test: when `include_kyc_in_roas=False`, `_apply_kyc_roas_adjustment` is not called and roas is unchanged.
- Unit test for `_render_kpis` KPI computation: with and without the flag, verify the correct denominator is used.

---

## Out of Scope

- No changes to the `_fig_campaign_roi` bar chart (shows spend vs revenue bars, not a ROAS ratio).
- No changes to `campaigns.py::roi_summary()` signature.
- No per-campaign DB KYC count query.
