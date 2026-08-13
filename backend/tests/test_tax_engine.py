"""The tax engine is where a bug costs real money, so this is the densest file.

Every rule in CLAUDE.md → "Domain rules — India / GST" has a test here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import tax_engine
from app.services.tax_engine import MissingTaxRate, TaxableLine, TaxContext

GUJARAT = "24"


def ctx(**kwargs) -> TaxContext:
    base = {
        "document_date": date(2025, 8, 12),
        "home_state_code": GUJARAT,
        "place_of_supply_state_code": GUJARAT,
    }
    base.update(kwargs)
    return TaxContext(**base)


def line(price="1250000", qty="4", hsn="8507", discount="0") -> TaxableLine:
    return TaxableLine(
        description="BESS 100 kWh",
        qty=Decimal(qty),
        unit_price=Decimal(price),
        hsn_code=hsn,
        discount_percent=Decimal(discount),
    )


# --- the split is derived, never chosen -----------------------------------

def test_intra_state_splits_into_cgst_and_sgst(db, tax_rates):
    result = tax_engine.compute(db, [line()], ctx(place_of_supply_state_code="24"))

    assert result.is_intra_state
    assert result.taxable_value == Decimal("5000000.00")
    assert result.cgst_amount == Decimal("450000.00")
    assert result.sgst_amount == Decimal("450000.00")
    assert result.igst_amount == Decimal("0")
    # CGST + SGST must equal what IGST would have been. Not approximately.
    assert result.cgst_amount + result.sgst_amount == Decimal("900000.00")


def test_inter_state_uses_igst_only(db, tax_rates):
    result = tax_engine.compute(db, [line()], ctx(place_of_supply_state_code="27"))

    assert not result.is_intra_state
    assert result.igst_amount == Decimal("900000.00")
    assert result.cgst_amount == Decimal("0")
    assert result.sgst_amount == Decimal("0")


def test_unknown_place_of_supply_is_treated_as_inter_state(db, tax_rates):
    """Guessing intra-state would under-collect. Inter-state is the safe default."""
    result = tax_engine.compute(db, [line()], ctx(place_of_supply_state_code=None))

    assert not result.is_intra_state
    assert result.igst_amount > 0


def test_export_and_sez_are_never_intra_state(db, tax_rates):
    exported = ctx(place_of_supply_state_code="24", is_export=True)
    assert not exported.is_intra_state

    sez = ctx(place_of_supply_state_code="24", is_sez=True)
    assert not sez.is_intra_state


# --- rates come from the table, keyed by document date --------------------

def test_rate_is_resolved_from_the_table_not_hardcoded(db, tax_rates):
    from app.models import TaxRate

    # Close the 18% row and open a 28% row from 1 Jan 2026.
    current = next(r for r in tax_rates if r.hsn_code == "8507")
    current.effective_to = date(2025, 12, 31)
    db.add(
        TaxRate(hsn_code="8507", effective_from=date(2026, 1, 1), rate_percent=Decimal("28"))
    )
    db.commit()

    old = tax_engine.compute(db, [line()], ctx(document_date=date(2025, 8, 12)))
    new = tax_engine.compute(db, [line()], ctx(document_date=date(2026, 2, 1)))

    assert old.lines[0].gst_rate_percent == Decimal("18.00")
    assert new.lines[0].gst_rate_percent == Decimal("28.00")


def test_an_old_invoice_reprints_at_its_own_rate(db, tax_rates):
    """The whole reason rates are date-keyed: history must not move."""
    from app.models import TaxRate

    current = next(r for r in tax_rates if r.hsn_code == "8507")
    current.effective_to = date(2025, 12, 31)
    db.add(
        TaxRate(hsn_code="8507", effective_from=date(2026, 1, 1), rate_percent=Decimal("28"))
    )
    db.commit()

    reprint = tax_engine.compute(db, [line()], ctx(document_date=date(2025, 8, 12)))
    assert reprint.igst_amount == Decimal("0")
    assert reprint.cgst_amount + reprint.sgst_amount == Decimal("900000.00")


def test_missing_rate_raises_rather_than_guessing(db):
    with pytest.raises(MissingTaxRate, match="8507"):
        tax_engine.compute(db, [line()], ctx())


def test_line_without_hsn_is_refused(db, tax_rates):
    with pytest.raises(MissingTaxRate, match="no HSN code"):
        tax_engine.compute(db, [line(hsn=None)], ctx())


# --- zero-rating and reverse charge ---------------------------------------

def test_export_under_lut_is_zero_rated_but_keeps_the_notional_rate(db, tax_rates):
    result = tax_engine.compute(
        db, [line()], ctx(is_export=True, lut_no="LUT/2025/001", place_of_supply_state_code="96")
    )

    assert result.igst_amount == Decimal("0")
    assert result.cgst_amount == Decimal("0")
    # The rate is still recorded — GSTR-1 needs it even when nothing is charged.
    assert result.lines[0].gst_rate_percent == Decimal("18.00")
    assert result.grand_total == Decimal("5000000")


def test_export_without_an_lut_still_attracts_tax(db, tax_rates):
    result = tax_engine.compute(
        db, [line()], ctx(is_export=True, lut_no=None, place_of_supply_state_code="96")
    )
    assert result.igst_amount == Decimal("900000.00")


def test_reverse_charge_moves_the_liability_off_the_invoice(db, tax_rates):
    result = tax_engine.compute(db, [line()], ctx(is_reverse_charge=True))

    assert result.cgst_amount == Decimal("0")
    assert result.sgst_amount == Decimal("0")
    assert result.igst_amount == Decimal("0")
    assert result.lines[0].gst_rate_percent == Decimal("18.00")


# --- arithmetic -----------------------------------------------------------

def test_discount_reduces_the_taxable_value_before_tax(db, tax_rates):
    result = tax_engine.compute(db, [line(discount="10")], ctx())

    assert result.taxable_value == Decimal("4500000.00")
    assert result.cgst_amount == Decimal("405000.00")


def test_round_off_makes_the_parts_sum_to_the_whole(db, tax_rates):
    # 3 x 3333.33 = 9999.99 → 18% → 1799.9982; totals land off a whole rupee.
    result = tax_engine.compute(db, [line(qty="3", price="3333.33")], ctx())

    parts = (
        result.taxable_value
        + result.cgst_amount
        + result.sgst_amount
        + result.igst_amount
        + result.cess_amount
        + result.round_off
    )
    assert parts == result.grand_total
    assert result.grand_total == result.grand_total.to_integral_value()
    assert abs(result.round_off) <= Decimal("0.50")


def test_round_off_can_be_disabled_for_documents_that_do_not_use_it(db, tax_rates):
    result = tax_engine.compute(
        db, [line(qty="3", price="3333.33")], ctx(), apply_round_off=False
    )
    assert result.round_off == Decimal("0")
    assert result.grand_total != result.grand_total.to_integral_value()


def test_multi_line_totals_are_the_sum_of_the_lines(db, tax_rates):
    lines = [
        line(price="1250000", qty="4", hsn="8507"),
        line(price="375000", qty="4", hsn="8504"),
        line(price="250000", qty="1", hsn="998719"),
    ]
    result = tax_engine.compute(db, lines, ctx(place_of_supply_state_code="27"))

    assert result.taxable_value == Decimal("6750000.00")
    assert result.igst_amount == Decimal("1215000.00")
    assert result.grand_total == Decimal("7965000")
    assert sum(line.taxable_value for line in result.lines) == result.taxable_value


def test_money_never_becomes_a_float(db, tax_rates):
    result = tax_engine.compute(db, [line(price="0.1", qty="3")], ctx())
    assert isinstance(result.taxable_value, Decimal)
    # 0.1 * 3 in binary float is 0.30000000000000004.
    assert result.taxable_value == Decimal("0.30")


# --- HSN summary ----------------------------------------------------------

def test_hsn_summary_groups_by_hsn_and_rate(db, tax_rates):
    lines = [
        line(price="1000", qty="1", hsn="8507"),
        line(price="2000", qty="1", hsn="8507"),
        line(price="3000", qty="1", hsn="8504"),
    ]
    result = tax_engine.compute(db, lines, ctx())

    summary = {row.hsn_code: row for row in result.hsn_summary}
    assert len(result.hsn_summary) == 2
    assert summary["8507"].taxable_value == Decimal("3000.00")
    assert summary["8504"].taxable_value == Decimal("3000.00")
    assert sum(r.taxable_value for r in result.hsn_summary) == result.taxable_value
