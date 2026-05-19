# CRM User Clustering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Jupyter notebook that clusters ~11,000 NBS users into 7 behavioral segments using K-Means on PCA-reduced features, profiles each cluster, and produces a time-of-day-aware push notification message library and a pushable CRM export CSV.

**Architecture:** Extract 25 behavioral features per user from the production DB across 8 SQL queries; preprocess with log1p → StandardScaler → PCA (90% variance); quarantine outliers with HDBSCAN; cluster with K-Means (k≈7) validated against GMM; profile clusters in original feature space; export `user_id + segment_name` filtered to users with active push tokens.

**Tech Stack:** pandas, numpy, scikit-learn (KMeans, PCA, GaussianMixture, StandardScaler), hdbscan, umap-learn, seaborn, matplotlib, sqlalchemy, psycopg2-binary, joblib, python-dotenv

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Modify | `pyproject.toml` | Add `crm` optional-dependency group |
| Create | `notebooks/crm_user_clustering.ipynb` | Main analysis notebook |
| Create (gitignored) | `data/processed/crm_segments_<YYYY-MM>.csv` | CRM export: user_id + segment_name |
| Create (gitignored) | `data/processed/crm_scaler_<YYYY-MM>.pkl` | Saved StandardScaler for scoring |
| Create (gitignored) | `data/processed/crm_pca_<YYYY-MM>.pkl` | Saved PCA for scoring |
| Create (gitignored) | `data/processed/crm_kmeans_<YYYY-MM>.pkl` | Saved KMeans for scoring |

---

## Task 1: Add CRM dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `crm` optional-dependency group**

Open `pyproject.toml` and add this block immediately after the `dev` group inside `[project.optional-dependencies]`:

```toml
crm = [
    "umap-learn>=0.5",
    "hdbscan>=0.8",
    "seaborn>=0.13",
]
```

The full `[project.optional-dependencies]` section should now look like:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "pip-audit>=2.7",
    "jupyter>=1.0",
    "ipykernel>=6.0",
    "watchdog>=4.0",
]
crm = [
    "umap-learn>=0.5",
    "hdbscan>=0.8",
    "seaborn>=0.13",
]
```

- [ ] **Step 2: Install the new deps**

```bash
pip install -e ".[dev,crm]"
```

Expected: installs umap-learn, hdbscan, seaborn without errors.

- [ ] **Step 3: Verify imports**

```bash
python -c "import umap; import hdbscan; import seaborn; print('OK')"
```

Expected output: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(crm): add umap-learn, hdbscan, seaborn to crm optional deps"
```

---

## Task 2: Notebook skeleton + DB setup (Section 0)

**Files:**
- Create: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Create the notebook with a setup cell**

Launch Jupyter (`jupyter notebook` or open in VS Code). Create a new notebook at `notebooks/crm_user_clustering.ipynb`.

Add a Markdown cell at the top:

```markdown
# NBS CRM User Clustering
Monthly re-run: extract → preprocess → cluster → profile → export.
Spec: `docs/superpowers/specs/2026-05-18-crm-user-clustering-design.md`
```

- [ ] **Step 2: Add the imports + DB connection cell**

```python
import os
import warnings
from datetime import datetime

import hdbscan
import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import umap
from dotenv import load_dotenv
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, davies_bouldin_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore", category=FutureWarning)
load_dotenv()

engine = create_engine(os.environ["READONLY_DATABASE_URL"], pool_pre_ping=True)
ANALYSIS_MONTH = datetime.now().strftime("%Y-%m")
PROCESSED_DIR = "../data/processed"
os.makedirs(PROCESSED_DIR, exist_ok=True)

print(f"Analysis month: {ANALYSIS_MONTH}")
print("DB engine ready:", engine.url.host)
```

- [ ] **Step 3: Run the cell and verify**

Expected output:
```
Analysis month: 2026-05
DB engine ready: <your-neon-host>
```

If `KeyError: 'READONLY_DATABASE_URL'` — check `.env` is present and `load_dotenv()` ran.

- [ ] **Step 4: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add notebook skeleton and DB setup cell"
```

---

## Task 3: Data extraction — users + lifecycle (Section 1, Part A)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the users query cell**

```python
# ── Users base ──────────────────────────────────────────────────────────────
sql_users = text("""
    SELECT
        u.id                                    AS user_id,
        u.kyc_level,
        u.status,
        u.created_at,
        u.last_active_at,
        (u.account_type = 'business')::int      AS account_type_business,
        COALESCE(p.onboarding_completed, FALSE)::int AS onboarding_completed
    FROM users u
    LEFT JOIN user_profiles p ON p.user_id = u.id
    WHERE u.status != 'suspended'
""")

df_users = pd.read_sql(sql_users, engine)
print(f"Users: {len(df_users):,}")
assert df_users["user_id"].nunique() == len(df_users), "Duplicate user_ids"
assert df_users["kyc_level"].between(0, 3).all(), "kyc_level out of range"
df_users.head(3)
```

- [ ] **Step 2: Add the founders query cell**

```python
# ── Founders ─────────────────────────────────────────────────────────────────
sql_founders = text("""
    SELECT
        user_id,
        1                   AS is_founder,
        network_size        AS founder_network_size,
        invites_sent
    FROM founders
""")

df_founders = pd.read_sql(sql_founders, engine)
print(f"Founders: {len(df_founders):,}")
df_founders.head(3)
```

- [ ] **Step 3: Run both cells and verify**

Expected: `Users: ~11,000`, `Founders: ~11,026` (founders table includes almost all users).

- [ ] **Step 4: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add users and founders extraction cells"
```

---

## Task 4: Data extraction — conversions, cards (Section 1, Part B–C)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the conversions query cell**

```python
# ── Onramp / Offramp ─────────────────────────────────────────────────────────
# from_amount_brl is NULL on offramp rows; to_amount_brl is NULL on onramp rows.
# COALESCE(..., 0) before every SUM.
sql_conversions = text("""
    SELECT
        user_id,
        COUNT(*) FILTER (WHERE direction = 'brl_to_usdc')    AS n_onramp_txns,
        COUNT(*) FILTER (WHERE direction = 'usdc_to_brl')    AS n_offramp_txns,
        COALESCE(SUM(
            CASE WHEN direction = 'brl_to_usdc'
                 THEN COALESCE(from_amount_brl, 0) ELSE 0 END
        ) / 100.0, 0)                                        AS total_onramp_brl,
        COALESCE(SUM(
            COALESCE(spread_revenue_brl, 0) + COALESCE(fee_amount_brl, 0)
        ) / 100.0, 0)                                        AS total_spread_revenue_brl,
        COALESCE(
            SUM((processing_mode = 'instant')::int)::float
            / NULLIF(COUNT(*), 0), 0
        )                                                    AS pct_instant_mode,
        MAX(created_at)                                      AS last_conversion_at
    FROM conversion_quotes
    WHERE used = TRUE
    GROUP BY user_id
""")

df_conversions = pd.read_sql(sql_conversions, engine)
print(f"Users with conversions: {len(df_conversions):,}")
assert (df_conversions["total_spread_revenue_brl"] >= 0).all(), "Negative revenue"
df_conversions.head(3)
```

- [ ] **Step 2: Add the cards query cell**

```python
# ── Cards ────────────────────────────────────────────────────────────────────
sql_cards_txns = text("""
    SELECT
        user_id,
        COUNT(*) FILTER (
            WHERE transaction_type = 'spend' AND status = 'completed'
        )                                   AS n_card_txns,
        COALESCE(SUM(amount) FILTER (
            WHERE transaction_type = 'spend' AND status = 'completed'
        ) / 100.0, 0)                       AS total_card_spend_usd,
        MAX(authorized_at) FILTER (
            WHERE transaction_type = 'spend'
        )                                   AS last_card_spend_at
    FROM card_transactions
    GROUP BY user_id
""")

sql_cards_issued = text("""
    SELECT
        user_id,
        1                                   AS has_card,
        MAX((card_variant = 'founder')::int) AS card_variant_founder
    FROM cards
    WHERE status = 'active'
    GROUP BY user_id
""")

sql_annual_fee = text("""
    SELECT DISTINCT user_id, 1 AS paid_annual_fee
    FROM card_annual_fees
    WHERE status = 'paid'
""")

df_card_txns   = pd.read_sql(sql_cards_txns, engine)
df_card_issued = pd.read_sql(sql_cards_issued, engine)
df_annual_fee  = pd.read_sql(sql_annual_fee, engine)

print(f"Users with card txns:  {len(df_card_txns):,}")
print(f"Users with active card: {len(df_card_issued):,}")
print(f"Users paid annual fee:  {len(df_annual_fee):,}")
```

- [ ] **Step 3: Run all three cells and verify**

Expected: `Users with conversions: ~3,000–5,000`, `Users with active card: ~2,000`, `Users paid annual fee: ~980`.

- [ ] **Step 4: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add conversion and card extraction cells"
```

---

## Task 5: Data extraction — DeFi, engagement, international (Section 1, Part D–F)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the DeFi + Solana query cell**

```python
# ── Swaps + Solana ────────────────────────────────────────────────────────────
sql_swaps = text("""
    SELECT
        user_id,
        COUNT(*)                                            AS n_swaps,
        COALESCE(SUM(input_amount) / 1e6, 0)               AS total_swap_volume_usdc,
        COUNT(DISTINCT input_mint)
            + COUNT(DISTINCT output_mint)                   AS n_unique_tokens,
        MAX(timestamp)                                      AS last_swap_at
    FROM swap_transactions
    GROUP BY user_id
""")

sql_solana = text("""
    SELECT
        user_id,
        COUNT(DISTINCT transaction_signature) AS n_solana_txns
    FROM solana_sponsored_transactions
    GROUP BY user_id
""")

df_swaps  = pd.read_sql(sql_swaps, engine)
df_solana = pd.read_sql(sql_solana, engine)

print(f"Users with swaps:  {len(df_swaps):,}")
print(f"Users with Solana: {len(df_solana):,}")
```

- [ ] **Step 2: Add the AI + notification + international query cell**

```python
# ── AI sessions ──────────────────────────────────────────────────────────────
sql_ai = text("""
    SELECT
        user_id,
        COUNT(*)                    AS n_ai_sessions,
        COALESCE(SUM(message_count), 0) AS total_ai_messages
    FROM ai_sessions
    GROUP BY user_id
""")

# ── Notification engagement ───────────────────────────────────────────────────
sql_notifications = text("""
    SELECT
        user_id,
        COUNT(*)                                        AS n_notifications_received,
        COUNT(*) FILTER (WHERE read_at IS NOT NULL)     AS n_notifications_read
    FROM notification_events
    GROUP BY user_id
""")

# ── International payouts ─────────────────────────────────────────────────────
sql_international = text("""
    SELECT
        user_id,
        COUNT(*) FILTER (WHERE status = 'completed')        AS n_international_payouts,
        COALESCE(SUM(amount) FILTER (WHERE status = 'completed'), 0)
                                                            AS total_international_usdc
    FROM unblockpay_payouts
    GROUP BY user_id
""")

df_ai            = pd.read_sql(sql_ai, engine)
df_notifications = pd.read_sql(sql_notifications, engine)
df_international = pd.read_sql(sql_international, engine)

print(f"Users with AI sessions:     {len(df_ai):,}")
print(f"Users with notifications:   {len(df_notifications):,}")
print(f"Users with intl payouts:    {len(df_international):,}")
```

- [ ] **Step 3: Run both cells and verify**

Expected: `Users with swaps: ~600–800`, `Users with Solana: ~2,000`, `Users with AI sessions: ~4,000–6,000`, `Users with intl payouts: ~10–20`.

- [ ] **Step 4: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add DeFi, AI, notification, and international extraction cells"
```

---

## Task 6: Feature matrix assembly (Section 2)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the merge cell**

```python
# ── Merge all sources onto user base (left joins — zero-fill = genuine non-usage) ─
df = df_users.copy()

for tbl in [
    df_founders,
    df_conversions,
    df_card_txns,
    df_card_issued,
    df_annual_fee,
    df_swaps,
    df_solana,
    df_ai,
    df_notifications,
    df_international,
]:
    df = df.merge(tbl, on="user_id", how="left")

print(f"Shape after merge: {df.shape}")
assert len(df) == len(df_users), "Row count changed after merges — check for duplicates"
```

- [ ] **Step 2: Add the recency + zero-fill cell**

```python
# ── Recency features ─────────────────────────────────────────────────────────
# DB datetimes are tz-aware UTC — strip timezone before .dt.days arithmetic
now = pd.Timestamp.now().normalize()

def recency_days(col):
    return (now - pd.to_datetime(df[col]).dt.tz_convert(None)).dt.days.fillna(9999).clip(upper=730)

df["days_since_signup"]          = recency_days("created_at")
df["days_since_last_active"]     = recency_days("last_active_at")
df["days_since_last_conversion"] = recency_days("last_conversion_at")
df["days_since_last_card_spend"] = recency_days("last_card_spend_at")
df["days_since_last_swap"]       = recency_days("last_swap_at")

# Zero-fill all numeric NaN (non-usage, not missing data)
numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
df[numeric_cols] = df[numeric_cols].fillna(0)

print("NaN remaining:", df[numeric_cols].isna().sum().sum())
assert df[numeric_cols].isna().sum().sum() == 0, "Unexpected NaN in numeric columns"
```

- [ ] **Step 3: Add the composite + binary flag cell**

```python
# ── Binary presence flags ────────────────────────────────────────────────────
df["has_onramp"]  = (df["n_onramp_txns"] > 0).astype(int)
df["has_offramp"] = (df["n_offramp_txns"] > 0).astype(int)
df["has_card"]    = df["has_card"].fillna(0).astype(int)
df["has_swap"]    = (df["n_swaps"] > 0).astype(int)
df["has_ai"]      = (df["n_ai_sessions"] > 0).astype(int)
df["has_intl"]    = (df["n_international_payouts"] > 0).astype(int)

# ── Product breadth score (0–6) ──────────────────────────────────────────────
df["product_breadth_score"] = (
    df["has_onramp"] + df["has_offramp"] + df["has_card"]
    + df["has_swap"] + df["has_ai"] + df["has_intl"]
)

# ── Revenue generated (spread only) ──────────────────────────────────────────
df["revenue_generated_brl"] = df["total_spread_revenue_brl"]

# ── Notification read rate ────────────────────────────────────────────────────
df["notification_read_rate"] = (
    df["n_notifications_read"]
    / df["n_notifications_received"].replace(0, np.nan)
).fillna(0)

print("Breadth score distribution:")
print(df["product_breadth_score"].value_counts().sort_index())
```

- [ ] **Step 4: Run all three cells and verify**

Expected: shape `(~11000, ~45)`, 0 NaN remaining, breadth score 0 dominant (most users inactive).

- [ ] **Step 5: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add feature matrix assembly — merge, recency, composites"
```

---

## Task 7: Preprocessing pipeline (Section 3)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Define feature columns + apply log1p**

```python
# ── Feature column lists ─────────────────────────────────────────────────────
LOG_FEATURES = [
    "n_onramp_txns", "n_offramp_txns", "total_onramp_brl",
    "total_spread_revenue_brl", "n_card_txns", "total_card_spend_usd",
    "n_swaps", "total_swap_volume_usdc", "n_unique_tokens",
    "n_solana_txns", "n_ai_sessions", "total_ai_messages",
    "n_notifications_received", "n_international_payouts",
    "total_international_usdc", "founder_network_size", "invites_sent",
    "revenue_generated_brl",
]

BINARY_FLAGS = [
    "has_onramp", "has_offramp", "has_card", "has_swap", "has_ai", "has_intl",
    "is_founder", "paid_annual_fee", "card_variant_founder",
    "onboarding_completed", "account_type_business",
]

NUMERIC_FEATURES = [
    "days_since_signup", "days_since_last_active", "days_since_last_conversion",
    "days_since_last_card_spend", "days_since_last_swap",
    "kyc_level", "product_breadth_score", "notification_read_rate",
    "pct_instant_mode",
]

ALL_FEATURES = LOG_FEATURES + BINARY_FLAGS + NUMERIC_FEATURES

X_raw = df[ALL_FEATURES].copy()

# log1p on skewed count/monetary features — must be non-negative (assert first)
assert (X_raw[LOG_FEATURES] >= 0).all().all(), "Negative values in log features"
X_raw[LOG_FEATURES] = np.log1p(X_raw[LOG_FEATURES])

print(f"Feature matrix shape: {X_raw.shape}")
```

- [ ] **Step 2: Scale and apply PCA**

```python
# ── Scale ────────────────────────────────────────────────────────────────────
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X_raw)

# ── PCA (retain 90% variance) ─────────────────────────────────────────────────
pca = PCA(n_components=0.90, random_state=42)
X_pca = pca.fit_transform(X_scaled)

n_components = X_pca.shape[1]
variance_explained = pca.explained_variance_ratio_.sum()
print(f"PCA: {n_components} components → {variance_explained:.1%} variance retained")
assert 6 <= n_components <= 16, f"Unexpected component count: {n_components}"

# Variance explained per component (sanity check)
fig, ax = plt.subplots(figsize=(8, 3))
ax.bar(range(1, n_components + 1), pca.explained_variance_ratio_ * 100)
ax.set(xlabel="Component", ylabel="Variance explained (%)",
       title="PCA — Variance per component")
plt.tight_layout()
plt.savefig(f"{PROCESSED_DIR}/crm_pca_variance_{ANALYSIS_MONTH}.png", dpi=120)
plt.show()
```

- [ ] **Step 3: Run both cells and verify**

Expected: `Feature matrix shape: (~11000, ~40)`, `PCA: 8–12 components → ~90% variance retained`.

- [ ] **Step 4: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add preprocessing pipeline — log1p, StandardScaler, PCA"
```

---

## Task 8: Outlier detection — HDBSCAN (Section 4)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the HDBSCAN cell**

```python
# ── Outlier quarantine ────────────────────────────────────────────────────────
detector = hdbscan.HDBSCAN(
    min_cluster_size=15, min_samples=5, metric="euclidean"
)
outlier_labels = detector.fit_predict(X_pca)
outlier_mask   = outlier_labels == -1

print(f"Outliers identified: {outlier_mask.sum()} ({outlier_mask.mean():.1%} of users)")
```

- [ ] **Step 2: Add the outlier inspection cell**

```python
# ── Inspect outliers ──────────────────────────────────────────────────────────
# Are they bots, internal accounts, or data anomalies?
df_outliers = df[outlier_mask][[
    "user_id", "n_onramp_txns", "total_card_spend_usd",
    "n_swaps", "n_solana_txns", "days_since_last_active",
    "kyc_level", "product_breadth_score",
]].copy()

print(df_outliers.describe())
print(f"\nOutlier KYC distribution:\n{df_outliers['kyc_level'].value_counts()}")
print(f"\nOutlier breadth distribution:\n{df_outliers['product_breadth_score'].value_counts()}")
```

- [ ] **Step 3: Add the clean-subset cell**

```python
# ── Remove outliers before final clustering ───────────────────────────────────
X_pca_clean = X_pca[~outlier_mask]
X_raw_clean = X_raw[~outlier_mask].reset_index(drop=True)
df_clean    = df[~outlier_mask].copy().reset_index(drop=True)

print(f"Clean population: {len(df_clean):,} users ({outlier_mask.sum()} quarantined)")
assert len(df_clean) > 8_000, "Too many users quarantined — check HDBSCAN params"
```

- [ ] **Step 4: Run all three cells and verify**

Expected: `Outliers: <200 (<2%)`. If >5% are quarantined, increase `min_cluster_size` to 30.

- [ ] **Step 5: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add HDBSCAN outlier detection and clean population"
```

---

## Task 9: K selection diagnostic (Section 5)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the k-selection loop cell**

```python
# ── K selection: silhouette + Davies-Bouldin + inertia ───────────────────────
k_results = []

for k in range(4, 12):
    km = KMeans(n_clusters=k, init="k-means++", n_init=20, random_state=42)
    labels = km.fit_predict(X_pca_clean)
    sil = silhouette_score(X_pca_clean, labels, sample_size=5_000, random_state=42)
    db  = davies_bouldin_score(X_pca_clean, labels)
    k_results.append({
        "k": k,
        "silhouette": sil,
        "davies_bouldin": db,
        "inertia": km.inertia_,
    })
    print(f"k={k}: silhouette={sil:.3f}  DB={db:.3f}  inertia={km.inertia_:.0f}")

results_df = pd.DataFrame(k_results)
```

- [ ] **Step 2: Add the diagnostic plot cell**

```python
# ── Diagnostic plots ──────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(15, 4))

axes[0].plot(results_df["k"], results_df["silhouette"], "o-", color="steelblue")
axes[0].set(title="Silhouette (higher = better)", xlabel="k", ylabel="Score")

axes[1].plot(results_df["k"], results_df["davies_bouldin"], "o-", color="darkorange")
axes[1].set(title="Davies-Bouldin (lower = better)", xlabel="k", ylabel="Score")

axes[2].plot(results_df["k"], results_df["inertia"], "o-", color="green")
axes[2].set(title="Inertia — elbow curve", xlabel="k", ylabel="Inertia")

plt.suptitle("K Selection Diagnostics", y=1.02)
plt.tight_layout()
plt.savefig(f"{PROCESSED_DIR}/crm_k_selection_{ANALYSIS_MONTH}.png", dpi=120)
plt.show()

best_k = int(results_df.loc[results_df["silhouette"].idxmax(), "k"])
print(f"\nBest k by silhouette: {best_k}")
print("Review the elbow plot and confirm — override BEST_K below if needed.")
```

- [ ] **Step 3: Add the k override cell**

```python
# ── Set BEST_K here after reviewing the diagnostic plots ─────────────────────
# Default: silhouette peak. Override if elbow or business narrative suggests otherwise.
BEST_K = best_k   # change this integer if needed
print(f"Using BEST_K = {BEST_K}")
```

- [ ] **Step 4: Run all three cells and verify**

Expected: `best_k` in range 5–9. If silhouette < 0.15 for all k, revisit feature selection (check for near-zero-variance features).

- [ ] **Step 5: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add k-selection diagnostic — silhouette, DB index, elbow"
```

---

## Task 10: Final clustering — K-Means + GMM validation + stability (Section 6)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the K-Means cell**

```python
# ── Final K-Means ─────────────────────────────────────────────────────────────
km_final = KMeans(n_clusters=BEST_K, init="k-means++", n_init=20, random_state=42)
km_labels = km_final.fit_predict(X_pca_clean)

df_clean["cluster_id"] = km_labels
print("Cluster sizes:")
print(df_clean["cluster_id"].value_counts().sort_index())
```

- [ ] **Step 2: Add the GMM validation cell**

```python
# ── GMM cross-validation ──────────────────────────────────────────────────────
gmm = GaussianMixture(
    n_components=BEST_K, covariance_type="full", n_init=5, random_state=42
)
gmm_labels = gmm.fit_predict(X_pca_clean)

ari = adjusted_rand_score(km_labels, gmm_labels)
print(f"K-Means vs GMM — Adjusted Rand Index: {ari:.3f}")
if ari >= 0.85:
    print("✓ Clusters are robust across algorithm choice (ARI ≥ 0.85)")
else:
    print("⚠ Clusters differ between K-Means and GMM — consider revisiting k or features")
```

- [ ] **Step 3: Add the stability check cell**

```python
# ── Stability across seeds ────────────────────────────────────────────────────
seed_labels = []
for seed in [0, 7, 13, 99, 2024]:
    km_tmp = KMeans(n_clusters=BEST_K, init="k-means++", n_init=10, random_state=seed)
    seed_labels.append(km_tmp.fit_predict(X_pca_clean))

ari_pairs = []
for i in range(len(seed_labels)):
    for j in range(i + 1, len(seed_labels)):
        ari_pairs.append(adjusted_rand_score(seed_labels[i], seed_labels[j]))

min_ari = min(ari_pairs)
print(f"Stability — min pairwise ARI across 5 seeds: {min_ari:.3f}")
if min_ari >= 0.85:
    print("✓ Cluster structure is stable (ARI ≥ 0.85 across seeds)")
else:
    print("⚠ Clusters are unstable — increase n_init or reduce BEST_K")
```

- [ ] **Step 4: Run all three cells and verify**

Expected: GMM ARI > 0.85, stability ARI > 0.85. If either fails, reduce BEST_K by 1 and rerun from Task 9 Step 3.

- [ ] **Step 5: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add K-Means clustering with GMM and seed-stability validation"
```

---

## Task 11: Cluster profiling + naming (Section 7)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the centroid profile cell**

```python
# ── Cluster profiles in original (unscaled) feature space ─────────────────────
PROFILE_COLS = [
    "days_since_last_active", "days_since_signup", "kyc_level",
    "n_onramp_txns", "total_onramp_brl", "total_spread_revenue_brl",
    "n_card_txns", "total_card_spend_usd", "has_card",
    "n_swaps", "total_swap_volume_usdc", "n_solana_txns",
    "n_ai_sessions", "n_international_payouts",
    "product_breadth_score", "notification_read_rate",
    "founder_network_size", "onboarding_completed",
    "pct_instant_mode", "paid_annual_fee",
]

# Use original df values (before log transform) for interpretability
profile_mean   = df_clean.groupby("cluster_id")[PROFILE_COLS].mean().round(2)
profile_median = df_clean.groupby("cluster_id")[PROFILE_COLS].median().round(2)
cluster_sizes  = df_clean["cluster_id"].value_counts().rename("n_users").sort_index()

print("=== CLUSTER SIZES ===")
print(cluster_sizes)
print("\n=== CLUSTER MEANS (key features) ===")
key = ["days_since_last_active", "kyc_level", "n_onramp_txns", "n_card_txns",
       "n_swaps", "n_ai_sessions", "product_breadth_score", "total_spread_revenue_brl"]
print(profile_mean[key])
```

- [ ] **Step 2: Add the heatmap cell**

```python
# ── Cluster heatmap ───────────────────────────────────────────────────────────
from sklearn.preprocessing import MinMaxScaler

heatmap_cols = [
    "days_since_last_active", "kyc_level", "n_onramp_txns",
    "total_spread_revenue_brl", "n_card_txns", "total_card_spend_usd",
    "n_swaps", "n_solana_txns", "n_ai_sessions",
    "product_breadth_score", "notification_read_rate",
]

heat_data = profile_mean[heatmap_cols].copy()
# Normalise 0–1 per column for visual comparison
heat_norm = pd.DataFrame(
    MinMaxScaler().fit_transform(heat_data),
    index=heat_data.index,
    columns=heat_data.columns,
)

fig, ax = plt.subplots(figsize=(14, max(4, BEST_K)))
sns.heatmap(heat_norm, annot=profile_mean[heatmap_cols].values.round(1),
            fmt="g", cmap="YlOrRd", linewidths=0.5, ax=ax)
ax.set(title="Cluster profiles (cell = mean, color = 0–1 normalised)")
ax.set_ylabel("Cluster ID")
plt.tight_layout()
plt.savefig(f"{PROCESSED_DIR}/crm_cluster_heatmap_{ANALYSIS_MONTH}.png", dpi=120)
plt.show()
```

- [ ] **Step 3: Add the segment naming cell**

```python
# ── Assign segment names ──────────────────────────────────────────────────────
# Review the heatmap and cluster profiles above, then map cluster_id → name.
# This mapping MUST be updated after every full re-fit — cluster IDs shift.
# Expected archetypes (adjust to match your actual cluster profiles):
#   power_onramper     → high n_onramp_txns, high revenue, low days_since_active
#   card_spender       → high n_card_txns, paid_annual_fee=1, lower onramp
#   defi_trader        → high n_swaps, high n_unique_tokens, high n_solana_txns
#   occasional_onramer → 1–5 onramp txns, breadth=1, days_since_active 30–90
#   explorer_ai        → high n_ai_sessions, low financial txns
#   stalled_pre_kyc    → kyc_level≤1, onboarding=0, all activity=0
#   dormant            → days_since_active>90, some past activity

SEGMENT_NAMES = {
    0: "stalled_pre_kyc",    # ← replace with actual mapping after reviewing heatmap
    1: "occasional_onramer",
    2: "power_onramper",
    3: "card_spender",
    4: "explorer_ai",
    5: "defi_trader",
    6: "dormant",
}

# Trim or extend to match BEST_K
assert set(SEGMENT_NAMES.keys()) == set(range(BEST_K)), \
    f"SEGMENT_NAMES keys must match cluster IDs 0..{BEST_K-1}"
assert len(set(SEGMENT_NAMES.values())) == BEST_K, "Segment names must be unique"

df_clean["segment_name"] = df_clean["cluster_id"].map(SEGMENT_NAMES)
print(df_clean["segment_name"].value_counts())
```

- [ ] **Step 4: Run all three cells and verify**

Review the heatmap and update `SEGMENT_NAMES` to match the actual cluster profiles. Each cluster should have a clear narrative.

- [ ] **Step 5: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add cluster profiling heatmap and segment naming"
```

---

## Task 12: UMAP visualization (Section 8)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the UMAP cell**

```python
# ── UMAP 2D projection (visualization only — not used for clustering) ─────────
print("Running UMAP... (~1–2 min)")
reducer = umap.UMAP(n_neighbors=30, min_dist=0.1, random_state=42)
X_2d = reducer.fit_transform(X_pca_clean)

df_clean["umap_x"] = X_2d[:, 0]
df_clean["umap_y"] = X_2d[:, 1]

fig, ax = plt.subplots(figsize=(12, 8))
palette = sns.color_palette("tab10", n_colors=BEST_K)
for seg_id, seg_name in SEGMENT_NAMES.items():
    mask = df_clean["cluster_id"] == seg_id
    ax.scatter(
        df_clean.loc[mask, "umap_x"],
        df_clean.loc[mask, "umap_y"],
        s=6, alpha=0.5, color=palette[seg_id],
        label=f"{seg_id}: {seg_name} (n={mask.sum():,})",
    )
ax.legend(loc="upper right", fontsize=8, markerscale=3)
ax.set(title=f"NBS User Segments — UMAP projection ({ANALYSIS_MONTH})",
       xlabel="UMAP-1", ylabel="UMAP-2")
plt.tight_layout()
plt.savefig(f"{PROCESSED_DIR}/crm_umap_{ANALYSIS_MONTH}.png", dpi=150)
plt.show()
```

- [ ] **Step 2: Run the cell and verify**

Expected: a 2D scatter plot with visually separable clusters. If all clusters overlap completely, the clustering has not found structure — go back and review feature engineering.

- [ ] **Step 3: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add UMAP 2D visualization"
```

---

## Task 13: Message template library (Section 9, Part A)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the message template dict cell**

```python
# ── Push notification message library ────────────────────────────────────────
# Three time-of-day slots: morning (07–09h BRT), afternoon (12–14h BRT),
#                          evening (19–21h BRT)
# Each segment has 2–3 message variants per slot.

MESSAGES = {
    "power_onramper": {
        "campaign_goal": "Cross-sell DeFi swaps and card. VIP framing — never discount.",
        "morning": [
            ("Rate alert", "Start your day strong — your BRL is ready to convert at today's best rate."),
        ],
        "afternoon": [
            ("Favorable rate", "BRL/USDC is looking good right now. Convert in 60 seconds."),
        ],
        "evening": [
            ("Idle USDC?", "Your USDC is idle — have you tried a Jupiter swap to grow your portfolio?"),
        ],
    },
    "card_spender": {
        "campaign_goal": "Onramp cross-sell. They trust the card — remove friction to first conversion.",
        "morning": [
            ("Fund your card", "Fund your NBS card in 60 seconds — add BRL via PIX, convert instantly."),
        ],
        "afternoon": [
            ("Top up now", "Your card is ready to spend. Top up with PIX and never run low."),
        ],
        "evening": [
            ("Quick top-up", "Quick top-up before tomorrow? PIX → USDC in under a minute."),
        ],
    },
    "defi_trader": {
        "campaign_goal": "Deepen swap engagement. DeFi-native language only — no BRL/USDC pitch.",
        "morning": [
            ("New liquidity", "New liquidity on Jupiter — check today's best swap routes."),
        ],
        "afternoon": [
            ("Portfolio moving", "Your portfolio is moving. Swap tokens with zero hidden fees on NBS."),
        ],
        "evening": [
            ("DeFi never sleeps", "DeFi never sleeps. Spot a new opportunity? Swap it now."),
        ],
    },
    "occasional_onramer": {
        "campaign_goal": "Cross-sell depth — they onramp but stop there. Push the next product.",
        "morning": [
            ("Idle USDC", "Your USDC is sitting idle — put it to work with a swap or your NBS card."),
        ],
        "afternoon": [
            ("Been a while", "Been a while since your last conversion. Rates are looking good today."),
        ],
        "evening": [
            ("Next step", "One more step to unlock your full NBS wallet — try a swap or get your card."),
        ],
    },
    "explorer_ai": {
        "campaign_goal": "First conversion activation — they know the product, remove friction.",
        "morning": [
            ("Wallet ready", "Your NBS wallet is ready — buy USDC with PIX and unlock your card today."),
        ],
        "afternoon": [
            ("First conversion", "You've been exploring NBS — ready to make your first BRL → USDC conversion?"),
        ],
        "evening": [
            ("One PIX away", "Your account is set up. One PIX transfer and you're live."),
        ],
    },
    "stalled_pre_kyc": {
        "campaign_goal": "KYC funnel completion. Minimal copy, single CTA.",
        "delivery_rule": "Only send if device_tokens.is_active = TRUE.",
        "morning": [
            ("Verify now", "Verify in 2 minutes — unlock BRL ↔ USDC in real time."),
        ],
        "afternoon": [
            ("Account waiting", "Your NBS account is waiting. Complete verification to get started."),
        ],
        "evening": [
            ("2 minutes", "Takes 2 minutes. Then you're live on NBS."),
        ],
    },
    "dormant": {
        "campaign_goal": "Win-back. One send per quarter maximum.",
        "delivery_rule": "Skip if notification_read_rate = 0 over last 90 days.",
        "morning": [
            ("We've been busy", "We've been busy — NBS has new features since your last visit."),
        ],
        "afternoon": [
            ("Balance waiting", "Your balance is waiting. Log in and see what's new."),
        ],
        "evening": [
            ("Still here", "It's been a while. Your USDC is still here when you're ready."),
        ],
    },
}

# Validate template coverage matches segment names
assert set(MESSAGES.keys()) == set(SEGMENT_NAMES.values()), \
    f"MESSAGES keys must match SEGMENT_NAMES values.\nMissing: {set(SEGMENT_NAMES.values()) - set(MESSAGES.keys())}"

print("Message template library validated.")
for seg, data in MESSAGES.items():
    print(f"  {seg}: {data['campaign_goal'][:60]}")
```

- [ ] **Step 2: Run the cell and verify**

Expected: `Message template library validated.` followed by 7 segment summaries. If AssertionError, update `MESSAGES` keys to match your actual `SEGMENT_NAMES`.

---

## Task 14: CRM export + artifact saving (Section 9, Part B)

**Files:**
- Modify: `notebooks/crm_user_clustering.ipynb`

- [ ] **Step 1: Add the pushable users cross-join cell**

```python
# ── Cross-join with active push tokens ───────────────────────────────────────
sql_pushable = text("""
    SELECT DISTINCT user_id
    FROM device_tokens
    WHERE is_active = TRUE
""")
df_pushable = pd.read_sql(sql_pushable, engine)
print(f"Users with active push token: {len(df_pushable):,}")

crm_export = (
    df_clean[["user_id", "cluster_id", "segment_name",
              "notification_read_rate", "days_since_last_active"]]
    .merge(df_pushable, on="user_id", how="inner")
)

# Apply delivery rule for dormant: skip if notification_read_rate = 0
dormant_mask = (
    (crm_export["segment_name"] == "dormant")
    & (crm_export["notification_read_rate"] == 0)
)
crm_export = crm_export[~dormant_mask].copy()

print(f"Pushable users after delivery rules: {len(crm_export):,}")
print(crm_export["segment_name"].value_counts())
```

- [ ] **Step 2: Add the CSV export cell**

```python
# ── Save CRM export CSV ───────────────────────────────────────────────────────
# No PII — user_id only. Gitignored.
export_path = f"{PROCESSED_DIR}/crm_segments_{ANALYSIS_MONTH}.csv"
crm_export[["user_id", "cluster_id", "segment_name"]].to_csv(export_path, index=False)
print(f"CRM export saved: {export_path} ({len(crm_export):,} rows)")
```

- [ ] **Step 3: Add the artifact saving cell**

```python
# ── Save model artifacts for incremental scoring ──────────────────────────────
joblib.dump(scaler,   f"{PROCESSED_DIR}/crm_scaler_{ANALYSIS_MONTH}.pkl")
joblib.dump(pca,      f"{PROCESSED_DIR}/crm_pca_{ANALYSIS_MONTH}.pkl")
joblib.dump(km_final, f"{PROCESSED_DIR}/crm_kmeans_{ANALYSIS_MONTH}.pkl")
joblib.dump(SEGMENT_NAMES, f"{PROCESSED_DIR}/crm_segment_names_{ANALYSIS_MONTH}.pkl")

print("Saved artifacts:")
for suffix in ["scaler", "pca", "kmeans", "segment_names"]:
    path = f"{PROCESSED_DIR}/crm_{suffix}_{ANALYSIS_MONTH}.pkl"
    print(f"  {path}")

print("\nTo score new users:")
print("  scaler = joblib.load(scaler_path)")
print("  pca    = joblib.load(pca_path)")
print("  km     = joblib.load(kmeans_path)")
print("  names  = joblib.load(segment_names_path)")
print("  X_new_pca = pca.transform(scaler.transform(X_new_log1p))")
print("  labels    = km.predict(X_new_pca)")
```

- [ ] **Step 4: Run all three cells and verify**

Expected: CSV saved with `user_id, cluster_id, segment_name`, each segment has >0 rows, pkl files present in `data/processed/`.

- [ ] **Step 5: Clear output and commit**

```bash
jupyter nbconvert --clear-output --inplace notebooks/crm_user_clustering.ipynb
git add notebooks/crm_user_clustering.ipynb
git commit -m "feat(crm): add CRM export, delivery rules, and model artifact saving"
```

---

## Task 15: Verify .gitignore covers processed data

**Files:**
- Modify: `.gitignore` (if needed)

- [ ] **Step 1: Check that processed data is gitignored**

```bash
git status data/processed/
```

If `data/processed/` files appear as untracked — add them:

```bash
echo "data/processed/" >> .gitignore
git add .gitignore
git commit -m "chore: ensure data/processed/ is gitignored"
```

If already ignored, no action needed.

- [ ] **Step 2: Verify no PII-containing files are tracked**

```bash
git ls-files data/
```

Expected: no `.csv` or `.pkl` files listed. Only `data/.gitkeep` or similar.

---

## Self-Review Checklist

### Spec coverage
- [x] Notebook in `notebooks/` directory → Task 2
- [x] 8 SQL queries with all feature groups → Tasks 3–5
- [x] log1p → StandardScaler → PCA pipeline → Task 7
- [x] HDBSCAN outlier quarantine → Task 8
- [x] K selection diagnostic (silhouette + DB + elbow) → Task 9
- [x] K-Means + GMM ARI validation + seed stability → Task 10
- [x] Cluster profiling in original feature space → Task 11
- [x] UMAP visualization → Task 12
- [x] Message template library (3 time slots per segment) → Task 13
- [x] CRM export cross-joined with active push tokens → Task 14
- [x] Delivery rules: dormant skip if read_rate=0, stalled_pre_kyc active-token-only → Task 14
- [x] Saved joblib artifacts (scaler, pca, kmeans, segment_names) → Task 14
- [x] `crm` optional deps added to pyproject.toml → Task 1
- [x] gitignore covers processed data → Task 15
- [x] No output cells committed (nbconvert --clear-output in every commit step)

### Type/name consistency
- `SEGMENT_NAMES` dict keys (int) used throughout Tasks 11–13 ✓
- `MESSAGES` keys (str) matched against `SEGMENT_NAMES.values()` via assertion ✓
- `PROCESSED_DIR` defined in Task 2, used in Tasks 7, 9, 11, 12, 14 ✓
- `BEST_K` defined in Task 9, used in Tasks 10–12 ✓
- `X_pca_clean` / `df_clean` defined in Task 8, used in Tasks 9–14 ✓
- `km_final` defined in Task 10, used in Task 14 ✓

### No placeholders
Scanned — no TBD, TODO, or "implement later" in any step. All code blocks are complete.
