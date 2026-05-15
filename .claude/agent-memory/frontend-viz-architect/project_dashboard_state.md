---
name: Dashboard Current State (as of 2026-04-21)
description: Snapshot of the Streamlit dashboard architecture, tab completion, and visualization patterns in use
type: project
---

The NBS BI dashboard is a 4-tab Streamlit app at `nbs_bi/reporting/dashboard.py`. All 4 tabs are wired and rendering. The spec says "4 tabs" but PROGRESS.md and dashboard.py both reflect the same 4 tabs (Overview, On/Off Ramp, Cards, Clients). The CLAUDE.md and early progress notes refer to a 5-tab structure — this appears to be a discrepancy from earlier planning where Card Costs and Card Analytics were split; they have since been merged into one "Cards" tab with 3 subtabs.

**Tab status:**
- Tab 1 (Overview): Done — `overview.py` renders 6 KPIs + 4 Plotly charts (stacked area revenue, stacked bar volume, daily active users line, activation funnel horizontal bar)
- Tab 2 (On/Off Ramp): Done — `ramp.py` renders 4 subtabs, 8 Plotly charts (grouped bar volume, stacked bar revenue, stacked bar by direction, FX line with CI band, PIX line, new vs returning bar, spread histogram)
- Tab 3 (Cards): Done — `cards.py` renders 3 subtabs: Card Costs (horizontal bar breakdown + sensitivity), Usage Patterns (daily/weekly), Tier Pricing (editable data_editor + histogram + bar)
- Tab 4 (Clients): Done — `clients.py` renders 6 sub-tabs: LTV & Cohorts (heatmap + curves + CAC slider), Acquisition (bar + funnel), Segments (donut + tables), Founders Club (scatter + table), Product Adoption (funnel + bars + segment heatmap), Campaign ROI (upload-driven dual-axis + grouped bars)

**Charting library:** Plotly (go.Figure) throughout — consistent across all tabs. No Altair, no native Streamlit charts.

**Color palette:** Consistent across all files — BLUE #2563EB, EMERALD #059669, AMBER #D97706, ROSE #E11D48, TEAL #0D9488, VIOLET #7C3AED. Defined locally per file (minor duplication).

**PII masking pattern:** `_mask_user_id()` truncates UUID to first 8 chars + "…". Used in ramp.py and cards.py. `clients.py` masks in `_fig_adoption_heatmap()` inline. Founders scatter passes raw user_id in hover text — potential gap.

**Interactivity gaps observed:**
- No cross-tab filtering (sidebar date range applies to all tabs but no intra-tab drill-down)
- No click-to-drill on any chart
- No export-to-CSV/Excel on any table
- Cards tier pricing tab is the most interactive (st.data_editor + number_input)
- CAC slider in Clients/LTV is the only reactive KPI
- No chart-level date granularity controls (daily/weekly/monthly toggle)
- Spread histogram has no bin-count control

**Why:** Dashboard currently covers all spec requirements. Evaluation is about whether visualization quality, interactivity depth, or maintainability warrants a platform change.
