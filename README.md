# NBS Business Intelligence (`nbs_bi`)

Python business intelligence and cost simulation platform for **Neobankless Brasil LTDA**.

Provides modular analytics for each business domain: card operations, transactions, on/off ramp, swaps, and AI usage — with a cost simulation engine to support financial projections and operational decisions.

---

## Quickstart

```bash
# Clone and install in editable mode
git clone <repo>
cd nbs_bi
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Copy and configure environment
cp .env.example .env
# edit .env with your credentials

# Run card cost simulation
python -m nbs_bi.cards.simulator

# Run tests
pytest tests/ -v --cov=nbs_bi
```

---

## Architecture

```
nbs_bi/                         ← repo root
│
├── nbs_bi/                     ← installable Python package
│   ├── config.py               ← global settings (loaded from .env)
│   ├── cards/                  ← card cost center simulation
│   │   ├── invoice_parser.py   ← parse Rain invoice line items
│   │   ├── models.py           ← cost model: fixed + variable + linear regression
│   │   └── simulator.py        ← scenario engine: sensitivity, projections, cost/tx
│   ├── transactions/           ← transaction analytics & KPIs
│   ├── onramp/                 ← on/off ramp (buy/sell crypto) analytics
│   ├── swaps/                  ← DEX/swap analytics
│   ├── ai_usage/               ← AI interaction cost & usage analytics
│   └── reporting/              ← cross-module dashboards, exports, projections
│
├── docs/
│   ├── PROGRESS.md             ← task tracking (update when tasks complete)
│   ├── specs/                  ← feature specs — edit to request changes
│   │   ├── cards.md
│   │   ├── transactions.md
│   │   ├── onramp.md
│   │   ├── swaps.md
│   │   ├── ai_usage.md
│   │   └── reporting.md
│   └── dev/
│       ├── scaffold_project.md ← developer workflow guide
│       └── new_project_prompt.md ← bootstrap template for new projects
│
├── data/
│   └── invoices/               ← raw invoice PDFs/CSVs (gitignored)
│
├── notebooks/                  ← exploratory Jupyter notebooks (clear output before commit)
│
├── tests/                      ← pytest test suite
│   └── cards/
│
├── pyproject.toml              ← packaging, deps, tooling config
├── CLAUDE.md                   ← AI assistant standards and conventions
└── CHANGELOG.md                ← version history
```

---

## Modules

### `nbs_bi.cards` — Card Cost Simulation

Models the full cost structure of the Rain card program. Inputs: invoice line items (qty, unit price). Outputs:

- **Total cost breakdown** by fee category
- **Weighted average cost per transaction**
- **Sensitivity analysis**: which levers move the needle most
- **Scenario simulation**: what-if (volume, card mix, cross-border ratio)
- **Linear projection model**: estimate next month's cost given expected metrics

Key insight from Feb 2026 invoice (6,885 transactions, $6,693.58 total):
- **Cost per transaction: ~$0.972**
- Largest cost drivers: Visa Infinite product type ($1,502.80), Compliance/Share Token ($968.75), Base Fee ($1,000)

### `nbs_bi.transactions`

Core transaction analytics: volumes, patterns, KPIs, daily/monthly trends.

### `nbs_bi.onramp`

On and off-ramp analytics: buy/sell crypto volumes, conversion rates, revenue, user funnel.

### `nbs_bi.swaps`

DEX/swap analytics: swap volumes, token pairs, spread, slippage, revenue.

### `nbs_bi.ai_usage`

AI interaction analytics: usage volume, cost per interaction, feature adoption, model cost breakdown.

### `nbs_bi.reporting`

Cross-module dashboards: consolidated cost center view, monthly projections, CSV/Excel exports.

---

## Development

```bash
# Lint
ruff check nbs_bi/ tests/

# Format
ruff format nbs_bi/ tests/

# Test with coverage
pytest tests/ -v --cov=nbs_bi --cov-report=term-missing
```

See [CLAUDE.md](CLAUDE.md) for full coding standards and conventions.
See [docs/PROGRESS.md](docs/PROGRESS.md) for current task status.
See [docs/dev/scaffold_project.md](docs/dev/scaffold_project.md) for the full developer workflow.
