# CLAUDE.md — NBS Business Intelligence

This file governs how Claude Code assists in this repository. Read it fully before making any change.

---

## Project Context

`nbs_bi` is a Python business intelligence and cost simulation platform for **Neobankless Brasil LTDA**. It processes sensitive financial data including invoice costs, transaction volumes, and user metrics. All work must reflect production-grade standards.

---

## Documentation

| File | Purpose |
|---|---|
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | Phase tracker — check before starting any task, update when tasks complete |
| [`docs/dev/scaffold_project.md`](docs/dev/scaffold_project.md) | Developer workflow: setup, branching, dev cycle, pre-commit checklist |
| [`docs/specs/cards.md`](docs/specs/cards.md) | Spec for `nbs_bi.cards` — card cost simulation |
| [`docs/specs/transactions.md`](docs/specs/transactions.md) | Spec for `nbs_bi.transactions` — transaction analytics |
| [`docs/specs/onramp.md`](docs/specs/onramp.md) | Spec for `nbs_bi.onramp` — on/off ramp analytics |
| [`docs/specs/swaps.md`](docs/specs/swaps.md) | Spec for `nbs_bi.swaps` — swap analytics |
| [`docs/specs/ai_usage.md`](docs/specs/ai_usage.md) | Spec for `nbs_bi.ai_usage` — AI interaction cost tracking |
| [`docs/specs/reporting.md`](docs/specs/reporting.md) | Spec for `nbs_bi.reporting` — cross-module reports |

**Before implementing any feature:** read the relevant spec in `docs/specs/`. Open questions in the spec must be resolved before coding begins.

---

## Non-Negotiable Standards

### Security
- **No secrets in code.** All credentials, API keys, and connection strings go in `.env` (never committed). Use `python-dotenv` for loading.
- **No PII in logs or outputs.** User IDs, card numbers, emails must be masked or excluded from any printed output or report.
- **No hardcoded financial data** beyond what is explicitly defined as reference constants for testing.
- Input validation is required on all public-facing functions that accept external data (invoice files, API payloads).
- Dependencies must be pinned in `pyproject.toml`. Run `pip-audit` before adding new ones.

### Code Quality
- All public functions and classes must have docstrings (Google style).
- Type hints are required on all function signatures.
- No function longer than 50 lines. Decompose.
- No `print()` in library code — use `logging` with a named logger (`logging.getLogger(__name__)`).
- Maximum cyclomatic complexity of 10 per function.

### Testing
- Every new function in `nbs_bi/` must have a corresponding test in `tests/`.
- Tests must be deterministic — no random seeds without `random.seed()` or `np.random.seed()`.
- Never mock the database or external data sources in integration tests — use real fixtures.
- Coverage target: ≥ 80% per module.
- Run tests before committing: `pytest tests/ -v`.

### Data Handling
- Raw data files go in `data/` and are never committed to git (see `.gitignore`).
- Processed/cached data goes in `data/processed/` (also gitignored).
- All monetary values are handled as `Decimal` or `float64` numpy — never `float32` (precision loss).
- Currency must always be explicit in variable names: `amount_usd`, `amount_brl`.

### Git Discipline
- Branch naming: `feat/<module>/<short-desc>`, `fix/<module>/<short-desc>`.
- Commit messages: imperative mood, ≤ 72 chars subject, body explains *why* not *what*.
- No force-push to `main`. No `--no-verify`.
- Update `CHANGELOG.md` with every meaningful change.
- Update `docs/PROGRESS.md` when tasks are completed.

### Python Standards
- Python ≥ 3.11.
- `pyproject.toml` is the single source of truth for packaging and dependencies.
- Install in editable mode: `pip install -e ".[dev]"`.
- Formatter: `ruff format`. Linter: `ruff check`. Both must pass before commit.
- Import order: stdlib → third-party → local (enforced by ruff).

---

## Module Ownership

| Module | Responsibility |
|---|---|
| `nbs_bi.cards` | Card cost simulation, invoice parsing, cost-per-transaction modeling |
| `nbs_bi.transactions` | Transaction analytics, KPIs, volume patterns |
| `nbs_bi.onramp` | On/off ramp analytics |
| `nbs_bi.swaps` | DEX/swap analytics |
| `nbs_bi.ai_usage` | AI interaction cost and usage tracking |
| `nbs_bi.reporting` | Cross-module reports, dashboards, projections |

---

## What NOT to Do

- Do not add features outside the scope of the current spec in `docs/specs/`.
- Do not refactor code that is not related to the current task.
- Do not create new files unless strictly necessary.
- Do not use `pandas` where plain Python or `numpy` is sufficient.
- Do not add optional parameters "for future use" — YAGNI.
- Do not commit Jupyter notebooks with uncleared output cells.

---

## How to Run

```bash
# Install
pip install -e ".[dev]"

# Test
pytest tests/ -v --cov=nbs_bi

# Lint
ruff check nbs_bi/ tests/
ruff format nbs_bi/ tests/

# Simulate card costs
python -m nbs_bi.cards.simulator
```
