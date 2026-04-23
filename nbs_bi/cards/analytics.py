"""Card transaction spend analytics — data layer and Plotly figure builders.

Loads completed card spend rows from the production DB (read-only), computes
daily/weekly aggregations, fee-model revenue estimates, EWMA demand forecasts,
B2B growth scenarios, and threshold-sweep optimisation for Model C.

All functions are pure (no Streamlit state).  UI rendering lives in
``nbs_bi.reporting.cards.CardAnalyticsSection``.

Typical usage::

    from nbs_bi.cards import analytics as ca

    raw   = ca.load_card_transactions()
    daily = ca.build_daily(raw)
    bins  = ca.bin_transactions(raw)
    fc    = ca.ewma_forecast(daily["daily_count"])
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import sqlalchemy as sa
from plotly.subplots import make_subplots

from nbs_bi.config import READONLY_DATABASE_URL
from nbs_bi.reporting.theme import (
    AMBER,
    BG,
    BLUE,
    EMERALD,
    GRID,
    PLOT_BG,
    ROSE,
    TEXT,
    TEXT_MUTED,
    VIOLET,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palette — dark theme (background/layout from theme.py)
# ---------------------------------------------------------------------------

# Aliases for local usage
PURPLE = VIOLET
SLATE = TEXT_MUTED
BLUE_LIGHT = "rgba(59, 130, 246, 0.35)"  # muted blue for secondary bar fills
AMBER_LIGHT = "#3B2800"  # dark amber for annotations/row highlights

# Fee model palette — used consistently across all fee charts
MODEL_COLORS: dict[str, str] = {
    "A — 1% volume": BLUE,
    "B — $0,99 fixo": AMBER,
    "C — $0,30 fixo / 1%": EMERALD,
    "D — 1% c/ teto $5": PURPLE,
}

# Scenario palette (baseline → aggressive)
SCENARIO_PALETTE = [SLATE, BLUE, EMERALD, PURPLE]

# Model C fee parameters
MODEL_C_FLAT: float = 0.30
MODEL_C_PCT: float = 0.01
MODEL_C_THRESHOLD_DEFAULT: float = 30.0

# Threshold sweep range for optimisation panel
SWEEP_THRESHOLDS: list[float] = [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200]

# Coverage grid sweep ranges (flat fee + pct combinations)
COVERAGE_FLAT_RANGE: list[float] = [0, 0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00]
COVERAGE_PCT_RANGE: list[float] = [0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03]
COVERAGE_FLAT_OPTIONS: list[float] = [0.10, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00]

# Spend-tier histogram bins
_BINS: list[float] = [0, 10, 25, 50, 100, 200, 500, float("inf")]
_BIN_LABELS: list[str] = ["$0–10", "$10–25", "$25–50", "$50–100", "$100–200", "$200–500", "$500+"]
_BIN_MIDPOINTS: list[float] = [5.0, 17.5, 37.5, 75.0, 150.0, 350.0, 750.0]

# Bin-based tiered fee — defaults and sweep range (public for reporting layer)
BIN_FEE_DEFAULTS: list[float] = [0.30, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00]
BIN_SWEEP_RANGE: list[float] = [round(v * 0.10, 2) for v in range(1, 21)]
RAIN_COST_DEFAULT: float = 6693.58

# Progressive fee model — defaults for the parametric sweep
PROG_FLAT_START_DEFAULT: float = 0.30
PROG_FLAT_END_DEFAULT: float = 3.00
PROG_PCT_START_DEFAULT: float = 0.02  # 2 %
PROG_PCT_END_DEFAULT: float = 0.0025  # 0.25 %

_EWMA_SPAN = 7
_FORECAST_DAYS = 5

_SQL = """\
SELECT amount, posted_at
FROM card_transactions
WHERE status = 'completed'
  AND transaction_type = 'spend'
  AND posted_at IS NOT NULL
ORDER BY posted_at
"""

_SQL_TOP_SPENDERS = """\
SELECT
    agg.user_id::text,
    u.full_name,
    COALESCE(
        ur.source_type,
        CASE WHEN f.invite_code IS NOT NULL AND f.invite_code <> ''
             THEN 'founder_invite' ELSE 'unknown' END
    )                               AS acquisition_source,
    rc.code                         AS referral_code,
    rc.public_name                  AS referral_code_name,
    agg.n_transactions,
    agg.total_usd,
    COALESCE(cq_agg.ramp_conversions, 0)::int AS ramp_conversions
FROM (
    SELECT
        user_id,
        COUNT(*)                AS n_transactions,
        SUM(amount)::float / 100 AS total_usd
    FROM card_transactions
    WHERE status = 'completed'
      AND transaction_type = 'spend'
      AND posted_at IS NOT NULL
      AND (:date_from IS NULL OR posted_at >= :date_from)
      AND (:date_to   IS NULL OR posted_at <  :date_to)
    GROUP BY user_id
    ORDER BY total_usd DESC
    LIMIT 20
) agg
LEFT JOIN users u               ON u.id = agg.user_id
LEFT JOIN user_registrations ur ON ur.user_id = agg.user_id
LEFT JOIN referral_codes rc     ON rc.id = ur.attributed_referral_code_id
LEFT JOIN founders f            ON f.user_id = agg.user_id
LEFT JOIN (
    SELECT user_id, COUNT(*) AS ramp_conversions
    FROM conversion_quotes
    WHERE used = TRUE
    GROUP BY user_id
) cq_agg                        ON cq_agg.user_id = agg.user_id
ORDER BY agg.total_usd DESC
"""

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_card_transactions(
    date_from: date | None = None,
    date_to: date | None = None,
    db_url: str = "",
) -> pd.DataFrame:
    """Fetch completed card spend rows from the DB.

    Args:
        date_from: Inclusive start filter (applied in Python after load).
        date_to: Inclusive end filter (applied in Python after load).
        db_url: Override the database URL; falls back to READONLY_DATABASE_URL.

    Returns:
        DataFrame with columns ``posted_at`` (UTC datetime) and
        ``amount_usd`` (float64, positive only). No PII is fetched.

    Raises:
        RuntimeError: If no database URL is configured.
    """
    url = db_url or READONLY_DATABASE_URL
    if not url:
        raise RuntimeError("No database URL configured. Set READONLY_DATABASE_URL in .env.")
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        raw = pd.read_sql(sa.text(_SQL), conn)
    logger.info("Loaded %d card spend rows from DB", len(raw))
    raw["amount_usd"] = raw["amount"].astype("float64") / 100
    raw["posted_at"] = pd.to_datetime(raw["posted_at"], utc=True)
    raw = raw[raw["amount_usd"] > 0].copy()
    raw = raw[["posted_at", "amount_usd"]]
    if date_from:
        raw = raw[raw["posted_at"].dt.date >= date_from]
    if date_to:
        raw = raw[raw["posted_at"].dt.date <= date_to]
    return raw


_SQL_ACTIVE_CARDS = """\
SELECT
    card_variant,
    COUNT(*) AS n_cards
FROM cards
WHERE status = 'active'
GROUP BY card_variant
"""


def load_active_cards_summary(db_url: str = "") -> dict[str, int]:
    """Query the cards table for live active card counts by variant.

    Args:
        db_url: Override the database URL; falls back to READONLY_DATABASE_URL.

    Returns:
        Dict with keys ``total``, ``founder``, ``basic`` (int counts).

    Raises:
        RuntimeError: If no database URL is configured.
    """
    url = db_url or READONLY_DATABASE_URL
    if not url:
        raise RuntimeError("No database URL configured. Set READONLY_DATABASE_URL in .env.")
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        rows = pd.read_sql(sa.text(_SQL_ACTIVE_CARDS), conn)
    counts: dict[str, int] = {"total": 0, "founder": 0, "basic": 0}
    for _, row in rows.iterrows():
        variant = str(row["card_variant"]).lower()
        n = int(row["n_cards"])
        counts["total"] += n
        if variant in counts:
            counts[variant] = n
    logger.info(
        "Active cards from DB — total: %d, founder: %d, basic: %d",
        counts["total"], counts["founder"], counts["basic"],
    )
    return counts


def load_top_card_spenders(
    date_from: date | None = None,
    date_to: date | None = None,
    db_url: str = "",
) -> pd.DataFrame:
    """Fetch per-user card spend aggregates with a ramp cross-sell signal.

    Args:
        date_from: Inclusive start filter on ``posted_at``.
        date_to: Exclusive end filter on ``posted_at``.
        db_url: Override the database URL; falls back to READONLY_DATABASE_URL.

    Returns:
        DataFrame with columns ``user_id`` (text), ``full_name``,
        ``acquisition_source``, ``referral_code``, ``referral_code_name``,
        ``n_transactions`` (int), ``total_usd`` (float64),
        ``ramp_conversions`` (int), sorted descending by ``total_usd``.
        Contains at most 20 rows.

    Raises:
        RuntimeError: If no database URL is configured.
    """
    url = db_url or READONLY_DATABASE_URL
    if not url:
        raise RuntimeError("No database URL configured. Set READONLY_DATABASE_URL in .env.")
    params = {
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
    }
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(_SQL_TOP_SPENDERS), conn, params=params)
    df["n_transactions"] = df["n_transactions"].astype("int64")
    df["ramp_conversions"] = df["ramp_conversions"].astype("int64")
    df["total_usd"] = df["total_usd"].astype("float64")
    logger.info("Loaded top %d card spenders from DB", len(df))
    return df


# ---------------------------------------------------------------------------
# Daily aggregation
# ---------------------------------------------------------------------------


def _observed_days(raw: pd.DataFrame) -> int:
    """Inclusive number of calendar days covered by raw transactions."""
    if raw.empty:
        return 1
    dates = pd.to_datetime(raw["posted_at"], utc=True).dt.tz_localize(None).dt.normalize()
    return max(1, int((dates.max() - dates.min()).days) + 1)


def build_daily(raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate raw spend rows to a complete daily time series with zero-fill.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.

    Returns:
        DataFrame indexed by ``date`` with columns
        ``daily_count`` and ``daily_volume_usd``.
    """
    df = raw.copy()
    df["date"] = df["posted_at"].dt.tz_localize(None).dt.normalize()
    agg = df.groupby("date").agg(
        daily_count=("amount_usd", "count"),
        daily_volume_usd=("amount_usd", "sum"),
    )
    full = pd.date_range(agg.index.min(), agg.index.max(), freq="D")
    return agg.reindex(full, fill_value=0).rename_axis("date")


# ---------------------------------------------------------------------------
# Spend-tier histogram
# ---------------------------------------------------------------------------


def bin_transactions(raw: pd.DataFrame) -> pd.DataFrame:
    """Bucket transactions into spend tiers.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.

    Returns:
        DataFrame[label, midpoint, count, pct] — one row per tier.
    """
    amounts = raw["amount_usd"].dropna()
    counts, _ = np.histogram(amounts, bins=_BINS)
    total = counts.sum()
    pct = counts / total * 100 if total > 0 else np.zeros(len(counts))
    return pd.DataFrame(
        {
            "label": _BIN_LABELS,
            "midpoint": _BIN_MIDPOINTS,
            "count": counts,
            "pct": pct,
        }
    )


# ---------------------------------------------------------------------------
# Fee model helpers
# ---------------------------------------------------------------------------


def _model_fee(
    amount: float,
    model: str,
    c_threshold: float = MODEL_C_THRESHOLD_DEFAULT,
) -> float:
    """Apply a single fee model to one transaction amount.

    Args:
        amount: Transaction value in USD.
        model: One of the MODEL_COLORS keys.
        c_threshold: Flat/% breakpoint for Model C.

    Returns:
        Fee in USD (float).
    """
    if model == "A — 1% volume":
        return round(amount * MODEL_C_PCT, 4)
    if model == "B — $0,99 fixo":
        return 0.99
    if model == "C — $0,30 fixo / 1%":
        return MODEL_C_FLAT if amount < c_threshold else round(amount * MODEL_C_PCT, 4)
    if model == "D — 1% c/ teto $5":
        return round(min(amount * MODEL_C_PCT, 5.00), 4)
    return 0.0


def fee_comparison(
    raw: pd.DataFrame,
    bins: pd.DataFrame,
    c_threshold: float = MODEL_C_THRESHOLD_DEFAULT,
) -> pd.DataFrame:
    """Total accumulated fee revenue per spend tier per model.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        bins: DataFrame from :func:`bin_transactions`.
        c_threshold: Model C flat/% breakpoint.

    Returns:
        DataFrame indexed by bin label, one column per fee model.
    """
    df = raw.copy()
    df["bin"] = pd.cut(df["amount_usd"], bins=_BINS, labels=_BIN_LABELS, right=False)
    rows = []
    for label in _BIN_LABELS:
        amounts = df.loc[df["bin"] == label, "amount_usd"]
        row: dict[str, Any] = {"label": label, "count": len(amounts)}
        for model in MODEL_COLORS:
            row[model] = round(amounts.apply(lambda x: _model_fee(x, model, c_threshold)).sum(), 2)
        rows.append(row)
    return pd.DataFrame(rows).set_index("label")


def monthly_revenue(
    raw: pd.DataFrame,
    c_threshold: float = MODEL_C_THRESHOLD_DEFAULT,
) -> dict[str, float]:
    """Estimate 30-day revenue per model extrapolated from the full history.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        c_threshold: Model C flat/% breakpoint.

    Returns:
        Dict mapping model name → estimated monthly revenue USD.
    """
    n_days = _observed_days(raw)
    result: dict[str, float] = {}
    for model in MODEL_COLORS:
        total = raw["amount_usd"].apply(lambda x: _model_fee(x, model, c_threshold)).sum()
        result[model] = round(total / n_days * 30, 2)
    return result


def coverage_analysis(
    monthly_rev: dict[str, float],
    rain_cost_usd: float,
    extra_models: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Coverage ratio for each fee model vs the Rain invoice monthly cost.

    Args:
        monthly_rev: Dict from :func:`monthly_revenue` — model name → 30-day revenue USD.
        rain_cost_usd: Target monthly cost (e.g. 6693.58 from Feb 2026 invoice).
        extra_models: Optional additional model name → monthly revenue USD mappings
            (e.g. inject a ``"$1,00 fixo"`` scenario).

    Returns:
        DataFrame with columns ``model``, ``revenue_usd``, ``cost_usd``,
        ``coverage_ratio``, ``margin_usd``. Sorted descending by coverage_ratio.
    """
    all_models = dict(monthly_rev)
    if extra_models:
        all_models.update(extra_models)
    rows = []
    for model, revenue in all_models.items():
        rows.append(
            {
                "model": model,
                "revenue_usd": round(revenue, 2),
                "cost_usd": round(rain_cost_usd, 2),
                "coverage_ratio": round(revenue / rain_cost_usd, 4) if rain_cost_usd else 0.0,
                "margin_usd": round(revenue - rain_cost_usd, 2),
            }
        )
    return pd.DataFrame(rows).sort_values("coverage_ratio", ascending=False).reset_index(drop=True)


def coverage_grid(
    raw: pd.DataFrame,
    rain_cost_usd: float,
    flat_range: list[float],
    pct_range: list[float],
) -> pd.DataFrame:
    """Coverage ratio grid over flat-fee × pct-fee combinations.

    For each (flat, pct) pair the per-transaction fee is
    ``flat + pct × amount_usd``. Monthly revenue is extrapolated to 30 days.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        rain_cost_usd: Target monthly Rain cost (USD).
        flat_range: Flat fee values to sweep (USD).
        pct_range: Percentage fee values to sweep (e.g. 0.01 = 1%).

    Returns:
        DataFrame indexed by flat_fee (rows), columns = pct_fee values.
        Cell values are coverage ratios (monthly revenue / rain_cost_usd).
    """
    n_days = _observed_days(raw)
    amounts = raw["amount_usd"].values
    rows = {}
    for flat in flat_range:
        row = {}
        for pct in pct_range:
            total_fee = float(np.sum(flat + pct * amounts))
            monthly = total_fee / n_days * 30
            row[pct] = round(monthly / rain_cost_usd, 4) if rain_cost_usd else 0.0
        rows[flat] = row
    df = pd.DataFrame(rows).T
    df.index.name = "flat_fee"
    df.columns.name = "pct_fee"
    return df


def flat_pct_monthly_revenue(
    raw: pd.DataFrame,
    flat_fee_usd: float,
    pct_fee: float,
) -> float:
    """Estimate monthly revenue for ``flat_fee_usd + pct_fee * amount_usd``.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        flat_fee_usd: Fixed fee per transaction in USD.
        pct_fee: Variable fee as a decimal (e.g. ``0.01`` for 1%).

    Returns:
        30-day extrapolated monthly revenue in USD.
    """
    if flat_fee_usd < 0:
        raise ValueError("flat_fee_usd must be non-negative")
    if pct_fee < 0:
        raise ValueError("pct_fee must be non-negative")

    n_days = _observed_days(raw)
    amounts = raw["amount_usd"].values
    total_fee = float(np.sum(flat_fee_usd + pct_fee * amounts))
    return round(total_fee / n_days * 30, 2)


def flat_pct_coverage_metrics(
    raw: pd.DataFrame,
    rain_cost_usd: float,
    flat_fee_usd: float,
    pct_fee: float,
) -> dict[str, float]:
    """Coverage and breakeven metrics for a flat + variable card fee.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        rain_cost_usd: Monthly Rain invoice cost to cover.
        flat_fee_usd: Fixed fee per transaction in USD.
        pct_fee: Variable fee as a decimal.

    Returns:
        Dict with projected monthly volume/count, revenue, coverage, margin,
        required percentage with the selected flat fee, and required flat fee
        with the selected percentage.
    """
    if rain_cost_usd < 0:
        raise ValueError("rain_cost_usd must be non-negative")

    n_days = _observed_days(raw)
    tx_month = len(raw) / n_days * 30
    volume_month_usd = float(raw["amount_usd"].sum()) / n_days * 30
    revenue_usd = flat_pct_monthly_revenue(raw, flat_fee_usd, pct_fee)
    flat_revenue_usd = flat_fee_usd * tx_month
    pct_revenue_usd = pct_fee * volume_month_usd

    required_pct = (
        max(0.0, (rain_cost_usd - flat_revenue_usd) / volume_month_usd)
        if volume_month_usd > 0
        else float("inf")
    )
    required_flat = (
        max(0.0, (rain_cost_usd - pct_revenue_usd) / tx_month) if tx_month > 0 else float("inf")
    )

    return {
        "n_days": float(n_days),
        "tx_month": round(tx_month, 2),
        "volume_month_usd": round(volume_month_usd, 2),
        "revenue_usd": revenue_usd,
        "cost_usd": round(rain_cost_usd, 2),
        "coverage_ratio": round(revenue_usd / rain_cost_usd, 4) if rain_cost_usd else 0.0,
        "margin_usd": round(revenue_usd - rain_cost_usd, 2),
        "required_pct_with_flat": round(required_pct, 6),
        "required_flat_with_pct": round(required_flat, 4),
    }


# ---------------------------------------------------------------------------
# Bin-based tiered fee
# ---------------------------------------------------------------------------


def bin_fee_revenue(
    raw: pd.DataFrame,
    bin_fees: list[float],
    bin_pct_fees: list[float] | None = None,
) -> pd.DataFrame:
    """Monthly revenue breakdown by spend tier for a tiered flat + pct fee structure.

    Each transaction is charged ``flat_fee + pct_fee * amount_usd`` for its tier.
    Revenue is extrapolated to 30 days.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        bin_fees: Flat fee in USD for each tier; must have len == 7.
        bin_pct_fees: Optional percentage fee (0–1) per tier.  Defaults to all
            zeros (flat-only).

    Returns:
        DataFrame[label, count, pct_count, fee_usd, revenue_obs_usd,
        revenue_month_usd] — one row per tier.

    Raises:
        ValueError: If ``bin_fees`` or ``bin_pct_fees`` has wrong length.
    """
    if len(bin_fees) != len(_BIN_LABELS):
        raise ValueError(f"bin_fees must have {len(_BIN_LABELS)} elements")
    pct_fees: list[float] = bin_pct_fees if bin_pct_fees is not None else [0.0] * len(_BIN_LABELS)
    if len(pct_fees) != len(_BIN_LABELS):
        raise ValueError(f"bin_pct_fees must have {len(_BIN_LABELS)} elements")
    n_days = _observed_days(raw)
    amounts = raw["amount_usd"].dropna().to_numpy(dtype="float64")
    bin_idx = np.clip(
        np.searchsorted(np.array(_BINS[1:]), amounts, side="right"), 0, len(_BIN_LABELS) - 1
    )
    total = len(amounts)
    rows = []
    for i, label in enumerate(_BIN_LABELS):
        mask = bin_idx == i
        amt_bin = amounts[mask]
        count = int(mask.sum())
        rev_obs = float((bin_fees[i] + pct_fees[i] * amt_bin).sum()) if count else 0.0
        rows.append(
            {
                "label": label,
                "count": count,
                "pct_count": round(count / total * 100, 2) if total > 0 else 0.0,
                "fee_usd": bin_fees[i],
                "revenue_obs_usd": round(rev_obs, 4),
                "revenue_month_usd": round(rev_obs / n_days * 30, 4),
            }
        )
    return pd.DataFrame(rows)


def bin_fee_coverage_metrics(
    raw: pd.DataFrame,
    bin_fees: list[float],
    rain_cost_usd: float = RAIN_COST_DEFAULT,
) -> dict[str, float]:
    """Coverage and breakeven metrics for a tiered flat fee structure.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        bin_fees: Flat fee in USD for each spend tier.
        rain_cost_usd: Monthly Rain invoice cost to cover.

    Returns:
        Dict with ``tx_month``, ``revenue_usd``, ``coverage_ratio``,
        ``margin_usd``, ``breakeven_uniform_flat``.
    """
    breakdown = bin_fee_revenue(raw, bin_fees)
    n_days = _observed_days(raw)
    tx_month = len(raw) / n_days * 30
    revenue_usd = round(float(breakdown["revenue_month_usd"].sum()), 2)
    coverage_ratio = round(revenue_usd / rain_cost_usd, 4) if rain_cost_usd else 0.0
    breakeven_flat = round(rain_cost_usd / tx_month, 4) if tx_month > 0 else float("inf")
    return {
        "tx_month": round(tx_month, 2),
        "revenue_usd": revenue_usd,
        "coverage_ratio": coverage_ratio,
        "margin_usd": round(revenue_usd - rain_cost_usd, 2),
        "breakeven_uniform_flat": breakeven_flat,
    }


def bin_fee_sweep(
    raw: pd.DataFrame,
    i_bin: int,
    j_bin: int,
    fee_range: list[float],
    fixed_fees: list[float],
    rain_cost_usd: float = RAIN_COST_DEFAULT,
) -> pd.DataFrame:
    """Coverage-ratio grid sweeping two bins across a fee range.

    All other bins are held at ``fixed_fees``.  Rows = i_bin fee values,
    columns = j_bin fee values, cells = coverage ratio.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        i_bin: Index of the row-axis bin (0–6).
        j_bin: Index of the column-axis bin (0–6).
        fee_range: Fee values (USD) to test for both bins.
        fixed_fees: Baseline flat fees for all 7 tiers.
        rain_cost_usd: Monthly Rain invoice cost.

    Returns:
        DataFrame indexed by i_bin fees, columns = j_bin fees.

    Raises:
        ValueError: If fixed_fees has wrong length or bin indices are out of range.
    """
    if len(fixed_fees) != len(_BIN_LABELS):
        raise ValueError(f"fixed_fees must have {len(_BIN_LABELS)} elements")
    if not (0 <= i_bin < len(_BIN_LABELS) and 0 <= j_bin < len(_BIN_LABELS)):
        raise ValueError("i_bin and j_bin must be in range 0–6")
    n_days = _observed_days(raw)
    amounts = raw["amount_usd"].dropna()
    counts, _ = np.histogram(amounts, bins=_BINS)
    rows: dict[float, dict[float, float]] = {}
    for i_fee in fee_range:
        row: dict[float, float] = {}
        for j_fee in fee_range:
            fees = list(fixed_fees)
            fees[i_bin] = i_fee
            fees[j_bin] = j_fee
            rev_obs = sum(int(c) * f for c, f in zip(counts, fees))
            monthly = rev_obs / n_days * 30
            row[j_fee] = round(monthly / rain_cost_usd, 4) if rain_cost_usd else 0.0
        rows[i_fee] = row
    df = pd.DataFrame(rows).T
    df.index.name = _BIN_LABELS[i_bin]
    df.columns.name = _BIN_LABELS[j_bin]
    return df


# ---------------------------------------------------------------------------
# Progressive fee model (equal-width bins, flat ↑ / pct ↓ progression)
# ---------------------------------------------------------------------------


def progressive_fee_revenue(
    raw: pd.DataFrame,
    n_bins: int,
    gap: float,
    flat_start: float = PROG_FLAT_START_DEFAULT,
    flat_end: float = PROG_FLAT_END_DEFAULT,
    pct_start: float = PROG_PCT_START_DEFAULT,
    pct_end: float = PROG_PCT_END_DEFAULT,
    rain_cost_usd: float = RAIN_COST_DEFAULT,
    flat_fees: list[float] | None = None,
) -> dict[str, float]:
    """Compute monthly revenue for a progressive flat+pct fee on equal-width bins.

    Bins are ``[0, gap)``, ``[gap, 2*gap)``, …, ``[(n_bins-1)*gap, infinity)``.
    Transactions above the last finite edge are assigned to the last bin.  Flat fee
    increases linearly from ``flat_start`` to ``flat_end``; pct fee decreases
    linearly from ``pct_start`` to ``pct_end``.

    Args:
        raw: DataFrame with ``amount_usd`` and ``posted_at`` columns.
        n_bins: Number of equal-width bins.
        gap: Bin width in USD.
        flat_start: Flat fee for the first (lowest) bin, in USD.
        flat_end: Flat fee for the last (highest) bin, in USD.
        pct_start: Percentage fee for the first bin (e.g. 0.02 for 2 %).
        pct_end: Percentage fee for the last bin.
        rain_cost_usd: Invoice cost used to compute coverage ratio.
        flat_fees: Optional explicit flat fee per bin. When provided, overrides
            the linear ``flat_start`` → ``flat_end`` progression.

    Returns:
        Dict with keys ``revenue_usd``, ``coverage_ratio``, ``gap``.

    Raises:
        ValueError: If ``flat_fees`` length does not match ``n_bins``.
    """
    if flat_fees is not None and len(flat_fees) != n_bins:
        raise ValueError(f"flat_fees must have {n_bins} elements")
    if raw.empty:
        return {"revenue_usd": 0.0, "coverage_ratio": 0.0, "gap": gap}
    flat_fee_values = (
        np.array(flat_fees, dtype="float64")
        if flat_fees is not None
        else np.linspace(flat_start, flat_end, n_bins)
    )
    pct_fees = np.linspace(pct_start, pct_end, n_bins)
    edges = np.arange(0, (n_bins + 1) * gap, gap)
    amounts = raw["amount_usd"].to_numpy(dtype="float64")
    bin_idx = np.searchsorted(edges[1:], amounts, side="right")
    bin_idx = np.clip(bin_idx, 0, n_bins - 1)
    revenue_obs = (flat_fee_values[bin_idx] + pct_fees[bin_idx] * amounts).sum()
    revenue_month = revenue_obs / _observed_days(raw) * 30
    return {
        "revenue_usd": float(revenue_month),
        "coverage_ratio": float(revenue_month / rain_cost_usd),
        "gap": float(gap),
    }


def progressive_fee_sweep(
    raw: pd.DataFrame,
    gap_values: list[float],
    n_bins: int = 10,
    flat_start: float = PROG_FLAT_START_DEFAULT,
    flat_end: float = PROG_FLAT_END_DEFAULT,
    pct_start: float = PROG_PCT_START_DEFAULT,
    pct_end: float = PROG_PCT_END_DEFAULT,
    rain_cost_usd: float = RAIN_COST_DEFAULT,
) -> pd.DataFrame:
    """Sweep over gap values and return coverage ratios.

    Args:
        raw: DataFrame with ``amount_usd`` and ``posted_at`` columns.
        gap_values: List of bin widths (USD) to evaluate.
        n_bins: Number of equal-width bins (fixed across all gaps).
        flat_start: Flat fee for the first bin (USD).
        flat_end: Flat fee for the last bin (USD).
        pct_start: Percentage fee for the first bin.
        pct_end: Percentage fee for the last bin.
        rain_cost_usd: Invoice cost used to compute coverage ratio.

    Returns:
        DataFrame with columns ``gap``, ``revenue_usd``, ``coverage_ratio``.
    """
    rows = [
        progressive_fee_revenue(
            raw, n_bins, g, flat_start, flat_end, pct_start, pct_end, rain_cost_usd
        )
        for g in gap_values
    ]
    return pd.DataFrame(rows)[["gap", "revenue_usd", "coverage_ratio"]]


def progressive_fee_breakdown(
    raw: pd.DataFrame,
    n_bins: int,
    gap: float,
    flat_start: float = PROG_FLAT_START_DEFAULT,
    flat_end: float = PROG_FLAT_END_DEFAULT,
    pct_start: float = PROG_PCT_START_DEFAULT,
    pct_end: float = PROG_PCT_END_DEFAULT,
    rain_cost_usd: float = RAIN_COST_DEFAULT,
    flat_fees: list[float] | None = None,
) -> pd.DataFrame:
    """Per-bin revenue breakdown for the progressive fee model.

    Same binning logic as :func:`progressive_fee_revenue` but returns one row
    per bin so the caller can display the contribution of each tier.

    Args:
        raw: DataFrame with ``amount_usd`` and ``posted_at`` columns.
        n_bins: Number of equal-width bins.
        gap: Bin width in USD.
        flat_start: Flat fee for the first bin (USD).
        flat_end: Flat fee for the last bin (USD).
        pct_start: Percentage fee for the first bin.
        pct_end: Percentage fee for the last bin.
        rain_cost_usd: Invoice cost used to compute per-bin coverage factor.
        flat_fees: Optional explicit flat fee per bin. When provided, overrides
            the linear ``flat_start`` → ``flat_end`` progression.

    Returns:
        DataFrame with columns ``bin``, ``from_usd``, ``to_usd``,
        ``flat_usd``, ``pct``, ``count``, ``pct_count``,
        ``revenue_month_usd``, ``invoice_factor``.

    Raises:
        ValueError: If ``flat_fees`` length does not match ``n_bins``.
    """
    if flat_fees is not None and len(flat_fees) != n_bins:
        raise ValueError(f"flat_fees must have {n_bins} elements")
    flat_fee_values = (
        np.array(flat_fees, dtype="float64")
        if flat_fees is not None
        else np.linspace(flat_start, flat_end, n_bins)
    )
    pct_fees = np.linspace(pct_start, pct_end, n_bins)
    edges = np.arange(0, (n_bins + 1) * gap, gap)
    obs_days = _observed_days(raw)
    amounts = raw["amount_usd"].to_numpy(dtype="float64") if not raw.empty else np.array([])
    if amounts.size:
        bin_idx = np.clip(np.searchsorted(edges[1:], amounts, side="right"), 0, n_bins - 1)
    else:
        bin_idx = np.array([], dtype=int)
    rows = []
    total = len(amounts)
    for i in range(n_bins):
        mask = bin_idx == i
        count = int(mask.sum())
        rev_obs = (
            float((flat_fee_values[i] + pct_fees[i] * amounts[mask]).sum()) if mask.any() else 0.0
        )
        rev_month = rev_obs / obs_days * 30
        rows.append(
            {
                "bin": i + 1,
                "from_usd": float(edges[i]),
                "to_usd": float(edges[i + 1]) if i < n_bins - 1 else float("inf"),
                "flat_usd": float(flat_fee_values[i]),
                "pct": float(pct_fees[i]),
                "count": count,
                "pct_count": round(count / total * 100, 2) if total > 0 else 0.0,
                "revenue_month_usd": rev_month,
                "invoice_factor": rev_month / rain_cost_usd,
            }
        )
    return pd.DataFrame(rows)


def _project_monthly_revenue(
    raw: pd.DataFrame,
    tx_mult: float,
    size_mult: float,
    c_threshold: float = MODEL_C_THRESHOLD_DEFAULT,
) -> dict[str, float]:
    """Project monthly revenue at a scaled transaction count and ticket size.

    Preserves the real distribution shape by scaling each historical amount
    by ``size_mult`` before computing fees, then scaling the daily rate by
    ``tx_mult``.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        tx_mult: Transaction volume multiplier.
        size_mult: Ticket-size multiplier.
        c_threshold: Model C flat/% breakpoint.

    Returns:
        Dict mapping model name → projected monthly revenue USD.
    """
    n_days = _observed_days(raw)
    result: dict[str, float] = {}
    for model in MODEL_COLORS:
        total = (
            raw["amount_usd"].apply(lambda x: _model_fee(x * size_mult, model, c_threshold)).sum()
        )
        result[model] = round(total / n_days * 30 * tx_mult, 2)
    return result


# ---------------------------------------------------------------------------
# B2B growth scenarios
# ---------------------------------------------------------------------------


def build_scenarios(
    raw: pd.DataFrame,
    tx_mults: list[float],
    size_mults: list[float],
    labels: list[str],
    c_threshold: float = MODEL_C_THRESHOLD_DEFAULT,
) -> pd.DataFrame:
    """Build a scenario × model monthly-revenue DataFrame.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        tx_mults: Transaction volume multipliers (one per scenario).
        size_mults: Ticket-size multipliers (one per scenario).
        labels: Human-readable scenario names.
        c_threshold: Model C flat/% breakpoint.

    Returns:
        DataFrame with columns: scenario, tx_mult, size_mult,
        avg_ticket_usd, and one column per fee model.
    """
    median_actual = raw["amount_usd"].median()
    rows = []
    for label, tx_mult, size_mult in zip(labels, tx_mults, size_mults):
        rev = _project_monthly_revenue(raw, tx_mult, size_mult, c_threshold=c_threshold)
        rows.append(
            {
                "scenario": label,
                "tx_mult": tx_mult,
                "size_mult": size_mult,
                "avg_ticket_usd": round(median_actual * size_mult, 2),
                **rev,
            }
        )
    return pd.DataFrame(rows)


def threshold_sweep(
    raw: pd.DataFrame,
    thresholds: list[float],
    scenarios: pd.DataFrame,
) -> pd.DataFrame:
    """Model C monthly revenue for every threshold × scenario combination.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        thresholds: List of threshold values to evaluate.
        scenarios: DataFrame from :func:`build_scenarios`.

    Returns:
        DataFrame[threshold, <scenario_label> …] — one row per threshold.
    """
    n_days = _observed_days(raw)
    rows = []
    for t in thresholds:
        row: dict[str, Any] = {"threshold": t}
        for _, sc in scenarios.iterrows():
            tx_mult, size_mult = sc["tx_mult"], sc["size_mult"]
            total = (
                raw["amount_usd"]
                .apply(lambda x: _model_fee(x * size_mult, "C — $0,30 fixo / 1%", t))
                .sum()
            )
            row[sc["scenario"]] = round(total / n_days * 30 * tx_mult, 2)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Combination grid
# ---------------------------------------------------------------------------


def compute_combinations(
    raw: pd.DataFrame,
    flat_fees: list[float],
    thresholds: list[float],
    pct: float,
    tx_mult: float,
    size_mult: float,
) -> pd.DataFrame:
    """Monthly revenue for every (flat_fee, threshold) pair.

    Args:
        raw: DataFrame from :func:`load_card_transactions`.
        flat_fees: List of flat fee values to test.
        thresholds: List of threshold values to test.
        pct: Percentage fee applied above threshold (e.g. 0.01 for 1%).
        tx_mult: Transaction volume multiplier.
        size_mult: Ticket-size multiplier.

    Returns:
        DataFrame[flat_fee, threshold, monthly_rev].
    """
    n_days = _observed_days(raw)
    amounts_scaled = raw["amount_usd"].values * size_mult
    rows = []
    for flat in flat_fees:
        for t in thresholds:
            fees = np.where(amounts_scaled < t, flat, amounts_scaled * pct)
            rows.append(
                {
                    "flat_fee": flat,
                    "threshold": t,
                    "monthly_rev": round(float(fees.sum()) / n_days * 30 * tx_mult, 2),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# EWMA forecast
# ---------------------------------------------------------------------------


def ewma_forecast(series: pd.Series) -> dict[str, Any]:
    """EWMA fit + 5-day mean-reverting forecast with seasonal adjustment and CI.

    Args:
        series: Daily time series (e.g. daily_count or daily_volume_usd).

    Returns:
        Dict with keys: ``fit``, ``forecast``, ``ci_lower``, ``ci_upper``
        (all pandas Series).
    """
    alpha = 2.0 / (_EWMA_SPAN + 1)
    ewm = series.ewm(span=_EWMA_SPAN, adjust=True).mean()
    residuals = series - ewm
    rolling_std = residuals.rolling(_EWMA_SPAN, min_periods=1).std().fillna(0)
    base_std = max(rolling_std.iloc[-1], residuals.std())

    dow_means = series.groupby(series.index.day_of_week).mean()
    overall_mean = series.mean()
    dow_factors = (
        (dow_means / overall_mean).reindex(range(7), fill_value=1.0)
        if overall_mean > 0
        else pd.Series(1.0, index=range(7))
    )
    long_run = series.iloc[-min(30, len(series)) :].mean()

    fc_dates = pd.date_range(series.index[-1] + timedelta(days=1), periods=_FORECAST_DAYS, freq="D")
    level = ewm.iloc[-1]
    pts, lo, hi = [], [], []
    for h, d in enumerate(fc_dates, start=1):
        level = alpha * long_run + (1 - alpha) * level
        seasonal = float(dow_factors.at[d.dayofweek]) if d.dayofweek in dow_factors.index else 1.0
        pt = max(0.0, level * seasonal)
        margin = 1.96 * base_std * (h**0.5)
        pts.append(pt)
        lo.append(max(0.0, pt - margin))
        hi.append(pt + margin)

    return {
        "fit": ewm,
        "forecast": pd.Series(pts, index=fc_dates),
        "ci_lower": pd.Series(lo, index=fc_dates),
        "ci_upper": pd.Series(hi, index=fc_dates),
    }


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------


def summary_metrics(
    daily: pd.DataFrame,
    count_fc: dict[str, Any],
    vol_fc: dict[str, Any],
) -> dict[str, Any]:
    """Key metrics for the Indicadores tab and DOCX report.

    Args:
        daily: DataFrame from :func:`build_daily`.
        count_fc: Output of :func:`ewma_forecast` on daily_count.
        vol_fc: Output of :func:`ewma_forecast` on daily_volume_usd.

    Returns:
        Dict with keys: periods, tx_count, volume_usd,
        avg_count_day, avg_vol_day.
    """
    today = daily.index.max()

    def _window(days: int) -> pd.DataFrame:
        return daily[daily.index >= today - timedelta(days=days - 1)]

    w7, w30 = _window(7), _window(30)
    fc_c, fc_v = count_fc["forecast"], vol_fc["forecast"]
    return {
        "periods": [
            "Últimos 7 dias",
            "Últimos 30 dias",
            "Histórico completo",
            "Próximos 5 dias (proj.)",
        ],
        "tx_count": [
            int(w7["daily_count"].sum()),
            int(w30["daily_count"].sum()),
            int(daily["daily_count"].sum()),
            round(fc_c.sum(), 1),
        ],
        "volume_usd": [
            w7["daily_volume_usd"].sum(),
            w30["daily_volume_usd"].sum(),
            daily["daily_volume_usd"].sum(),
            fc_v.sum(),
        ],
        "avg_count_day": [
            round(w7["daily_count"].mean(), 1),
            round(w30["daily_count"].mean(), 1),
            round(daily["daily_count"].mean(), 1),
            round(fc_c.mean(), 1),
        ],
        "avg_vol_day": [
            round(w7["daily_volume_usd"].mean(), 2),
            round(w30["daily_volume_usd"].mean(), 2),
            round(daily["daily_volume_usd"].mean(), 2),
            round(fc_v.mean(), 2),
        ],
    }


# ---------------------------------------------------------------------------
# Shared Plotly layout helper
# ---------------------------------------------------------------------------


def _panel_layout(title: str, height: int = 370) -> dict[str, Any]:
    axis_style: dict[str, Any] = dict(
        gridcolor=GRID,
        zerolinecolor=GRID,
        tickfont=dict(color=TEXT_MUTED, size=11),
        linecolor=GRID,
        title_font=dict(size=11, color=TEXT_MUTED),
    )
    return dict(
        title=dict(
            text=title,
            font=dict(size=13, color=BLUE, family="Arial"),
            x=0.0,
            xanchor="left",
            pad=dict(l=4),
        ),
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(color=TEXT, family="Arial", size=11),
        height=height,
        autosize=True,
        margin=dict(l=55, r=15, t=50, b=55),
        legend=dict(
            orientation="h",
            x=0.0,
            y=-0.18,
            font=dict(size=10, color=TEXT),
            bgcolor="rgba(0,0,0,0)",
        ),
        barmode="group",
        xaxis=axis_style,
        yaxis=axis_style,
    )


# ---------------------------------------------------------------------------
# Plotly figure builders
# ---------------------------------------------------------------------------


def fig_distribution(
    bins: pd.DataFrame,
    median_amt: float,
    mean_amt: float,
) -> go.Figure:
    """Transaction size histogram, modal bin highlighted in amber.

    Args:
        bins: DataFrame from :func:`bin_transactions`.
        median_amt: Median transaction amount USD.
        mean_amt: Mean transaction amount USD.

    Returns:
        Plotly Figure.
    """
    mode_idx = int(bins["count"].idxmax())
    colors = [AMBER if i == mode_idx else BLUE for i in range(len(bins))]
    fig = go.Figure(
        go.Bar(
            x=bins["label"],
            y=bins["count"],
            marker_color=colors,
            text=[f"<b>{int(c)}</b><br>{p:.0f}%" for c, p in zip(bins["count"], bins["pct"])],
            textposition="outside",
            textfont=dict(color=TEXT, size=10),
            showlegend=False,
        )
    )
    fig.update_layout(
        **_panel_layout("① Distribuição por faixa de valor"),
        yaxis_title="Nº de transações",
    )
    fig.add_annotation(
        xref="x domain",
        yref="y domain",
        x=0.99,
        y=0.97,
        xanchor="right",
        yanchor="top",
        text=f"Mediana: <b>${median_amt:.2f}</b>  ·  Média: <b>${mean_amt:.2f}</b>",
        showarrow=False,
        font=dict(size=11, color=TEXT),
        bgcolor=AMBER_LIGHT,
        bordercolor=AMBER,
        borderpad=6,
        borderwidth=1,
    )
    return fig


def fig_fee_comparison(
    fee_df: pd.DataFrame,
    monthly_rev: dict[str, float],
) -> go.Figure:
    """Grouped bars of accumulated fee revenue per spend tier per model.

    Args:
        fee_df: DataFrame from :func:`fee_comparison`.
        monthly_rev: Dict from :func:`monthly_revenue`.

    Returns:
        Plotly Figure.
    """
    traces = [
        go.Bar(
            x=fee_df.index.tolist(),
            y=fee_df[model].round(2),
            name=model,
            marker_color=color,
            opacity=0.85,
        )
        for model, color in MODEL_COLORS.items()
    ]
    fig = go.Figure(traces)
    fig.update_layout(
        **_panel_layout("② Receita acumulada por modelo de cobrança"),
        yaxis_title="Receita USD",
    )
    fig.add_annotation(
        xref="x domain",
        yref="y domain",
        x=0.99,
        y=0.97,
        xanchor="right",
        yanchor="top",
        text=(
            "<b>Estimativa mensal</b><br>"
            + "<br>".join(f"{m}: <b>${monthly_rev[m]:,.2f}</b>" for m in MODEL_COLORS)
        ),
        showarrow=False,
        font=dict(size=10, color=TEXT),
        bgcolor="#F0FDF4",
        bordercolor=EMERALD,
        borderpad=6,
        borderwidth=1,
        align="left",
    )
    return fig


def fig_daily_timeline(daily: pd.DataFrame) -> go.Figure:
    """Daily transaction count (bars) + 7-day rolling volume (line, dual y).

    Args:
        daily: DataFrame from :func:`build_daily`.

    Returns:
        Plotly Figure.
    """
    rolling_vol = daily["daily_volume_usd"].rolling(7, min_periods=1).mean()
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=daily.index,
            y=daily["daily_count"],
            name="Transações/dia",
            marker_color=BLUE_LIGHT,
            opacity=0.9,
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=daily.index,
            y=daily["daily_count"].rolling(7, min_periods=1).mean().round(1),
            name="Média 7d (qtd)",
            mode="lines",
            line=dict(color=BLUE, width=2),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=daily.index,
            y=rolling_vol.round(2),
            name="Volume 7d (USD)",
            mode="lines",
            line=dict(color=AMBER, width=2, dash="dot"),
        ),
        secondary_y=True,
    )
    axis_style = dict(
        gridcolor=GRID,
        zerolinecolor=GRID,
        tickfont=dict(color=TEXT_MUTED, size=11),
        linecolor=GRID,
        title_font=dict(size=11, color=TEXT_MUTED),
    )
    fig.update_layout(**_panel_layout("③ Frequência e volume diário"))
    fig.update_xaxes(axis_style)
    fig.update_yaxes(axis_style, secondary_y=False, title_text="Transações/dia")
    fig.update_yaxes(
        axis_style, secondary_y=True, title_text="Volume (7d avg, USD)", showgrid=False
    )
    return fig


def fig_weekly_patterns(daily: pd.DataFrame) -> go.Figure:
    """Activity rate (%) by day-of-week + avg spend on active days (dual y).

    Args:
        daily: DataFrame from :func:`build_daily`.

    Returns:
        Plotly Figure.
    """
    df = daily.copy()
    df["dow"] = df.index.day_of_week
    labels = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    total_dow = df.groupby("dow")["daily_count"].count()
    active_dow = (df["daily_count"] > 0).groupby(df["dow"]).sum()
    rate = (active_dow / total_dow * 100).reindex(range(7), fill_value=0)
    avg_spend = (
        df[df["daily_volume_usd"] > 0]
        .groupby("dow")["daily_volume_usd"]
        .mean()
        .reindex(range(7), fill_value=0)
    )
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=labels,
            y=rate.round(1),
            name="Dias ativos (%)",
            marker_color=BLUE,
            opacity=0.75,
            text=[f"{v:.0f}%" for v in rate],
            textposition="outside",
            textfont=dict(color=TEXT, size=10),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=labels,
            y=avg_spend.round(2),
            name="Gasto médio (dias ativos, USD)",
            mode="lines+markers",
            line=dict(color=AMBER, width=2.5),
            marker=dict(color=AMBER, size=8),
        ),
        secondary_y=True,
    )
    axis_style = dict(
        gridcolor=GRID,
        zerolinecolor=GRID,
        tickfont=dict(color=TEXT_MUTED, size=11),
        linecolor=GRID,
        title_font=dict(size=11, color=TEXT_MUTED),
    )
    fig.update_layout(**_panel_layout("④ Padrões semanais"))
    fig.update_xaxes(axis_style)
    fig.update_yaxes(axis_style, secondary_y=False, title_text="Dias ativos (%)", ticksuffix="%")
    fig.update_yaxes(axis_style, secondary_y=True, title_text="Gasto médio USD", showgrid=False)
    return fig


def fig_forecast(
    daily: pd.DataFrame,
    count_fc: dict[str, Any],
) -> go.Figure:
    """Historical count + EWMA fit + 5-day forecast with 95% CI.

    Args:
        daily: DataFrame from :func:`build_daily`.
        count_fc: Output of :func:`ewma_forecast` on daily_count.

    Returns:
        Plotly Figure.
    """
    fc = count_fc
    fc_dates = fc["forecast"].index
    fig = go.Figure(
        [
            go.Scatter(
                x=daily.index,
                y=daily["daily_count"],
                name="Histórico (qtd)",
                mode="lines",
                line=dict(color=SLATE, width=1),
                opacity=0.5,
            ),
            go.Scatter(
                x=daily.index,
                y=fc["fit"].round(2),
                name="EWMA ajustado",
                mode="lines",
                line=dict(color=BLUE, width=2),
            ),
            go.Scatter(
                x=list(fc_dates) + list(reversed(fc_dates.tolist())),
                y=fc["ci_upper"].round(2).tolist() + fc["ci_lower"].round(2).tolist()[::-1],
                fill="toself",
                fillcolor="rgba(37,99,235,0.12)",
                line=dict(color="rgba(0,0,0,0)"),
                name="IC 95%",
            ),
            go.Scatter(
                x=fc_dates,
                y=fc["forecast"].round(2),
                name="Projeção 5 dias",
                mode="lines+markers",
                line=dict(color=BLUE, width=2, dash="dash"),
                marker=dict(color=BLUE, size=7),
            ),
        ]
    )
    fig.add_vline(
        x=fc_dates[0].isoformat(),
        line_width=1,
        line_dash="dot",
        line_color=SLATE,
    )
    fig.update_layout(
        **_panel_layout("⑤ Projeção de demanda — próximos 5 dias (EWMA + IC 95%)"),
        yaxis_title="Transações/dia",
    )
    return fig


def fig_summary_table(smry: dict[str, Any]) -> go.Figure:
    """Summary metrics table (7d / 30d / all-time / forecast).

    Args:
        smry: Output of :func:`summary_metrics`.

    Returns:
        Plotly Figure containing a Table trace.
    """
    fig = go.Figure(
        go.Table(
            header=dict(
                values=["Período", "Transações", "Volume (USD)", "Qtd/dia", "USD/dia"],
                fill_color=BLUE,
                font=dict(color=TEXT, size=11, family="Arial"),
                align="center",
                height=30,
            ),
            cells=dict(
                values=[
                    smry["periods"],
                    smry["tx_count"],
                    [f"${v:,.2f}" for v in smry["volume_usd"]],
                    smry["avg_count_day"],
                    [f"${v:,.2f}" for v in smry["avg_vol_day"]],
                ],
                fill_color=[[PLOT_BG, BG, PLOT_BG, AMBER_LIGHT]],
                font=dict(color=TEXT, size=10, family="Arial"),
                align=["left", "center", "right", "center", "right"],
                height=26,
            ),
        )
    )
    layout = _panel_layout("⑥ Indicadores-chave", height=240)
    layout["margin"] = dict(l=10, r=10, t=50, b=10)
    fig.update_layout(**layout)
    return fig


def fig_b2b_projection(scenarios: pd.DataFrame) -> go.Figure:
    """Grouped bar chart of projected monthly revenue per scenario × model.

    Args:
        scenarios: DataFrame from :func:`build_scenarios`.

    Returns:
        Plotly Figure.
    """
    traces = [
        go.Bar(
            x=scenarios["scenario"],
            y=scenarios[model],
            name=model,
            marker_color=color,
            opacity=0.88,
            text=[f"${v:,.0f}" for v in scenarios[model]],
            textposition="outside",
            textfont=dict(color=TEXT, size=9),
        )
        for model, color in MODEL_COLORS.items()
    ]
    fig = go.Figure(traces)
    fig.update_layout(
        **_panel_layout("⑦ Projeção de crescimento B2B — receita mensal estimada (USD)"),
        yaxis_title="Receita mensal (USD)",
    )
    baseline = scenarios.iloc[0]
    last = scenarios.iloc[-1]
    best_model = max(MODEL_COLORS, key=lambda m: last[m])
    fig.add_annotation(
        xref="x domain",
        yref="y domain",
        x=0.01,
        y=0.97,
        xanchor="left",
        yanchor="top",
        text=(
            "<b>Premissas</b><br>"
            f"Base: {int(baseline['tx_mult'])}× = {baseline['scenario']}<br>"
            "Método: distribuição real reescalada<br>"
            f"Limiar Mod. C: ${MODEL_C_THRESHOLD_DEFAULT:.0f}"
        ),
        showarrow=False,
        font=dict(size=9, color=TEXT),
        bgcolor="#F0F9FF",
        bordercolor=BLUE,
        borderpad=6,
        borderwidth=1,
        align="left",
    )
    fig.add_annotation(
        xref="x domain",
        yref="y domain",
        x=0.99,
        y=0.97,
        xanchor="right",
        yanchor="top",
        text=(
            f"<b>{last['scenario']}</b>: {last['tx_mult']:.0f}× txns · "
            f"{last['size_mult']:.0f}× ticket<br>"
            f"Ticket mediano proj.: <b>${last['avg_ticket_usd']:,.2f}</b><br>"
            f"Melhor modelo: <b>{best_model.split('—')[0].strip()}</b> → "
            f"<b>${last[best_model]:,.2f}/mês</b>"
        ),
        showarrow=False,
        font=dict(size=10, color=TEXT),
        bgcolor=AMBER_LIGHT,
        bordercolor=AMBER,
        borderpad=6,
        borderwidth=1,
        align="left",
    )
    return fig


def fig_threshold_sweep(
    sweep: pd.DataFrame,
    scenarios: pd.DataFrame,
    selected_threshold: float | None = None,
) -> go.Figure:
    """Model C monthly revenue vs flat/% threshold, one line per scenario.

    Args:
        sweep: DataFrame from :func:`threshold_sweep`.
        scenarios: DataFrame from :func:`build_scenarios`.
        selected_threshold: Currently selected threshold to mark with a vline.

    Returns:
        Plotly Figure.
    """
    scenario_labels = [sc["scenario"] for _, sc in scenarios.iterrows()]
    traces = [
        go.Scatter(
            x=sweep["threshold"],
            y=sweep[label],
            name=label,
            mode="lines+markers",
            line=dict(color=color, width=2.5),
            marker=dict(color=color, size=7),
        )
        for label, color in zip(scenario_labels, SCENARIO_PALETTE)
    ]
    last_label = scenario_labels[-1]
    opt_idx = int(sweep[last_label].idxmax())
    opt_t = float(sweep.loc[opt_idx, "threshold"])
    opt_rev = float(sweep.loc[opt_idx, last_label])
    breakeven = MODEL_C_FLAT / MODEL_C_PCT

    fig = go.Figure(traces)
    fig.add_vline(x=opt_t, line_width=1.5, line_dash="dot", line_color=AMBER)
    if selected_threshold is not None and selected_threshold != opt_t:
        fig.add_vline(
            x=selected_threshold,
            line_width=2,
            line_dash="solid",
            line_color=ROSE,
            annotation_text=f"Selecionado: ${selected_threshold:.0f}",
            annotation_position="top right",
            annotation_font=dict(color=ROSE, size=9),
        )
    fig.update_layout(
        **_panel_layout(
            "⑧ Limiar ótimo — Modelo C ($0,30 fixo / 1%): receita mensal vs limiar (USD)"
        ),
        yaxis_title="Receita mensal (USD)",
        xaxis_title="Limiar de transição flat→1% (USD)",
    )
    fig.add_annotation(
        x=breakeven,
        yref="paper",
        y=0.02,
        xanchor="center",
        yanchor="bottom",
        text=f"<b>Equilíbrio<br>${breakeven:.0f}</b>",
        showarrow=True,
        arrowhead=2,
        arrowcolor=SLATE,
        arrowwidth=1,
        font=dict(size=9, color=SLATE),
        bgcolor="rgba(255,255,255,0.85)",
        borderpad=4,
    )
    fig.add_annotation(
        xref="x domain",
        yref="y domain",
        x=0.99,
        y=0.97,
        xanchor="right",
        yanchor="top",
        text=(
            f"Limiar ótimo ({last_label}): <b>${opt_t:.0f}</b><br>"
            f"Receita máx: <b>${opt_rev:,.2f}/mês</b><br>"
            f"Fórmula: $0,30 se tx < $T · 1% se tx ≥ $T"
        ),
        showarrow=False,
        font=dict(size=10, color=TEXT),
        bgcolor=AMBER_LIGHT,
        bordercolor=AMBER,
        borderpad=6,
        borderwidth=1,
        align="left",
    )
    return fig


def fig_combo_heatmap(
    combo_df: pd.DataFrame,
    pct: float,
    scenario_label: str,
) -> go.Figure:
    """Heatmap: flat_fee (Y) × threshold (X) → monthly revenue.

    Args:
        combo_df: DataFrame from :func:`compute_combinations`.
        pct: Percentage fee above threshold.
        scenario_label: Label of the selected B2B scenario.

    Returns:
        Plotly Figure.
    """
    pivot = combo_df.pivot(index="flat_fee", columns="threshold", values="monthly_rev")
    z = pivot.values
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[f"${v:.0f}" for v in pivot.columns],
            y=[f"${v:.2f}" for v in pivot.index],
            colorscale="Blues",
            text=[[f"${v:,.0f}" for v in row] for row in z],
            texttemplate="%{text}",
            textfont=dict(size=10),
            showscale=True,
            colorbar=dict(title="USD/mês", tickprefix="$"),
        )
    )
    fig.update_layout(
        title=dict(
            text=(
                f"Receita mensal — flat fee × limiar ({pct * 100:.2g}% acima · {scenario_label})"
            ),
            font=dict(size=13, color=BLUE),
            x=0,
            xanchor="left",
        ),
        xaxis_title="Limiar flat→% (USD)",
        yaxis_title="Taxa fixa (USD)",
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(family="Arial", color=TEXT),
        height=max(300, 80 + len(pivot.index) * 55),
        margin=dict(l=70, r=30, t=70, b=60),
    )
    return fig


def fig_combo_lines(
    combo_df: pd.DataFrame,
    pct: float,
    scenario_label: str,
) -> go.Figure:
    """Revenue vs threshold, one line per flat fee value.

    Args:
        combo_df: DataFrame from :func:`compute_combinations`.
        pct: Percentage fee above threshold.
        scenario_label: Label of the selected B2B scenario.

    Returns:
        Plotly Figure.
    """
    palette = [BLUE, AMBER, EMERALD, PURPLE, ROSE, "#0891B2", "#65A30D", "#EA580C"]
    flat_fees = sorted(combo_df["flat_fee"].unique())
    fig = go.Figure()
    for i, flat in enumerate(flat_fees):
        sub = combo_df[combo_df["flat_fee"] == flat].sort_values("threshold")
        fig.add_trace(
            go.Scatter(
                x=sub["threshold"],
                y=sub["monthly_rev"],
                name=f"flat ${flat:.2f}",
                mode="lines+markers",
                line=dict(color=palette[i % len(palette)], width=2.5),
                marker=dict(size=7),
            )
        )
    fig.update_layout(
        title=dict(
            text=f"Receita vs limiar por taxa fixa ({pct * 100:.2g}% acima · {scenario_label})",
            font=dict(size=13, color=BLUE),
            x=0,
            xanchor="left",
        ),
        xaxis_title="Limiar flat→% (USD)",
        yaxis_title="Receita mensal (USD)",
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(family="Arial", color=TEXT),
        height=380,
        margin=dict(l=65, r=20, t=60, b=55),
        legend=dict(orientation="h", x=0, y=-0.18, font=dict(size=10)),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID),
    )
    return fig


def fig_coverage_bar(coverage_df: pd.DataFrame) -> go.Figure:
    """Horizontal bar chart: fee revenue per model vs Rain invoice cost.

    Bars are green when coverage ≥ 1.0 (profitable) and red when < 1.0
    (loss). A red dashed vertical line marks the Rain cost target.

    Args:
        coverage_df: DataFrame from :func:`coverage_analysis`.

    Returns:
        Plotly Figure.
    """
    df = coverage_df.sort_values("revenue_usd", ascending=True).reset_index(drop=True)
    colors = [EMERALD if r >= 1.0 else ROSE for r in df["coverage_ratio"]]
    labels = [f"{r:.2f}×" for r in df["coverage_ratio"]]
    rain_cost = float(df["cost_usd"].iloc[0])

    fig = go.Figure(
        go.Bar(
            x=df["revenue_usd"],
            y=df["model"],
            orientation="h",
            marker_color=colors,
            text=labels,
            textposition="outside",
            textfont=dict(color=TEXT, size=11),
        )
    )
    fig.add_vline(
        x=rain_cost,
        line_width=2,
        line_dash="dash",
        line_color=ROSE,
        annotation_text=f"Custo Rain ${rain_cost:,.2f}",
        annotation_position="top right",
        annotation_font=dict(color=ROSE, size=10),
    )
    fig.update_layout(
        **_panel_layout("Receita mensal por modelo vs custo Rain (USD)", height=400),
        xaxis_title="Receita mensal (USD)",
        showlegend=False,
    )
    return fig


def fig_coverage_heatmap(grid_df: pd.DataFrame, rain_cost_usd: float) -> go.Figure:
    """Heatmap of coverage ratios over flat-fee × pct-fee space.

    Colour scale runs red (0×) → white (1×, breakeven) → green (≥1.5×).
    A contour line is drawn at coverage = 1.0.

    Args:
        grid_df: DataFrame from :func:`coverage_grid`.
        rain_cost_usd: Monthly Rain cost used to label the colour axis.

    Returns:
        Plotly Figure.
    """
    z = grid_df.values
    x_labels = [f"{v * 100:.1f}%" for v in grid_df.columns]
    y_labels = [f"${v:.2f}" for v in grid_df.index]

    colorscale = [
        [0.0, "#FEE2E2"],  # red-100 — loss
        [0.5, "#FFFFFF"],  # white — breakeven (≈1×)
        [1.0, "#D1FAE5"],  # emerald-100 — profitable
    ]

    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=x_labels,
            y=y_labels,
            colorscale=colorscale,
            zmin=0,
            zmax=2,
            text=[[f"{v:.2f}×" for v in row] for row in z],
            texttemplate="%{text}",
            textfont=dict(size=10),
            showscale=True,
            colorbar=dict(
                title="Cobertura",
                tickvals=[0, 0.5, 1.0, 1.5, 2.0],
                ticktext=["0×", "0.5×", "1.0× (breakeven)", "1.5×", "2.0×"],
            ),
        )
    )
    fig.add_contour(
        z=z,
        x=x_labels,
        y=y_labels,
        contours=dict(
            start=1.0,
            end=1.0,
            size=0.01,
            coloring="none",
            showlabels=True,
            labelfont=dict(size=10, color=SLATE),
        ),
        line=dict(color=SLATE, width=2, dash="dot"),
        showscale=False,
        name="breakeven",
    )
    fig.update_layout(
        title=dict(
            text="Cobertura flat + % por combinação de tarifas",
            font=dict(size=13, color=BLUE),
            x=0,
            xanchor="left",
        ),
        xaxis_title="Taxa variável (%)",
        yaxis_title="Taxa fixa (USD/tx)",
        xaxis=dict(
            tickfont=dict(color=TEXT),
            title=dict(font=dict(color=TEXT_MUTED)),
            linecolor=GRID,
            tickcolor=GRID,
        ),
        yaxis=dict(
            tickfont=dict(color=TEXT),
            title=dict(font=dict(color=TEXT_MUTED)),
            linecolor=GRID,
            tickcolor=GRID,
        ),
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(family="Arial", color=TEXT),
        height=400,
        margin=dict(l=70, r=30, t=60, b=60),
    )
    return fig


def fig_bin_revenue_breakdown(
    breakdown_df: pd.DataFrame,
    coverage_ratio: float,
    rain_cost_usd: float,
) -> go.Figure:
    """Per-bin revenue contribution chart for a tiered flat fee structure.

    Dual-axis: transaction count (bars, left) and monthly revenue (bars, right).
    An annotation summarises total revenue, coverage, and margin.

    Args:
        breakdown_df: DataFrame from :func:`bin_fee_revenue`.
        coverage_ratio: Monthly revenue / rain_cost_usd.
        rain_cost_usd: Monthly Rain invoice cost target.

    Returns:
        Plotly Figure.
    """
    labels = breakdown_df["label"].tolist()
    counts = breakdown_df["count"].tolist()
    revenues = breakdown_df["revenue_month_usd"].tolist()
    fees = breakdown_df["fee_usd"].tolist()
    total_rev = float(breakdown_df["revenue_month_usd"].sum())
    bar_colors = [EMERALD if f > 0 else SLATE for f in fees]
    margin = total_rev - rain_cost_usd
    summary_color = EMERALD if coverage_ratio >= 1.0 else ROSE

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(
        x=labels,
        y=counts,
        name="Transações (obs.)",
        marker_color=BLUE_LIGHT,
        opacity=0.7,
        secondary_y=False,
    )
    fig.add_bar(
        x=labels,
        y=revenues,
        name="Receita/mês (USD)",
        marker_color=bar_colors,
        opacity=0.9,
        secondary_y=True,
    )
    fig.add_annotation(
        text=(
            f"<b>Receita total: ${total_rev:,.2f}/mês</b><br>"
            f"Cobertura: {coverage_ratio * 100:.1f}% da invoice<br>"
            f"Margem: ${margin:+,.2f}"
        ),
        xref="paper",
        yref="paper",
        x=1.02,
        y=1.0,
        showarrow=False,
        font=dict(size=11, color=summary_color),
        align="left",
        xanchor="left",
    )
    layout = _panel_layout("Receita mensal por faixa de valor", height=400)
    layout["margin"] = dict(l=60, r=200, t=60, b=60)
    fig.update_layout(**layout)
    fig.update_yaxes(title_text="Transações observadas", secondary_y=False)
    fig.update_yaxes(title_text="Receita mensal (USD)", secondary_y=True)
    return fig


def fig_bin_sweep_heatmap(
    sweep_df: pd.DataFrame,
    i_label: str,
    j_label: str,
) -> go.Figure:
    """Coverage-ratio heatmap for a two-bin fee sweep.

    Colour scale: red (loss) → white (1× breakeven) → green (profit).
    A contour line marks exact breakeven (coverage = 1.0).

    Args:
        sweep_df: DataFrame from :func:`bin_fee_sweep`.
        i_label: Display name of the row-bin (y-axis).
        j_label: Display name of the column-bin (x-axis).

    Returns:
        Plotly Figure.
    """
    z = sweep_df.values
    x_labels = [f"${v:.2f}" for v in sweep_df.columns]
    y_labels = [f"${v:.2f}" for v in sweep_df.index]
    colorscale = [[0.0, "#FEE2E2"], [0.5, "#FFFFFF"], [1.0, "#D1FAE5"]]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=x_labels,
            y=y_labels,
            colorscale=colorscale,
            zmin=0,
            zmax=2,
            text=[[f"{v:.2f}×" for v in row] for row in z],
            texttemplate="%{text}",
            textfont=dict(size=9),
            showscale=True,
            colorbar=dict(
                title="Cobertura",
                tickvals=[0, 0.5, 1.0, 1.5, 2.0],
                ticktext=["0×", "0.5×", "1.0×", "1.5×", "2.0×"],
            ),
        )
    )
    fig.add_contour(
        z=z,
        x=x_labels,
        y=y_labels,
        contours=dict(
            start=1.0,
            end=1.0,
            size=0.01,
            coloring="none",
            showlabels=True,
            labelfont=dict(size=10, color=SLATE),
        ),
        line=dict(color=SLATE, width=2, dash="dot"),
        showscale=False,
        name="breakeven",
    )
    fig.update_layout(
        title=dict(
            text=f"Cobertura por tarifa: {i_label} (y) × {j_label} (x)",
            font=dict(size=13, color=BLUE),
            x=0,
            xanchor="left",
        ),
        xaxis_title=f"Taxa — {j_label} (USD/tx)",
        yaxis_title=f"Taxa — {i_label} (USD/tx)",
        xaxis=dict(tickfont=dict(color="#000000"), title=dict(font=dict(color="#000000"))),
        yaxis=dict(tickfont=dict(color="#000000"), title=dict(font=dict(color="#000000"))),
        paper_bgcolor=BG,
        plot_bgcolor=PLOT_BG,
        font=dict(family="Arial", color=TEXT),
        height=440,
        margin=dict(l=80, r=30, t=60, b=60),
    )
    return fig


def fig_flat_pct_revenue_lines(
    grid_df: pd.DataFrame,
    rain_cost_usd: float,
    selected_flat_fees: list[float] | None = None,
) -> go.Figure:
    """Monthly revenue lines for flat + % fee combinations.

    One line per flat fee value; X-axis sweeps % variable fee.
    A dashed reference line marks the Rain invoice breakeven cost.

    Args:
        grid_df: DataFrame from :func:`coverage_grid` (coverage ratios,
            indexed by flat_fee, columns = pct_fee).
        rain_cost_usd: Monthly Rain invoice cost — used as the breakeven
            reference and to convert coverage ratios to USD revenue.
        selected_flat_fees: Subset of ``grid_df.index`` to plot.
            Defaults to all rows.

    Returns:
        Plotly Figure.
    """
    revenue_df = grid_df * rain_cost_usd
    if selected_flat_fees:
        avail = [f for f in selected_flat_fees if f in revenue_df.index]
        if avail:
            revenue_df = revenue_df.loc[avail]

    palette = [BLUE, AMBER, EMERALD, PURPLE, ROSE, SLATE, "#0891B2", "#65A30D", "#EA580C"]
    x_labels = [f"{v * 100:.2g}%" for v in revenue_df.columns]

    fig = go.Figure()
    for i, (flat, row) in enumerate(revenue_df.iterrows()):
        color = palette[i % len(palette)]
        fig.add_trace(
            go.Scatter(
                x=x_labels,
                y=row.values.tolist(),
                name=f"${flat:.2f} fixo",
                mode="lines+markers",
                line=dict(color=color, width=2.5),
                marker=dict(size=7, color=color),
                hovertemplate=(
                    f"<b>${flat:.2f} fixo</b><br>"
                    "% variável: %{x}<br>"
                    "Receita: $%{y:,.2f}<extra></extra>"
                ),
            )
        )

    fig.add_hline(
        y=rain_cost_usd,
        line_dash="dash",
        line_color=ROSE,
        annotation_text=f"Invoice Rain  ${rain_cost_usd:,.0f}",
        annotation_position="right",
        annotation_font=dict(color=ROSE, size=10),
    )
    layout = _panel_layout("Receita mensal — taxa fixa + % variável", height=420)
    fig.update_layout(
        **layout,
        yaxis_title="Receita mensal (USD)",
        xaxis_title="Taxa variável (%)",
        yaxis_tickprefix="$",
        yaxis_tickformat=",.0f",
    )
    return fig


def fig_progressive_coverage(
    sweep_df: pd.DataFrame,
    rain_cost_usd: float,
) -> go.Figure:
    """Coverage-ratio curve for the progressive fee sweep.

    X-axis = gap (bin width in USD); Y-axis = coverage ratio (revenue / invoice).
    Markers are coloured green above breakeven, rose below.  A dashed reference
    line marks 1.0 (full coverage).

    Args:
        sweep_df: DataFrame from :func:`progressive_fee_sweep` with columns
            ``gap``, ``revenue_usd``, ``coverage_ratio``.
        rain_cost_usd: Monthly invoice cost (reference only, for the label).

    Returns:
        Plotly Figure.
    """
    gaps = sweep_df["gap"].tolist()
    ratios = sweep_df["coverage_ratio"].tolist()
    colours = [EMERALD if r >= 1.0 else ROSE for r in ratios]

    fig = go.Figure()
    # Filled area below / above breakeven
    fig.add_trace(
        go.Scatter(
            x=gaps,
            y=ratios,
            fill="tozeroy",
            fillcolor="rgba(5,150,105,0.10)",
            line=dict(color="rgba(0,0,0,0)"),
            showlegend=False,
            hoverinfo="skip",
        )
    )
    # Main line
    fig.add_trace(
        go.Scatter(
            x=gaps,
            y=ratios,
            mode="lines+markers",
            line=dict(color=BLUE, width=2),
            marker=dict(color=colours, size=9, line=dict(color=BG, width=1)),
            name="Cobertura",
            hovertemplate="Gap $%{x}<br>Cobertura %{y:.2f}×<extra></extra>",
        )
    )
    # Breakeven reference
    fig.add_hline(
        y=1.0,
        line_dash="dash",
        line_color=SLATE,
        annotation_text=f"Equilíbrio  ${rain_cost_usd:,.0f}",
        annotation_position="right",
        annotation_font=dict(color=SLATE, size=10),
    )
    # Annotate first breakeven point
    be_rows = sweep_df[sweep_df["coverage_ratio"] >= 1.0]
    if not be_rows.empty:
        first_g = float(be_rows.iloc[0]["gap"])
        first_r = float(be_rows.iloc[0]["coverage_ratio"])
        fig.add_annotation(
            x=first_g,
            y=first_r,
            text="⬆ break-even",
            showarrow=True,
            arrowhead=2,
            arrowcolor=EMERALD,
            font=dict(color=EMERALD, size=10),
            yshift=12,
        )
    layout = _panel_layout("Cobertura da Invoice — Sweep por Largura de Faixa", height=420)
    layout["margin"] = dict(l=60, r=80, t=60, b=60)
    fig.update_layout(
        **layout,
        xaxis_title="Gap (USD/faixa)",
        yaxis_title="Cobertura da Invoice",
        yaxis_tickformat=".2f",
    )
    return fig
