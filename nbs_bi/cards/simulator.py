"""Card cost scenario simulator and linear projection model.

Provides:
- What-if scenario simulation (override any input variable)
- Linear regression model fitted to historical invoice data
- Monthly cost projection given expected next-month inputs
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import LinearRegression

from nbs_bi.cards.invoice_parser import CardInvoiceInputs
from nbs_bi.cards.models import CardCostModel, CardFeeRates, CostBreakdown

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Output of a single simulation run."""

    inputs: CardInvoiceInputs
    breakdown: CostBreakdown
    total_cost_usd: float
    cost_per_transaction_usd: float
    scenario_label: str = "custom"

    def summary(self) -> dict[str, Any]:
        """Return a flat summary dict for display or export."""
        return {
            "scenario": self.scenario_label,
            "total_cost_usd": round(self.total_cost_usd, 2),
            "cost_per_transaction_usd": round(self.cost_per_transaction_usd, 4),
            "n_transactions": self.inputs.n_transactions,
            "n_active_cards": self.inputs.n_active_cards,
        }


class CardCostSimulator:
    """Run what-if scenarios and fit projection models for card costs.

    Args:
        base: Baseline CardCostModel or CardInvoiceInputs (typically last known invoice).
        rates: Fee rates. Defaults to current Rain pricing. Ignored if a
               CardCostModel is passed (its rates are used instead).
    """

    def __init__(
        self,
        base: "CardCostModel | CardInvoiceInputs",
        rates: CardFeeRates | None = None,
    ) -> None:
        if isinstance(base, CardCostModel):
            self.base_inputs = base.inputs
            self.rates = base.rates
        else:
            self.base_inputs = base
            self.rates = rates or CardFeeRates()
        self._regression_model: LinearRegression | None = None
        self._regression_features: list[str] = []

    @classmethod
    def from_february_2026(cls) -> "CardCostSimulator":
        """Convenience factory using the February 2026 reference invoice."""
        return cls(CardInvoiceInputs.from_february_2026())

    def run(self, label: str = "custom", **overrides: Any) -> SimulationResult:
        """Run a scenario by overriding any input field from the baseline.

        Args:
            label: Human-readable label for this scenario.
            **overrides: Any CardInvoiceInputs field to override.

        Returns:
            SimulationResult with full breakdown.

        Example:
            sim.run(label="2x growth", n_transactions=13_770, n_active_cards=1_000)
        """
        base_dict = asdict(self.base_inputs)
        base_dict.update(overrides)
        inputs = CardInvoiceInputs(**base_dict)
        model = CardCostModel(inputs, self.rates)
        breakdown = model.cost_breakdown()

        return SimulationResult(
            inputs=inputs,
            breakdown=breakdown,
            total_cost_usd=breakdown.total,
            cost_per_transaction_usd=model.cost_per_transaction(),
            scenario_label=label,
        )

    def compare_scenarios(self, scenarios: list[dict[str, Any]]) -> list[SimulationResult]:
        """Run multiple scenarios and return all results for comparison.

        Args:
            scenarios: List of dicts, each with optional 'label' key and
                       any CardInvoiceInputs field overrides.

        Returns:
            List of SimulationResult, one per scenario.
        """
        results = []
        for scenario in scenarios:
            label = scenario.pop("label", "unnamed")
            results.append(self.run(label=label, **scenario))
        return results

    def fit_linear_model(
        self,
        historical_inputs: list[CardInvoiceInputs],
        historical_totals: list[float],
        features: list[str] | None = None,
    ) -> dict[str, float]:
        """Fit a linear regression to predict total monthly cost.

        The model learns: total_cost ≈ β₀ + β₁·x₁ + β₂·x₂ + ...

        Args:
            historical_inputs: List of CardInvoiceInputs, one per past month.
            historical_totals: Actual total costs for those months (USD).
            features: Input field names to use as regression features.
                      Defaults to the main volume drivers.

        Returns:
            Dict of feature → coefficient, plus 'intercept'.
        """
        if len(historical_inputs) != len(historical_totals):
            raise ValueError("historical_inputs and historical_totals must have equal length.")
        if len(historical_inputs) < 2:
            raise ValueError("Need at least 2 data points to fit a linear model.")

        default_features = [
            "n_transactions",
            "n_active_cards",
            "tx_volume_usd",
            "n_infinite_txs",
            "n_platinum_txs",
            "n_share_tokens",
            "n_cross_border",
        ]
        self._regression_features = features or default_features

        X = np.array(
            [[getattr(inp, f) for f in self._regression_features] for inp in historical_inputs]
        )
        y = np.array(historical_totals)

        self._regression_model = LinearRegression()
        self._regression_model.fit(X, y)

        r2 = self._regression_model.score(X, y)
        logger.info("Linear model fitted. R²=%.4f on %d months.", r2, len(historical_inputs))

        coefficients = dict(zip(self._regression_features, self._regression_model.coef_, strict=True))
        coefficients["intercept"] = float(self._regression_model.intercept_)
        coefficients["r_squared"] = round(r2, 4)
        return coefficients

    def project(self, **input_overrides: Any) -> float:
        """Project next month's total cost.

        Uses the fitted linear model if available (call fit_linear_model() first
        to capture historical variance). Falls back to the deterministic rate model
        when no regression has been fitted — useful for single-month analysis.

        Args:
            **input_overrides: Any CardInvoiceInputs field to override from baseline.

        Returns:
            Projected total cost in USD.
        """
        base_dict = asdict(self.base_inputs)
        base_dict.update(input_overrides)
        inputs = CardInvoiceInputs(**base_dict)

        if self._regression_model is None:
            logger.info("No regression fitted — using deterministic rate model for projection.")
            projected = CardCostModel(inputs, self.rates).cost_breakdown().total
        else:
            x = np.array([[getattr(inputs, f) for f in self._regression_features]])
            projected = float(self._regression_model.predict(x)[0])

        logger.info("Projected monthly cost: $%.2f", projected)
        return projected

    def baseline_report(self) -> dict[str, Any]:
        """Return a full diagnostic report for the baseline inputs.

        Returns:
            Dict with total, cost/tx, breakdown, sensitivity, and contributions.
        """
        model = CardCostModel(self.base_inputs, self.rates)
        breakdown = model.cost_breakdown()

        return {
            "period": self.base_inputs.period,
            "invoice_id": self.base_inputs.invoice_id,
            "total_cost_usd": round(breakdown.total, 2),
            "cost_per_transaction_usd": round(model.cost_per_transaction(), 4),
            "n_transactions": self.base_inputs.n_transactions,
            "n_active_cards": self.base_inputs.n_active_cards,
            "breakdown": breakdown.as_dict(),
            "top_cost_drivers": breakdown.sorted_by_amount()[:5],
            "sensitivity_10pct": model.sensitivity_analysis(delta=0.10),
            "cost_contribution_pct": model.cost_contribution_pct(),
        }


def _build_markdown_report(report: dict[str, Any]) -> str:
    """Render a simulation report as a Markdown string.

    Args:
        report: Output of CardCostSimulator.baseline_report().

    Returns:
        Multi-line Markdown string ready to write to a .md file.
    """
    contributions = report["cost_contribution_pct"]
    lines: list[str] = [
        f"# NBS Card Cost Simulation — {report['period']}",
        "",
        f"**Invoice:** {report['invoice_id']}  ",
        f"**Generated:** {__import__('datetime').date.today()}  ",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Total cost | ${report['total_cost_usd']:,.2f} |",
        f"| Transactions | {report['n_transactions']:,} |",
        f"| Active cards | {report['n_active_cards']:,} |",
        f"| Cost per transaction | ${report['cost_per_transaction_usd']:.4f} |",
        "",
        "---",
        "",
        "## Cost Breakdown",
        "",
        "| Line Item | Amount (USD) | % of Total |",
        "|---|---:|---:|",
    ]

    for item, amount in report["breakdown"].items():
        if item == "total":
            lines.append(f"| **TOTAL** | **${amount:,.2f}** | **100.00%** |")
        else:
            pct = contributions.get(item, 0)
            lines.append(f"| {item} | ${amount:,.2f} | {pct:.2f}% |")

    lines += [
        "",
        "---",
        "",
        "## Top 5 Cost Drivers",
        "",
        "| Rank | Line Item | Amount (USD) |",
        "|---|---|---:|",
    ]
    for rank, (name, amount) in enumerate(report["top_cost_drivers"], 1):
        lines.append(f"| {rank} | {name} | ${amount:,.2f} |")

    lines += [
        "",
        "---",
        "",
        "## Sensitivity Analysis",
        "",
        "> Dollar impact on total cost when each driver increases by 10%.",
        "",
        "| Driver | +10% Impact (USD) |",
        "|---|---:|",
    ]
    for driver, impact in list(report["sensitivity_10pct"].items())[:10]:
        if impact > 0:
            lines.append(f"| {driver} | ${impact:,.2f} |")

    lines += ["", "---", "", "*Generated by `nbs_bi.cards.simulator`*", ""]
    return "\n".join(lines)


def main() -> None:
    """CLI entrypoint: print a baseline report and save it as a .md file."""
    from pathlib import Path

    from rich import box
    from rich import print as rprint
    from rich.table import Table

    from nbs_bi.config import ROOT_DIR

    sim = CardCostSimulator.from_february_2026()
    report = sim.baseline_report()

    # --- terminal output ---
    rprint(f"\n[bold cyan]NBS Card Cost Report — {report['period']}[/bold cyan]")
    rprint(f"Invoice: {report['invoice_id']}")
    rprint(f"Total cost: [bold green]${report['total_cost_usd']:,.2f}[/bold green]")
    rprint(f"Transactions: {report['n_transactions']:,}")
    rprint(f"Active cards: {report['n_active_cards']:,}")
    rprint(f"Cost per transaction: [bold yellow]${report['cost_per_transaction_usd']:.4f}[/bold yellow]\n")

    table = Table(title="Cost Breakdown", box=box.SIMPLE)
    table.add_column("Line Item", style="cyan")
    table.add_column("Amount (USD)", justify="right", style="green")
    table.add_column("% of Total", justify="right")

    contributions = report["cost_contribution_pct"]
    for item, amount in report["breakdown"].items():
        if item == "total":
            table.add_row("[bold]TOTAL[/bold]", f"[bold]${amount:,.2f}[/bold]", "100.00%")
        else:
            table.add_row(item, f"${amount:,.2f}", f"{contributions.get(item, 0):.2f}%")

    rprint(table)

    rprint("\n[bold]Sensitivity Analysis (10% increase per driver → $ impact on total)[/bold]")
    for driver, impact in list(report["sensitivity_10pct"].items())[:8]:
        rprint(f"  {driver:<35} ${impact:>8.2f}")

    rprint("\n[bold]Top 5 Cost Drivers[/bold]")
    for name, amount in report["top_cost_drivers"]:
        rprint(f"  {name:<30} ${amount:,.2f}")

    # --- .md file output ---
    out_dir = ROOT_DIR / "data" / "cards_simulation"
    out_dir.mkdir(parents=True, exist_ok=True)
    period_slug = report["period"].replace("-", "_")
    out_path = out_dir / f"card_simulation_{period_slug}.md"
    out_path.write_text(_build_markdown_report(report), encoding="utf-8")
    rprint(f"\n[dim]Report saved → {out_path}[/dim]")


if __name__ == "__main__":
    main()
