"""Card cost model: compute total cost, breakdown, and cost per transaction.

The model maps CardInvoiceInputs to a full cost breakdown matching the Rain
invoice structure. Each fee line is an explicit formula — no magic numbers
outside of the RATES dataclass.
"""

import logging
from dataclasses import dataclass, field

import numpy as np

from nbs_bi.cards.invoice_parser import CardInvoiceInputs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CardFeeRates:
    """Unit prices for all Rain card fee lines (USD).

    Frozen so rates are never accidentally mutated during simulation.
    Update these when Rain changes its pricing.
    """

    base_program_fee: float = 2_500.00
    virtual_card_fee: float = 0.20
    transaction_fee: float = 0.075
    network_volume_bps: float = 0.00147  # 14.7 basis points
    fee_3ds: float = 0.04
    visa_infinite_fee: float = 1.70
    visa_platinum_fee: float = 0.25
    applepay_count_fee: float = 0.03
    applepay_amount_fee: float = 0.0015
    googlepay_count_fee: float = 0.03
    share_token_fee: float = 1.25
    verify_domestic_fee: float = 0.0075
    verify_intl_fee: float = 0.09
    chip_auth_intl_fee: float = 0.04
    network_tx_cost: float = 0.20
    network_3ds_cost: float = 0.02
    cross_border_fee: float = 0.01


@dataclass
class CostBreakdown:
    """Itemised cost for one invoice period."""

    base_program: float = 0.0
    virtual_cards: float = 0.0
    transaction_fee: float = 0.0
    network_volume: float = 0.0
    fee_3ds: float = 0.0
    visa_infinite: float = 0.0
    visa_platinum: float = 0.0
    applepay_count: float = 0.0
    applepay_amount: float = 0.0
    googlepay_count: float = 0.0
    share_token: float = 0.0
    verify_domestic: float = 0.0
    verify_intl: float = 0.0
    chip_auth_intl: float = 0.0
    network_tx_cost: float = 0.0
    network_3ds_cost: float = 0.0
    cross_border: float = 0.0

    @property
    def total(self) -> float:
        """Sum of all line items."""
        return sum(
            v for k, v in self.__dict__.items() if not k.startswith("_")
        )

    def as_dict(self) -> dict[str, float]:
        """Return breakdown as a plain dict including total."""
        d = {k: round(v, 2) for k, v in self.__dict__.items() if not k.startswith("_")}
        d["total"] = round(self.total, 2)
        return d

    def sorted_by_amount(self) -> list[tuple[str, float]]:
        """Return line items sorted descending by cost (excluding total)."""
        items = [(k, v) for k, v in self.__dict__.items() if not k.startswith("_")]
        return sorted(items, key=lambda x: x[1], reverse=True)


class CardCostModel:
    """Computes the full cost for a given set of card program inputs.

    Args:
        inputs: The monthly volume/count inputs.
        rates: Fee rates to apply. Defaults to current Rain pricing.
    """

    def __init__(
        self,
        inputs: CardInvoiceInputs,
        rates: CardFeeRates | None = None,
    ) -> None:
        self.inputs = inputs
        self.rates = rates or CardFeeRates()

    @classmethod
    def from_february_2026(cls) -> "CardCostModel":
        """Convenience factory using the February 2026 reference invoice."""
        return cls(CardInvoiceInputs.from_february_2026())

    @classmethod
    def from_invoice(cls, path: str, rates: "CardFeeRates | None" = None) -> "CardCostModel":
        """Load a model from a JSON file of invoice inputs.

        Args:
            path: Path to a JSON file with keys matching CardInvoiceInputs fields.
            rates: Optional custom fee rates. Defaults to current Rain pricing.

        Returns:
            CardCostModel ready to compute costs.
        """
        inputs = CardInvoiceInputs.from_json(path)
        return cls(inputs, rates)

    def cost_breakdown(self) -> CostBreakdown:
        """Compute itemised cost for the current inputs.

        Returns:
            CostBreakdown with all line items populated.
        """
        r = self.rates
        i = self.inputs

        breakdown = CostBreakdown(
            base_program=i.base_program_fee if i.base_program_fee > 0 else r.base_program_fee,
            virtual_cards=i.n_active_cards * r.virtual_card_fee,
            transaction_fee=i.n_transactions * r.transaction_fee,
            network_volume=i.tx_volume_usd * r.network_volume_bps,
            fee_3ds=i.n_3ds * r.fee_3ds,
            visa_infinite=i.n_infinite_txs * r.visa_infinite_fee,
            visa_platinum=i.n_platinum_txs * r.visa_platinum_fee,
            applepay_count=i.n_applepay_txs * r.applepay_count_fee,
            applepay_amount=i.applepay_volume_usd * r.applepay_amount_fee,
            googlepay_count=i.n_googlepay_txs * r.googlepay_count_fee,
            share_token=i.n_share_tokens * r.share_token_fee,
            verify_domestic=i.n_verify_domestic * r.verify_domestic_fee,
            verify_intl=i.n_verify_intl * r.verify_intl_fee,
            chip_auth_intl=i.n_chip_auth_intl * r.chip_auth_intl_fee,
            network_tx_cost=i.n_transactions * r.network_tx_cost,
            network_3ds_cost=i.n_3ds * r.network_3ds_cost,
            cross_border=i.n_cross_border * r.cross_border_fee,
        )

        logger.debug("Cost breakdown total: $%.2f", breakdown.total)
        return breakdown

    def cost_per_transaction(self) -> float:
        """Compute total cost divided by number of transactions.

        This is the weighted average cost per transaction — it allocates
        fixed costs (base fee) and semi-fixed costs proportionally across
        all transactions in the period.

        Returns:
            Cost in USD per transaction.

        Raises:
            ValueError: If n_transactions is zero.
        """
        if self.inputs.n_transactions == 0:
            raise ValueError("Cannot compute cost per transaction with zero transactions.")
        total = self.cost_breakdown().total
        cpt = total / self.inputs.n_transactions
        logger.info("Cost per transaction: $%.4f (total $%.2f / %d txs)", cpt, total, self.inputs.n_transactions)
        return cpt

    def sensitivity_analysis(self, delta: float = 0.10) -> dict[str, float]:
        """Estimate how much the total cost changes when each input increases by `delta` (10%).

        This is a local sensitivity / partial derivative estimate. It shows
        which drivers have the highest dollar impact per unit change.

        Args:
            delta: Fractional increase to apply to each input (default 10%).

        Returns:
            Dict mapping input field name → dollar change in total cost.
        """
        base_total = self.cost_breakdown().total
        results: dict[str, float] = {}

        numeric_fields = {
            k: v for k, v in self.inputs.__dict__.items()
            if isinstance(v, (int, float)) and k not in ("invoice_id", "period")
        }

        for field_name, base_value in numeric_fields.items():
            if base_value == 0:
                results[field_name] = 0.0
                continue
            delta_value = base_value * delta
            perturbed = CardInvoiceInputs(
                **{**self.inputs.__dict__, field_name: base_value + delta_value}
            )
            perturbed_model = CardCostModel(perturbed, self.rates)
            perturbed_total = perturbed_model.cost_breakdown().total
            results[field_name] = round(perturbed_total - base_total, 4)

        return dict(sorted(results.items(), key=lambda x: abs(x[1]), reverse=True))

    def cost_contribution_pct(self) -> dict[str, float]:
        """Return each line item's share of total cost as a percentage.

        Returns:
            Dict mapping line item name → percentage of total (0–100).
        """
        bd = self.cost_breakdown()
        total = bd.total
        return {
            k: round((v / total) * 100, 2)
            for k, v in bd.as_dict().items()
            if k != "total"
        }
