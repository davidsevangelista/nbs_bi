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

- [~] Parse invoice line items into structured data (`invoice_parser.py`)
- [ ] Implement cost model with all fee categories (`models.py`)
  - [ ] Fixed costs (Base Program Fee)
  - [ ] Per-card costs (Virtual Cards Fee)
  - [ ] Per-transaction costs (Tx Fee + Network Passthrough Tx Cost)
  - [ ] Volume-based costs (Network Passthrough Volume Fee in bps)
  - [ ] Product-type costs (Visa Infinite, Visa Platinum)
  - [ ] Tokenization costs (ApplePay count/amount, GooglePay)
  - [ ] Compliance costs (Share Token)
  - [ ] Network extras (cross-border, 3DS, verification, chip auth)
- [ ] Implement scenario simulator (`simulator.py`)
  - [ ] Sensitivity analysis: impact of each variable on total cost
  - [ ] Weighted average cost per transaction
  - [ ] What-if scenarios (e.g., 2x transactions, different Visa tier mix)
- [ ] Implement linear regression model for monthly projection
- [ ] Unit tests for all card cost components
- [ ] Validate model output against February 2026 invoice ($6,693.58)

---

## Phase 2 — Transaction Analytics (`nbs_bi.transactions`)

- [ ] Define schema for transaction data (see [specs/transactions.md](specs/transactions.md))
- [ ] Volume KPIs (daily/weekly/monthly)
- [ ] Transaction pattern analysis

---

## Phase 3 — On/Off Ramp Analytics (`nbs_bi.onramp`)

- [ ] Define schema and KPIs (see [specs/onramp.md](specs/onramp.md))

---

## Phase 4 — Swap Analytics (`nbs_bi.swaps`)

- [ ] Define schema and KPIs (see [specs/swaps.md](specs/swaps.md))

---

## Phase 5 — AI Usage Analytics (`nbs_bi.ai_usage`)

- [ ] Define schema and KPIs (see [specs/ai_usage.md](specs/ai_usage.md))

---

## Phase 6 — Reporting (`nbs_bi.reporting`)

- [ ] Cross-module cost dashboard
- [ ] Monthly projection report
- [ ] Export to CSV/Excel

---

## Backlog

- [ ] Jupyter notebook: card cost exploration
- [ ] CLI entrypoint for running simulations
- [ ] Integration with live database (schema TBD)
