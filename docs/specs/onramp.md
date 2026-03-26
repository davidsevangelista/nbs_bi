# SPEC — `nbs_bi.onramp`: On/Off Ramp Analytics

> Edit this file to request specific changes.

---

## Overview

Analytics for fiat ↔ crypto conversion (buy/sell). Tracks user funnel, volumes, revenue, and provider costs.

---

## Planned Capabilities

- **Volume KPIs**: monthly on-ramp (fiat → crypto) and off-ramp (crypto → fiat) volumes
- **Conversion funnel**: initiated → KYC passed → payment processed → completed
- **Revenue**: spread earned per transaction, fees charged to users
- **Provider costs**: if using a third-party on-ramp provider, track their fees
- **User segmentation**: first-time vs. repeat, average order size
- **Geography**: BRL/USD breakdown, regional distribution

---

## Data Schema (TBD)

Expected columns:
- `ramp_id`, `user_id`
- `direction` (onramp/offramp)
- `fiat_amount`, `fiat_currency`
- `crypto_amount`, `crypto_asset`
- `exchange_rate`, `spread_pct`
- `provider`, `provider_fee_usd`
- `status` (initiated/completed/failed/refunded)
- `created_at`, `completed_at`

---

## Open Questions

- [ ] Which on-ramp provider(s) is NBS using?
- [ ] Is there a separate fee structure for BRL on-ramps vs. USD?
- [ ] What is the target spread margin?
