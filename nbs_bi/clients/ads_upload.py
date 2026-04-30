"""CLI: filter Rain card CSV to ad spend rows and upsert into meta_ads_spend.

Extracts Meta (FACEBK) and Google Ads rows — only id, date, amount_usd, and
platform are stored. No PII is persisted.
Running the script multiple times is safe (ON CONFLICT DO NOTHING).

Usage::

    nbs-ads-upload rain-transactions-export-2026-04-22.csv
    nbs-ads-upload rain-transactions-export-2026-04-22.csv --db-url postgresql://...
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine, create_engine

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS meta_ads_spend (
    id          TEXT PRIMARY KEY,
    date        DATE          NOT NULL,
    amount_usd  NUMERIC(10,4) NOT NULL,
    platform    TEXT          NOT NULL DEFAULT 'meta'
);
CREATE INDEX IF NOT EXISTS meta_ads_spend_date_idx ON meta_ads_spend (date);
"""

# Idempotent migration for existing tables that pre-date the platform column.
_ALTER_TABLE_SQL = """
ALTER TABLE meta_ads_spend
    ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'meta';
"""

_INSERT_SQL = """
INSERT INTO meta_ads_spend (id, date, amount_usd, platform)
VALUES (:id, :date, :amount_usd, :platform)
ON CONFLICT (id) DO NOTHING
"""

_META_PREFIX = "FACEBK"
_GOOGLE_ADS_SUBSTR = "GOOGLE ADS"

_BANK_STMT_COLS = [
    "Data", "Descricao", "Tipo",
    "Entrada_BRL", "Saida_BRL", "Saldo_BRL",
    "Categoria", "Subcategoria", "ID",
]

_FX_WINDOW_DAYS = 3  # ± days around payment date when querying conversion_quotes

_FX_DAY_SQL = """
SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY effective_rate) AS fx_rate
FROM conversion_quotes
WHERE used = TRUE
  AND direction = 'brl_to_usdc'
  AND created_at >= :start
  AND created_at <  :end
"""

_FX_FALLBACK = 5.80


def _filter_bank_statement(xlsx_path: Path) -> pd.DataFrame:
    """Read Nubank management account Excel and return FACEBOOK spend rows.

    Args:
        xlsx_path: Path to the Nubank management account .xlsx export.

    Returns:
        DataFrame with columns ``id``, ``date``, ``amount_brl``.
    """
    df = pd.read_excel(xlsx_path, header=None, skiprows=2)
    df.columns = _BANK_STMT_COLS
    mask = df["Descricao"].str.contains("FACEBOOK", case=False, na=False) | (
        df["Subcategoria"].str.strip().str.lower() == "marketing - meta ads"
    )
    fb = df[mask].copy()
    fb["id"] = fb["ID"].astype(str)
    fb["date"] = pd.to_datetime(fb["Data"]).dt.date
    fb["amount_brl"] = pd.to_numeric(fb["Saida_BRL"], errors="coerce").fillna(0.0)
    return fb[["id", "date", "amount_brl"]].reset_index(drop=True)


def _fetch_daily_fx(
    dates: list[datetime.date], engine: Engine
) -> dict[datetime.date, float]:
    """Return BRL-per-USD effective rate for each date, using a ±WINDOW day window.

    Args:
        dates: Unique payment dates to look up.
        engine: Live SQLAlchemy engine connected to the analytics DB.

    Returns:
        Mapping of date → fx_rate (BRL per 1 USDC). Falls back to 5.80.
    """
    result: dict[datetime.date, float] = {}
    with engine.connect() as conn:
        for d in dates:
            start = datetime.datetime(d.year, d.month, d.day) - datetime.timedelta(
                days=_FX_WINDOW_DAYS
            )
            end = datetime.datetime(d.year, d.month, d.day) + datetime.timedelta(
                days=_FX_WINDOW_DAYS + 1
            )
            row = conn.execute(
                text(_FX_DAY_SQL), {"start": start, "end": end}
            ).fetchone()
            if row and row[0] is not None:
                result[d] = float(row[0])
            else:
                logger.warning(
                    "No FX data within %d days of %s — using fallback %.2f",
                    _FX_WINDOW_DAYS, d, _FX_FALLBACK,
                )
                result[d] = _FX_FALLBACK
    return result


def upload_bank_statement(
    xlsx_path: Path, db_url: str, analytics_db_url: str | None = None
) -> tuple[int, int]:
    """Parse Nubank bank statement and upsert FACEBOOK spend into meta_ads_spend.

    BRL amounts are converted to USD using the median effective rate from
    conversion_quotes within a ±3-day window of each payment date.

    Args:
        xlsx_path: Path to Nubank management account .xlsx export.
        db_url: Writable PostgreSQL URL (meta_ads_spend lives here).
        analytics_db_url: Read-only analytics DB URL (conversion_quotes lives here).
            Defaults to db_url if not provided.

    Returns:
        Tuple of (rows_inserted, rows_skipped).
    """
    rows = _filter_bank_statement(xlsx_path)
    if rows.empty:
        logger.warning("No FACEBOOK rows found in %s", xlsx_path.name)
        return 0, 0

    fx_engine = create_engine(analytics_db_url or db_url)
    fx_map = _fetch_daily_fx(list(rows["date"].unique()), fx_engine)
    engine = create_engine(db_url)

    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
        conn.execute(text(_ALTER_TABLE_SQL))
        inserted = 0
        skipped = 0
        for _, row in rows.iterrows():
            fx = fx_map[row["date"]]
            amount_usd = float(row["amount_brl"]) / fx
            result = conn.execute(
                text(_INSERT_SQL),
                {
                    "id": row["id"],
                    "date": row["date"],
                    "amount_usd": round(amount_usd, 4),
                    "platform": "meta",
                },
            )
            if result.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

    return inserted, skipped


def _filter_spend(csv_path: Path) -> pd.DataFrame:
    """Read CSV and return Meta and Google Ads rows with id, date, amount_usd, platform."""
    df = pd.read_csv(csv_path, parse_dates=["date"])
    meta_mask = df["merchantName"].str.startswith(_META_PREFIX, na=False)
    google_mask = df["merchantName"].str.contains(_GOOGLE_ADS_SUBSTR, case=False, na=False)

    parts: list[pd.DataFrame] = []
    if meta_mask.any():
        meta = df[meta_mask].copy()
        meta["platform"] = "meta"
        parts.append(meta)
    if google_mask.any():
        google = df[google_mask].copy()
        google["platform"] = "google"
        parts.append(google)

    if not parts:
        return pd.DataFrame(columns=["id", "date", "amount_usd", "platform"])

    combined = pd.concat(parts, ignore_index=True)
    combined["date"] = combined["date"].dt.date
    combined["amount_usd"] = combined["amount"].abs()
    return combined[["id", "date", "amount_usd", "platform"]].reset_index(drop=True)


def upload(csv_path: Path, db_url: str) -> tuple[int, int]:
    """Filter CSV and upsert Meta and Google Ads rows into meta_ads_spend.

    Args:
        csv_path: Path to Rain card CSV export.
        db_url: Writable PostgreSQL connection string.

    Returns:
        Tuple of (rows_inserted, rows_skipped).
    """
    rows = _filter_spend(csv_path)
    if rows.empty:
        logger.warning("No ad spend rows found in %s", csv_path.name)
        return 0, 0

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
        conn.execute(text(_ALTER_TABLE_SQL))
        inserted = 0
        skipped = 0
        for _, row in rows.iterrows():
            result = conn.execute(
                text(_INSERT_SQL),
                {
                    "id": row["id"],
                    "date": row["date"],
                    "amount_usd": float(row["amount_usd"]),
                    "platform": row["platform"],
                },
            )
            if result.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

    return inserted, skipped


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ad spend upload."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Upload Meta + Google Ads spend from Rain card CSV into meta_ads_spend table."
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to Rain CSV export (rain-transactions-export-YYYY-MM-DD.csv).",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Writable PostgreSQL URL. Defaults to ADS_DATABASE_URL env var.",
    )
    args = parser.parse_args(argv)

    db_url = args.db_url or os.environ.get("ADS_DATABASE_URL", "")
    if not db_url:
        logger.error("No database URL provided. Set ADS_DATABASE_URL or pass --db-url.")
        return 1

    if not args.csv_path.exists():
        logger.error("CSV not found: %s", args.csv_path)
        return 1

    logger.info("Processing %s ...", args.csv_path.name)
    try:
        inserted, skipped = upload(args.csv_path, db_url)
    except Exception as exc:
        logger.error("Upload failed: %s", exc)
        return 1

    logger.info("Done — %d rows inserted, %d skipped (already present).", inserted, skipped)
    return 0


def main_bank(argv: list[str] | None = None) -> int:
    """CLI entry point for uploading Meta Ads spend from a Nubank bank statement."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Upload Meta Ads spend from Nubank bank statement (.xlsx) into meta_ads_spend."
    )
    parser.add_argument(
        "xlsx_path",
        type=Path,
        help="Path to Nubank management account .xlsx export.",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Writable PostgreSQL URL for meta_ads_spend. Defaults to ADS_DATABASE_URL.",
    )
    parser.add_argument(
        "--analytics-db-url",
        default=None,
        help="Read-only analytics DB URL for FX lookup. Defaults to READONLY_DATABASE_URL.",
    )
    args = parser.parse_args(argv)

    db_url = args.db_url or os.environ.get("ADS_DATABASE_URL", "")
    if not db_url:
        logger.error("No database URL provided. Set ADS_DATABASE_URL or pass --db-url.")
        return 1

    analytics_db_url = args.analytics_db_url or os.environ.get("READONLY_DATABASE_URL") or db_url

    if not args.xlsx_path.exists():
        logger.error("File not found: %s", args.xlsx_path)
        return 1

    logger.info("Processing %s ...", args.xlsx_path.name)
    try:
        inserted, skipped = upload_bank_statement(args.xlsx_path, db_url, analytics_db_url)
    except Exception as exc:
        logger.error("Upload failed: %s", exc)
        return 1

    logger.info("Done — %d rows inserted, %d skipped (already present).", inserted, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
