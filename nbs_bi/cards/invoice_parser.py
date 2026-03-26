"""Parse Rain invoice data into structured CardInvoiceInputs.

Supports loading from a JSON file or constructing directly from known values.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CardInvoiceInputs:
    """All measurable inputs that drive the card cost model for one month.

    All quantities are non-negative. Monetary values are in USD.
    """

    # Volume drivers
    n_active_cards: int
    n_transactions: int
    tx_volume_usd: float

    # Security
    n_3ds: int

    # Visa product type (transaction counts billed per product tier)
    n_infinite_txs: int
    n_platinum_txs: int

    # Tokenization
    n_applepay_txs: int
    applepay_volume_usd: float  # qty=117,184 × $0.0015 in Feb invoice
    n_googlepay_txs: int

    # Compliance
    n_share_tokens: int

    # Network extras
    n_verify_domestic: int
    n_verify_intl: int
    n_chip_auth_intl: int
    n_cross_border: int

    # Metadata (optional, for reporting)
    invoice_id: str = ""
    period: str = ""  # e.g. "2026-02"

    def __post_init__(self) -> None:
        """Validate all inputs are non-negative."""
        for field, value in self.__dict__.items():
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"Field '{field}' must be non-negative, got {value}")

    @classmethod
    def from_json(cls, path: str | Path) -> "CardInvoiceInputs":
        """Load inputs from a JSON file.

        Args:
            path: Path to a JSON file with keys matching field names.

        Returns:
            CardInvoiceInputs instance.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Invoice inputs file not found: {path}")
        with path.open() as f:
            data = json.load(f)
        logger.info("Loaded invoice inputs from %s", path)
        return cls(**data)

    @classmethod
    def from_february_2026(cls) -> "CardInvoiceInputs":
        """Return the exact inputs from Rain invoice NKEMEJLO-0008 (Feb 2026).

        Used as a reference baseline and for model validation.
        Total expected cost: $6,693.58
        """
        return cls(
            invoice_id="NKEMEJLO-0008",
            period="2026-02",
            n_active_cards=577,
            n_transactions=6_885,
            tx_volume_usd=390.44 / 0.00147,  # back-calculated from 14.7 bps passthrough
            n_3ds=54,
            n_infinite_txs=884,
            n_platinum_txs=829,
            n_applepay_txs=4_138,
            applepay_volume_usd=117_184.0,  # qty used as the "amount" unit for this fee
            n_googlepay_txs=1_312,
            n_share_tokens=775,
            n_verify_domestic=160,
            n_verify_intl=354,
            n_chip_auth_intl=1,
            n_cross_border=23_994,
        )
