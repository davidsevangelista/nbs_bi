# Developer Workflow — NBS BI

This document describes how to set up the project and the expected development workflow for contributing to `nbs_bi`.

---

## 1. Initial Setup

```bash
# Clone and enter the repo
git clone <repo-url>
cd nbs_bi

# Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Copy environment template and fill in secrets
cp .env.example .env
# Edit .env — never commit this file
```

---

## 2. Project Structure

```
nbs_bi/                   # Main package
  cards/                  # Card cost simulation
  transactions/           # Transaction analytics
  onramp/                 # On/off ramp analytics
  swaps/                  # Swap analytics
  ai_usage/               # AI usage tracking
  reporting/              # Cross-module reports
  config.py               # Shared config (loaded from .env)

tests/                    # Mirrors nbs_bi/ structure
  cards/
  transactions/
  ...

data/                     # Gitignored — raw and processed data
  invoices/
  processed/

docs/                     # Project documentation
  PROGRESS.md             # Phase tracker — update when tasks complete
  specs/                  # Module-level feature specs
    cards.md
    transactions.md
    onramp.md
    swaps.md
    ai_usage.md
    reporting.md
  dev/
    scaffold_project.md   # This file

notebooks/                # Exploratory notebooks (output must be cleared before commit)
```

---

## 3. Adding a New Module

When a new module is needed (e.g., `nbs_bi.fx`):

1. Create the directory: `nbs_bi/fx/`
2. Add `__init__.py` with a module docstring
3. Create a spec at `docs/specs/fx.md` — define capabilities and schema before writing code
4. Add a `PROGRESS.md` phase entry for the new module
5. Create the test directory: `tests/fx/`
6. Implement, test, lint — then commit

---

## 4. Development Cycle

For each task in `docs/PROGRESS.md`:

```bash
# 1. Branch
git checkout -b feat/<module>/<short-desc>

# 2. Write code in nbs_bi/<module>/
# 3. Write tests in tests/<module>/

# 4. Verify
pytest tests/ -v --cov=nbs_bi
ruff check nbs_bi/ tests/
ruff format nbs_bi/ tests/

# 5. Update docs
#    - Mark task [x] in docs/PROGRESS.md
#    - Add entry to CHANGELOG.md

# 6. Commit
git add <specific files>
git commit -m "feat(cards): implement cost_breakdown method"

# 7. Open PR against main
```

---

## 5. Working with Specs

Each `docs/specs/<module>.md` file is the **source of truth** for what a module should do. Before implementing anything:

- Read the spec to understand the planned interface and data schema
- Open questions in the spec must be answered before implementation begins
- If you discover something new during implementation, update the spec first, then code

To request a new capability: edit the spec file directly. Claude Code reads specs before making changes.

---

## 6. Data Files

```
data/invoices/       → raw invoice JSON files (e.g. Invoice-NKEMEJLO-0008-actuals.json)
data/processed/      → cached or transformed outputs
```

Neither directory is committed to git. Place fixture data used in tests under `tests/fixtures/` (committed, anonymized, small).

---

## 7. Environment Variables

All secrets and environment-specific config live in `.env`. The pattern in code:

```python
# nbs_bi/config.py
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
```

Never access `os.environ` outside of `config.py`. Import config values from `nbs_bi.config`.

---

## 8. Pre-Commit Checklist

Before every commit, confirm:

- [ ] `pytest tests/ -v` passes
- [ ] `ruff check nbs_bi/ tests/` passes (zero errors)
- [ ] `ruff format nbs_bi/ tests/` applied
- [ ] `docs/PROGRESS.md` updated for completed tasks
- [ ] `CHANGELOG.md` updated if the change is user-facing
- [ ] Notebook output cells cleared (if any notebooks changed)
- [ ] No `.env`, `data/`, or secrets staged
