"""Proforma and tax invoice behaviour, including the reprint guarantee."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import DocType, InvoiceStatus, MilestoneStatus, OrderStatus, Receipt
from app.services import invoices as invoice_service
from app.services import orders as order_service


@pytest.fixture()
def order_with_milestones(db, maharashtra_customer, order_factory):
    order = order_factory(maharashtra_customer)
    order_service.build_milestones(db, order, actor="tester")
    db.commit()
    db.refresh(order)
    return order


# --- proforma --------------------------------------------------------------

def test_a_proforma_carries_no_gst_and_uses_its_own_series(
    db, company, tax_rates, order_with_milestones
):
    """Proformas are not tax documents. This is the test that says so."""
    milestone = order_with_milestones.milestones[0]
    invoice = invoice_service.create_proforma(
        db, order_with_milestones, milestone, actor="tester", invoice_date=date(2025, 8, 12)
    )
    db.commit()

    assert invoice.doc_type == DocType.PROFORMA
    assert invoice.number.startswith("URJ/PI/")
    assert invoice.cgst_amount == Decimal("0")
    assert invoice.sgst_amount == Decimal("0")
    assert invoice.igst_amount == Decimal("0")
    assert invoice.grand_total == milestone.amount


def test_a_proforma_bills_exactly_its_milestone_amount(
    db, company, tax_rates, order_with_milestones
):
    milestone = order_with_milestones.milestones[0]  # 30%
    invoice = invoice_service.create_proforma(db, order_with_milestones, milestone, actor="t")
    db.commit()

    assert milestone.percent == Decimal("30.00")
    assert invoice.grand_total == Decimal("1500000.00")  # 30% of 50,00,000
    assert invoice.grand_total == milestone.amount


def test_raising_a_proforma_marks_the_milestone_invoiced(
    db, company, tax_rates, order_with_milestones
):
    milestone = order_with_milestones.milestones[0]
    invoice = invoice_service.create_proforma(db, order_with_milestones, milestone, actor="t")
    db.commit()

    assert milestone.status == MilestoneStatus.INVOICED
    assert milestone.proforma_invoice_id == invoice.id


def test_the_same_milestone_cannot_be_billed_twice(
    db, company, tax_rates, order_with_milestones
):
    milestone = order_with_milestones.milestones[0]
    invoice_service.create_proforma(db, order_with_milestones, milestone, actor="t")
    db.commit()

    with pytest.raises(invoice_service.InvoiceError, match="already"):
        invoice_service.create_proforma(db, order_with_milestones, milestone, actor="t")


def test_all_proformas_for_an_order_sum_to_the_order_value(
    db, company, tax_rates, order_with_milestones
):
    total = Decimal("0")
    for milestone in list(order_with_milestones.milestones):
        invoice = invoice_service.create_proforma(db, order_with_milestones, milestone, actor="t")
        total += Decimal(invoice.grand_total)
    db.commit()

    assert total == Decimal(order_with_milestones.order_value)


# --- tax invoice -----------------------------------------------------------

def test_a_tax_invoice_derives_igst_for_an_out_of_state_customer(
    db, company, tax_rates, order_with_milestones
):
    invoice = invoice_service.create_tax_invoice(
        db, order_with_milestones, actor="t", invoice_date=date(2025, 8, 12)
    )
    db.commit()

    assert invoice.number.startswith("URJ/25-26/")
    assert invoice.igst_amount == Decimal("900000.00")
    assert invoice.cgst_amount == Decimal("0")
    assert invoice.grand_total == Decimal("5900000")


def test_a_tax_invoice_derives_cgst_sgst_for_a_gujarat_customer(
    db, company, tax_rates, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    invoice = invoice_service.create_tax_invoice(
        db, order, actor="t", invoice_date=date(2025, 8, 12)
    )
    db.commit()

    assert invoice.cgst_amount == Decimal("450000.00")
    assert invoice.sgst_amount == Decimal("450000.00")
    assert invoice.igst_amount == Decimal("0")


def test_proforma_and_tax_invoice_numbers_never_collide(
    db, company, tax_rates, order_with_milestones
):
    proforma = invoice_service.create_proforma(
        db, order_with_milestones, order_with_milestones.milestones[0], actor="t"
    )
    tax = invoice_service.create_tax_invoice(db, order_with_milestones, actor="t")
    db.commit()

    assert proforma.number != tax.number
    assert proforma.series_key != tax.series_key


def test_an_order_with_no_lines_cannot_be_invoiced(db, company, tax_rates, maharashtra_customer):
    from app.models import SalesOrder

    order = SalesOrder(
        customer_id=maharashtra_customer.id,
        customer_po_number="EMPTY",
        customer_po_date=date(2025, 8, 4),
        status="received",
    )
    db.add(order)
    db.commit()

    with pytest.raises(invoice_service.InvoiceError, match="no lines"):
        invoice_service.create_tax_invoice(db, order, actor="t")


# --- issuing, freezing and reprinting -------------------------------------

def test_issuing_renders_both_formats_and_freezes_the_context(
    db, company, tax_rates, order_with_milestones
):
    from pathlib import Path

    invoice = invoice_service.create_tax_invoice(db, order_with_milestones, actor="t")
    invoice_service.issue(db, invoice, actor="t")
    db.commit()

    assert invoice.status == InvoiceStatus.ISSUED
    assert Path(invoice.pdf_path).exists()
    assert Path(invoice.docx_path).exists()
    assert invoice.template_name == "standard"
    assert invoice.template_version == 1
    assert invoice.render_context_json["document"]["number"] == invoice.number


def test_a_reprint_reproduces_the_original_not_todays_data(
    db, company, tax_rates, order_with_milestones
):
    """The frozen context is the whole mechanism. Change the master data
    afterwards and the reprint must not notice."""
    invoice = invoice_service.create_tax_invoice(db, order_with_milestones, actor="t")
    invoice_service.issue(db, invoice, actor="t")
    db.commit()
    original_name = invoice.render_context_json["party"]["name"]

    customer = order_with_milestones.customer
    customer.name = "Renamed After The Fact Ltd"
    db.commit()

    path = invoice_service.reprint(db, invoice, "pdf")
    assert path
    # The frozen context still holds the name as it was at issue time.
    assert invoice.render_context_json["party"]["name"] == original_name
    assert original_name != "Renamed After The Fact Ltd"


def test_reprinting_something_never_issued_is_refused(db, company, tax_rates, order_with_milestones):
    invoice = invoice_service.create_tax_invoice(db, order_with_milestones, actor="t")
    db.commit()

    with pytest.raises(invoice_service.InvoiceError, match="never issued"):
        invoice_service.reprint(db, invoice)


def test_a_proforma_pdf_is_labelled_as_not_a_tax_invoice(
    db, company, tax_rates, order_with_milestones
):
    invoice = invoice_service.create_proforma(
        db, order_with_milestones, order_with_milestones.milestones[0], actor="t"
    )
    invoice_service.issue(db, invoice, actor="t")
    db.commit()

    context = invoice.render_context_json
    assert context["document"]["is_proforma"] is True
    assert context["document"]["title"] == "PROFORMA INVOICE"
    assert "not a tax invoice" in context["document"]["subtitle"].lower()


# --- cancellation ----------------------------------------------------------

def test_a_cancelled_invoice_keeps_its_number(db, company, tax_rates, order_with_milestones):
    """Gapless means cancelled numbers are not recycled."""
    invoice = invoice_service.create_tax_invoice(db, order_with_milestones, actor="t")
    db.commit()
    number = invoice.number

    invoice_service.cancel(db, invoice, actor="t", reason="raised in error")
    db.commit()

    assert invoice.status == InvoiceStatus.CANCELLED
    assert invoice.number == number
    assert invoice.cancelled_reason == "raised in error"

    # And the next invoice takes the following number, not the cancelled one.
    nxt = invoice_service.create_tax_invoice(db, order_with_milestones, actor="t")
    db.commit()
    assert nxt.number != number
    assert int(nxt.number.split("/")[-1]) == int(number.split("/")[-1]) + 1


def test_cancelling_a_proforma_frees_its_milestone(db, company, tax_rates, order_with_milestones):
    milestone = order_with_milestones.milestones[0]
    invoice = invoice_service.create_proforma(db, order_with_milestones, milestone, actor="t")
    db.commit()
    assert milestone.status == MilestoneStatus.INVOICED

    invoice_service.cancel(db, invoice, actor="t", reason="wrong milestone")
    db.commit()

    assert milestone.status == MilestoneStatus.PENDING
    assert milestone.proforma_invoice_id is None
    # And it can be billed again.
    again = invoice_service.create_proforma(db, order_with_milestones, milestone, actor="t")
    db.commit()
    assert again.id != invoice.id


def test_a_cancelled_invoice_cannot_be_issued(db, company, tax_rates, order_with_milestones):
    invoice = invoice_service.create_tax_invoice(db, order_with_milestones, actor="t")
    invoice_service.cancel(db, invoice, actor="t", reason="oops")
    db.commit()

    with pytest.raises(invoice_service.InvoiceError, match="cancelled"):
        invoice_service.issue(db, invoice, actor="t")


# --- receipts netting ------------------------------------------------------

def test_advances_already_received_appear_on_the_tax_invoice(
    db, company, tax_rates, order_with_milestones
):
    db.add(
        Receipt(
            customer_id=order_with_milestones.customer_id,
            sales_order_id=order_with_milestones.id,
            amount=Decimal("1500000"),
            received_on=date(2025, 8, 5),
            mode="NEFT",
        )
    )
    db.commit()

    invoice = invoice_service.create_tax_invoice(
        db, order_with_milestones, actor="t", invoice_date=date(2025, 8, 12)
    )
    invoice_service.issue(db, invoice, actor="t")
    db.commit()

    payment = invoice.render_context_json["payment"]
    assert Decimal(payment["already_received"]["raw"]) == Decimal("1500000.00")
    assert Decimal(payment["balance_due"]["raw"]) == Decimal(invoice.grand_total) - Decimal(
        "1500000.00"
    )


def test_receipts_after_the_invoice_date_are_not_netted_off(
    db, company, tax_rates, order_with_milestones
):
    db.add(
        Receipt(
            customer_id=order_with_milestones.customer_id,
            sales_order_id=order_with_milestones.id,
            amount=Decimal("1000000"),
            received_on=date(2025, 12, 1),
            mode="NEFT",
        )
    )
    db.commit()

    invoice = invoice_service.create_tax_invoice(
        db, order_with_milestones, actor="t", invoice_date=date(2025, 8, 12)
    )
    invoice_service.issue(db, invoice, actor="t")
    db.commit()

    assert Decimal(invoice.render_context_json["payment"]["already_received"]["raw"]) == Decimal("0")


# --- order state machine ---------------------------------------------------

def test_the_legal_path_through_the_state_machine(db, gujarat_customer, order_factory):
    order = order_factory(gujarat_customer)
    for target in ("in_production", "dispatched", "invoiced", "partially_paid", "paid"):
        order_service.transition(db, order, target, actor="t")
    db.commit()
    assert order.status == OrderStatus.PAID


def test_illegal_transitions_are_refused_with_a_useful_message(
    db, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    with pytest.raises(order_service.IllegalTransition, match="allowed"):
        order_service.transition(db, order, "paid", actor="t")


def test_a_paid_order_is_terminal(db, gujarat_customer, order_factory):
    order = order_factory(gujarat_customer)
    for target in ("in_production", "dispatched", "invoiced", "paid"):
        order_service.transition(db, order, target, actor="t")
    db.commit()

    with pytest.raises(order_service.IllegalTransition):
        order_service.transition(db, order, "cancelled", actor="t")


def test_an_order_can_be_cancelled_from_any_live_state(db, gujarat_customer, order_factory):
    for state in ("received", "in_production", "dispatched"):
        order = order_factory(gujarat_customer, po_number=f"PO-{state}")
        while str(order.status) != state:
            nxt = {"received": "in_production", "in_production": "dispatched"}[str(order.status)]
            order_service.transition(db, order, nxt, actor="t")
        order_service.transition(db, order, "cancelled", actor="t")
        db.commit()
        assert order.status == OrderStatus.CANCELLED
