# CHANGELOG

All notable changes to `nbs_bi` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

## [0.6.0] — 2026-04-20

### Added
- `nbs_bi/clients/queries.py` — `ClientQueries`: 11 parameterised SQL queries covering user cohort base (with attribution inference), onramp revenue (period + monthly time-series), card fees, card transactions, billing charges, cashback, revenue share, swaps, payouts, and FX rate; Parquet cache + `_scale_brl` helper
- `nbs_bi/clients/models.py` — `ClientModel`: master user DataFrame joining all revenue/cost streams; unified USD LTV with BRL→USD FX conversion; pro-rata Rain invoice card cost allocation; `revenue_leaderboard`, `product_adoption`, `acquisition_summary`, `referral_code_summary`, `founders_report`, `at_risk_users`, `cohort_ltv`, `ltv_by_source`, `cac_breakeven` methods
- `nbs_bi/clients/segments.py` — `ClientSegments`: champion/active/at-risk/dormant RFM classification; `segment_summary`, `founders_vs_non_founders`, `referral_performance`
- `nbs_bi/clients/report.py` — `ClientReport`: orchestrates all analyses into a structured dict; `to_json_api()` for future API consumption
- `nbs_bi/reporting/clients.py` — `ClientSection`: 5-tab Streamlit dashboard (LTV & Cohorts, Acquisition, Segments, Founders Club, Product Adoption); CAC breakeven slider; Plotly figure builders for cohort heatmap, LTV curves, acquisition bars, funnel, segment donut, founders scatter, adoption heatmap
- `nbs_bi/clients/__init__.py` — exposes `ClientModel`, `ClientReport`
- `billing_charges` integrated as direct card tx fee revenue (USDC micros ÷ 1,000,000, `status='settled'`)
- Tests: 38 tests across `test_queries.py`, `test_models.py`, `test_segments.py` (fixture-based, no DB)

### Changed
- `nbs_bi/reporting/dashboard.py` — Tab 5 wired to `ClientSection`; sidebar adds Rain Invoice Total input; `_sidebar` returns invoice total; new `_load_client_report` cached loader
- `docs/specs/clients.md` — resolved all 8 open questions; added LTV/CAC Analysis section with cohort matrix spec, CAC breakeven formula, attribution inference rule, and JSON API export note

## [0.5.0] — 2026-04-20

### Added
- `nbs_bi/cards/analytics.py` — pure data-layer module for card spend analytics (live DB via SQLAlchemy):
  - `load_card_transactions()` — DB fetch with date filters; `build_daily()`, `bin_transactions()` — aggregations
  - `fee_comparison()`, `monthly_revenue()` — fee-model comparisons for Models A/B/C/D
  - `ewma_forecast()` — EWMA demand forecast (span=7) with 95% CI for count and volume
  - `build_scenarios()`, `threshold_sweep()`, `compute_combinations()` — B2B growth scenarios and Model C optimisation
  - `flat_pct_monthly_revenue()`, `flat_pct_coverage_metrics()` — invoice-coverage math for flat + percentage card fee structures
  - Plotly figure builders: `fig_daily_timeline`, `fig_weekly_patterns`, `fig_distribution`, `fig_fee_comparison`, `fig_forecast`, `fig_summary_table`, `fig_b2b_projection`, `fig_threshold_sweep`, `fig_combo_heatmap`, `fig_combo_lines`, `fig_coverage_bar`, `fig_coverage_heatmap`
- `nbs_bi/reporting/ramp.py` — `RampSection`: Streamlit + Plotly Tab 2 renderer; 7 charts — BRL conversion volume, monthly fee vs spread revenue, implicit FX rate with p10/p90 band, USDC position, cumulative PnL, top users (masked), PIX IN/OUT flows
- `nbs_bi/reporting/cards.py`:
  - `CardSection` (Tab 3): cost breakdown, sensitivity (+10%), cost-per-tx trend, top spenders
  - `CardAnalyticsSection` (Tab 4): 8-tab interactive dashboard (patterns, distribution & models, EWMA forecast, indicators, invoice coverage, B2B projection, Model C threshold, combination grid)
- `nbs_bi/reporting/dashboard.py` — Streamlit entry point: 5 tabs, global sidebar date picker, 1-hour `@st.cache_data`, DB connection status
- `OnrampReport._build_revenue_monthly()` — monthly fee vs spread revenue; exposed as `revenue_monthly` key in `build()` return dict
- `docs/specs/card_usage_forecast.md` — spec for standalone forecast script (HTML output, 7 sections, EWMA + 95% CI)
- Unit tests: `tests/cards/test_analytics.py`, `tests/onramp/test_report.py` (23 tests), `tests/reporting/test_ramp.py`, `tests/reporting/test_cards.py`

### Changed
- Card Analytics dashboard expanded from 7 to 8 tabs — "Cobertura Invoice" tab added between Indicators and B2B Projection
- Monthly card analytics extrapolation uses inclusive observed calendar days (off-by-one fix)
- `nbs_bi.cards.__init__` lazy-loads `CardCostSimulator` — analytics imports never blocked by absent `scikit-learn`
- `nbs_bi.reporting.cards` importable in non-UI environments without `streamlit`
- `pyproject.toml`: added `streamlit>=1.32`, `plotly>=5.20` to runtime dependencies

### Findings
- Live DB (`2026-02-01` → `2026-04-13`): `$0.30 + 1.00%` → $4,495.99/month → 67.17% coverage of February Rain invoice ($6,693.58)
- Breakeven: ~1.87% variable (with $0.30 fixed) or ~$0.64 fixed (with 1.00% variable)

## [0.4.0] — 2026-04-09

### Added
- `nbs_bi/cards/analytics.py` — pure data-layer module for card spend analytics:
  - `load_card_transactions()` — DB fetch via SQLAlchemy with date filters
  - `build_daily()`, `bin_transactions()`, `fee_comparison()`, `monthly_revenue()` — aggregations and fee-model comparisons (Models A/B/C/D)
  - `ewma_forecast()` — EWMA demand forecast with 95% CI for count and volume
  - `build_scenarios()`, `threshold_sweep()`, `compute_combinations()` — B2B growth scenarios and Model C threshold optimisation
  - Plotly figure builders: `fig_daily_timeline`, `fig_weekly_patterns`, `fig_distribution`, `fig_fee_comparison`, `fig_forecast`, `fig_summary_table`, `fig_b2b_projection`, `fig_threshold_sweep`, `fig_combo_heatmap`, `fig_combo_lines`
- `nbs_bi/reporting/ramp.py` — `RampSection`: Streamlit + Plotly rendering for the On/Off Ramp tab (Tab 2); 7 charts covering volume, monthly revenue split, implicit FX rate with p10/p90 band, USDC position, cumulative PnL, top users (user IDs masked), and PIX IN/OUT flows
- `nbs_bi/reporting/cards.py` — two section classes:
  - `CardSection` (Tab 3): cost breakdown, sensitivity (+10%), cost-per-tx trend, top spenders
  - `CardAnalyticsSection` (Tab 4): 7-tab interactive dashboard driven by live DB data — usage patterns, distribution & models, EWMA forecast, indicators, B2B projection, Model C threshold, and combination grid; sidebar controls for threshold slider and editable B2B scenario table
- `nbs_bi/reporting/dashboard.py` — Streamlit entry point with 5 tabs (Overview, On/Off Ramp, Card Costs, Card Analytics, Clients), global date picker, 1-hour cached DB loads, and DB connection status indicator
- `OnrampReport._build_revenue_monthly()` — monthly fee vs spread revenue breakdown added to the report output dict as `revenue_monthly`
- Unit tests for `reporting/ramp.py` figure builders and helpers (`tests/reporting/test_ramp.py`)
- Unit tests for `reporting/cards.py` figure builders (`tests/reporting/test_cards.py`)
- Unit tests for `onramp/report.py` (`tests/onramp/test_report.py`)

### Changed
- `pyproject.toml`: added `sqlalchemy>=2.0`, `psycopg2-binary>=2.9`, `pyarrow>=15.0`, `streamlit>=1.32`, `plotly>=5.20` to runtime dependencies
- `OnrampReport.build()` return dict now includes `revenue_monthly` key alongside existing keys

## [0.3.0] — 2026-03-26

### Added
- `docs/` directory with structured project documentation
- `docs/PROGRESS.md` — phase tracker (moved from root)
- `docs/specs/` — module specs moved from `nbs_bi/<module>/SPEC.md` into dedicated doc files: `cards.md`, `transactions.md`, `onramp.md`, `swaps.md`, `ai_usage.md`, `reporting.md`
- `docs/dev/scaffold_project.md` — full developer workflow guide (setup, dev cycle, pre-commit checklist)
- `docs/dev/new_project_prompt.md` — reusable Claude Code prompt template to bootstrap new projects with the same structure

### Changed
- `CLAUDE.md`: added Documentation section with a reference table linking to all docs; updated Git Discipline to point to `docs/PROGRESS.md`; updated "What NOT to Do" to reference `docs/specs/` instead of in-module SPEC files

### Removed
- Root-level `PROGRESS.md` (moved to `docs/PROGRESS.md`)
- Per-module `SPEC.md` files from `nbs_bi/cards/`, `nbs_bi/transactions/`, `nbs_bi/onramp/`, `nbs_bi/swaps/`, `nbs_bi/ai_usage/`, `nbs_bi/reporting/` (moved to `docs/specs/`)

## [0.2.0] — 2026-03-25

### Added
- `CardCostModel.from_invoice(path)` classmethod — loads inputs from a JSON file, matching the planned SPEC interface
- `data/invoices/Invoice-NKEMEJLO-0008-actuals.json` — structured actuals for the February 2026 Rain invoice
- `notebooks/cards.ipynb` — full analysis notebook: cost breakdown, cost/tx decomposition, sensitivity chart, scenario comparison, deterministic and regression projections, volume cost curve
- `_build_markdown_report()` in `simulator.py` — renders a simulation report as a clean Markdown document
- CLI (`python -m nbs_bi.cards.simulator`) now saves output to `data/cards_simulation/card_simulation_<period>.md` in addition to terminal output
- `data/cards_simulation/` to `.gitignore` (generated output)
- **Visa Card Tiers** section in `cards/SPEC.md` — documents that Infinite ($1.70/event) and Platinum ($0.25/event) are per-client card assignments, not generic transaction buckets; notes the $1.45/event savings from Infinite → Platinum migration; flags the unresolved 1,713 vs 6,885 billing gap for confirmation with Rain
- `matplotlib>=3.8` added to package dependencies

### Changed
- `CardCostSimulator.__init__` now accepts either `CardCostModel` or `CardInvoiceInputs` — matches `CardCostSimulator(model)` from the SPEC
- `CardCostSimulator.project()` no longer raises when called without a fitted regression — falls back to the deterministic rate model, making it usable from day one with a single invoice month
- `cards/SPEC.md` cost table: "Product type" category renamed to "Card tier"; Visa Infinite/Platinum fee lines clarified
- `cards/SPEC.md` open questions: Visa tier question marked resolved; two new questions added (billing gap, tier migration rules); CLI output section added

### Fixed
- `nbs_bi/cards/__init__.py` exported non-existent `InvoiceLineItem` — corrected to `CardInvoiceInputs`
- `pyproject.toml` build backend corrected from `setuptools.backends.legacy:build` to `setuptools.build_meta`
- Test `test_project_without_fit_raises` updated to reflect new fallback behaviour of `project()`
- Unused `CardFeeRates` import removed from test module

---

## [0.1.0] — 2026-03-25

### Added
- Project scaffolding: `CLAUDE.md`, `README.md`, `PROGRESS.md`, `CHANGELOG.md`
- `pyproject.toml` with package metadata, dependencies (`numpy`, `pandas`, `scikit-learn`, `pydantic`, `rich`, `tabulate`, `python-dotenv`), and dev tooling (`pytest`, `ruff`, `pip-audit`)
- Installable Python package `nbs_bi` with submodules: `cards`, `transactions`, `onramp`, `swaps`, `ai_usage`, `reporting`
- `SPEC.md` for each module defining scope, planned interface, data schema, and open questions
- `nbs_bi/cards/invoice_parser.py` — `CardInvoiceInputs` dataclass with validation and `from_february_2026()` factory
- `nbs_bi/cards/models.py` — `CardFeeRates`, `CostBreakdown`, `CardCostModel` with full breakdown, cost/tx, sensitivity analysis, and cost contribution percentage
- `nbs_bi/cards/simulator.py` — `CardCostSimulator` with scenario runner, `compare_scenarios`, `fit_linear_model`, `project`, `baseline_report`, and `main()` CLI entrypoint
- 16 unit tests in `tests/cards/test_simulator.py` validating against the February 2026 invoice ($6,693.58)
- Invoice reference: Rain NKEMEJLO-0008, February 2026 — $6,693.58 USD, 6,885 transactions, $0.972/tx
- `nbs-cards` CLI script entrypoint via `pyproject.toml`

---

## Format Reference

```
## [x.y.z] — YYYY-MM-DD

### Added      ← new features
### Changed    ← changes to existing behavior
### Deprecated ← soon-to-be removed
### Removed    ← removed features
### Fixed      ← bug fixes
### Security   ← security patches
```
