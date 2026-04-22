# SPEC — `nbs_bi.reporting`: Business Dashboard & Cross-Module Reporting

> Edit this file to request specific changes.

---

## Purpose

One place for NBS leadership to answer:
- **Where is revenue coming from?** (by product, by period)
- **Which clients generate the most value?**
- **Is the business growing or slowing?**
- **What should we act on this week?**

This module does **not** produce research. Every chart and table must support a specific operational or strategic decision.

---

## Platform Recommendation

**Deliver as a Streamlit app** (`nbs_bi/reporting/dashboard.py`), deployed on Streamlit Community Cloud or an internal server.

Why:
- Already the established pattern in `contabil_pipeline`
- Runs directly on top of `nbs_bi` models — no data export/ETL needed
- CEO opens a URL; no Python skills required
- Date filters, tabs, and download buttons are trivial to add
- If Metabase self-service SQL is needed later, the read-only PostgreSQL connection already exists

Deployment: `streamlit run nbs_bi/reporting/dashboard.py`

---

## Dashboard Structure

The dashboard has **4 tabs**. Each tab maps to a decision the CEO needs to make.

```
Tab 1 — Overview      → Is the business healthy this month?
Tab 2 — On/Off Ramp   → Is the FX operation profitable? Should I change rates?
Tab 3 — Card Program  → Are card costs under control?
Tab 4 — Clients       → Who are my best clients? Where should I grow?
```

---

## Tab 1 — Overview

**Decision: Is the business healthy right now?**

### KPI Cards (top row)
| Metric | Source | Notes |
|---|---|---|
| New Users 24H | `users.created_at` last 24h | subtitle: KYC'd count + active count |
| PIX Volume 24H | `pix_daily` last row, pix_in + pix_out | BRL |
| Card Spend 24H | `card_daily` last row, amount_usd | USD |
| Revenue | onramp summary total revenue BRL | period total from date range |

### Activity Strip (secondary row)
| Metric | Definition | Source |
|---|---|---|
| DAU | Users with `last_active_at >= NOW() - 1 day` | `users.last_active_at` |
| WAU | Users with `last_active_at >= NOW() - 7 days` | `users.last_active_at` |
| MAU | Users with `last_active_at >= NOW() - 30 days` | `users.last_active_at` |
| KYC % | `kyc_level >= 1` / total users | `users.kyc_level` |

> **Active user definition**: a user is considered active if the platform has recorded activity (any product interaction) recently enough to update `users.last_active_at`. DAU/WAU/MAU are always computed relative to `NOW()` — they are not bounded by the dashboard date range filter.

### Charts (always visible)
- **Monthly revenue** (stacked area): onramp fees + spread in BRL
- **Monthly BRL volume** (stacked bar): onramp vs offramp with MoM % annotation
- **Daily active users** (area): `active_daily` from transaction activity
- **Activation funnel** (horizontal bar): Registered → KYC → Active (any revenue)

---

## Tab 2 — On/Off Ramp

**Decisions: Should I adjust FX margins? Is my USDC inventory at risk? Which clients are driving volume?**

### KPI Row
| Metric | Formula |
|---|---|
| Total conversions | count of used=TRUE rows |
| Onramp volume BRL | sum(from_amount_brl) where direction=brl_to_usdc |
| Offramp volume BRL | sum(to_amount_brl) where direction=usdc_to_brl |
| Total revenue BRL | sum(fee_amount_brl + spread_revenue_brl) |
| USDC net position | stock_in_usdc - stock_out_usdc (running) |
| Avg FX margin % | mean(spread_percentage) |

### Charts

**Volume over time** — bar chart, daily, onramp vs offramp in BRL.
- *Decision: Is volume growing? Is there a day-of-week pattern I can act on?*

**Revenue breakdown** — stacked bar monthly: explicit fees vs spread revenue.
- *Decision: Is the spread margin holding or compressing?*

**FX implicit rate** — line chart with p10–p90 band, split by onramp/offramp.
- *Decision: Am I pricing consistently? Are outliers hurting me?*

**Top 10 clients by BRL volume** — simple table: user_id (masked), volume_brl, revenue_brl, n_conversions.
- *Decision: Are there VIP clients I should call? Any concentration risk?*

**PIX IN vs PIX OUT daily** — line chart.
- *Decision: Is my BRL liquidity net positive or am I draining reserves?*

---

## Tab 3 — Card Program

**Decisions: Are card costs growing faster than volume? Should I change the Visa tier mix?**

### KPI Row
| Metric | Source |
|---|---|
| Monthly card cost (USD) | CardCostModel.cost_breakdown().total |
| Cost per transaction (USD) | CardCostModel.cost_per_transaction() |
| Active cards | n_active_cards input |
| Total card spend by users (USD) | card_transactions sum |
| Top cost driver | largest line in cost_breakdown |

### Charts

**Cost breakdown waterfall** — one bar per fee line item, sorted descending.
- *Decision: Which cost line should I negotiate with Rain first?*

**Cost per transaction trend** — line, monthly (requires multiple invoice months).
- *Decision: Is unit economics improving as volume grows?*

**Card spend by user** — top 20 spenders table.
- *Decision: Are my top card users also my top ramp users? (cross-sell signal)*

---

## Tab 4 — Clients

**Decisions: Who are my best clients? Where should I focus growth?**

→ Powered by `nbs_bi.clients` module (see [specs/clients.md](clients.md)).

### KPI Row
| Metric | |
|---|---|
| Total users (all time) | users table count |
| KYC verified users | users where kyc_level = verified |
| Multi-product users | users active in ≥ 2 products |
| Top 10% revenue concentration | revenue from top decile / total |

### Charts

**Revenue per user distribution** — histogram (log scale).
- *Decision: Is revenue heavily concentrated? Do I have a long tail worth activating?*

**Product adoption heatmap** — rows = users, columns = products (card/onramp/swap), fill = active/inactive.
Aggregate as: % of users active in each product combination.
- *Decision: What is my cross-sell opportunity?*

**User segments table** — champion / active / at-risk / dormant, with avg revenue per segment.
- *Decision: Where should retention effort go?*

**Income band vs revenue scatter** — x: estimated_income_brl (from CPF data), y: total_revenue_brl per user.
- *Decision: Is my highest-revenue segment who I think it is?*

---

## Module Structure

```
nbs_bi/reporting/
├── __init__.py
├── dashboard.py      ← Streamlit app entry point
├── overview.py       ← Tab 1: monthly KPIs and trends
├── ramp.py           ← Tab 2: on/off ramp visuals (wraps OnrampReport)
├── cards.py          ← Tab 3: card cost visuals (wraps CardCostModel)
└── clients.py        ← Tab 4: client analytics (wraps nbs_bi.clients)
```

---

## Planned Interface

```python
# Run the dashboard
# streamlit run nbs_bi/reporting/dashboard.py

# Or use programmatically to extract DataFrames
from nbs_bi.reporting.overview import OverviewReport
from nbs_bi.reporting.ramp import RampSection

overview = OverviewReport(start_date="2026-01-01", end_date="2026-03-31")
kpis = overview.kpis()           # → dict of headline numbers
monthly = overview.monthly()     # → DataFrame[month, revenue_brl, active_users, new_users, ...]

ramp = RampSection(start_date="2026-01-01", end_date="2026-03-31")
ramp.render()                    # → renders Streamlit components inline
```

---

## Implementation Order

1. `nbs_bi/reporting/ramp.py` — Tab 2 first (data already implemented in `nbs_bi.onramp`)
2. `nbs_bi/reporting/cards.py` — Tab 3 (data already implemented in `nbs_bi.cards`)
3. `nbs_bi/reporting/dashboard.py` — wire tabs together, add date picker
4. `nbs_bi/reporting/clients.py` — Tab 4 (depends on `nbs_bi.clients`, implement last)
5. `nbs_bi/reporting/overview.py` — Tab 1 (aggregates from all modules)

---

## Open Questions

- [ ] Should the dashboard require a login (Streamlit auth) or is internal-only access sufficient?
- [ ] What is the primary review cadence — weekly, monthly? (Affects default date range)
- [ ] Should reports be exportable to PDF/Excel for board meetings?
- [ ] Which currency should headline numbers show by default — BRL or USD?
