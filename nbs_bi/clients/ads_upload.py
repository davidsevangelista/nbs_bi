"""CLI: filter Rain card CSV to FACEBK rows and upsert into meta_ads_spend.

Extracts only id, date, and amount_usd — no PII stored.
Running the script multiple times is safe (ON CONFLICT DO NOTHING).

Usage::

    nbs-ads-upload rain-transactions-export-2026-04-22.csv
    nbs-ads-upload rain-transactions-export-2026-04-22.csv --db-url postgresql://...
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import create_engine

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS meta_ads_spend (
    id          TEXT PRIMARY KEY,
    date        DATE         NOT NULL,
    amount_usd  NUMERIC(10,4) NOT NULL
);
CREATE INDEX IF NOT EXISTS meta_ads_spend_date_idx ON meta_ads_spend (date);
"""

_INSERT_SQL = """
INSERT INTO meta_ads_spend (id, date, amount_usd)
VALUES (:id, :date, :amount_usd)
ON CONFLICT (id) DO NOTHING
"""

_MERCHANT_PREFIX = "FACEBK"


def _filter_spend(csv_path: Path) -> pd.DataFrame:
    """Read CSV and return FACEBK rows with only id, date, amount_usd columns."""
    df = pd.read_csv(csv_path, parse_dates=["date"])
    mask = df["merchantName"].str.startswith(_MERCHANT_PREFIX, na=False)
    fb = df[mask].copy()
    if fb.empty:
        return pd.DataFrame(columns=["id", "date", "amount_usd"])
    fb["date"] = fb["date"].dt.date
    fb["amount_usd"] = fb["amount"].abs()
    return fb[["id", "date", "amount_usd"]].reset_index(drop=True)


def upload(csv_path: Path, db_url: str) -> tuple[int, int]:
    """Filter CSV and upsert FACEBK rows into meta_ads_spend.

    Args:
        csv_path: Path to Rain card CSV export.
        db_url: Writable PostgreSQL connection string.

    Returns:
        Tuple of (rows_inserted, rows_skipped).
    """
    rows = _filter_spend(csv_path)
    if rows.empty:
        logger.warning("No FACEBK rows found in %s", csv_path.name)
        return 0, 0

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
        inserted = 0
        skipped = 0
        for _, row in rows.iterrows():
            result = conn.execute(
                text(_INSERT_SQL),
                {"id": row["id"], "date": row["date"], "amount_usd": float(row["amount_usd"])},
            )
            if result.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

    return inserted, skipped


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for Meta Ads spend upload."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Filter Rain card CSV to FACEBK rows and upsert into meta_ads_spend table."
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to Rain CSV export (rain-transactions-export-YYYY-MM-DD.csv).",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Writable PostgreSQL URL. Defaults to DATABASE_URL env var.",
    )
    args = parser.parse_args(argv)

    db_url = args.db_url or os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("No database URL provided. Set DATABASE_URL or pass --db-url.")
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


if __name__ == "__main__":
    sys.exit(main())
