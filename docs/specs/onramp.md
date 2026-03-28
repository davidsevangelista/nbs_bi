# SPEC — `nbs_bi.onramp`: On/Off Ramp Analytics

> Edit this file to request specific changes. Each section is a capability.

---

## Overview

Analytics for BRL ⇄ USDC conversions (on/off ramp). NBS acts as principal on every conversion:

- **Onramp** (`brl_to_usdc`): client sends BRL → receives USDC. NBS **sells** USDC.
- **Offramp** (`usdc_to_brl`): client sends USDC → receives BRL. NBS **buys** USDC.

Revenue comes from the spread (implicit FX margin) and explicit fees embedded in the effective rate. This module provides volume KPIs, revenue analytics, PnL by position, FX rate tracking, and user-level segmentation — mirroring the analytics currently in `contabil_pipeline/scripts/dashboards/on_off_ramp.py`.

**Reference system:** `contabil_pipeline` repo — `PixDatabaseQueries`, `OnOffRampStoryReport`, `compute_ramp_pnl`.

---

## Data Source

**Database:** PostgreSQL — `READONLY_DATABASE_URL` (same connection used by `contabil_pipeline`).

**Primary table: `conversion_quotes`** (rows where `used = TRUE` only)

| Column | Type | Scale | Description |
|---|---|---|---|
| `id` | UUID | — | Quote primary key |
| `user_id` | UUID | — | Customer reference |
| `direction` | enum | — | `brl_to_usdc` or `usdc_to_brl` |
| `from_amount_brl` | int | ÷100 → BRL | Source BRL (centavos) |
| `from_amount_usdc` | int | ÷1e6 → USDC | Source USDC (micros) |
| `to_amount_brl` | int | ÷100 → BRL | Destination BRL (centavos) |
| `to_amount_usdc` | int | ÷1e6 → USDC | Destination USDC (micros) |
| `exchange_rate` | float | — | Quoted BRL/USDC rate |
| `effective_rate` | float | — | Actual rate applied (includes spread) |
| `fee_amount_brl` | int | ÷100 → BRL | Explicit fee in BRL |
| `fee_amount_usdc` | int | ÷1e6 → USDC | Explicit fee in USDC |
| `spread_revenue_brl` | int | ÷100 → BRL | Spread profit captured in BRL |
| `spread_revenue_usdc` | int | ÷1e6 → USDC | Spread profit captured in USDC |
| `spread_percentage` | float | — | Spread as % of notional |
| `processing_mode` | str | — | Processing type (e.g. instant, scheduled) |
| `conversion_request_id` | UUID | — | Parent request reference |
| `conversion_quote_id` | UUID | — | Quote ID for tracing |
| `ledger_transaction_id` | UUID | — | Ledger entry reference |
| `expires_at` | timestamp | — | Quote expiry |
| `created_at` | timestamp | — | Conversion timestamp |
| `updated_at` | timestamp | — | Last status update |

**Supporting tables:**

| Table | Query purpose |
|---|---|
| `pix_requests` | PIX deposits (BRL inflows) — for PIX IN / PIX OUT reporting |
| `pix_transfers` | PIX withdrawals (BRL outflows) |
| `users` | Customer metadata (KYC status, join date) |

---

## Derived Fields

These fields are computed on load, not stored in the DB:

| Derived field | Formula | Description |
|---|---|---|
| `volume_brl` | `ABS(from_amount_brl) + ABS(to_amount_brl)` | Total BRL moved per conversion |
| `volume_usdc` | `ABS(from_amount_usdc) + ABS(to_amount_usdc)` | Total USDC moved per conversion |
| `revenue_brl` | `fee_amount_brl + spread_revenue_brl` | Total BRL revenue per conversion |
| `revenue_usdc` | `fee_amount_usdc + spread_revenue_usdc` | Total USDC revenue per conversion |
| `side` | `onramp` if `brl_to_usdc`, else `offramp` | Business-facing direction label |
| `stock_out_usdc` | USDC sold by NBS (onramp rows) | NBS USDC inventory consumed |
| `stock_in_usdc` | USDC bought by NBS (offramp rows) | NBS USDC inventory replenished |

---

## Planned Capabilities

### 1. Volume KPIs
- Total conversions, total unique users, total BRL volume, total USDC volume
- Split by direction: onramp vs offramp
- Daily, weekly, monthly aggregations

### 2. Revenue Analytics
- Total revenue (BRL + USDC) by direction and period
- Revenue per conversion and per user
- Spread % distribution (mean, p10, p90) by direction

### 3. FX Rate Tracking
- Effective rate time series by direction (daily mean, p10, p90)
- Implicit margin vs reference rate (when available)

### 4. Position & PnL
- Running NBS USDC position: `stock_in_usdc - stock_out_usdc`
- Weighted average cost (PM) of USDC inventory
- PnL per period:
  - Onramp PnL = `(sell_rate - PM_before) × stock_out_usdc`
  - Offramp PnL = `(PM_before - buy_rate) × stock_in_usdc`
  - Margin % = `(rate - PM) / PM × 100`

### 5. User Segmentation
- Top users by volume (BRL/USDC)
- First-time vs repeat conversion users
- Active user counts (daily / monthly)
- Cohort analysis: retention of converting users by first-conversion month

### 6. PIX Flow Analytics
- PIX IN (deposits via `pix_requests`) vs PIX OUT (withdrawals via `pix_transfers`) in BRL
- Net PIX position per period
- Unique PIX senders/receivers

---

## Planned Interface

```python
from nbs_bi.onramp.queries import OnrampQueries
from nbs_bi.onramp.models import OnrampModel
from nbs_bi.onramp.report import OnrampReport

# Load conversions from DB (caches as parquet via DB_CACHE_DIR)
q = OnrampQueries()
df = q.conversions(start_date="2026-01-01", end_date="2026-03-31")

# Build analytics model
model = OnrampModel(df)

# Volume KPIs
kpis = model.kpis()
# → {total_conversions, unique_users, volume_brl, volume_usdc,
#    revenue_brl, revenue_usdc} split by side and period

# FX rate stats
fx = model.fx_stats(freq="D")
# → DataFrame[date, side, mean_rate, p10_rate, p90_rate]

# Position and PnL
position = model.position()
# → DataFrame[date, stock_in_usdc, stock_out_usdc, net_usdc, pm_brl_per_usdc,
#             pnl_brl, margin_onramp_pct, margin_offramp_pct]

# User segmentation
top_users = model.top_users(n=20, metric="volume_brl")
active = model.active_users(freq="D")

# Aggregated report (mirrors OnOffRampStoryReport)
report = OnrampReport(q)
summary = report.build(start_date="2026-01-01", end_date="2026-03-31")
# → summary, conv_daily, pix_daily, fx_stats, active_daily, top_users DataFrames
```

---

## Module Structure

```
nbs_bi/onramp/
├── __init__.py
├── queries.py       ← DB queries against conversion_quotes, pix_requests, pix_transfers
├── models.py        ← OnrampModel: KPIs, FX stats, position, PnL, user segmentation
└── report.py        ← OnrampReport: aggregated multi-DataFrame report (mirrors OnOffRampStoryReport)
```

---

## Database Connection

Uses `READONLY_DATABASE_URL` from `nbs_bi.config` (same value as `contabil_pipeline/.env`).
Queries are cached as Parquet files using `DB_CACHE_DIR`.

```python
# nbs_bi/config.py additions needed:
READONLY_DATABASE_URL = os.environ["READONLY_DATABASE_URL"]
DB_CACHE_DIR = os.environ.get("DB_CACHE_DIR", ".cache/db")
```

---

## Open Questions

- [ ] Should `nbs_bi.onramp.queries` reuse `PixDatabaseQueries` from `contabil_pipeline` directly (as a dependency), or reimplement the queries independently?
- [ ] Is `effective_rate` always populated, or do some rows require fallback to `exchange_rate`? (The `contabil_pipeline` has `_ensure_effective_rate()` — port this logic?)
- [ ] Is `spread_percentage` always populated in the DB, or must it be derived from `spread_revenue_brl / volume_brl`?
- [ ] What is `processing_mode`? What are the distinct values and do we need to filter by it?
- [ ] Should PIX IN/OUT analytics be part of this module or a separate `nbs_bi.pix` module?
- [ ] What reference FX rate should be used for margin benchmarking (BCB PTAX, open market, etc.)?
- [ ] Are there cancelled/expired quotes that sneak into `used = TRUE` rows? Need a secondary status filter?
