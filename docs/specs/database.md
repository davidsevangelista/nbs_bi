# SPEC — NBS Production Database Schema

> Read-only access via `READONLY_DATABASE_URL` (Neon PostgreSQL, AWS sa-east-1).
> This document is the authoritative reference for all table schemas used in `nbs_bi`.
> Last updated: 2026-03-27. 72 tables total.

---

## Monetary Scaling Rules

| Pattern | Storage | Scale factor | Real unit |
|---|---|---|---|
| `*_brl` bigint | centavos | ÷ 100 | BRL |
| `*_usdc` bigint | micros | ÷ 1,000,000 | USDC |
| `*_usdc` numeric | already real | × 1 | USDC |
| `amount` bigint (cards) | USD cents | ÷ 100 | USD |
| `amount` bigint (swap) | token micros | ÷ 1,000,000 | token unit |
| `fee_lamports` bigint | lamports | ÷ 1,000,000,000 | SOL |

> Always check column type (`bigint` vs `numeric`) before dividing. `numeric` columns are already in real units.

---

## Table Index by Domain

| Domain | Tables |
|---|---|
| [Users & Identity](#users--identity) | `users`, `user_profiles`, `user_roles`, `user_wallets`, `user_addresses`, `user_documents`, `crypto_addresses` |
| [KYC & Compliance](#kyc--compliance) | `kyc_verifications`, `kyc_levels`, `user_conversion_usage`, `sumsub_webhook_logs` |
| [CPF Enrichment](#cpf-enrichment) | `cpf_validation_data` |
| [Acquisition & Network](#acquisition--network) | `user_registrations`, `referral_codes`, `founders`, `invites`, `teams`, `team_members`, `team_audit_log` |
| [Cards](#cards) | `cards`, `card_transactions`, `card_transaction_holds`, `card_annual_fees`, `card_waitlist`, `card_upgrade_operations`, `card_transaction_receipts`, `tapguard_cards` |
| [Conversions (On/Off Ramp)](#conversions-onoff-ramp) | `conversion_quotes`, `conversion_requests`, `quote_instant_details` |
| [PIX (BRL Payments)](#pix-brl-payments) | `pix_requests`, `pix_transfers`, `payment_state_transitions`, `transfer_state_transitions`, `payment_webhooks`, `payment_providers` |
| [Swaps](#swaps) | `swap_transactions`, `swap_fee_events` |
| [Rewards & Revenue Share](#rewards--revenue-share) | `cashback_rewards`, `revenue_share_rewards` |
| [Unblockpay (International)](#unblockpay-international-payouts) | `unblockpay_payouts`, `unblockpay_quotes`, `unblockpay_bank_accounts`, `unblockpay_customers` |
| [AI Features](#ai-features) | `ai_interactions`, `ai_sessions`, `user_ai_quotas`, `ai_audit_logs` |
| [Ledger / Accounting](#ledger--accounting) | `accounts_v2`, `entries_v2`, `account_balances`, `account_holds`, `transactions_v2`, `accounts`, `entries`, `transactions` |
| [Blockchain / Solana](#blockchain--solana) | `solana_sponsored_transactions`, `solana_user_quotas` |
| [Notifications](#notifications) | `notification_events`, `notification_preferences`, `notification_templates`, `notification_batches`, `device_tokens` |
| [Infrastructure](#infrastructure) | `event_outbox`, `webhook_queue`, `webhook_performance_metrics`, `webhook_processing_status`, `orphan_webhooks`, `orphan_webhooks_summary`, `hold_creation_failures`, `user_payment_accounts`, `_sqlx_migrations` |

---

## Users & Identity

### `users`
*~11,058 rows* — Master user table. One row per registered account.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK — join key used everywhere |
| `privy_did` | varchar | Auth provider DID |
| `email` | varchar | PII — never expose in reports |
| `email_verified` | boolean | |
| `phone` | varchar | PII |
| `phone_verified` | boolean | |
| `full_name` | varchar | PII |
| `status` | enum | `active`, `suspended`, `pending` (USER-DEFINED) |
| `kyc_level` | integer | FK → `kyc_levels.level` |
| `created_at` | timestamptz | Signup date |
| `updated_at` | timestamptz | |
| `last_active_at` | timestamptz | Last recorded activity |
| `rain_application_id` | text | Rain card program linkage |
| `account_type` | varchar | `personal` / `business` |
| `company_name` | varchar | Business accounts only |
| `company_registration_number` | varchar | Business accounts only |

---

### `user_profiles`
*~10,804 rows* — Extended profile. One row per user (optional).

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | FK → `users.id` |
| `display_name` | varchar | |
| `avatar_url` | text | |
| `country_code` | char(3) | ISO 3166-1 alpha-3 |
| `language` | varchar | Preferred language code |
| `preferred_currency` | varchar | `BRL`, `USD`, `USDC` |
| `onboarding_completed` | boolean | Funnel completion flag |
| `experimental_features` | jsonb | Feature flags, e.g. `{"rain_cards": true}` |
| `tag` | varchar | User-facing handle / tag |

---

### `user_roles`
*~264 rows* — Role assignments (admin, ops, etc.).

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | FK → `users.id` |
| `role` | enum | USER-DEFINED role type |
| `granted_at` | timestamptz | |
| `granted_by` | uuid | |
| `expires_at` | timestamptz | Nullable |

---

### `user_wallets`
*~540 rows* — On-chain wallet addresses per user.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `users.id` |
| `chain` | varchar | `solana`, `ethereum`, etc. |
| `address` | varchar | Wallet address |
| `is_primary` | boolean | |

---

### `user_addresses`
*~278 rows* — Physical mailing addresses.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `street_line_1/2` | varchar | |
| `city`, `state_province`, `postal_code` | varchar | |
| `country_code` | char | ISO |
| `address_type` | varchar | `residential`, `business` |
| `is_primary` | boolean | |
| `verified` | boolean | |

---

### `user_documents`
*~10,352 rows* — Identity documents (passports, RG, CNH).

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `document_type` | varchar | `passport`, `cpf`, `cnpj`, etc. |
| `document_number` | varchar | PII |
| `country_code` | char | |
| `expiry_date` | date | |
| `verified` | boolean | |
| `validation_source` | text | Source system |

---

### `crypto_addresses`
*~21 rows* — Saved recipient crypto addresses (address book).

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `label` | varchar | User-defined label |
| `address` | varchar | On-chain address |
| `network` | varchar | Chain identifier |
| `memo` | varchar | Optional memo/tag |
| `last_used_at` | timestamptz | |

---

## KYC & Compliance

> **KYB note:** There is no separate KYB (Know Your Business) table. Business verification reuses `kyc_verifications` with `applicant_type = 'company'`. Filter on this field to isolate business applicant flows.

### `kyc_verifications`
*~8,958 rows* — SumSub KYC/KYB check results. Covers both individual (KYC) and business (KYB) applicants — `applicant_type` distinguishes them.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `users.id` |
| `sumsub_applicant_id` | varchar | SumSub applicant reference |
| `external_user_id` | varchar | SumSub external user reference |
| `status` | varchar | `pending`, `completed`, `failed` |
| `review_answer` | varchar | `GREEN` (pass) / `RED` (fail) |
| `reject_type` | varchar | Rejection category |
| `reject_labels` | array | Specific rejection reasons |
| `applicant_type` | varchar | `individual` (KYC) / `company` (KYB) |
| `account_id` | uuid | FK → `accounts_v2.id` (nullable) |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |
| `completed_at` | timestamptz | |

---

### `kyc_levels`
*~4 rows (config table)* — KYC tier definitions and limits.

| Column | Type | Notes |
|---|---|---|
| `level` | integer | PK (0, 1, 2, 3…) |
| `name` | varchar | Tier label |
| `description` | text | Human-readable tier description |
| `daily_limit_brl` | bigint | centavos ÷ 100 = BRL |
| `daily_limit_usdc` | bigint | micros ÷ 1,000,000 = USDC |
| `per_operation_limit_brl` | bigint | centavos |
| `per_operation_limit_usdc` | bigint | micros |
| `required_documents` | array | Doc types required for this level |
| `features_enabled` | array | Feature flags unlocked |

---

### `user_conversion_usage`
*~4,814 rows* — Daily conversion limit tracking per user.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `usage_date` | date | |
| `daily_usage_brl` | bigint | centavos |
| `daily_usage_usdc` | bigint | micros |

> Users hitting their daily ceiling regularly are KYC-upgrade candidates.

---

### `sumsub_webhook_logs`
*~22,607 rows* — Raw SumSub webhook events (audit trail, not for analytics).

---

## CPF Enrichment

### `cpf_validation_data`
*~2,731 rows* — Receita Federal CPF enrichment. One row per user.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | FK → `users.id` |
| `validated_at` | timestamptz | |
| `nome` | text | PII — full name from Receita |
| `cpf` | text | PII — masked in reports |
| `data_nascimento` | text | DOB string — derive age |
| `sexo` | text | `M` / `F` |
| `status_receita_federal` | text | CPF status |
| `consta_obito` | text | Death flag |
| `raw_data` | jsonb | Full raw response including: |
| | | `raw_renda` — estimated annual income BRL |
| | | `raw_faixa_poder_aquisitivo` — purchasing power tier |
| | | `raw_score` — credit score |
| | | `raw_cbo` — occupation code |
| | | `raw_escolaridade` — education level |
| `validation_source` | text | Source system |

> All CPF-derived analytics are aggregate only. Individual values must never appear in exports.

---

## Acquisition & Network

### `user_registrations`
*~627 rows* — One row per user signup with attribution data.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | FK → `users.id` |
| `source_type` | varchar | Acquisition channel (organic, referral, founder_invite, etc.) |
| `attributed_referral_code_id` | uuid | FK → `referral_codes.id` |
| `invited_by_user_id` | uuid | Direct inviter |
| `founder_invite_code` | varchar | Founder invite code used |
| `registration_metadata` | jsonb | Additional attribution data |

---

### `referral_codes`
*~162 rows* — Referral and distributor codes with commission structure.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `code` | varchar | The referral code string |
| `public_name` | varchar | Displayed label |
| `owner_user_id` | uuid | FK → `users.id` |
| `code_type` | varchar | `referral`, `distributor`, `agent` |
| `parent_distributor_id` | uuid | Self-referential hierarchy |
| `commission_rate_basis_points` | integer | bps — e.g., 50 = 0.5% |
| `agent_share_basis_points` | integer | Agent's cut of commission |
| `max_uses` | integer | Nullable = unlimited |
| `uses_count` | integer | |
| `is_active` | boolean | |

---

### `founders`
*~11,026 rows* — Founders Club membership. One row per founder.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | FK → `users.id` (PK) |
| `founder_number` | integer | Sequential founder ID |
| `invites_remaining` | smallint | Unused invite slots |
| `invites_sent` | integer | |
| `invite_code` | varchar | Their personal invite code |
| `network_size` | integer | Downstream users attributed |
| `activated_at` | timestamptz | |

---

### `invites`
*~6,264 rows* — Invite records sent by users.

| Column | Type | Notes |
|---|---|---|
| `inviter_user_id` | uuid | |
| `invite_type` | varchar | `email`, `sms`, `link` |
| `recipient_email` | varchar | PII |
| `recipient_phone` | varchar | PII |
| `referral_code_id` | uuid | |
| `founder_invite_code` | varchar | |
| `campaign_name` | varchar | Marketing campaign |
| `source_channel` | varchar | Channel tracking |
| `status` | varchar | `pending`, `accepted`, `expired` |
| `accepted_user_id` | uuid | FK → `users.id` if accepted |
| `accepted_at` | timestamptz | |

---

### `teams`
*~0 rows* — Distributor/agent team definitions.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `owner_id` | uuid | Team owner |
| `name` | varchar | |
| `team_type` | varchar | `distributor`, `agent`, etc. |
| `referral_code_id` | uuid | Team's referral code |
| `monthly_target_brl` | bigint | centavos |
| `max_members` | integer | |

---

### `team_members`
*~0 rows* — Team membership with granular permissions.

| Column | Type | Notes |
|---|---|---|
| `team_id` | uuid | |
| `member_user_id` | uuid | |
| `team_role` | varchar | `owner`, `admin`, `member` |
| `can_view_analytics` | boolean | |
| `can_view_financial_data` | boolean | |
| `can_view_personal_data` | boolean | |
| `can_view_sensitive_data` | boolean | |
| `data_access_from` | timestamptz | Time-bounded data access |
| `is_active` | boolean | |

---

### `team_audit_log`
*~0 rows* — Audit trail for team management actions.

---

## Cards

### `cards`
*~2,008 rows* — Physical/virtual card records. One row per card issued.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `rain_card_id` | text | Rain API card ID |
| `user_id` | uuid | FK → `users.id` |
| `rain_application_id` | text | |
| `card_type` | text | `virtual`, `physical` |
| `status` | text | `active`, `frozen`, `terminated` |
| `last_four` | text | Last 4 digits — safe to display |
| `expiry_month`, `expiry_year` | smallint | |
| `daily_limit` | bigint | USD cents ÷ 100 |
| `monthly_limit` | bigint | USD cents ÷ 100 |
| `per_transaction_limit` | bigint | USD cents ÷ 100 |
| `card_variant` | text | `founder`, `basic` |

---

### `card_transactions`
*~22,220 rows* — Card transaction events from Rain.

| Column | Type | Notes |
|---|---|---|
| `internal_id` | uuid | PK |
| `transaction_id` | text | Rain transaction ID |
| `user_id` | uuid | |
| `transaction_type` | text | `spend`, `refund`, `reversal` (confirmed from prod) |
| `status` | text | `completed`, `pending`, `declined` (confirmed from prod) |
| `amount` | bigint | USD cents ÷ 100 |
| `currency` | text | Always `USD` |
| `transaction_data` | jsonb | Merchant name, category, MCC, etc. |
| `decline_reason` | text | Nullable |
| `authorized_at` | timestamptz | |
| `posted_at` | timestamptz | Settlement time |

---

### `card_transaction_holds`
*~18,212 rows* — Authorization holds on cards.

| Column | Type | Notes |
|---|---|---|
| `transaction_id` | text | Rain transaction ID |
| `user_id` | uuid | |
| `amount` | bigint | USD cents |
| `status` | text | `active`, `released`, `settled` |
| `authorized_amount` | bigint | |
| `captured_amount` | bigint | |
| `reversed_amount` | bigint | |
| `expires_at` | timestamptz | |

---

### `card_annual_fees`
*~980 rows* — Annual card fee charges per user.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `amount_usdc` | numeric | Already real USDC (not micros) |
| `status` | text | `pending`, `paid`, `failed` |
| `paid_at` | timestamptz | |
| `payment_tx_signature` | text | Solana tx signature |

---

### `card_waitlist`
*~272 rows* — Users waiting for card issuance.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `position` | integer | Queue position |
| `joined_at` | timestamptz | |

---

### `card_upgrade_operations`
*~4 rows* — Card variant upgrade tracking (e.g., basic → founder).

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `old_card_id` | text | |
| `target_variant` | text | `founder` |
| `fee_id` | uuid | FK → `card_annual_fees.id` |
| `status` | text | |

---

### `tapguard_cards`
*~0 rows* — NFC/tap-to-pay card registrations.

---

### `card_transaction_receipts`
*~5 rows* — Receipt file storage (binary data, not for analytics).

---

## Conversions (On/Off Ramp)

### `conversion_quotes`
*~84,337 rows* — **Primary revenue table**. Each BRL⇄USDC quote, including used and expired.

| Column | Type | Scale | Notes |
|---|---|---|---|
| `id` | uuid | | PK |
| `user_id` | uuid | | FK → `users.id` |
| `direction` | enum | | `brl_to_usdc` (onramp) / `usdc_to_brl` (offramp) |
| `from_amount_brl` | bigint | ÷ 100 | Input BRL amount (onramp) |
| `from_amount_usdc` | bigint | ÷ 1,000,000 | Input USDC amount (offramp) |
| `to_amount_brl` | bigint | ÷ 100 | Output BRL amount (offramp) |
| `to_amount_usdc` | bigint | ÷ 1,000,000 | Output USDC amount (onramp) |
| `exchange_rate` | numeric | real | Quoted BRL/USDC rate |
| `effective_rate` | numeric | real | Actual rate incl. spread |
| `fee_amount_brl` | bigint | ÷ 100 | Explicit fee in BRL |
| `fee_amount_usdc` | bigint | ÷ 1,000,000 | Explicit fee in USDC |
| `spread_percentage` | numeric | real | Spread as % |
| `spread_revenue_brl` | bigint | ÷ 100 | NBS spread captured BRL |
| `spread_revenue_usdc` | bigint | ÷ 1,000,000 | NBS spread captured USDC |
| `expires_at` | timestamptz | | Quote expiry |
| `used` | boolean | | `TRUE` = executed quote |
| `conversion_request_id` | uuid | | FK → `conversion_requests.id` |
| `processing_mode` | varchar | | `instant`, `standard` |

> **Filter**: `WHERE used = TRUE` for completed conversions only.
> Revenue per quote = `fee_amount_brl + spread_revenue_brl` (both ÷ 100 for real BRL).

---

### `conversion_requests`
*~11,541 rows* — Executed conversion operations (settlement records).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | |
| `direction` | enum | Same as `conversion_quotes.direction` |
| `from_amount_brl` | bigint | centavos |
| `to_amount_usdc` | bigint | micros |
| `exchange_rate`, `effective_rate` | numeric | |
| `fee_amount_brl`, `fee_amount_usdc` | bigint | |
| `spread_revenue_brl`, `spread_revenue_usdc` | bigint | |
| `conversion_quote_id` | uuid | FK → `conversion_quotes.id` |
| `ledger_transaction_id` | uuid | FK → `transactions_v2.id` |
| `status` | enum | `pending`, `completed`, `failed`, `reversed` |
| `executed_at` | timestamptz | |
| `completed_at` | timestamptz | |
| `dbaas_payout_id` | text | DBAAS settlement reference |

---

### `quote_instant_details`
*~15,467 rows* — PIX recipient metadata for instant onramp quotes.

| Column | Type | Notes |
|---|---|---|
| `quote_id` | uuid | FK → `conversion_quotes.id` |
| `pix_key` | varchar | Destination PIX key |
| `pix_key_type` | varchar | `cpf`, `email`, `phone`, `random` |
| `recipient_name` | varchar | |
| `recipient_document` | varchar | PII |
| `bank_code`, `bank_name` | varchar | |

---

## PIX (BRL Payments)

### `pix_requests`
*~10,029 rows* — Inbound PIX (BRL deposits into NBS). User sends BRL → triggers onramp.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | |
| `amount_brl` | bigint | centavos ÷ 100 |
| `state` | varchar | `pending`, `confirmed`, `expired`, `failed` |
| `instant_onramp_quote_id` | uuid | FK → `conversion_quotes.id` if linked to a conversion |
| `provider_name` | varchar | `asaas` |
| `expires_at` | timestamptz | |
| `created_at` | timestamptz | |

---

### `pix_transfers`
*~5,054 rows* — Outbound PIX (BRL withdrawals from NBS). User receives BRL ← offramp.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | |
| `amount_brl` | bigint | centavos ÷ 100 |
| `fee_brl` | bigint | centavos ÷ 100 |
| `net_amount_brl` | bigint | centavos ÷ 100 |
| `pix_key` | varchar | Recipient PIX key |
| `pix_key_type` | varchar | |
| `status` | varchar | `pending`, `processing`, `completed`, `failed` |
| `failure_reason` | text | |
| `end_to_end_identifier` | varchar | E2E ID for reconciliation |
| `executed_at` | timestamptz | |
| `provider_name` | varchar | `asaas` |

---

### `payment_state_transitions`
*~21,654 rows* — State machine audit log for PIX request state changes.

---

### `transfer_state_transitions`
*~0 rows* — State machine audit log for PIX transfer state changes.

---

### `payment_webhooks`
*~9,460 rows* — Raw webhook payloads from payment providers (Asaas).

---

### `payment_providers`
*~0 rows (config)* — Payment provider registry (Asaas, etc.).

---

## Swaps

### `swap_transactions`
*~5,933 rows* — Jupiter DEX swap events. One row per swap.

| Column | Type | Notes |
|---|---|---|
| `signature` | text | Solana tx signature (PK) |
| `user_id` | uuid | |
| `input_mint` | text | Input token mint address |
| `output_mint` | text | Output token mint address |
| `input_amount` | bigint | Token micros ÷ 1,000,000 (for USDC) |
| `output_amount` | bigint | Token micros |
| `swap_type` | text | `exactIn`, `exactOut` |
| `slippage_bps` | integer | Slippage tolerance in bps |
| `platform_fee_bps` | integer | NBS fee in bps |
| `timestamp` | timestamptz | On-chain time |
| `is_backfill` | boolean | Historical backfill flag |

> Revenue per swap = `input_amount × platform_fee_bps / 10,000` (in input token units).

---

### `swap_fee_events`
*~0 rows* — Granular fee token transfer events from Jupiter swaps.

| Column | Type | Notes |
|---|---|---|
| `signature` | text | Solana tx signature |
| `user_id` | uuid | |
| `recipient_account` | text | Fee recipient wallet |
| `mint` | text | Fee token mint |
| `amount` | bigint | Token micros |
| `timestamp` | timestamptz | |

> Currently empty — may be populated as a more precise fee tracking mechanism.

---

## Rewards & Revenue Share

### `cashback_rewards`
*~37,218 rows* — Cashback paid to users on card spend. **Cost item** in revenue model.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `transaction_id` | text | FK → `card_transactions.transaction_id` |
| `transaction_amount_cents` | bigint | USD cents |
| `rate_bps` | integer | Cashback rate in bps |
| `reward_amount` | bigint | Token micros |
| `token_mint` | text | Reward token address |
| `token_symbol` | text | `USDC`, etc. |
| `status` | text | `pending`, `completed`, `failed` |
| `reward_usd_value` | numeric | USD value of reward (real units) |
| `recipient_type` | text | `user`, `referrer` |
| `source_user_id` | uuid | User who generated the spend |
| `recipient_user_id` | uuid | User receiving the reward (may differ for referrals) |

---

### `revenue_share_rewards`
*~5,315 rows* — Revenue share payouts to distributors/agents. **Cost item** in P&L.

| Column | Type | Notes |
|---|---|---|
| `revenue_type` | text | `onramp_spread`, `card_fee`, `swap_fee`, etc. |
| `recipient_type` | text | `distributor`, `agent`, `founder` |
| `recipient_user_id` | uuid | Who received the reward |
| `source_user_id` | uuid | User who generated the revenue |
| `source_reference` | text | Reference ID of originating transaction |
| `source_amount_cents` | bigint | |
| `rate_bps` | integer | Revenue share rate |
| `reward_amount` | bigint | Token micros |
| `reward_usd_value` | numeric | USD value (real units) |
| `token_symbol` | text | |
| `status` | text | `pending`, `completed`, `failed` |

---

## Unblockpay (International Payouts)

### `unblockpay_payouts`
*~227 rows* — International USDC → fiat payouts via Unblockpay.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | |
| `bank_account_id` | uuid | FK → `unblockpay_bank_accounts.id` |
| `quote_id` | uuid | FK → `unblockpay_quotes.id` |
| `amount` | numeric | USDC amount (real units) |
| `currency` | varchar | Always `USDC` |
| `fiat_currency` | varchar | `USD`, `EUR`, `MXN`, etc. |
| `status` | varchar | `pending`, `completed`, `failed` |
| `final_fiat_amount` | numeric | Fiat received (real units) |
| `unblockpay_fee` | numeric | Fee in USDC (real units) — NBS revenue or pass-through |
| `deposit_address` | varchar | USDC deposit address |
| `transaction_hash` | varchar | On-chain hash |
| `completed_at` | timestamptz | |

---

### `unblockpay_quotes`
*~240 rows* — FX quotes for international payouts.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `amount_usdc` | numeric | |
| `estimated_amount_fiat` | varchar | |
| `exchange_rate` | varchar | |
| `currency` | varchar | Input currency |

---

### `unblockpay_bank_accounts`
*~14 rows* — Registered international bank accounts per user.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `payment_rail` | varchar | `ACH`, `SEPA`, `SPEI` |
| `bank_name` | varchar | |
| `routing_number` | varchar | US routing number |
| `iban` | varchar | EU IBAN |
| `sepa_country` | varchar | |
| `spei_clabe` | varchar | Mexico CLABE |
| `address_country` | varchar | |

---

### `unblockpay_customers`
*~7 rows* — Unblockpay customer registrations.

---

## AI Features

### `ai_interactions`
*~399 rows* — Individual AI request/response events.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `session_id` | uuid | FK → `ai_sessions.id` |
| `user_id` | uuid | |
| `request_data` | jsonb | Prompt and parameters |
| `response_data` | jsonb | Model response |
| `processing_time_ms` | integer | Latency |
| `created_at` | timestamptz | |

---

### `ai_sessions`
*~6,933 rows* — AI conversation sessions.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | |
| `status` | varchar | `active`, `closed` |
| `language` | varchar | Session language |
| `message_count` | integer | Messages in session |
| `max_turns` | smallint | Session turn limit |
| `last_activity` | timestamptz | |
| `preferences` | jsonb | User AI preferences |
| `context_data` | jsonb | Session context |

---

### `user_ai_quotas`
*~6,077 rows* — Daily AI usage limits per user.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | PK |
| `daily_limit` | integer | Max queries/day |
| `queries_today` | integer | Current day usage |
| `last_reset` | timestamptz | Daily reset time |
| `credits_available` | bigint | Credit balance |

---

### `ai_audit_logs`
*~7,745 rows* — Security audit trail for AI feature access.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `event_type` | varchar | Login, quota_exceeded, etc. |
| `success` | boolean | |
| `error_code` | varchar | |
| `ip_address` | varchar | |
| `metadata` | jsonb | |

---

## Ledger / Accounting

### `accounts_v2`
*~837 rows* — Chart of accounts (v2 ledger). One account per user per currency.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `account_code` | varchar | Account identifier |
| `user_id` | uuid | Nullable (system accounts) |
| `ledger_type` | varchar | `asset`, `liability`, `revenue`, `expense` |
| `account_type` | varchar | `user_brl`, `user_usdc`, `float_brl`, etc. |
| `currency` | varchar | `BRL`, `USDC` |
| `status` | varchar | `active`, `suspended`, `closed` |

---

### `entries_v2`
*~140,230 rows* — Double-entry ledger entries (v2). Every financial event creates 2+ entries.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `transaction_id` | uuid | FK → `transactions_v2.id` |
| `account_id` | uuid | FK → `accounts_v2.id` |
| `amount` | numeric | Real units (BRL or USDC) |
| `direction` | varchar | `debit` / `credit` |
| `sequence_number` | integer | Entry order within transaction |

---

### `account_balances`
*~13,984 rows* — Current balance per account (denormalized view).

| Column | Type | Notes |
|---|---|---|
| `account_id` | uuid | FK → `accounts_v2.id` |
| `posted_balance` | numeric | Settled balance |
| `pending_balance` | numeric | Including holds |
| `available_balance` | numeric | Spendable balance |
| `version` | bigint | Optimistic locking |

---

### `account_holds`
*~13,187 rows* — Funds on hold against an account.

| Column | Type | Notes |
|---|---|---|
| `account_id` | uuid | |
| `transaction_id` | uuid | |
| `hold_type` | varchar | `card_auth`, `pix_pending`, etc. |
| `amount` | numeric | |
| `status` | varchar | `active`, `released`, `settled` |
| `expires_at` | timestamptz | |

---

### `transactions_v2`
*~44,345 rows* — Ledger transaction headers (v2). Every financial event has one row here.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `idempotency_key` | varchar | Dedup key |
| `external_reference` | varchar | Reference to source system |
| `external_source` | varchar | `pix_request`, `conversion_request`, `card_tx`, etc. |
| `transaction_type` | varchar | Type label |
| `status` | varchar | `pending`, `posted`, `reversed` |
| `user_id` | uuid | |
| `posted_at` | timestamptz | Settlement time |
| `value_date` | date | Accounting date |
| `reversal_of` | uuid | FK → reversed transaction |
| `metadata` | jsonb | Additional context |

---

### `accounts`, `entries`, `transactions`
*~0 rows* — Legacy v1 ledger tables. Superseded by `accounts_v2`, `entries_v2`, `transactions_v2`. Do not use for analytics.

---

## Blockchain / Solana

### `solana_sponsored_transactions`
*~30,728 rows* — NBS-sponsored Solana transactions (gas fee subsidies).

| Column | Type | Notes |
|---|---|---|
| `transaction_signature` | varchar | PK |
| `user_id` | uuid | |
| `fee_payer_address` | varchar | NBS fee payer wallet |
| `estimated_fee_lamports` | bigint | lamports ÷ 1,000,000,000 = SOL |
| `metadata` | jsonb | |

---

### `solana_user_quotas`
*~2,222 rows* — Solana transaction fee subsidies per user.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `daily_transaction_count` | integer | |
| `daily_fee_lamports_total` | bigint | |
| `monthly_transaction_count` | integer | |
| `monthly_fee_lamports_total` | bigint | |
| `daily_reset_at` | timestamptz | |
| `monthly_reset_at` | timestamptz | |

---

## Notifications

### `notification_events`
*~189,752 rows* — All push notifications sent or queued.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `event_type` | varchar | `conversion_completed`, `card_authorized`, etc. |
| `title`, `body` | text | Notification content |
| `category` | varchar | `transaction`, `security`, `marketing` |
| `priority` | varchar | `high`, `normal` |
| `status` | varchar | `pending`, `sent`, `failed` |
| `read_at` | timestamptz | User read time |
| `deep_link` | text | In-app navigation target |

---

### `notification_preferences`
*~0 rows* — User notification opt-in settings per category.

---

### `notification_templates`
*~0 rows* — Notification copy templates by event type.

---

### `notification_batches`
*~0 rows* — Bulk notification campaign records.

---

### `device_tokens`
*~8,509 rows* — Push notification tokens per device.

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | |
| `token` | text | FCM/APNs token |
| `platform` | varchar | `ios`, `android` |
| `app_version` | text | |
| `is_active` | boolean | |
| `last_used_at` | timestamptz | |

---

## Infrastructure

> These tables support the platform's internal operations. Generally not useful for business analytics.

### `event_outbox`
*~356,171 rows* — Transactional outbox for event publishing (Kafka/pub-sub pattern). Internal.

### `webhook_queue`
*~0 rows* — Pending webhook delivery queue.

### `webhook_performance_metrics`
View: hourly webhook processing stats (throughput, latency percentiles).

### `webhook_processing_status`
View: current webhook queue status by state.

### `orphan_webhooks`
*~0 rows* — Webhooks that arrived with no matching record.

### `orphan_webhooks_summary`
View: summary of unresolved orphan webhooks.

### `hold_creation_failures`
*~2 rows* — Failed card hold creation attempts (audit).

### `user_payment_accounts`
*~0 rows* — Links users to external payment provider accounts (Asaas customer IDs).

### `_sqlx_migrations`
Migration history table. Do not query for analytics.

---

## Quick Reference: Key Joins

```sql
-- User revenue profile
users u
  LEFT JOIN user_profiles up ON up.user_id = u.id
  LEFT JOIN user_registrations ur ON ur.user_id = u.id
  LEFT JOIN founders f ON f.user_id = u.id
  LEFT JOIN cpf_validation_data cpf ON cpf.user_id = u.id

-- Onramp revenue
conversion_quotes q
  WHERE q.used = TRUE
  -- BRL revenue: (q.fee_amount_brl + q.spread_revenue_brl) / 100

-- Card spend per user
card_transactions ct
  WHERE ct.status = 'posted'
  -- USD amount: ct.amount / 100

-- Cashback cost per user
cashback_rewards cr
  WHERE cr.status = 'completed'
  -- USD cost: cr.reward_usd_value (already real)

-- Revenue share cost
revenue_share_rewards rr
  WHERE rr.status = 'completed'
  -- USD cost: rr.reward_usd_value (already real)
```
