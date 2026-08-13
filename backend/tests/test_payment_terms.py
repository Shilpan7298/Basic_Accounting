"""Payment-terms parsing and milestone allocation.

The plan calls this the milestone most likely to be wrong. So the tests
assert two different things: that the target dialect parses correctly, and —
more importantly — that everything outside it is *refused* rather than
guessed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import MilestoneTrigger
from app.services import payment_terms
from app.services.payment_terms import allocate_amounts, due_date_for, parse

HUNDRED = Decimal("100")


# --- the dialect we actually get -------------------------------------------

def test_the_canonical_three_way_split():
    result = parse("30% advance along with PO, 60% before dispatch, 10% after commissioning")

    assert result.is_usable
    assert result.confidence >= 0.9
    assert [m.percent for m in result.milestones] == [
        Decimal("30.00"), Decimal("60.00"), Decimal("10.00")
    ]
    assert [m.trigger_event for m in result.milestones] == [
        MilestoneTrigger.ON_PO,
        MilestoneTrigger.BEFORE_DISPATCH,
        MilestoneTrigger.AFTER_COMMISSIONING,
    ]


def test_single_milestone_full_payment_before_dispatch():
    result = parse("100% against proforma prior to despatch")

    assert result.is_usable
    assert len(result.milestones) == 1
    assert result.milestones[0].trigger_event is MilestoneTrigger.BEFORE_DISPATCH
    assert result.milestones[0].percent == HUNDRED


def test_balance_wording_resolves_to_the_remainder():
    result = parse(
        "70% against proforma prior to despatch, balance within 30 days of commissioning"
    )

    assert result.is_usable
    assert [m.percent for m in result.milestones] == [Decimal("70.00"), Decimal("30.00")]
    assert result.milestones[1].net_days == 30


def test_semicolons_and_the_word_and_both_split_clauses():
    for text in (
        "40% advance with order; 50% before dispatch; 10% on delivery",
        "40% advance with order, 50% before dispatch and 10% on delivery",
    ):
        result = parse(text)
        assert result.is_usable, text
        assert len(result.milestones) == 3, text


def test_before_dispatch_beats_a_later_mention_of_delivery():
    """Ordering of the keyword rules matters — 'prior to despatch' wins."""
    result = parse("100% prior to despatch of the material to the delivery site")
    assert result.milestones[0].trigger_event is MilestoneTrigger.BEFORE_DISPATCH


# --- what it must refuse ---------------------------------------------------

def test_percentages_that_do_not_sum_to_100_are_not_usable():
    result = parse("50% advance, 40% before dispatch")

    assert not result.is_usable
    assert result.total_percent == Decimal("90.00")
    assert result.confidence < 0.5
    assert any("100" in w for w in result.warnings)


def test_over_100_percent_is_also_refused():
    result = parse("60% advance, 60% before dispatch")

    assert not result.is_usable
    assert result.total_percent == Decimal("120.00")


def test_prose_with_no_percentages_yields_nothing():
    result = parse("Payment by NEFT as mutually agreed between the parties")

    assert not result.is_usable
    assert result.milestones == []
    assert result.confidence == 0.0


def test_empty_terms_are_handled_without_raising():
    for value in (None, "", "   "):
        result = parse(value)
        assert not result.is_usable
        assert result.confidence == 0.0


def test_two_balance_clauses_are_ambiguous_and_refused():
    result = parse("balance on delivery, balance after commissioning")

    assert not result.is_usable
    assert any("more than one" in w for w in result.warnings)


def test_an_unrecognised_trigger_lowers_confidence():
    result = parse("100% as per mutually agreed schedule")

    assert result.confidence <= 0.55
    if result.milestones:
        assert result.milestones[0].trigger_event is MilestoneTrigger.MANUAL


# --- allocation must not lose money ---------------------------------------

def test_allocation_sums_exactly_to_the_order_value():
    amounts = allocate_amounts(Decimal("6750000"), [Decimal(30), Decimal(60), Decimal(10)])

    assert sum(amounts) == Decimal("6750000.00")
    assert amounts == [Decimal("2025000.00"), Decimal("4050000.00"), Decimal("675000.00")]


def test_the_last_milestone_absorbs_the_rounding_remainder():
    """Three equal thirds of ₹100 cannot be equal. The order must still close."""
    amounts = allocate_amounts(Decimal("100"), [Decimal("33.33")] * 3)

    assert sum(amounts) == Decimal("100.00")
    assert amounts == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]


@pytest.mark.parametrize(
    "value", ["1", "0.01", "999999999.99", "6750000", "51000000.55", "7.77"]
)
def test_allocation_never_loses_a_paisa(value):
    percents = [Decimal("30"), Decimal("60"), Decimal("10")]
    assert sum(allocate_amounts(Decimal(value), percents)) == Decimal(value).quantize(
        Decimal("0.01")
    )


# --- due dates -------------------------------------------------------------

def test_due_dates_are_only_computed_when_derivable():
    po_date = date(2025, 8, 4)
    delivery = date(2025, 9, 30)

    assert due_date_for(
        MilestoneTrigger.ON_PO, po_date=po_date, delivery_due_date=delivery, net_days=None
    ) == po_date
    assert due_date_for(
        MilestoneTrigger.BEFORE_DISPATCH, po_date=po_date, delivery_due_date=delivery,
        net_days=None,
    ) == delivery
    # Commissioning has no knowable date until it happens. None is honest.
    assert due_date_for(
        MilestoneTrigger.AFTER_COMMISSIONING, po_date=po_date, delivery_due_date=delivery,
        net_days=None,
    ) is None


def test_net_days_offsets_the_base_date():
    assert due_date_for(
        MilestoneTrigger.NET_DAYS,
        po_date=date(2025, 8, 4),
        delivery_due_date=date(2025, 9, 30),
        net_days=30,
    ) == date(2025, 10, 30)


def test_missing_delivery_date_yields_no_due_date_rather_than_a_wrong_one():
    assert due_date_for(
        MilestoneTrigger.ON_DELIVERY,
        po_date=date(2025, 8, 4),
        delivery_due_date=None,
        net_days=None,
    ) is None


# --- integration with the order service ------------------------------------

def test_a_usable_parse_creates_milestones(db, gujarat_customer, order_factory):
    from app.services import orders as order_service

    order = order_factory(gujarat_customer)
    created, parsed = order_service.build_milestones(db, order, actor="tester")

    assert len(created) == 3
    assert parsed.is_usable
    assert sum(m.amount for m in created) == order.order_value
    assert sum(m.percent for m in created) == HUNDRED


def test_an_unusable_parse_creates_nothing_and_asks_for_the_builder(
    db, gujarat_customer, order_factory
):
    """We never persist a schedule we are not sure about."""
    from app.services import orders as order_service

    order = order_factory(gujarat_customer, terms="50% advance, 40% before dispatch")
    created, parsed = order_service.build_milestones(db, order, actor="tester")

    assert created == []
    assert not parsed.is_usable
    assert order.milestones == []


def test_the_manual_builder_overrides_the_parser(db, gujarat_customer, order_factory):
    from app.services import orders as order_service

    order = order_factory(gujarat_customer, terms="unparseable nonsense")
    created, parsed = order_service.build_milestones(
        db,
        order,
        actor="tester",
        manual=[
            {"label": "Advance", "trigger_event": "ON_PO", "percent": Decimal("50")},
            {"label": "On delivery", "trigger_event": "ON_DELIVERY", "percent": Decimal("50")},
        ],
    )

    assert parsed is None
    assert len(created) == 2
    assert all(m.source == "manual" for m in created)
    assert sum(m.amount for m in created) == order.order_value


def test_rebuilding_replaces_the_previous_schedule(db, gujarat_customer, order_factory):
    from app.services import orders as order_service

    order = order_factory(gujarat_customer)
    order_service.build_milestones(db, order, actor="tester")
    db.refresh(order)
    assert len(order.milestones) == 3

    order_service.build_milestones(
        db, order, actor="tester",
        manual=[{"label": "All up front", "trigger_event": "ON_PO", "percent": Decimal("100")}],
    )
    db.refresh(order)
    assert len(order.milestones) == 1


def test_the_fixture_po_terms_all_behave_as_expected():
    """The three sample POs exercise the three outcomes we care about."""
    clean = parse("30% advance along with PO, 60% before dispatch, 10% after commissioning")
    awkward = parse(
        "70% against proforma invoice prior to despatch, "
        "balance 30% within 30 days of commissioning."
    )
    bad = parse("50% advance, 40% before dispatch. Retention to be discussed separately.")

    assert clean.is_usable and awkward.is_usable
    assert not bad.is_usable
