---
name: Cumulative profit feature — Marketing tab
description: Cumulative profit tracking added to Meta Ads dashboard tab; where it lives and how it works
type: project
---

Cumulative profit tracking was added to the Marketing - Ads tab (Tab 5) in the Streamlit dashboard.

**Formula:** `cum_profit = cumsum(daily_rev − daily_cogs − daily_ad_spend)`

**Implementation:**

`nbs_bi/clients/campaigns.py` — two new additions:
- `_daily_cogs_from_invoices(dates, invoice_history)` — module-level helper; maps each date to a daily Rain invoice rate (invoice_total_usd ÷ days_in_month). Falls back to most recent available period if date has no matching invoice.
- `CampaignAnalyzer.cumulative_profit(campaign_id, invoice_history)` — calls `cumulative_revenue()`, merges with COGS series and ad spend, returns DataFrame with `date`, `daily_rev_usd`, `daily_cogs_usd`, `daily_ad_spend_usd`, `daily_profit_usd`, `cum_profit_usd`.

`nbs_bi/reporting/marketing.py` — updated:
- `_fig_cumulative_spend()` now accepts `cum_profit_df`; adds a VIOLET dotted line for cumulative profit and a y=0 breakeven reference line.
- `_fig_campaign_roi()` now accepts `cum_profit_df`; adds a diamond marker at net profit for the latest campaign.
- `_render_kpis()` now accepts `cum_profit_df`; shows a 6th "Net Profit" KPI tile when profit data is available.
- `_render_spend_charts()` passes `cum_profit_df` through to both figure builders.
- `_try_upload()` loads invoice history via `_load_all_invoice_models()` and calls `analyzer.cumulative_profit()`, storing the result as `"cum_profit_df"` in the campaign_data dict.
- `render()` extracts `cum_profit_df` from `campaign_data` and passes it to `_render_kpis()` and `_render_spend_charts()`.

**Why:** COGS comes from Rain invoice JSON files (data/invoices/). Profit is at whole-cohort level (not per-user) to match the campaign-level chart context. Invoice data is read from disk — no new DB queries.

**How to apply:** When asked about profitability analysis in the marketing tab, the data is already there. To add per-user profit, the pro-rata logic in `ClientModel._compute_card_cost()` is the right hook.
