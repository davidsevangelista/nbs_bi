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
- [x] Implement `report.py` — `OnrampReport`: full pipeline returning summary, conv_daily, pix_daily, fx_stats, active_daily, position, top_users, cohort
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
- [ ] `reporting/ramp.py` — Tab 2: on/off ramp visuals (wraps OnrampReport)
- [ ] `reporting/cards.py` — Tab 3: card cost visuals (wraps CardCostModel)
- [ ] `reporting/dashboard.py` — Streamlit entry point with date picker and tabs
- [ ] `reporting/clients.py` — Tab 4 (depends on nbs_bi.clients)
- [ ] `reporting/overview.py` — Tab 1 monthly KPIs (aggregates from all modules)

---

## Phase 7 — Client Revenue & Behaviour (`nbs_bi.clients`)

- [x] Define spec: per-user revenue model, CPF enrichment, segmentation, cohort LTV (see [specs/clients.md](specs/clients.md))
- [ ] `clients/queries.py` — fetch users, CPF data, all revenue/cost tables
- [ ] `clients/models.py` — ClientModel: revenue per user, product adoption, profile join
- [ ] `clients/segments.py` — champion/active/at-risk/dormant + cohort LTV
- [ ] Unit tests (fixture-based, no DB)

---

## Backlog

- [x] Jupyter notebook: card cost exploration (`notebooks/cards.ipynb`)
- [ ] CLI entrypoint for running simulations
- [x] Integration with live database (`OnrampQueries` via `READONLY_DATABASE_URL`)
- [x] Full DB schema documented in `docs/specs/database.md` (72 tables, column types, scaling rules, row counts)
