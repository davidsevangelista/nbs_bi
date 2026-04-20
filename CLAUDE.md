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
| [`docs/dev/session_memory_protocol.md`](docs/dev/session_memory_protocol.md) | Full session memory protocol — types, file format, start/end rituals, anti-patterns |
| [`docs/specs/cards.md`](docs/specs/cards.md) | Spec for `nbs_bi.cards` — card cost simulation |
| [`docs/specs/transactions.md`](docs/specs/transactions.md) | Spec for `nbs_bi.transactions` — transaction analytics |
| [`docs/specs/onramp.md`](docs/specs/onramp.md) | Spec for `nbs_bi.onramp` — on/off ramp analytics |
| [`docs/specs/swaps.md`](docs/specs/swaps.md) | Spec for `nbs_bi.swaps` — swap analytics |
| [`docs/specs/ai_usage.md`](docs/specs/ai_usage.md) | Spec for `nbs_bi.ai_usage` — AI interaction cost tracking |
| [`docs/specs/reporting.md`](docs/specs/reporting.md) | Spec for `nbs_bi.reporting` — Streamlit dashboard, cross-module reports |
| [`docs/specs/clients.md`](docs/specs/clients.md) | Spec for `nbs_bi.clients` — per-user revenue, segmentation, CPF enrichment |
| [`docs/specs/database.md`](docs/specs/database.md) | Full DB schema reference — all 72 tables, column types, scaling rules, key joins |

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

## Session Memory

Full protocol: [`docs/dev/session_memory_protocol.md`](docs/dev/session_memory_protocol.md)

### On session START
1. Read `MEMORY.md` (the index) from the project memory directory.
2. Load any individual memory files whose description is relevant to the current task.
3. Treat all memories as **point-in-time observations** — verify file paths, function names, and state claims against current code before asserting them as fact.

### During the session — save when you learn:

| Type | Save when |
|---|---|
| `user` | User's role, expertise, domain knowledge, or working preferences |
| `feedback` | User corrects OR confirms a non-obvious approach — record both |
| `project` | Who is doing what, why, or by when; decisions and constraints not in code |
| `reference` | Where to look for information in external systems |

**Do NOT save:** code patterns, file structure, git history, debugging recipes, or anything derivable from the code or already in CLAUDE.md.

Every `feedback` and `project` memory must include:
- **Why:** the user's stated reason or the constraint behind the decision
- **How to apply:** when this guidance kicks in

Convert all relative dates to absolute dates at save time.

### On session END
1. Review what changed. Update or delete any memory that is now stale.
2. If a research question was opened, a decision was made, or module state changed, update the relevant memory file.
3. Update the `MEMORY.md` index to match.

---

## What NOT to Do

- Do not add features outside the scope of the current spec in `docs/specs/`.
- Do not refactor code that is not related to the current task.
- Do not create new files unless strictly necessary.
- Do not use `pandas` where plain Python or `numpy` is sufficient.
- Do not add optional parameters "for future use" — YAGNI.
- Do not commit Jupyter notebooks with uncleared output cells.

---

## Current Status (v0.5.0, 2026-04-20)

| Phase | Module | Status | Next |
|---|---|---|---|
| 1 | `cards` | Done | — |
| 2 | `transactions` | Not started | Schema definition |
| 3 | `onramp` | Core done | Smoke test + KPI validation vs contabil_pipeline |
| 4 | `swaps` | Not started | Schema definition |
| 5 | `ai_usage` | Not started | Schema definition |
| 6 | `reporting` | 4/6 tabs done | `overview.py` (Tab 1), `clients.py` (Tab 5) |
| 7 | `clients` | Spec done | `queries.py` → `models.py` → `segments.py` |

### Key Decisions

- **Dashboard platform:** Streamlit (see `docs/specs/reporting.md` for rationale)
- **Dashboard structure:** 5 tabs — Overview, On/Off Ramp, Card Costs, Card Analytics, Clients
- **Card fee models:** 4 models compared (A/B/C/D) in `cards/analytics.py` for fee-model evaluation
- **Forecasting:** EWMA with 95% CI chosen for card demand projection (not ARIMA — insufficient data)
- **DB caching:** 1-hour `@st.cache_data` in dashboard; Parquet cache in `OnrampQueries`
- **PII masking:** Top-user tables mask user IDs in all reporting outputs

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
