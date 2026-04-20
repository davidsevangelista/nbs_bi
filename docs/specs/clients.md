# SPEC — `nbs_bi.clients`: Client Revenue & Behaviour Analysis

> Edit this file to request specific changes.

---

## Purpose

Answer the questions that drive growth decisions:

1. **Who generates the most revenue, and what do they look like?**
2. **Which clients use only one product — and could use more?**
3. **Who is churning, and can we catch them early?**
4. **What is the income/demographic profile of my best clients?**
5. **Which acquisition channel produces the most valuable users?**
6. **Who are the Founders Club members, and are they pulling their weight?**

This is not a research module. Every output maps directly to a business action: call a VIP, run a re-engagement campaign, adjust product targeting, decide which income segment to acquire next.

---

## Data Sources

All tables from the NBS production read-only database (`READONLY_DATABASE_URL`).

### User Identity & Profile

| Table | What it contributes |
|---|---|
| `users` | Account status, KYC level, signup date, last active date |
| `user_profiles` | `country_code`, `preferred_currency`, `onboarding_completed`, `experimental_features` (feature flags: `rain_cards`, etc.) |
| `cpf_validation_data` | Income estimate, credit score, purchasing power tier, occupation (CBO), education, gender, DOB — from Receita Federal enrichment |
| `kyc_verifications` | SumSub result (`review_answer`: GREEN/RED), `reject_labels`, attempt count — KYC quality signal |
| `kyc_levels` | Tier limits: `daily_limit_brl`, `daily_limit_usdc`, `per_operation_limit_brl` — capacity signal |

### Acquisition & Network

| Table | What it contributes |
|---|---|
| `user_registrations` | `source_type`, `attributed_referral_code_id` — acquisition channel |
| `referral_codes` | `commission_rate_basis_points`, `agent_share_basis_points`, `parent_distributor_id` — referral hierarchy |
| `founders` | `founder_number`, `invites_remaining`, `network_size` — Founders Club status |
| `teams` / `team_members` | Distributor/agent hierarchy, `monthly_target_brl` — sales network context |

### Revenue Sources

| Table | What it contributes |
|---|---|
| `conversion_quotes` | Onramp/offramp revenue per user (fees + spread) |
| `card_transactions` | Card spend volume per user; authorization rate |
| `cards` | `card_variant` (founder/basic), card limits, `last_four` — card tier per user |
| `swap_transactions` | Swap volume per user |
| `swap_fee_events` | Granular fee events from Jupiter swaps (signature, mint, amount) — more precise than swap_transactions |
| `card_annual_fees` | Annual card fee paid per user (revenue item) |
| `unblockpay_payouts` | International USDC→fiat withdrawals: `fiat_currency`, `final_fiat_amount`, `unblockpay_fee` — international revenue |

### Cost Items

| Table | What it contributes |
|---|---|
| `cashback_rewards` | Cashback paid out per user (cost item) |
| `revenue_share_rewards` | Revenue sharing paid per user (cost item) |

### Activity Signals

| Table | What it contributes |
|---|---|
| `pix_requests` / `pix_transfers` | PIX deposit/withdrawal activity per user |
| `user_wallets` | Chains the user has a wallet on (on-chain breadth) |
| `user_conversion_usage` | Daily conversion limit usage per user — proxy for ceiling-constrained power users |
| `ai_interactions` / `ai_sessions` | AI feature usage per user (session count, message count, processing time) — product adoption signal |

---

## Per-User Revenue Model

For each user, compute a **total revenue score** as the sum of revenue items minus cost items in a given period:

```
revenue_brl_per_user =
    + onramp_spread_brl          (conversion_quotes: spread_revenue_brl where brl_to_usdc)
    + offramp_spread_brl         (conversion_quotes: spread_revenue_brl where usdc_to_brl)
    + onramp_fees_brl            (conversion_quotes: fee_amount_brl)
    + card_annual_fee_usd        (card_annual_fees: amount_usdc × fx_rate)
    + swap_platform_fee_usd      (swap_fee_events: amount by platform fee mint)
    + unblockpay_fee_usd         (unblockpay_payouts: unblockpay_fee in USDC)
    − cashback_paid_usd          (cashback_rewards: reward_usd_value)
    − revenue_share_paid_usd     (revenue_share_rewards: reward_usd_value)
```

> Note: card cost (Rain invoice) is a pool cost, not per-user. Allocate it as:
> `card_cost_allocated_usd = total_card_cost_usd × (user_n_card_txs / total_n_card_txs)`

---

## Client Profile (per user)

Joined from `users` + `user_profiles` + `cpf_validation_data`:

| Field | Source | Use |
|---|---|---|
| `user_id` | users | Join key |
| `status` | users | Active / suspended / pending |
| `kyc_level` | users | KYC tier |
| `signup_date` | users.created_at | Tenure |
| `last_active_at` | users.last_active_at | Recency |
| `days_since_last_active` | computed | Churn signal |
| `country_code` | user_profiles | Geographic segment |
| `preferred_currency` | user_profiles | Product preference |
| `onboarding_completed` | user_profiles | Funnel completion |
| `card_variant` | cards | founder / basic tier |
| `is_founder` | founders (join) | Founders Club membership |
| `founder_network_size` | founders | Referral network depth |
| `acquisition_source` | user_registrations.source_type | Channel attribution |
| `referral_code_id` | user_registrations.attributed_referral_code_id | Referral attribution |
| `kyc_result` | kyc_verifications.review_answer | GREEN / RED |
| `kyc_limit_daily_brl` | kyc_levels | Max daily capacity |
| `estimated_income_brl` | cpf_validation_data.raw_renda | Income segment |
| `purchasing_power_tier` | cpf_validation_data.raw_faixa_poder_aquisitivo | Segment label |
| `credit_score` | cpf_validation_data.raw_score | Risk/quality signal |
| `occupation_cbo` | cpf_validation_data.raw_cbo | Professional segment |
| `education_level` | cpf_validation_data.raw_escolaridade | Demographic |
| `gender` | cpf_validation_data.raw_sexo | Demographic |
| `age` | derived from cpf_validation_data.data_nascimento | Demographic |
| `n_wallets` | user_wallets | On-chain activity breadth |
| `wallet_chains` | user_wallets.chain | Which chains (Solana, ETH, etc.) |

---

## Planned Capabilities

### 1. Revenue Leaderboard
Top N clients by total revenue in a period, with product breakdown.
- Columns: user_id (masked), revenue_brl, onramp_rev, swap_rev, card_fee, unblockpay_fee, cashback_cost, net_rev
- **Action**: identify VIPs to call or protect from churn

### 2. Product Adoption Matrix
For each user: which products have they used in the last 90 days?

| user_id | onramp | offramp | card | swap | pix_deposit | pix_withdrawal | unblockpay | ai_feature |
|---|---|---|---|---|---|---|---|---|
| u1 | ✓ | ✗ | ✓ | ✗ | ✓ | ✓ | ✗ | ✓ |

Aggregated view: % of users active in each product combination.
- **Action**: identify largest cross-sell gaps (e.g., "60% of card users have never done onramp")

### 3. Client Segments
Classify each user into one of four segments based on recency + revenue:

| Segment | Criteria | Action |
|---|---|---|
| **Champion** | Active last 30d AND top 20% revenue | Protect, upsell |
| **Active** | Active last 30d AND lower revenue | Grow usage |
| **At-Risk** | Active 31–90d ago | Re-engagement campaign |
| **Dormant** | No activity > 90d | Low priority or win-back |

### 4. Acquisition Channel Analysis
Group users by `source_type` (from `user_registrations`) and compute:
- Avg revenue per user by channel
- Conversion rate: registered → KYC verified → first transaction
- **Action**: which channel produces the most valuable users? Shift acquisition budget there.

### 5. Founders Club Report
For all users in `founders` table:
- Revenue generated vs. non-founders
- Network size vs. revenue (do larger networks produce more revenue?)
- `invites_remaining` as a call-to-action proxy (high unused invites = under-leveraged)
- **Action**: identify top-performing founders for ambassador program; flag inactive founders for re-engagement

### 6. Income Band Analysis
Group users by `purchasing_power_tier` (from CPF data) and compute:
- Avg revenue per user by band
- Avg n_products per band
- % KYC verified per band
- **Action**: understand which income segment has the best unit economics; guide acquisition targeting

### 7. Occupation / Professional Segment
Group users by `occupation_cbo` (CBO code) and compute revenue metrics.
- Top 10 occupations by avg revenue per user
- **Action**: which professional categories over-index? Focus acquisition there.

### 8. Churn Signal
For each user, compute `days_since_last_active`. Flag users with:
- 30–60 days inactive + revenue > median → at-risk VIPs
- 60–90 days inactive + multi-product → worth re-engaging
- Users hitting daily conversion limits regularly → ceiling-constrained, offer KYC upgrade
- **Action**: export list for outreach

### 9. Cohort LTV
Monthly cohorts (by signup month): track cumulative revenue per user over M+1, M+2, … M+N.
- Split by acquisition channel (organic vs. referral vs. founder invite)
- **Action**: understand how long it takes a user to become profitable; adjust CAC budget accordingly

### 10. Geographic & Currency Mix
Group by `country_code` and `preferred_currency`:
- Revenue concentration by country
- % of users using `unblockpay` (international withdrawal signal)
- **Action**: are international users worth a dedicated product push?

---

## Module Structure

```
nbs_bi/clients/
├── __init__.py
├── queries.py        ← fetches users, CPF data, all revenue/cost tables, founders, registrations
├── models.py         ← ClientModel: revenue per user, profile join, segmentation
└── segments.py       ← segment logic: champion/active/at-risk/dormant, cohort LTV, founder report
```

---

## Planned Interface

```python
from nbs_bi.clients import ClientModel

model = ClientModel(start_date="2026-01-01", end_date="2026-03-31")

# Revenue leaderboard
top = model.revenue_leaderboard(n=50)
# → DataFrame[user_id, revenue_brl, onramp_rev, swap_rev, card_fee_allocated, unblockpay_fee, ...]

# Product adoption
adoption = model.product_adoption()
# → DataFrame[user_id, has_onramp, has_offramp, has_card, has_swap, has_unblockpay, has_ai, n_products]

# Client segments
segments = model.segments()
# → DataFrame[user_id, segment, last_active_at, revenue_brl, n_products]

# Founders Club
founders = model.founders_report()
# → DataFrame[user_id, founder_number, network_size, invites_remaining, revenue_brl, n_products]

# Acquisition channel breakdown
channels = model.acquisition_summary()
# → DataFrame[source_type, n_users, n_kyc_verified, n_transacting, avg_revenue_brl]

# Income band breakdown
income_bands = model.income_band_summary()
# → DataFrame[purchasing_power_tier, n_users, avg_revenue_brl, avg_n_products, pct_kyc_verified]

# Occupation analysis
occ = model.occupation_summary(top_n=10)
# → DataFrame[occupation_cbo, n_users, avg_revenue_brl, median_revenue_brl]

# Churn signals — users to contact this week
at_risk = model.at_risk_users(min_revenue_brl=100, inactive_days_min=30, inactive_days_max=90)
# → DataFrame[user_id, days_since_active, revenue_brl, n_products, estimated_income_brl]

# Cohort LTV
ltv = model.cohort_ltv()
# → pivot: cohort_month × M+N → avg cumulative revenue per user
```

---

## Privacy Notes

- `user_id` is always a UUID — never expose `email`, `full_name`, `cpf`, or `phone` in dashboard displays.
- CPF enrichment data (`raw_renda`, `raw_score`, etc.) is used only in aggregate. Individual income values must not appear in any shareable export.
- At-risk user lists for outreach must be handled by ops team directly — never embed PII in report files.
- `referral_codes` hierarchy may expose distributor structures — treat as internal-only.

---

## Resolved Decisions

| Question | Resolution |
|---|---|
| FX rate for BRL → USD | `PERCENTILE_CONT(0.5)` of `effective_rate` from `conversion_quotes WHERE used=TRUE AND direction='brl_to_usdc'` over the analysis window. `amount_usd = amount_brl / fx_rate`. |
| `card_annual_fees.amount_usdc` units | Real USDC (not micros) — no divisor needed. |
| Swap revenue source | `swap_transactions.platform_fee_bps × input_amount / 10_000 / 1_000_000`. `swap_fee_events` has 0 rows. |
| `unblockpay_fee` | NBS revenue (not a pass-through cost). |
| Cashback treatment | Pure cost — deducted from net revenue. |
| Attribution fallback | `COALESCE(ur.source_type, CASE WHEN f.invite_code IS NOT NULL AND f.invite_code <> '' THEN 'founder_invite' ELSE 'unknown' END)`. Only ~627 of ~11,058 users have `user_registrations` rows. |
| Card cost allocation | Rain invoice total passed as constructor param (default `$6,693.58` for Feb 2026). `card_cost_allocated_usd = invoice_total × (user_tx_count / total_tx_count)`. |
| `billing_charges` | Actual per-user card tx fee revenue (NBS charges users). Covers 2026-04-03+. `amount` is USDC micros ÷ 1,000,000. Fee codes: `txn_fixed`, `txn_percentage` (legacy) and `TXN_FEE_FIXED_*`, `TXN_FEE_PCT_*` (new). Filter `WHERE status = 'settled'`. For periods before 2026-04-03, rely on Rain invoice pro-rata only. |
| CPF enrichment coverage | Unknown — verify at query time; `cpf_validation_data` may have 0 rows. |
| `source_type` enum | Query at runtime — values not fully documented. |

---

## LTV / CAC Analysis

### Cohort Definition

Cohorts are defined by signup month: `DATE_TRUNC('month', users.created_at)`.

### LTV Metric

`net_revenue_usd` per user = sum of all revenue streams minus costs:

```
net_revenue_usd =
    + onramp_revenue_usd          (conversion_quotes: SUM(fee_amount_brl + spread_revenue_brl) / 100 / fx_rate)
    + card_fee_usd                (card_annual_fees: SUM(amount_usdc) — already real USDC)
    + card_tx_fee_usd             (billing_charges: SUM(amount) / 1_000_000 WHERE status='settled')
    + swap_fee_usd                (swap_transactions: SUM(input_amount / 1e6 × platform_fee_bps / 10000))
    + payout_fee_usd              (unblockpay_payouts: SUM(unblockpay_fee) WHERE status='completed')
    − cashback_usd                (cashback_rewards: SUM(reward_usd_value) WHERE status='completed')
    − revenue_share_paid_usd      (revenue_share_rewards: SUM(reward_usd_value) WHERE status='completed', by source_user_id)
    − card_cost_allocated_usd     (Rain invoice pro-rata: invoice_total × user_tx_count / total_tx_count)
```

### Cohort Matrix

`cohort_month × months_since_signup → avg cumulative net_revenue_usd, n_users_in_cohort`

- `months_since_signup = 0` = the signup month itself.
- Revenue is accumulated from onramp monthly time-series (full history, no date filter).
- Card fees, swap fees, billing charges are period-summed and attached at point-in-time.

### CAC Breakeven

`payback_months = first M where avg_cumulative_ltv(M) ≥ cac_usd`

Used in the dashboard as a slider: input any CAC value → see payback period and ROI per cohort and acquisition source.

### Attribution Inference Rule

1. If `user_registrations` row exists → use `source_type` directly.
2. Else if `founders.invite_code IS NOT NULL AND invite_code <> ''` → `'founder_invite'`
3. Else → `'unknown'`

### JSON API Export

`ClientReport.to_json_api()` converts all DataFrames to `{"key": [records], ...}` for consumption by future non-Python dashboards.
