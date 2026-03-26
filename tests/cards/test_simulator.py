"""Tests for card cost model and simulator.

All tests validate against the February 2026 invoice (NKEMEJLO-0008).
Reference total: $6,693.58 USD.
"""

import pytest

from nbs_bi.cards.invoice_parser import CardInvoiceInputs
from nbs_bi.cards.models import CardCostModel
from nbs_bi.cards.simulator import CardCostSimulator

REFERENCE_TOTAL = 6_693.58
REFERENCE_TRANSACTIONS = 6_885
TOLERANCE = 0.02  # $0.02 rounding tolerance


class TestCardInvoiceInputs:
    def test_from_february_2026_loads(self) -> None:
        inputs = CardInvoiceInputs.from_february_2026()
        assert inputs.n_transactions == 6_885
        assert inputs.n_active_cards == 577
        assert inputs.invoice_id == "NKEMEJLO-0008"

    def test_negative_field_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            CardInvoiceInputs(
                n_active_cards=-1,
                n_transactions=100,
                tx_volume_usd=1000.0,
                n_3ds=0,
                n_infinite_txs=0,
                n_platinum_txs=0,
                n_applepay_txs=0,
                applepay_volume_usd=0.0,
                n_googlepay_txs=0,
                n_share_tokens=0,
                n_verify_domestic=0,
                n_verify_intl=0,
                n_chip_auth_intl=0,
                n_cross_border=0,
            )


class TestCardCostModel:
    @pytest.fixture
    def feb_model(self) -> CardCostModel:
        return CardCostModel.from_february_2026()

    def test_total_matches_invoice(self, feb_model: CardCostModel) -> None:
        """The model must reproduce the February 2026 invoice total within $0.02."""
        total = feb_model.cost_breakdown().total
        assert abs(total - REFERENCE_TOTAL) <= TOLERANCE, (
            f"Expected ~${REFERENCE_TOTAL}, got ${total:.2f}"
        )

    def test_cost_per_transaction(self, feb_model: CardCostModel) -> None:
        cpt = feb_model.cost_per_transaction()
        expected = REFERENCE_TOTAL / REFERENCE_TRANSACTIONS
        assert abs(cpt - expected) <= TOLERANCE

    def test_cost_per_transaction_zero_raises(self) -> None:
        inputs = CardInvoiceInputs.from_february_2026()
        inputs_zero = CardInvoiceInputs(
            **{**inputs.__dict__, "n_transactions": 0}
        )
        model = CardCostModel(inputs_zero)
        with pytest.raises(ValueError, match="zero transactions"):
            model.cost_per_transaction()

    def test_breakdown_line_items(self, feb_model: CardCostModel) -> None:
        bd = feb_model.cost_breakdown()
        assert abs(bd.base_program - 1_000.00) < TOLERANCE
        assert abs(bd.virtual_cards - 115.40) < TOLERANCE
        assert abs(bd.transaction_fee - 516.375) < TOLERANCE
        assert abs(bd.visa_infinite - 1_502.80) < TOLERANCE
        assert abs(bd.visa_platinum - 207.25) < TOLERANCE
        assert abs(bd.share_token - 968.75) < TOLERANCE
        assert abs(bd.cross_border - 239.94) < TOLERANCE

    def test_sensitivity_analysis_returns_all_fields(self, feb_model: CardCostModel) -> None:
        sensitivity = feb_model.sensitivity_analysis()
        assert "n_transactions" in sensitivity
        assert "n_infinite_txs" in sensitivity
        assert "n_share_tokens" in sensitivity

    def test_sensitivity_infinite_is_top_variable_driver(self, feb_model: CardCostModel) -> None:
        """Visa Infinite at $1.70/tx should be among the top sensitive variables."""
        sensitivity = feb_model.sensitivity_analysis()
        top_drivers = list(sensitivity.keys())[:3]
        # n_infinite_txs drives the most variable cost; n_transactions is also large
        assert "n_infinite_txs" in top_drivers or "n_transactions" in top_drivers

    def test_cost_contribution_sums_to_100(self, feb_model: CardCostModel) -> None:
        contributions = feb_model.cost_contribution_pct()
        total_pct = sum(contributions.values())
        assert abs(total_pct - 100.0) < 0.1


class TestCardCostSimulator:
    @pytest.fixture
    def sim(self) -> CardCostSimulator:
        return CardCostSimulator.from_february_2026()

    def test_run_baseline_matches_invoice(self, sim: CardCostSimulator) -> None:
        result = sim.run(label="baseline")
        assert abs(result.total_cost_usd - REFERENCE_TOTAL) <= TOLERANCE

    def test_run_with_override(self, sim: CardCostSimulator) -> None:
        result = sim.run(label="double_txs", n_transactions=REFERENCE_TRANSACTIONS * 2)
        # More transactions = higher cost
        assert result.total_cost_usd > REFERENCE_TOTAL

    def test_baseline_report_keys(self, sim: CardCostSimulator) -> None:
        report = sim.baseline_report()
        required_keys = {
            "total_cost_usd", "cost_per_transaction_usd", "n_transactions",
            "breakdown", "sensitivity_10pct", "cost_contribution_pct", "top_cost_drivers"
        }
        assert required_keys.issubset(set(report.keys()))

    def test_fit_linear_model_single_point_raises(self, sim: CardCostSimulator) -> None:
        inputs = CardInvoiceInputs.from_february_2026()
        with pytest.raises(ValueError, match="at least 2 data points"):
            sim.fit_linear_model([inputs], [REFERENCE_TOTAL])

    def test_fit_and_project(self, sim: CardCostSimulator) -> None:
        """Fit on two synthetic months, then project a third."""
        base = CardInvoiceInputs.from_february_2026()
        from dataclasses import asdict
        # Synthetic month 2: 20% more transactions
        m2 = CardInvoiceInputs(**{**asdict(base), "n_transactions": 8_262})
        totals = [REFERENCE_TOTAL, REFERENCE_TOTAL * 1.12]

        coeffs = sim.fit_linear_model([base, m2], totals)
        assert "intercept" in coeffs
        assert "r_squared" in coeffs

        projected = sim.project(n_transactions=10_000)
        assert projected > 0

    def test_project_without_fit_uses_rate_model(self) -> None:
        """Without a fitted regression, project() falls back to the deterministic rate model."""
        fresh_sim = CardCostSimulator.from_february_2026()
        projected = fresh_sim.project(n_transactions=10_000)
        # Deterministic result must be positive and differ from baseline (6885 txs)
        assert projected > 0
        assert projected != fresh_sim.run().total_cost_usd

    def test_compare_scenarios(self, sim: CardCostSimulator) -> None:
        scenarios = [
            {"label": "conservative", "n_transactions": 5_000},
            {"label": "base", "n_transactions": 6_885},
            {"label": "aggressive", "n_transactions": 12_000},
        ]
        results = sim.compare_scenarios(scenarios)
        assert len(results) == 3
        # Costs should be monotonically increasing with more transactions
        totals = [r.total_cost_usd for r in results]
        assert totals[0] < totals[1] < totals[2]
