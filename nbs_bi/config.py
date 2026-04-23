"""Global configuration loaded from environment variables."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Paths
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
INVOICES_DIR = DATA_DIR / "invoices"
PROCESSED_DIR = DATA_DIR / "processed"

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

# Database (read-write, for future use)
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Database — read-only replica used by onramp and other analytics modules
READONLY_DATABASE_URL = os.environ.get("READONLY_DATABASE_URL", "")

# Database — ads spend only (Neon), used by the Marketing - Ads tab
ADS_DATABASE_URL = os.environ.get("ADS_DATABASE_URL", "")

# Parquet cache directory for DB query results (set DB_CACHE_DIR= to disable)
DB_CACHE_DIR = os.getenv("DB_CACHE_DIR", "")
