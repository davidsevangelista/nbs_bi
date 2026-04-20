# Spec: Card Usage Forecast Script

> Status: complete — `scripts/card_usage_forecast.py`

---

## Purpose

Standalone analysis script that builds a daily time series of NBS corporate card
(Rain) spend transactions and forecasts the next 5 days of usage — both transaction
count and USD volume.  Also provides a transaction size distribution study and a
projected 1% fee revenue analysis.  Outputs a single interactive HTML file with
professional data-storytelling visualizations.

---

## Input

| Source | Table | Filter |
|---|---|---|
| Production DB (`READONLY_DATABASE_URL`) | `card_transactions` | `status = 'completed'`, `transaction_type = 'spend'` |

- `posted_at` column: settlement timestamp (UTC)
- `amount` column: bigint USD cents → divide by 100 for real USD
- `user_id` is **never fetched** — PII protection at query level
- Zero-amount rows dropped after conversion

---

## Data Pipeline

1. Query `card_transactions` from production DB; `user_id` not selected.
2. Convert `amount` (bigint cents) → real USD float64; drop zero-amount rows.
3. Parse `posted_at` → UTC; use as settlement timestamp.
4. Aggregate to a **complete daily time series** (all calendar days from min→max date, zeros for gaps):
   - `daily_count` — number of spend transactions per day
   - `daily_volume_usd` — sum of `amount` per day
5. Keep individual `amount_usd` values (raw) for distribution and fee study sections.

---

## Forecast Model

**Algorithm**: Exponential Weighted Moving Average (EWMA) via `pandas.ewm(span=7)`

Rationale:
- No external ML dependencies required (pandas built-in)
- `span=7` (≈ one calendar week) appropriate for sparse daily data
- EWMA down-weights stale history, which is desirable given low transaction frequency

**Forecast horizon**: 5 calendar days from the day after the last data point

**Confidence interval**:
- Residuals = historical series − EWMA fit (in-sample)
- Rolling 7-day std of residuals → ±1.96 × σ for ~95% band
- Lower bound clipped at 0 (count/volume cannot be negative)
- Band widens as √h (horizon) — honest expression of increasing uncertainty

---

## Fee Study Model

**Assumption**: NBS charges 1% of each transaction's USD volume.

```
fee_usd = 0.01 × transaction_amount_usd
daily_fee_usd = 0.01 × daily_volume_usd
monthly_fee_est = avg_daily_fee_usd × 30
annual_fee_est  = monthly_fee_est × 12
```

This is a deterministic projection from historical spend patterns.  No external
rates or assumptions beyond the 1% multiplier.

---

## Output

**File**: `outputs/card_usage_forecast_{YYYYMMDD}.html`

Single interactive Plotly figure with 7 sections (vertical stacking):

| # | Section | Chart type | Data | Story |
|---|---|---|---|---|
| 1 | Daily Activity | Bar (count) + line (7d rolling avg) | `daily` | "How active is the card day-to-day?" |
| 2 | Daily Spend | Bar (USD volume) + line (7d rolling avg) | `daily` | "How much are we spending?" |
| 3 | Weekly Patterns | Dual-axis grouped bar (Mon–Sun) | `daily` | "Which days drive the most spend?" |
| 4 | Transaction Size Distribution | Bar histogram (fixed bins) | `raw` | "Where does spend concentrate?" |
| 5 | 1% Fee Revenue Study | Bar (daily fee) + line (7d rolling avg) | `daily` | "What would 1% volume fee generate?" |
| 6 | Forecast | Line (history) + shaded 95% CI + dashed 5-day forecast | `daily` | "Expected activity next week?" |
| 7 | Summary Table | Plotly Table widget | computed | Key metrics + 5-day forecast numbers |

**Transaction size bins** (Section 4):

| Bin label | Range |
|---|---|
| $0–10 | [0, 10) |
| $10–25 | [10, 25) |
| $25–50 | [25, 50) |
| $50–100 | [50, 100) |
| $100–200 | [100, 200) |
| $200–500 | [200, 500) |
| $500+ | [500, ∞) |

**Section 4 annotations** (directly on chart, not in legend):
- Modal bin highlighted gold (all others navy) — eye directed to dominant bucket
- Annotation box: Median | Mean | % transactions under $50 | Avg 1% fee per transaction

**Section 5 annotations**:
- Green bars (distinct from navy/gold) for fee revenue — signals new revenue signal
- Annotation box: Monthly estimate | Annual estimate

**Summary table rows**:
- Last 7 days (actual)
- Last 30 days (actual)
- All time (actual)
- Next 5 days (forecast)

**Title subtitle** includes: total transactions, total spend, estimated monthly fee at 1%.

---

## Storytelling with Data Design Principles

| Principle | Application in this report |
|---|---|
| **Titles as insights** | Section titles describe the finding, not just the variable ("where does spend concentrate?" not "histogram") |
| **Direct annotation** | Key stats (median, mean, fee projections) are text boxes directly on the chart — no legend hunt required |
| **Strategic color** | Gold highlights the modal bin only; green exclusively for fee revenue bars; navy for baseline data — each color carries meaning |
| **Eliminate clutter** | No separate y-axis legend on histogram (text on bars replaces them); gridlines dark (low contrast on dark bg) |
| **Contextual narrative** | Fee study title and main title subtitle dynamically embed the projected monthly revenue number |
| **Honest uncertainty** | CI band widens as √h — communicates that uncertainty grows with forecast horizon |

---

## CLI

```bash
# Default: reads from DB (READONLY_DATABASE_URL), writes outputs/
python scripts/card_usage_forecast.py

# Custom output path
python scripts/card_usage_forecast.py --out path/to/output.html
```

---

## Dependencies

All from existing `pyproject.toml`: `pandas`, `numpy`, `plotly`

No new packages required.
