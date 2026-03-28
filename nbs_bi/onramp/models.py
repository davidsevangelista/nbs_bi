"""Analytics model for on/off ramp conversions.

Accepts a DataFrame of conversions (as returned by OnrampQueries.conversions)
and computes KPIs, FX statistics, USDC position, PnL, and user segmentation.

Terminology:
  onramp  — client sends BRL, receives USDC (direction=brl_to_usdc).
            NBS *sells* USDC → stock_out_usdc increases.
  offramp — client sends USDC, receives BRL (direction=usdc_to_brl).
            NBS *buys* USDC  → stock_in_usdc increases.
"""

from __future__ import annotations

import logging
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_ONRAMP_DIRECTIONS = {"brl_to_usdc", "buy", "onramp"}
_OFFRAMP_DIRECTIONS = {"usdc_to_brl", "sell", "offramp"}

# pandas to_period() uses legacy aliases ("M" not "ME", "Y" not "YE")
_PERIOD_ALIAS_MAP = {"ME": "M", "QE": "Q", "YE": "Y", "YS": "Y", "QS": "Q", "MS": "M"}


def _to_period_freq(freq: str) -> str:
    """Normalise a resample-style frequency alias to a Period-compatible one."""
    return _PERIOD_ALIAS_MAP.get(freq, freq)


def _require_columns(df: pd.DataFrame, cols: list[str], context: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{context}: missing columns {missing}")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise types and compute derived volume/revenue columns.

    Args:
        df: Raw conversions DataFrame from OnrampQueries.

    Returns:
        Cleaned DataFrame with volume_brl, volume_usdc, revenue_brl,
        revenue_usdc, and side columns added.
    """
    out = df.copy()

    if "user_id" in out.columns:
        out["user_id"] = out["user_id"].astype(str)

    for col in ["created_at", "updated_at", "expires_at"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")

    out["direction"] = out.get("direction", pd.Series("", index=out.index)).astype(str).str.lower()

    num_cols = [
        "from_amount_brl",
        "from_amount_usdc",
        "to_amount_brl",
        "to_amount_usdc",
        "fee_amount_brl",
        "fee_amount_usdc",
        "spread_revenue_brl",
        "spread_revenue_usdc",
        "exchange_rate",
        "effective_rate",
        "spread_percentage",
    ]
    for col in num_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    def _col(df: pd.DataFrame, col: str) -> pd.Series:
        return df[col] if col in df.columns else pd.Series(0.0, index=df.index)

    out["volume_brl"] = _col(out, "from_amount_brl").abs() + _col(out, "to_amount_brl").abs()
    out["volume_usdc"] = _col(out, "from_amount_usdc").abs() + _col(out, "to_amount_usdc").abs()
    out["revenue_brl"] = _col(out, "fee_amount_brl").fillna(0.0) + _col(
        out, "spread_revenue_brl"
    ).fillna(0.0)
    out["revenue_usdc"] = _col(out, "fee_amount_usdc").fillna(0.0) + _col(
        out, "spread_revenue_usdc"
    ).fillna(0.0)

    out["side"] = np.where(out["direction"].isin(_ONRAMP_DIRECTIONS), "onramp", "offramp")

    return out


class OnrampModel:
    """Analytics model over a set of on/off ramp conversions.

    Args:
        conversions: DataFrame as returned by OnrampQueries.conversions().
            Monetary columns must already be in real units (BRL, USDC).

    Raises:
        ValueError: If the DataFrame is missing required columns.
    """

    def __init__(self, conversions: pd.DataFrame) -> None:
        _require_columns(conversions, ["direction", "created_at"], "OnrampModel")
        self._df = _clean(conversions)
        logger.info("OnrampModel loaded: %d conversions", len(self._df))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def kpis(self) -> dict[str, float | int]:
        """Aggregate KPIs across the full dataset.

        Returns:
            Dict with total_conversions, unique_users, volume_brl,
            volume_usdc, revenue_brl, revenue_usdc — for onramp, offramp,
            and combined.
        """
        df = self._df
        result: dict[str, float | int] = {
            "total_conversions": int(len(df)),
            "unique_users": int(df["user_id"].nunique()) if "user_id" in df.columns else 0,
            "volume_brl": float(df["volume_brl"].sum()),
            "volume_usdc": float(df["volume_usdc"].sum()),
            "revenue_brl": float(df["revenue_brl"].sum()),
            "revenue_usdc": float(df["revenue_usdc"].sum()),
        }
        for side in ("onramp", "offramp"):
            sub = df[df["side"] == side]
            result[f"{side}_conversions"] = int(len(sub))
            result[f"{side}_volume_brl"] = float(sub["volume_brl"].sum())
            result[f"{side}_volume_usdc"] = float(sub["volume_usdc"].sum())
            result[f"{side}_revenue_brl"] = float(sub["revenue_brl"].sum())
        return result

    def volume_by_period(self, freq: str = "D") -> pd.DataFrame:
        """Daily/weekly/monthly volume aggregated by direction.

        Args:
            freq: Pandas offset alias — "D" (daily), "W" (weekly), "ME" (monthly).

        Returns:
            DataFrame indexed by period with columns volume_brl, volume_usdc,
            n_conversions, split by side.
        """
        df = self._df.dropna(subset=["created_at"]).copy()
        df["period"] = df["created_at"].dt.to_period(_to_period_freq(freq)).dt.to_timestamp()
        agg = (
            df.groupby(["period", "side"])
            .agg(
                volume_brl=("volume_brl", "sum"),
                volume_usdc=("volume_usdc", "sum"),
                n_conversions=("direction", "count"),
            )
            .reset_index()
        )
        return agg.sort_values(["period", "side"])

    def fx_stats(self, freq: str = "D") -> pd.DataFrame:
        """Implicit FX rate statistics by direction and period.

        The implicit rate is derived from the amounts:
          - onramp: from_amount_brl / to_amount_usdc
          - offramp: to_amount_brl / from_amount_usdc

        Args:
            freq: Pandas offset alias for grouping ("D", "W", "ME").

        Returns:
            DataFrame with columns: period, side, fx_mean, fx_p10, fx_p90, n.
        """
        df = self._df.dropna(subset=["created_at"]).copy()
        df["period"] = df["created_at"].dt.to_period(_to_period_freq(freq)).dt.to_timestamp()

        df["fx"] = np.nan
        on_mask = df["direction"].isin(_ONRAMP_DIRECTIONS)
        off_mask = df["direction"].isin(_OFFRAMP_DIRECTIONS)

        valid_on = on_mask & (df.get("to_amount_usdc", 0) > 0)
        valid_off = off_mask & (df.get("from_amount_usdc", 0) > 0)

        if "from_amount_brl" in df.columns and "to_amount_usdc" in df.columns:
            df.loc[valid_on, "fx"] = (
                df.loc[valid_on, "from_amount_brl"] / df.loc[valid_on, "to_amount_usdc"]
            )

        if "to_amount_brl" in df.columns and "from_amount_usdc" in df.columns:
            df.loc[valid_off, "fx"] = (
                df.loc[valid_off, "to_amount_brl"] / df.loc[valid_off, "from_amount_usdc"]
            )

        def _p10(s: pd.Series) -> float:
            clean = s.dropna()
            return float(clean.quantile(0.10)) if len(clean) > 0 else np.nan

        def _p90(s: pd.Series) -> float:
            clean = s.dropna()
            return float(clean.quantile(0.90)) if len(clean) > 0 else np.nan

        stats = (
            df.groupby(["period", "side"])["fx"]
            .agg(fx_mean="mean", fx_p10=_p10, fx_p90=_p90, n="count")
            .reset_index()
        )
        return stats.sort_values(["period", "side"])

    def position(self) -> pd.DataFrame:
        """Running USDC inventory position with weighted average cost and PnL.

        Processes conversions chronologically:
          - Offramp events (NBS buys USDC): update weighted average price (PM).
          - Onramp events (NBS sells USDC): realise PnL = (sell_rate - PM) × qty.

        Returns:
            DataFrame sorted by created_at with columns:
            created_at, side, stock_in_usdc, stock_out_usdc,
            position_qty_usdc, avg_price_brl_per_usdc,
            nbs_sell_onramp_rate, nbs_buy_offramp_rate,
            pnl_brl, pnl_cum_brl, margin_sell_pct, margin_buy_pct.
        """
        df = self._df.dropna(subset=["created_at"]).copy()
        df = df.sort_values("created_at").reset_index(drop=True)

        rate_col = "effective_rate" if "effective_rate" in df.columns else "exchange_rate"

        df["stock_out_usdc"] = 0.0
        df["stock_in_usdc"] = 0.0
        on_mask = df["direction"].isin(_ONRAMP_DIRECTIONS)
        off_mask = df["direction"].isin(_OFFRAMP_DIRECTIONS)

        if "to_amount_usdc" in df.columns:
            df.loc[on_mask, "stock_out_usdc"] = df.loc[on_mask, "to_amount_usdc"].abs()
        if "from_amount_usdc" in df.columns:
            df.loc[off_mask, "stock_in_usdc"] = df.loc[off_mask, "from_amount_usdc"].abs()

        df["nbs_sell_onramp_rate"] = np.where(on_mask, df[rate_col], np.nan)
        df["nbs_buy_offramp_rate"] = np.where(off_mask, df[rate_col], np.nan)

        qty_list: list[float] = []
        avg_list: list[float | None] = []
        qty = 0.0
        avg: float | None = None

        for _, row in df.iterrows():
            s_in = float(row["stock_in_usdc"] or 0.0)
            s_out = float(row["stock_out_usdc"] or 0.0)
            price = float(row.get(rate_col) or 0.0)

            if s_in > 0:  # NBS buys USDC — update weighted average
                total_qty = qty + s_in
                avg = ((qty * (avg or 0.0)) + s_in * price) / total_qty if total_qty > 0 else price
                qty = total_qty

            if s_out > 0:  # NBS sells USDC — reduce position
                qty -= s_out

            qty_list.append(qty)
            avg_list.append(avg)

        df["position_qty_usdc"] = qty_list
        df["avg_price_brl_per_usdc"] = avg_list

        df["avg_before"] = df["avg_price_brl_per_usdc"].shift().ffill()
        sell_price = df["nbs_sell_onramp_rate"]
        buy_price = df["nbs_buy_offramp_rate"]

        df["pnl_brl"] = 0.0
        sell_mask = df["stock_out_usdc"] > 0
        df.loc[sell_mask, "pnl_brl"] = (
            (sell_price.loc[sell_mask] - df.loc[sell_mask, "avg_before"])
            * df.loc[sell_mask, "stock_out_usdc"]
        ).fillna(0.0)
        df["pnl_cum_brl"] = df["pnl_brl"].cumsum()

        df["margin_sell_pct"] = np.where(
            sell_mask & df["avg_before"].notna(),
            (sell_price - df["avg_before"]) / df["avg_before"],
            np.nan,
        )
        buy_mask = df["stock_in_usdc"] > 0
        df["margin_buy_pct"] = np.where(
            buy_mask & df["avg_before"].notna(),
            (df["avg_before"] - buy_price) / df["avg_before"],
            np.nan,
        )

        keep = [
            "created_at",
            "side",
            "stock_in_usdc",
            "stock_out_usdc",
            "position_qty_usdc",
            "avg_price_brl_per_usdc",
            "nbs_sell_onramp_rate",
            "nbs_buy_offramp_rate",
            "pnl_brl",
            "pnl_cum_brl",
            "margin_sell_pct",
            "margin_buy_pct",
        ]
        return df[[c for c in keep if c in df.columns]]

    def top_users(
        self,
        n: int = 20,
        metric: Literal["volume_brl", "volume_usdc", "revenue_brl", "n_conversions"] = "volume_brl",
    ) -> pd.DataFrame:
        """Rank users by a chosen metric.

        Args:
            n: Number of users to return.
            metric: Column to rank by.

        Returns:
            DataFrame with user_id, volume_brl, volume_usdc, revenue_brl,
            n_conversions, sorted descending by metric.
        """
        if "user_id" not in self._df.columns:
            return pd.DataFrame()
        agg = (
            self._df.groupby("user_id")
            .agg(
                volume_brl=("volume_brl", "sum"),
                volume_usdc=("volume_usdc", "sum"),
                revenue_brl=("revenue_brl", "sum"),
                n_conversions=("direction", "count"),
            )
            .reset_index()
            .sort_values(metric, ascending=False)
            .head(n)
        )
        return agg

    def active_users(self, freq: str = "D") -> pd.DataFrame:
        """Count distinct active users per period.

        Args:
            freq: Pandas offset alias ("D", "W", "ME").

        Returns:
            DataFrame with period and active_users columns.
        """
        if "user_id" not in self._df.columns:
            return pd.DataFrame()
        df = self._df.dropna(subset=["created_at"]).copy()
        df["period"] = df["created_at"].dt.to_period(_to_period_freq(freq)).dt.to_timestamp()
        return (
            df.groupby("period")["user_id"]
            .nunique()
            .reset_index(name="active_users")
            .sort_values("period")
        )
