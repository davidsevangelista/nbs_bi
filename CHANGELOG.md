# CHANGELOG

All notable changes to `nbs_bi` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

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
