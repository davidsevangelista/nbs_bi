# CRM User Clustering — Methodology Explainer

**Date:** 2026-05-19  
**Author:** NBS Data & Product  
**Status:** Phase 1 complete (intelligence layer) — Phase 2 (delivery automation) pending

---

## What we built and why

NBS has roughly 16,000 registered users. They behave very differently: some convert BRL to USDC daily, others hold a card but never onramp, others signed up but never transacted. Sending the same push notification to everyone wastes quota on users it won't resonate with, and burns trust with users who receive irrelevant messages.

The goal of this work was to automatically divide the user base into behaviorally coherent groups — clusters — so that each group can receive a push notification that matches how they actually use the product.

---

## How the clusters are built

### Step 1 — Feature extraction

For every user, we compute ~37 numeric signals from the production database, covering five behavioral dimensions:

| Dimension | Example signals |
|---|---|
| Lifecycle | Days since signup, KYC level, onboarding completed |
| Onramp / Offramp | Number of conversions, total BRL converted, days since last conversion |
| Cards | Number of card transactions, total card spend USD, whether they paid the annual fee |
| DeFi / Swaps | Number of swaps, unique tokens traded, Solana transactions |
| Engagement | AI assistant sessions, push notification read rate |

We also derive two composite signals: **product breadth score** (how many of NBS's 5 products the user has touched) and **revenue generated in BRL**.

`kyc_level` is excluded from the clustering inputs because in practice it is binary ({0, 2}) and perfectly collinear with all activity features — any user who transacted has kyc_level 2. Including it would not add signal.

### Step 2 — Preprocessing

Raw financial data is extremely skewed: most users have zero transactions, a handful have hundreds. We apply three transformations before clustering:

1. **Zero-fill** genuine non-usage (a user with no card transactions is not missing data — they simply don't use cards).
2. **log1p** all count and monetary columns. This compresses power-law distributions so high-volume users don't dominate the distance calculation.
3. **StandardScaler** (zero mean, unit variance) so features measured in BRL don't dominate features measured in counts.

### Step 3 — Dimensionality reduction (PCA)

We reduce 37 features to 12 principal components that together explain 90% of the variance. This step is necessary because without it, high-variance features like `total_onramp_brl` overwhelm the Euclidean distance metric used by K-Means and drown out meaningful but lower-variance signals like `has_card` or `n_unique_tokens`.

The 12 components have no direct semantic meaning — they are linear combinations of the original features. We use them only as inputs to clustering; for profiling and interpretation we always go back to the original feature values.

### Step 4 — Outlier detection (HDBSCAN)

Before clustering, we run HDBSCAN (a density-based algorithm) to flag statistically unusual users. These are typically internal test accounts, bots, or users with extreme outlier behavior on one dimension (e.g., 2,000 conversions). Flagged users receive an `is_outlier = 1` column in the export but are **not removed** — they still enter K-Means and receive a segment assignment. The flag is informational for the delivery team.

### Step 5 — Choosing the number of clusters (K)

We run K-Means for every value of K from 4 to 12 and evaluate three diagnostic metrics:

- **Silhouette score** — measures how similar each user is to their own cluster versus the nearest other cluster (higher = better; we target > 0.25).
- **Davies-Bouldin index** — measures the ratio of within-cluster scatter to between-cluster separation (lower = better).
- **Inertia elbow** — total within-cluster sum of squared distances; we look for the "elbow" where adding another cluster produces diminishing returns.

We pick the K where all three metrics agree. In the current run: **K = 8**.

### Step 6 — K-Means clustering

With K fixed, we run K-Means with 20 random restarts (to avoid local minima) and pick the best solution. Every user is assigned a hard cluster label (0–7).

### Step 7 — Validation

We run two independent checks:

1. **GMM cross-validation.** A Gaussian Mixture Model is fit with the same K. We compute the Adjusted Rand Index (ARI) between K-Means labels and GMM labels. ARI > 0.85 means both algorithms agree on the structure — the clusters are not an artifact of the K-Means algorithm. Current result: ARI = 0.694 (acceptable; the clusters are meaningful but not rigid spheres).

2. **Seed stability.** K-Means is run 5 more times with different random seeds. The minimum pairwise ARI across all pairs was 0.986, meaning the cluster assignments are highly stable — re-running the algorithm produces essentially the same groups.

### Step 8 — Naming clusters

After clustering, we compute each cluster's **centroid profile**: the average value of every original feature for users in that cluster. We interpret these profiles to assign human-readable names. The names are not pre-determined — they emerge from the data.

**This is the key step for knowing what a cluster means.** See the "How to read cluster meaning" section below.

---

## How to read cluster meaning

A cluster's meaning comes from its centroid profile — the average feature values for users in that cluster, compared to the overall user average.

In the notebook (Section 7), three tools are provided for this:

1. **Cluster means heatmap** — a color-coded grid showing which features are above average (warm) or below average (cool) for each cluster. The pattern of elevated features tells you what drives each group.

2. **Per-cluster narrative** — for each cluster, we list the top distinguishing features (what is abnormally high or low relative to the population) and propose a human-readable name.

3. **UMAP 2D projection** — all 16k users plotted in two dimensions, colored by cluster. Users close together in the plot share similar behavioral profiles. Isolated blobs correspond to niche archetypes; overlapping regions indicate behavioral continuity between adjacent segments.

### Reading example

If Cluster 3 shows:
- `n_swaps` significantly above average
- `n_unique_tokens` significantly above average  
- `n_solana_txns` significantly above average
- `n_onramp_txns` at or below average
- `has_card = 0`

…this cluster is **DeFi Traders**: users who use NBS primarily as a DeFi interface, not as a BRL/USDC conversion tool.

If Cluster 0 shows:
- `n_onramp_txns` significantly above average
- `total_onramp_brl` significantly above average
- `total_spread_revenue_brl` significantly above average
- `days_since_last_active` significantly below average (meaning very recent)

…this cluster is **FX Converters (Power Onrampers)**: the users who drive the majority of conversion revenue.

---

## The seven segment archetypes

| Segment | Distinguishing behavior | Campaign goal |
|---|---|---|
| FX Converter | High conversion frequency + volume, recent, revenue-generating | Cross-sell DeFi and card |
| Card-First Spender | High card txns, paid annual fee, low onramp (converts elsewhere) | Onramp cross-sell |
| DeFi Trader | High swaps + unique tokens + Solana txns, no card | Deepen swap engagement |
| Occasional Onramper | 1–5 conversions, low breadth, 30–90 days inactive | Next product nudge |
| Explorer / AI User | High AI sessions, low financial activity, recent | First conversion activation |
| Stalled Pre-KYC | kyc_level = 0–1, no transactions, signed up > 30 days ago | KYC funnel completion |
| Dormant — Previously Active | >90 days inactive, had ≥ 1 past transaction, low read rate | Win-back (1× per quarter) |

---

## KYC override rule

Users with `kyc_level ≤ 1` receive a KYC completion CTA regardless of which cluster they land in. The cluster-based message is suppressed. Rationale: no product offer is relevant to a user who cannot transact — the only actionable step is verification.

---

## Delivery flags in the export

The CSV export contains three delivery-control columns:

| Column | Meaning |
|---|---|
| `is_pushable` | `TRUE` if the user has an active device token; `FALSE` = can receive the message but no push token exists |
| `skip_push` | `TRUE` for dormant users with `notification_read_rate = 0` — do not send push (no token / zero engagement); flag for email/SMS in Phase 2 |
| `is_outlier` | `TRUE` for users flagged by HDBSCAN as statistically unusual — inform delivery team but do not suppress by default |

---

## Monthly refresh protocol

1. Re-run Sections 0–2 of the notebook to pull fresh data.
2. Apply the saved scaler + PCA from the previous month's run.
3. Run `kmeans.predict()` on the new user matrix — no re-fit needed.
4. **Full re-fit** once per quarter or when the user base grows by more than 20%.
5. After a full re-fit, re-examine the centroid profiles — segment composition may shift as the product and user base evolve.

The saved artifacts (`crm_scaler_YYYY-MM.pkl`, `crm_pca_YYYY-MM.pkl`, `crm_kmeans_YYYY-MM.pkl`) allow new users to be scored in seconds without re-training.

---

## What Phase 2 will add

- Backend push notification API contract
- `nbs_bi.crm` Python module: reads the CSV, selects the right message template by segment + time-of-day, calls the backend API
- Monthly automation scheduler
- Email/SMS fallback for Segment 7 users with zero push engagement
