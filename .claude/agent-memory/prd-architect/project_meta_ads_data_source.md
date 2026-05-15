---
name: Meta Ads tab data source decision
description: Resolved data source hierarchy for the Meta Ads ROI tab — Rain CSV only, P&L dropped
type: project
---

Rain corp card CSV (`data/nbs_corp_card/`) is the sole data source for the Meta Ads ROI tab (Tab 6). The monthly P&L Excel (`data/company_expenses/monthly_pnl.xlsx`) was considered for pre-Feb 2026 backfill but explicitly dropped from scope.

**Why:** Rain CSV provides daily granularity and direct charge-level data, which enables precise campaign window detection and signup-impact mapping. The P&L has monthly granularity only and was simply not updated for recent months — it was never a reconciliation source, just stale. The user decided cleaner data beats broader coverage.

**How to apply:** Any future feature touching Meta Ads spend in nbs_bi should default to Rain CSV as the source. Do not reintroduce P&L as a backfill source without an explicit decision to do so. If pre-Feb 2026 history is needed in the future, treat it as a separate spec item.
