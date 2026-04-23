# PROGRESS.md — NBS Business Intelligence

Tracks the status of all active and planned work.

---

## Legend
- `[ ]` Not started
- `[~]` In progress
- `[x]` Done

---

## Phase 0 — Project Setup

- [x] Define project architecture and rationale
- [x] Create `CLAUDE.md` (standards, security, conventions)
- [x] Create `README.md` (overview, structure, quickstart)
- [x] Create `CHANGELOG.md`
- [x] Create `pyproject.toml` (package config, deps)
- [x] Create `.gitignore`, `.env.example`
- [x] Scaffold all module directories with `__init__.py`
- [x] Create module specs in `docs/specs/`
- [x] Create `docs/PROGRESS.md` (this file)
- [x] Create `docs/dev/scaffold_project.md` (developer workflow)
- [x] Create `docs/dev/new_project_prompt.md` (bootstrap template)

---

## Phase 1 — Card Cost Simulation (`nbs_bi.cards`)

Reference: Rain Invoice NKEMEJLO-0008, February 2026 ($6,693.58 USD)

- [x] Parse invoice line items into structured data (`invoice_parser.py`)
- [x] Implement cost model with all fee categories (`models.py`)
  - [x] Fixed costs (Base Program Fee)
  - [x] Per-card costs (Virtual Cards Fee)
  - [x] Per-transaction costs (Tx Fee + Network Passthrough Tx Cost)
  - [x] Volume-based costs (Network Passthrough Volume Fee in bps)
  - [x] Product-type costs (Visa Infinite, Visa Platinum)
  - [x] Tokenization costs (ApplePay count/amount, GooglePay)
  - [x] Compliance costs (Share Token)
  - [x] Network extras (cross-border, 3DS, verification, chip auth)
- [x] Implement scenario simulator (`simulator.py`)
  - [x] Sensitivity analysis: impact of each variable on total cost
  - [x] Weighted average cost per transaction
  - [x] What-if scenarios (e.g., 2x transactions, different Visa tier mix)
- [x] Implement linear regression model for monthly projection
- [x] Unit tests for all card cost components (16 tests)
- [x] Validate model output against February 2026 invoice ($6,693.58)
- [x] Keep card analytics importable without simulator-only dependencies by lazy-loading `CardCostSimulator`

---

## Phase 2 — Transaction Analytics (`nbs_bi.transactions`)

- [ ] Define schema for transaction data (see [specs/transactions.md](specs/transactions.md))
- [ ] Volume KPIs (daily/weekly/monthly)
- [ ] Transaction pattern analysis

---

## Phase 3 — On/Off Ramp Analytics (`nbs_bi.onramp`)

- [x] Define schema and KPIs (see [specs/onramp.md](specs/onramp.md))
- [x] Implement `queries.py` — `OnrampQueries`: DB connection, fixed SQL, BRL/USDC scaling, Parquet cache; 7 active-user query methods (PIX in/out, card txs, card fees, billing charges, swaps, payouts)
- [x] Implement `models.py` — `OnrampModel`: KPIs, volume by period, FX stats, position + PnL, top users, user behavior, spread stats, revenue by direction, new vs returning; tz-aware datetime normalised to UTC-naive in `_clean()`; `volume_brl` NaN bug fixed
- [x] Implement `report.py` — `OnrampReport`: full pipeline; daily active users union of all 7 revenue-generating sources
- [x] Unit tests (23+ tests, all green, no DB required)
- [ ] Smoke test against production DB
- [ ] Validate KPIs against contabil_pipeline dashboard for same period

---

## Phase 4 — Swap Analytics (`nbs_bi.swaps`)

- [ ] Define schema and KPIs (see [specs/swaps.md](specs/swaps.md))

---

## Phase 5 — AI Usage Analytics (`nbs_bi.ai_usage`)

- [ ] Define schema and KPIs (see [specs/ai_usage.md](specs/ai_usage.md))

---

## Phase 6 — Reporting (`nbs_bi.reporting`)

- [x] Define spec (see [specs/reporting.md](specs/reporting.md)) and [specs/marketing.md](specs/marketing.md)
- [x] `reporting/theme.py` — shared visual module: colour palette, `panel()`, formatters, `mask_user_id()`, `rgba()`; `panel()` uses `legend y=-0.2, b=60` to prevent title/legend overlap
- [x] `reporting/overview.py` — Tab 1: dark CSS KPI cards; 6-metric strip (users, KYC rate, active rate, conversions, volume BRL, revenue BRL); 4 charts (monthly revenue, monthly volume with MoM deltas, daily active users, activation funnel)
- [x] `reporting/ramp.py` — Tab 2: 4 subtabs (Visão Geral, Receita, Clientes, FX & Volume); granularity toggle; 8+ charts
- [x] `reporting/cards.py` — Tab 3 (Cards): 4 sub-tabs — Program Costs (invoice selector, cost breakdown, sensitivity, trend), Usage Patterns, Price Tiers, Evolution (cross-invoice trend + Δ vs prior + **line-per-driver evolution** + summary table + revenue KPI row); fully translated to English
- [x] `reporting/clients.py` — Tab 4: `ClientSection` with 5 sub-tabs (LTV & Cohorts, Acquisition, Segments, Founders Club, Product Adoption); LTV & Cohorts tab has 5 KPIs (avg LTV, best source, FX rate, multi-product users %, top-10% revenue concentration) + revenue-per-user histogram (log scale); Founders leaderboard shows full 9-column revenue breakdown per user
- [x] `reporting/marketing.py` — Tab 5: `MetaAdsSection`; auto-loads most-recent Rain CSV from `data/nbs_corp_card/`; shows only most-recent campaign; 3-chart stack (cumulative spend vs cohort revenue; cumulative contribution margin with dual-axis card txns + conversions; stacked revenue breakdown by source); KPI strip (spend, revenue, ROAS, CAC, net contribution margin); referral selectbox filters entire cohort analysis; tracking start gated at `2026-04-12`
- [x] `reporting/dashboard.py` — Streamlit entry point: **5 tabs** (Overview, Conversions, Cards, Clients, Marketing - Ads); no sidebar panel; date range computed inline; invoice total auto-loaded from latest parsed JSON; NBS logo favicon; sidebar collapsed; title "NBS Data Analytics"
- [x] `use_container_width=True` → `width="stretch"` everywhere (Streamlit deprecation)
- [x] Dark NBS green theme: Plotly charts, `theme.py` constants, and `overview.py` CSS all aligned to dark shell (`#0D1117` bg / `#161B22` plot bg / `#00E676` accent)
- [x] Full English translation: Cards tab and Conversions (Ramp) tab; activation funnels use `go.Funnel` (correct top-to-bottom direction)
- [x] Deployed to **Streamlit Community Cloud** (personal GitHub mirror); viewer auth via email whitelist; `READONLY_DATABASE_URL` injected as secret

---

## Phase 7 — Client Revenue & Behaviour (`nbs_bi.clients`)

- [x] Define spec (see [specs/clients.md](specs/clients.md))
- [x] `clients/queries.py` — 11 SQL queries, Parquet cache
- [x] `clients/models.py` — `ClientModel`: master join, unified USD LTV, product adoption, cohort LTV, activation funnel, CAC breakeven
- [x] `clients/segments.py` — `ClientSegments`: champion/active/at-risk/dormant
- [x] `clients/report.py` — `ClientReport.build()` dict
- [x] `clients/campaigns.py` — `CampaignAnalyzer`: `load_ad_spend()`, `_detect_campaigns()`, `roi_summary()`, `daily_context()`, `cumulative_revenue()`, `cumulative_profit()`, `referral_code_options()`
  - `_DAILY_COHORT_REVENUE_SQL`: discriminated revenue by source (conversion spread, card fees, billing, swap) and cost (cashback, rev share — cohort-scoped only); 7 labeled columns
  - `_cost_per_txn_from_invoices()` + `_cogs_for_cohort_txns()`: per-transaction card COGS from Rain invoice history; fallback to nearest period when no exact match
  - `cumulative_profit()`: contribution margin = revenue − card program COGS (Meta Ads spend excluded — tracked separately); cumulative breakdowns for all 6 revenue/cost streams + txn count + conversion count
  - Referral filter: all 4 SQL cohort CTEs accept `:referral_code` param; empty string = no filter (short-circuit `'' = ''`); `referral_code_options()` fetches distinct codes from DB
- [x] Unit tests (130+ tests across all modules, fixture-based, no DB)
- [x] Smoke test against production DB

---

## Current State — 2026-04-22 (v1.5.0)

### What's been built

`nbs_bi` is a fully operational BI platform deployed on Streamlit Community Cloud. All 5 dashboard tabs are live. Data flows from two PostgreSQL databases (production read-only replica + Neon ads DB) through module-specific query/model/report pipelines into a dark-themed Streamlit dashboard with Plotly visualisations.

**Phase 1 — Cards** (`nbs_bi.cards`):
- `CardCostModel` validates against Feb 2026 invoice ($6,693.58). March 2026 invoice ($7,857.40) also parsed and loaded.
- `invoice_total_usd` field stored in each actuals JSON; `nbs-invoices --force` re-parses all PDFs to populate it.
- Known gap: `CardFeeRates` model accounts for ~$6,357 of the March invoice but Rain billed $7,857.40; ~$1,500 is unmodelled ("Outros"). Visible in the Evolução stacked-driver chart.

**Phase 3 — Onramp** (`nbs_bi.onramp`): `OnrampQueries` + `OnrampModel` + `OnrampReport` cover conversions, PIX flows, FX stats, daily active users (7 sources), top users with attribution, monthly revenue by direction, cohort retention. Revenue USD computed by converting `fee_amount_brl + spread_revenue_brl` at per-tx `exchange_rate`.

**Phase 6 — Reporting** (`nbs_bi.reporting`): 5-tab Streamlit dashboard titled "NBS Data Analytics", deployed and accessible at `nbs-data-analytics.streamlit.app`:
- Tab 1 — Overview: 2 KPI rows (Conversions: count, volume BRL, revenue USD; Cards: txns, volume USD, revenue USD from card fees + billing); revenue trend, volume, daily active users, activation funnel
- Tab 2 — Conversions: 4 subtabs, 8+ charts, granularity toggle (Daily/Weekly/Monthly); fully translated to English
- Tab 3 — Cards: 4 sub-tabs — Program Costs (invoice selector, cost breakdown, sensitivity, trend), Usage Patterns, Price Tiers, Evolution (cross-invoice trend + Δ vs prior + line-per-driver evolution + revenue KPI row + cost KPI row + summary table); fully translated to English
- Tab 4 — Clients: LTV cohorts, acquisition, segments, founders (full 9-column revenue breakdown per user), product adoption
- Tab 5 — Marketing - Ads: cohort P&L — cumulative spend vs revenue; contribution margin with dual-axis txn counts; stacked revenue breakdown; referral code filter
- Dark NBS green theme throughout: `#0D1117` background, `#161B22` chart bg, `#00E676` primary accent
- Default date range: full history from 2025-08-15 to today

**Phase 7 — Clients** (`nbs_bi.clients`): Full per-user revenue pipeline + Meta Ads cohort P&L:
- Discriminated revenue SQL: conversion spread, card fees, billing charges, swap fees (revenue); cashback + rev share cohort-scoped (costs)
- Per-transaction card COGS: `cost_per_txn = invoice_total / invoice_txn_count` × daily cohort txn count
- Contribution margin = Revenue − Card Program COGS (Meta Ads spend is acquisition cost, tracked separately)
- Referral filter on all cohort queries; `referral_code_options()` populates UI selectbox dynamically

**Deployment**:
- Live on Streamlit Community Cloud at `nbs-data-analytics.streamlit.app`
- Mirrored on personal GitHub (`davidsevangelista/nbs_bi`) — this is what Streamlit Cloud pulls from
- Two DB secrets: `READONLY_DATABASE_URL` (production analytics) + `ADS_DATABASE_URL` (Neon, Meta Ads spend only)
- Neon DB populated with 57 rows of Meta Ads spend via `nbs-ads-upload` CLI
- `requirements.txt` with `-e .` ensures package install; no Poetry dependency

### Key metrics from live DB (as of 2026-04-22)

- **Users**: 11,478 registered · 5,921 KYC done (52%) · 2,407 revenue-generating (21%)
- **Conversions**: 8,697 completed (used = TRUE in conversion_quotes)
- **Card spend**: ~22,728 card spend rows in DB history
- **Invoices on file**: NKEMEJLO-0008 (Feb 2026, $6,693.58) · NKEMEJLO-0009 (Mar 2026, $7,857.40)
- **March modelled gap**: $7,857.40 billed vs $6,357.39 modelled → $1,500.01 in unmodelled fees
- **Meta Ads**: campaign_3 (Apr 14–20, $715 spend); ROAS still maturing — revisit at 30-day cohort mark (~May 14)
- **Cost per transaction**: Feb $0.972/txn · Mar $1.124/txn (6,885 and 6,990 transactions respectively)

### Known environment notes

- `sklearn` absent locally → `CardCostSimulator` lazy-loaded so analytics imports always work
- `streamlit` absent locally → `reporting/cards.py` has an import-time shim so figure-builder tests collect without the UI runtime
- 7 pre-existing test failures: `test_simulator.py` (4, missing `sklearn`), `test_campaigns.py` (1, referral DB error mock), `test_marketing.py` (2, profit chart shape assertions) — non-blocking

---

## Backlog

### Immediate
- [ ] Verify Overview KPI values are correct after revenue USD fix (check conversion revenue USD vs known period)
- [ ] Upload future Rain CSV exports to Neon as new ad spend data arrives (`nbs-ads-upload <file> --db-url $ADS_DATABASE_URL`)

### Campaign monitoring
- [ ] Watch campaign_3 ROAS at ~30-day cohort mark (~May 14, 2026); compare referral filter vs all-users

### Cards
- [ ] Investigate and close the ~$1,500 unmodelled fee gap in NKEMEJLO-0009 (March 2026)
- [ ] Parse April 2026 invoice (NKEMEJLO-0010) when available; evolution chart will auto-pick it up

### Onramp validation
- [ ] Smoke test: validate `OnrampModel` KPIs against contabil_pipeline for a known date window

### Future phases
- [ ] Phase 2 — transactions analytics (schema definition first)
- [ ] Phase 4 — swaps analytics (schema definition first)
- [ ] Phase 5 — AI usage analytics (schema definition first)
