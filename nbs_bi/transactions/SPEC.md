# SPEC — `nbs_bi.transactions`: Transaction Analytics

> Edit this file to request specific changes.

---

## Overview

Core transaction analytics for all card and crypto transactions. Provides KPIs, trend analysis, and segmentation to support operational and financial decisions.

---

## Planned Capabilities

- **Volume KPIs**: daily, weekly, monthly transaction counts and volumes (USD/BRL)
- **Success/failure rates**: authorization rate, decline rate by reason code
- **Segmentation**: by card type (Infinite/Platinum), channel (ApplePay/GooglePay/physical), geography (domestic/international/cross-border)
- **Trend analysis**: month-over-month growth, rolling averages
- **Top users/merchants**: concentration analysis

---

## Data Schema (TBD)

Expected columns from the database:
- `transaction_id`, `user_id`, `card_id`
- `amount_usd`, `amount_brl`, `fx_rate`
- `status` (approved/declined/reversed)
- `card_type` (infinite/platinum)
- `channel` (applepay/googlepay/tap/online)
- `is_cross_border` (bool)
- `is_3ds` (bool)
- `created_at`

---

## Open Questions

- [ ] What is the primary database source? PostgreSQL? Which schema/table?
- [ ] Is there a data warehouse or are we querying the operational DB directly?
- [ ] What currencies does NBS operate in (USD, BRL, USDC)?
