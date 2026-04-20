"""Card cost center simulation module."""

from nbs_bi.cards.invoice_parser import CardInvoiceInputs
from nbs_bi.cards.models import CardCostModel

__all__ = ["CardCostModel", "CardCostSimulator", "CardInvoiceInputs"]


def __getattr__(name: str) -> object:
    """Load simulator dependencies only when the simulator is requested."""
    if name == "CardCostSimulator":
        from nbs_bi.cards.simulator import CardCostSimulator

        return CardCostSimulator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
