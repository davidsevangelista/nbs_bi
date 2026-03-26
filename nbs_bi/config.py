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

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")
