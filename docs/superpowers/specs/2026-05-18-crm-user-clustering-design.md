# CRM User Clustering — Design Spec

**Date:** 2026-05-18
**Status:** Approved
**Phase:** 1 of 2 (Intelligence layer only — delivery automation is Phase 2)

---

## Problem

NBS has ~11,000 registered users with very different behavioral profiles: some are high-volume onrampers, others are DeFi traders, others registered but never transacted. The existing `clients/segments.py` produces a 4-bucket rule-based classification (champion / active / at_risk / dormant) based only on recency and revenue. This is too coarse for targeted push notification campaigns — it doesn't distinguish *how* users use the product, only *how recently* and *how profitably*.

The goal is a richer ML-derived segmentation that maps each user to a behavioral archetype, enabling per-segment push notification campaigns with time-of-day aware message variants.

---

## Scope (Phase 1)

**In scope:**
- Jupyter notebook (`notebooks/crm_user_clustering.ipynb`) that extracts behavioral features from the production DB, clusters users, profiles each cluster, and produces a pushable CRM export
- Message template library: 2–3 copy variants per segment × 3 time-of-day slots
- Monthly-refresh mechanism: saved scaler/PCA/K-Means artifacts so new users can be scored without re-fitting
- CSV export: `data/processed/crm_segments_<YYYY-MM>.csv` — `user_id + segment_name`, filtered to users with active push tokens

**Out of scope (Phase 2):**
- Backend notification API design and contract
- `nbs_bi.crm` delivery module
- Scheduled automation
- Email/SMS fallback for users without active push tokens

---

## Architecture

```
notebooks/
└── crm_user_clustering.ipynb
    ├── Section 0:  Setup & DB connection
    ├── Section 1:  Data extraction (8 SQL queries)
    ├── Section 2:  Feature matrix assembly
    ├── Section 3:  Preprocessing pipeline
    ├── Section 4:  Outlier detection (HDBSCAN)
    ├── Section 5:  K selection (elbow + silhouette)
    ├── Section 6:  Final clustering (K-Means) + GMM validation
    ├── Section 7:  Cluster profiling
    ├── Section 8:  UMAP visualization
    └── Section 9:  Message templates + CRM export

data/processed/                          # gitignored — no PII
├── crm_segments_<YYYY-MM>.csv           # user_id, segment_name (pushable users only)
├── crm_scaler_<YYYY-MM>.pkl
├── crm_pca_<YYYY-MM>.pkl
└── crm_kmeans_<YYYY-MM>.pkl

docs/superpowers/specs/
└── 2026-05-18-crm-user-clustering-design.md   # this file
```

---

## Feature Engineering

One row per user, ~25 numeric features. All PII excluded — `user_id` is the only identifier.

### A. Lifecycle
Source: `users`, `user_profiles`, `founders`

| Feature | Derivation |
|---|---|
| `days_since_signup` | `CURRENT_DATE - users.created_at::date` |
| `days_since_last_active` | `CURRENT_DATE - users.last_active_at::date`; null → 9999 |
| `kyc_level` | `users.kyc_level` (0–3) |
| `onboarding_completed` | `user_profiles.onboarding_completed::int` |
| `is_founder` | 1 if row exists in `founders`, else 0 |
| `founder_network_size` | `COALESCE(founders.network_size, 0)` |
| `account_type_business` | 1 if `users.account_type = 'business'`, else 0 |

### B. Onramp / Offramp
Source: `conversion_quotes WHERE used = TRUE`

| Feature | Derivation |
|---|---|
| `n_onramp_txns` | `COUNT(*) WHERE direction = 'brl_to_usdc'` |
| `n_offramp_txns` | `COUNT(*) WHERE direction = 'usdc_to_brl'` |
| `total_onramp_brl` | `SUM(COALESCE(from_amount_brl,0)) / 100.0` |
| `total_spread_revenue_brl` | `SUM(COALESCE(spread_revenue_brl,0) + COALESCE(fee_amount_brl,0)) / 100.0` |
| `pct_instant_mode` | Fraction of conversions using `processing_mode = 'instant'` |
| `days_since_last_conversion` | `CURRENT_DATE - MAX(created_at)::date`; null → 9999 |

> `from_amount_brl` is NULL on offramp rows; `to_amount_brl` is NULL on onramp rows. Always `COALESCE(..., 0)` before summing.

### C. Cards
Source: `card_transactions`, `cards`, `card_annual_fees`

| Feature | Derivation |
|---|---|
| `n_card_txns` | `COUNT(*) WHERE transaction_type='spend' AND status='completed'` |
| `total_card_spend_usd` | `SUM(amount) / 100.0` for completed spend txns |
| `has_card` | 1 if active card exists, else 0 |
| `paid_annual_fee` | 1 if `card_annual_fees.status='paid'`, else 0 |
| `days_since_last_card_spend` | `CURRENT_DATE - MAX(authorized_at)::date`; null → 9999 |

### D. DeFi / Solana
Source: `swap_transactions`, `solana_sponsored_transactions`

| Feature | Derivation |
|---|---|
| `n_swaps` | `COUNT(*)` from `swap_transactions` |
| `total_swap_volume_usdc` | `SUM(input_amount) / 1e6` |
| `n_unique_tokens` | `COUNT(DISTINCT input_mint) + COUNT(DISTINCT output_mint)` |
| `n_solana_txns` | `COUNT(DISTINCT transaction_signature)` from `solana_sponsored_transactions` |
| `days_since_last_swap` | `CURRENT_DATE - MAX(timestamp)::date`; null → 9999 |

### E. International Payouts
Source: `unblockpay_payouts`

| Feature | Derivation |
|---|---|
| `n_international_payouts` | `COUNT(*) WHERE status = 'completed'` |
| `total_international_usdc` | `SUM(amount) WHERE status = 'completed'` |

### F. Engagement
Source: `ai_sessions`, `notification_events`

| Feature | Derivation |
|---|---|
| `n_ai_sessions` | `COUNT(DISTINCT id)` from `ai_sessions` |
| `notification_read_rate` | `COUNT(*) FILTER (WHERE read_at IS NOT NULL) / NULLIF(COUNT(*), 0)` from `notification_events` |

### G. Composite (derived in Python)

| Feature | Formula |
|---|---|
| `product_breadth_score` | Sum of: `(n_onramp>0) + (n_offramp>0) + has_card + (n_swaps>0) + (n_ai_sessions>0) + (n_international_payouts>0)`. Range 0–6. |
| `revenue_generated_brl` | `total_spread_revenue_brl` — spread revenue only; card annual fee excluded (USDC-denominated, FX-rate dependent, minor relative to spread) |

### Excluded

`cpf_validation_data` income/credit fields are excluded from clustering inputs (~75% of users have no CPF row — forced imputation would dominate the signal). Used post-hoc to enrich cluster profiles only.

---

## Preprocessing Pipeline

```
Raw features (25 columns, ~11k rows)
    ↓
1. Zero-fill NaN  (genuine non-usage, not missing data)
2. Add has_X binary flags alongside every count feature
3. log1p() on all count + monetary features  (fintech power-law distributions)
4. StandardScaler  (zero mean, unit variance)
5. PCA(n_components=0.90, random_state=42)  → expect 8–12 components
```

PCA before K-Means is required here: without it, high-variance features like `total_onramp_brl` dominate Euclidean distance and drown out low-variance but meaningful signals like `has_card` or `n_unique_tokens`.

---

## Clustering Pipeline

### Step 1 — Outlier quarantine (HDBSCAN)

```python
hdbscan.HDBSCAN(min_cluster_size=15, min_samples=5, metric='euclidean')
```

Users labeled `-1` are quarantined before K-Means. Inspect them: likely internal test accounts or bots. Expect <2% of users. Remove before final clustering.

### Step 2 — K selection

For k in 4–12, compute:
- **Silhouette score** (higher = better; target >0.25)
- **Davies-Bouldin index** (lower = better)
- **Inertia** (elbow curve)

Expected optimal k: **7**, but the diagnostic runs before committing.

### Step 3 — K-Means (primary)

```python
KMeans(n_clusters=k, init='k-means++', n_init=20, random_state=42)
```

Hard cluster assignment. Centroids inverse-transformed to original feature space for profiling.

### Step 4 — GMM validation

```python
GaussianMixture(n_components=k, covariance_type='full', n_init=5, random_state=42)
```

Compute Adjusted Rand Index between K-Means and GMM hard labels. If ARI > 0.85 → clusters are robust. If not, revisit k or feature set.

### Step 5 — Stability check

Re-run K-Means 5× with different seeds. ARI > 0.85 across all pairs = stable. Critical for monthly re-clustering: unstable clusters produce drifting segment assignments.

### Saved artifacts

```
data/processed/crm_scaler_<YYYY-MM>.pkl
data/processed/crm_pca_<YYYY-MM>.pkl
data/processed/crm_kmeans_<YYYY-MM>.pkl
```

New users can be scored incrementally: extract their features → apply saved scaler + PCA → `predict()` on saved K-Means. No re-fit needed until the monthly refresh.

---

## Segment Archetypes

Expected clusters at k=7. Names are assigned post-clustering based on centroid profiles — not pre-assigned. The archetypes below are the expected emergent structure.

### Segment 1: Power Onrampers (~3–5% users, ~40% revenue)

**Profile:** High `n_onramp_txns` (>10), high `total_onramp_brl` (>R$5,000), high `total_spread_revenue_brl`, KYC level 2–3, `days_since_last_active` <14, `product_breadth_score` 3–5.

**Campaign goal:** Cross-sell DeFi (swaps) and card. VIP framing — never discount.

| Time | Message |
|---|---|
| Morning (07–09h) | "Start your day strong — your BRL is ready to convert at today's best rate." |
| Afternoon (12–14h) | "Rate alert: BRL/USDC is favorable right now. Convert in 60 seconds." |
| Evening (19–21h) | "Your USDC is idle — have you tried a Jupiter swap to grow your portfolio?" |

---

### Segment 2: Card-First Spenders (~5–8%)

**Profile:** High `n_card_txns` + `total_card_spend_usd`, `paid_annual_fee=1`, low `n_onramp_txns` (convert elsewhere or hold USDC externally), `days_since_last_card_spend` <30.

**Campaign goal:** Onramp cross-sell. They trust the card — remove friction to first conversion.

| Time | Message |
|---|---|
| Morning | "Fund your NBS card in 60 seconds — add BRL via PIX, convert instantly." |
| Afternoon | "Your card is ready to spend. Top up with PIX and never run low." |
| Evening | "Quick top-up before tomorrow? PIX → USDC in under a minute." |

---

### Segment 3: DeFi Traders (~3–6%)

**Profile:** High `n_swaps`, high `n_unique_tokens`, high `n_solana_txns`, moderate-to-low onramp, no card activity.

**Campaign goal:** Deepen swap engagement. DeFi-native language only — no BRL/USDC conversion pitch, no card offer.

| Time | Message |
|---|---|
| Morning | "New liquidity on Jupiter — check today's best swap routes." |
| Afternoon | "Your portfolio is moving. Swap tokens with zero hidden fees on NBS." |
| Evening | "DeFi never sleeps. Spot a new opportunity? Swap it now." |

---

### Segment 4: Occasional Onrampers (~15–20%)

**Profile:** 1–5 onramp txns, moderate volume, `product_breadth_score=1`, KYC 1–2, last active 30–90 days.

**Campaign goal:** Cross-sell depth. They onramp but stop there — push the next product.

| Time | Message |
|---|---|
| Morning | "Your USDC is sitting idle — put it to work with a swap or your NBS card." |
| Afternoon | "Been a while since your last conversion. Rates are looking good today." |
| Evening | "One more step to unlock your full NBS wallet — try a swap or get your card." |

---

### Segment 5: Explorer / AI Users (~10–15%)

**Profile:** High `n_ai_sessions`, low financial transaction count, no card, `n_onramp_txns` <2, `days_since_last_active` <30.

**Campaign goal:** First conversion activation. They know the product — remove friction, don't educate.

| Time | Message |
|---|---|
| Morning | "Your NBS wallet is ready — buy USDC with PIX and unlock your card today." |
| Afternoon | "You've been exploring NBS — ready to make your first BRL → USDC conversion?" |
| Evening | "Your account is set up. One PIX transfer and you're live." |

---

### Segment 6: Stalled Pre-KYC (~30–40%)

**Profile:** `kyc_level` 0–1, `onboarding_completed=0`, no transactions, signed up >30 days ago, `days_since_last_active` >60.

**Campaign goal:** KYC funnel completion. Minimal copy, single CTA.

> **Delivery rule:** Only send if `device_tokens.is_active = TRUE`. This segment has the highest proportion of stale tokens — do not burn quota on dead tokens.

| Time | Message |
|---|---|
| Morning | "Verify in 2 minutes — unlock BRL ↔ USDC in real time." |
| Afternoon | "Your NBS account is waiting. Complete verification to get started." |
| Evening | "Takes 2 minutes. Then you're live on NBS." |

---

### Segment 7: Dormant — Previously Active (~15–25%)

**Profile:** `days_since_last_active` >90, previously had ≥1 transaction, low `notification_read_rate`.

**Campaign goal:** Win-back. One send per quarter maximum.

> **Delivery rule:** If `notification_read_rate = 0` over the last 90 days, skip push entirely. Flag for email/SMS re-engagement in Phase 2.

| Time | Message |
|---|---|
| Morning | "We've been busy — NBS has new features since your last visit." |
| Afternoon | "Your balance is waiting. Log in and see what's new." |
| Evening | "It's been a while. Your USDC is still here when you're ready." |

---

## CRM Export Format

```csv
user_id,segment_name,segment_id,pushable
<uuid>,power_onramper,1,true
<uuid>,dormant,7,true
...
```

Cross-joined with `device_tokens WHERE is_active = TRUE`. No PII — `user_id` only. Saved to `data/processed/crm_segments_<YYYY-MM>.csv` (gitignored).

---

## Monthly Refresh Protocol

1. Re-run Sections 0–2 of the notebook to pull fresh data from the DB.
2. Apply saved scaler + PCA from the previous month.
3. Run `kmeans.predict()` on the new user matrix — no re-fit.
4. **Full re-fit** (re-run Sections 3–6) once per quarter or when the user base grows by >20%.
5. After a full re-fit, re-validate cluster names against centroid profiles — segments may shift.

---

## Data Quality Rules

- All DB timestamps are `timestamptz` (UTC-aware). Strip timezone with `.dt.tz_convert(None)` before any `.dt.days` arithmetic.
- `from_amount_brl` / `to_amount_brl` NULL pattern in `conversion_quotes`: always `COALESCE(..., 0)` before summing.
- Monetary values: BRL stored as centavos (÷100), USDC stored as micros (÷1,000,000). Apply scaling before any arithmetic.
- No PII in notebook outputs, exports, or printed cells. `user_id` (UUID) is the only identifier.

---

## Dependencies

```
pandas>=2.0
numpy>=1.26
scikit-learn>=1.4
umap-learn>=0.5
hdbscan>=0.8
sqlalchemy>=2.0
psycopg2-binary
python-dotenv
seaborn
matplotlib
joblib
```

Add to `pyproject.toml` under `[project.optional-dependencies]` as `crm = [...]` so they don't pollute the core install.

---

## What Phase 2 Will Add

- Backend notification API contract (endpoint, payload format, authentication)
- `nbs_bi.crm` Python module: reads the monthly CSV, selects message template by segment + time-of-day, calls the backend API
- Scheduler: monthly re-clustering + campaign trigger
- Email/SMS fallback for Segment 7 users with zero push engagement
