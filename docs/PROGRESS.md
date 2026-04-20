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
- [x] Implement `queries.py` — `OnrampQueries`: DB connection, fixed SQL (schema-grounded), BRL/USDC scaling, Parquet cache
- [x] Implement `models.py` — `OnrampModel`: KPIs, volume by period, FX stats, position + PnL, top users, active users
- [x] Implement `report.py` — `OnrampReport`: full pipeline returning summary, conv_daily, pix_daily, fx_stats, active_daily, position, top_users, cohort, revenue_monthly
- [x] Unit tests (23 tests, all green, no DB required)
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

- [x] Define spec: 4-tab Streamlit dashboard, platform recommendation, per-tab decisions (see [specs/reporting.md](specs/reporting.md))
- [x] `reporting/ramp.py` — Tab 2: on/off ramp visuals (wraps OnrampReport); 7 charts incl. volume, revenue, FX, position/PnL, PIX flows, top users
- [x] `reporting/cards.py` — Tab 3: card cost visuals (wraps CardCostModel); `CardSection` + `CardAnalyticsSection` (8-tab live DB dashboard)
- [x] `reporting/dashboard.py` — Streamlit entry point: 5 tabs (Overview, Ramp, Card Costs, Card Analytics, Clients), sidebar date picker, DB connection status
- [x] `cards/analytics.py` — `CardAnalyticsSection` data layer: DB fetch, daily/weekly aggregations, fee-model comparisons (A/B/C/D), invoice coverage, EWMA forecast, B2B scenarios, threshold sweep, combination grid
- [x] Add card invoice-coverage decision tooling:
  - [x] Compute flat + percentage revenue (`flat_fee_usd + pct_fee * amount_usd`) against Rain invoice cost
  - [x] Show coverage ratio, margin, required variable percentage, and required fixed fee
  - [x] Render coverage bar and flat/% breakeven heatmap in Card Analytics
  - [x] Unit-test inclusive observed-day monthly extrapolation and coverage math
- [x] Unit tests for `reporting/ramp.py` and `reporting/cards.py` figure builders
- [ ] `reporting/clients.py` — Tab 5 (depends on nbs_bi.clients)
- [ ] `reporting/overview.py` — Tab 1 monthly KPIs (aggregates from all modules)

---

## Current State — 2026-04-20

### What's been built

The project has a working BI foundation covering card-cost modelling, live card-spend analytics, on/off-ramp analytics, and a 5-tab Streamlit reporting shell.

**Phase 1 — Cards**: `CardCostModel` reproduces the February 2026 Rain invoice (`NKEMEJLO-0008`) at $6,693.58. `CardCostSimulator` runs what-if and linear-projection scenarios. `cards/analytics.py` adds a live DB data layer with daily/weekly aggregations, four fee-model comparisons (A/B/C/D), EWMA demand forecasting, B2B scenarios, threshold sweep, and invoice-coverage math.

**Phase 3 — Onramp**: `OnrampQueries` + `OnrampModel` + `OnrampReport` deliver the full ramp pipeline (conversions, PIX flows, FX stats, position/PnL, active users, top users, monthly revenue split). 23 unit tests, no DB required.

**Phase 6 — Reporting**: Streamlit `dashboard.py` (5 tabs), `reporting/ramp.py` (Tab 2, 7 charts), `reporting/cards.py` (Tab 3 cost breakdown + Tab 4 eight-tab analytics dashboard).

**Card fee coverage finding** (live DB, `2026-02-01` → `2026-04-13`):
- 15,703 completed card-spend transactions over 72 days; monthlyized: 6,543 tx/month, $253,311.42/month spend
- `$0.30 + 1.00%` → $4,495.99/month projected revenue → 67.17% coverage; -$2,197.59/month gap
- Breakeven: ~1.87% variable (with $0.30 fixed) or ~$0.64 fixed (with 1.00% variable)

### What's pending (committed changes not yet in a versioned release)

The following are staged / unstaged changes that will form the next release:

- `nbs_bi/cards/analytics.py` (new) — full data layer + figure builders
- `nbs_bi/reporting/ramp.py` (new) — Tab 2 ramp visuals
- `nbs_bi/reporting/cards.py` (new) — Tab 3 + Tab 4 card visuals
- `nbs_bi/reporting/dashboard.py` (new) — Streamlit entry point
- `nbs_bi/onramp/report.py` — added `revenue_monthly` key to `build()` output
- `nbs_bi/cards/__init__.py` — lazy-loads `CardCostSimulator`
- `pyproject.toml` — added `streamlit>=1.32`, `plotly>=5.20` to runtime deps
- `tests/cards/test_analytics.py`, `tests/onramp/test_report.py`, `tests/reporting/` — new test suites
- `docs/specs/card_usage_forecast.md` — spec for standalone forecast script (complete)

### Known environment notes

- `sklearn` absent locally → `CardCostSimulator` is lazy-loaded so analytics imports always work.
- `streamlit` absent locally → `reporting/cards.py` has an import-time shim so pure figure-builder tests collect without the UI runtime.

---

## Phase 7 — Client Revenue & Behaviour (`nbs_bi.clients`)

- [x] Define spec: per-user revenue model, CPF enrichment, segmentation, cohort LTV (see [specs/clients.md](specs/clients.md))
- [x] `clients/queries.py` — 11 SQL queries: cohort base (attribution inference), onramp revenue + monthly time-series, card fees, card txs, billing_charges (actual card tx fee revenue), cashback, revenue share, swaps, payouts, FX rate; Parquet cache
- [x] `clients/models.py` — `ClientModel`: master join of all revenue/cost streams → unified USD LTV; pro-rata Rain invoice card cost; `revenue_leaderboard`, `product_adoption`, `acquisition_summary`, `referral_code_summary`, `founders_report`, `at_risk_users`, `cohort_ltv`, `ltv_by_source`, `cac_breakeven`
- [x] `clients/segments.py` — `ClientSegments`: champion/active/at-risk/dormant + `referral_performance` + `founders_vs_non_founders`
- [x] `clients/report.py` — `ClientReport.build()` → structured dict; `to_json_api()` for future API layer
- [x] Unit tests (38 tests, fixture-based, no DB)
- [x] Smoke test against production DB (11,478 users, 9-cohort LTV matrix, 4 acquisition sources)
- [x] `reporting/clients.py` — 5-tab `ClientSection`: LTV & Cohorts (heatmap + CAC slider), Acquisition, Segments, Founders Club, Product Adoption
- [x] `reporting/dashboard.py` — Tab 5 wired to `ClientSection`; sidebar adds Rain Invoice Total input

---

## Backlog

- [x] Jupyter notebook: card cost exploration (`notebooks/cards.ipynb`)
- [x] CLI entrypoint for running simulations
- [x] Integration with live database (`OnrampQueries` via `READONLY_DATABASE_URL`)
- [x] Full DB schema documented in `docs/specs/database.md` (72 tables, column types, scaling rules, row counts)
- [ ] Feed uploaded/current Rain invoice cost from Card Costs tab into Card Analytics automatically, instead of using the default February 2026 target unless overridden
- [ ] Add date-window presets for Card Analytics coverage decisions (last 7d, last 30d, current invoice period, all-time)
- [ ] Validate dashboard KPIs end-to-end in Streamlit once full runtime dependencies are installed
