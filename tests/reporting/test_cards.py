"""Tests for nbs_bi.reporting.cards — figure builders and helpers."""

import pandas as pd
import plotly.graph_objects as go
import pytest

from nbs_bi.cards.models import CardCostModel, CardFeeRates
from nbs_bi.cards.invoice_parser import CardInvoiceInputs
from nbs_bi.reporting.cards import (
    _fig_breakdown,
    _fig_sensitivity,
    _fig_trend,
    _mask_user_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def model() -> CardCostModel:
    return CardCostModel.from_february_2026()


@pytest.fixture()
def minimal_model() -> CardCostModel:
    inputs = CardInvoiceInputs(
        n_active_cards=100,
        n_transactions=500,
        tx_volume_usd=50_000.0,
        n_3ds=100,
        n_infinite_txs=300,
        n_platinum_txs=200,
        n_applepay_txs=50,
        applepay_volume_usd=5_000.0,
        n_googlepay_txs=20,
        n_share_tokens=10,
        n_verify_domestic=30,
        n_verify_intl=5,
        n_chip_auth_intl=5,
        n_cross_border=10,
        period="2026-01",
    )
    return CardCostModel(inputs)


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_mask_user_id() -> None:
    uid = "550e8400-e29b-41d4-a716-446655440000"
    assert _mask_user_id(uid) == "550e8400…"


# ---------------------------------------------------------------------------
# Figure builder tests
# ---------------------------------------------------------------------------


def test_fig_breakdown_is_figure(model: CardCostModel) -> None:
    fig = _fig_breakdown(model.cost_breakdown())
    assert isinstance(fig, go.Figure)


def test_fig_breakdown_one_trace(model: CardCostModel) -> None:
    fig = _fig_breakdown(model.cost_breakdown())
    assert len(fig.data) == 1


def test_fig_breakdown_horizontal(model: CardCostModel) -> None:
    fig = _fig_breakdown(model.cost_breakdown())
    assert fig.data[0].orientation == "h"


def test_fig_breakdown_excludes_zero_items() -> None:
    inputs = CardInvoiceInputs(
        n_active_cards=10,
        n_transactions=100,
        tx_volume_usd=10_000.0,
        n_3ds=0,
        n_infinite_txs=100,
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
    model = CardCostModel(inputs)
    bd = model.cost_breakdown()
    fig = _fig_breakdown(bd)
    n_bars = len(fig.data[0].y)
    zero_items = sum(1 for _, v in bd.sorted_by_amount() if v == 0)
    total_items = len(bd.sorted_by_amount())
    assert n_bars == total_items - zero_items


def test_fig_trend_two_traces(model: CardCostModel) -> None:
    history = [("2026-01", model), ("2026-02", model)]
    fig = _fig_trend(history)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2


def test_fig_trend_x_labels(model: CardCostModel) -> None:
    history = [("Jan", model), ("Feb", model), ("Mar", model)]
    fig = _fig_trend(history)
    assert list(fig.data[0].x) == ["Jan", "Feb", "Mar"]


def test_fig_trend_totals_match(model: CardCostModel, minimal_model: CardCostModel) -> None:
    history = [("2026-01", minimal_model), ("2026-02", model)]
    fig = _fig_trend(history)
    totals = list(fig.data[0].y)
    assert totals[0] == pytest.approx(minimal_model.cost_breakdown().total, rel=1e-4)
    assert totals[1] == pytest.approx(model.cost_breakdown().total, rel=1e-4)


def test_fig_sensitivity_is_figure(model: CardCostModel) -> None:
    fig = _fig_sensitivity(model)
    assert isinstance(fig, go.Figure)


def test_fig_sensitivity_max_ten_bars(model: CardCostModel) -> None:
    fig = _fig_sensitivity(model)
    assert len(fig.data[0].y) <= 10


def test_fig_sensitivity_positive_values_only(model: CardCostModel) -> None:
    fig = _fig_sensitivity(model)
    for v in fig.data[0].x:
        assert v >= 0
