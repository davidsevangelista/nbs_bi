"""Preprocess Rain invoice PDFs into CardInvoiceInputs JSON files.

Scans ``data/invoices/*.pdf``, parses each with regex against Rain's
consistent line-item structure, and writes ``<invoice_id>-actuals.json``
next to the source PDF.  Existing JSON files are skipped unless --force.

Usage::

    python -m nbs_bi.cards.preprocess_invoices
    python -m nbs_bi.cards.preprocess_invoices --force
    python -m nbs_bi.cards.preprocess_invoices data/invoices/Invoice-NKEMEJLO-0009.pdf
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rain invoice field extraction
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_PATTERNS: dict[str, re.Pattern] = {
    # pypdf maps both '(' and '-' to \x00; we normalise to '(' before matching,
    # so NKEMEJLO-0009 becomes NKEMEJLO(0009 in the extracted text.
    "invoice_id": re.compile(r"Invoice number\s+(NKEMEJLO[\-\(]\d+)"),
    "invoice_date": re.compile(r"Invoice date\s+(\w+ \d+, \d{4})"),
    "n_active_cards": re.compile(r"Virtual Cards Fee \((\d[\d,]*) cards?\)"),
    "n_transactions": re.compile(r"Transaction Fee \((\d[\d,]*) transactions?\)"),
    "passthrough_bps": re.compile(r"Network Passthrough Transaction Volume Fee \(([\d.]+) bps\)"),
    "passthrough_amt": re.compile(
        r"Network Passthrough Transaction Volume Fee.*?\$[\d,]+(?:\.\d+)?\s+\$([\d,]+\.\d+)"
    ),
    "n_3ds": re.compile(r"3D Secure Transactions Fee\s+([\d,]+)"),
    "n_infinite_txs": re.compile(r"Visa Product Type Fee - Infinite\s+([\d,]+)"),
    "n_platinum_txs": re.compile(r"Visa Product Type Fee - Platinum\s+([\d,]+)"),
    "n_applepay_txs": re.compile(r"Tokenized Transaction Count Fee - ApplePay\s+([\d,]+)"),
    "applepay_volume": re.compile(r"Tokenized Transaction Amount Fee - ApplePay\s+([\d,]+)"),
    "n_googlepay_txs": re.compile(r"Tokenized Transaction Count Fee - GooglePay\s+([\d,]+)"),
    "n_share_tokens": re.compile(r"Compliance Check Fee - Share Token\s+([\d,]+)"),
    "n_verify_domestic": re.compile(
        r"Network Passthrough Account Verification Fee - Domestic\s+([\d,]+)"
    ),
    "n_verify_intl": re.compile(
        r"Network Passthrough Account Verification Fee - International\s+([\d,]+)"
    ),
    "n_chip_auth_intl": re.compile(r"Network Passthrough Chip Auth.*?International\s+([\d,]+)"),
    "n_cross_border": re.compile(r"Network Passthrough Cross Border Transaction Fee\s+([\d,]+)"),
    "base_program_fee": re.compile(r"Base Program Fee\s+1\s+\$([\d,]+\.\d+)"),
}

# Ordered list of patterns to try when extracting the invoice grand total.
# Rain invoices consistently show "Total  $X,XXX.XX" and "Amount due $X,XXX.XX".
_TOTAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"Amount\s+due\s+\$([\d,]+\.\d{2})", re.IGNORECASE),
    re.compile(r"\bTotal\s+\$([\d,]+\.\d{2})"),
]


def _int(raw: str) -> int:
    return int(raw.replace(",", ""))


def _float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _extract_period(date_str: str) -> str:
    """Convert 'March 31, 2026' to '2026-03'."""
    try:
        dt = datetime.strptime(date_str.strip(), "%B %d, %Y")
        return dt.strftime("%Y-%m")
    except ValueError:
        parts = date_str.lower().split()
        month = _MONTH_MAP.get(parts[0], 0)
        year = parts[-1].rstrip(",")
        return f"{year}-{month:02d}"


def parse_invoice_text(text: str) -> dict:
    """Extract CardInvoiceInputs fields from the full text of a Rain invoice PDF.

    Args:
        text: Concatenated text of all PDF pages.

    Returns:
        Dict with keys matching CardInvoiceInputs fields.

    Raises:
        ValueError: If required fields cannot be found.
    """

    def find(key: str) -> re.Match | None:
        return _PATTERNS[key].search(text)

    def require(key: str, label: str) -> re.Match:
        m = find(key)
        if m is None:
            raise ValueError(f"Could not find '{label}' in invoice text")
        return m

    invoice_id = require("invoice_id", "Invoice number").group(1).replace("(", "-")
    date_str = require("invoice_date", "Invoice date").group(1)
    period = _extract_period(date_str)

    bps_match = require("passthrough_bps", "Passthrough bps")
    amt_match = require("passthrough_amt", "Passthrough amount")
    bps = float(bps_match.group(1)) / 10_000
    passthrough_usd = _float(amt_match.group(1))
    tx_volume_usd = round(passthrough_usd / bps, 2) if bps > 0 else 0.0

    chip_match = find("n_chip_auth_intl")

    invoice_total_usd = 0.0
    for pat in _TOTAL_PATTERNS:
        m = pat.search(text)
        if m:
            invoice_total_usd = _float(m.group(1))
            break
    if invoice_total_usd == 0.0:
        logger.warning("Could not extract invoice grand total from text")

    base_fee_match = find("base_program_fee")
    base_program_fee = _float(base_fee_match.group(1)) if base_fee_match else 0.0

    return {
        "invoice_id": invoice_id,
        "period": period,
        "invoice_total_usd": invoice_total_usd,
        "base_program_fee": base_program_fee,
        "n_active_cards": _int(require("n_active_cards", "Virtual Cards Fee").group(1)),
        "n_transactions": _int(require("n_transactions", "Transaction Fee").group(1)),
        "tx_volume_usd": tx_volume_usd,
        "n_3ds": _int(require("n_3ds", "3D Secure Fee").group(1)),
        "n_infinite_txs": _int(require("n_infinite_txs", "Visa Infinite Fee").group(1)),
        "n_platinum_txs": _int(require("n_platinum_txs", "Visa Platinum Fee").group(1)),
        "n_applepay_txs": _int(require("n_applepay_txs", "ApplePay count fee").group(1)),
        "applepay_volume_usd": float(
            _int(require("applepay_volume", "ApplePay amount fee").group(1))
        ),
        "n_googlepay_txs": _int(require("n_googlepay_txs", "GooglePay count fee").group(1)),
        "n_share_tokens": _int(require("n_share_tokens", "Share Token fee").group(1)),
        "n_verify_domestic": _int(require("n_verify_domestic", "Verify Domestic fee").group(1)),
        "n_verify_intl": _int(require("n_verify_intl", "Verify International fee").group(1)),
        "n_chip_auth_intl": _int(chip_match.group(1)) if chip_match else 0,
        "n_cross_border": _int(require("n_cross_border", "Cross Border fee").group(1)),
    }


# ---------------------------------------------------------------------------
# PDF reader
# ---------------------------------------------------------------------------


def _read_pdf_text(pdf_path: Path) -> str:
    """Extract all text from a PDF using pypdf.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Concatenated text from all pages.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError("pypdf is required: pip install 'pypdf>=4.0'") from exc

    reader = PdfReader(str(pdf_path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    # pypdf encodes both '(' and '-' as \x00 in some Rain PDFs; normalise to '('
    # so patterns can match uniformly. Invoice IDs are fixed up after capture.
    return text.replace("\x00", "(")


# ---------------------------------------------------------------------------
# File discovery and orchestration
# ---------------------------------------------------------------------------


def _output_path(pdf_path: Path) -> Path:
    """Return the actuals JSON path for a given PDF."""
    invoice_id = pdf_path.stem.split("-", 1)[-1] if "-" in pdf_path.stem else pdf_path.stem
    return pdf_path.parent / f"Invoice-{invoice_id}-actuals.json"


def process_pdf(pdf_path: Path, force: bool = False) -> Path | None:
    """Parse one Rain invoice PDF and write its actuals JSON.

    Args:
        pdf_path: Path to the PDF file.
        force: Overwrite existing JSON if True.

    Returns:
        Path to the written JSON, or None if skipped.
    """
    out = _output_path(pdf_path)
    if out.exists() and not force:
        logger.info("Skipping %s (already exists, use --force to overwrite)", out.name)
        return None

    logger.info("Parsing %s …", pdf_path.name)
    text = _read_pdf_text(pdf_path)
    data = parse_invoice_text(text)

    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote %s", out)
    return out


def discover_pdfs(invoices_dir: Path) -> list[Path]:
    """Return all Rain invoice PDFs in the given directory, sorted by name."""
    return sorted(invoices_dir.glob("Invoice-NKEMEJLO-*.pdf"))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the invoice preprocessor."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Preprocess Rain invoice PDFs into CardInvoiceInputs JSON files."
    )
    parser.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        help="Specific PDF files to process. Defaults to all in data/invoices/.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing JSON files.",
    )
    args = parser.parse_args(argv)

    if args.pdfs:
        targets = args.pdfs
    else:
        repo_root = Path(__file__).resolve().parents[2]
        invoices_dir = repo_root / "data" / "invoices"
        targets = discover_pdfs(invoices_dir)
        if not targets:
            logger.error("No invoice PDFs found in %s", invoices_dir)
            return 1

    errors = 0
    for pdf in targets:
        try:
            process_pdf(pdf, force=args.force)
        except Exception as exc:
            logger.error("Failed to process %s: %s", pdf.name, exc)
            errors += 1

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
