"""Proforma and tax invoice creation, issuing, rendering and cancellation.

Numbering happens inside the same transaction that inserts the invoice, which
is what makes the series gapless. Rendering happens at *issue* time and the
context is frozen onto the row, which is what makes a reprint reproduce the
original rather than today's data.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AuditAction,
    Company,
    Customer,
    DocType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    MilestoneStatus,
    PaymentMilestone,
    SalesOrder,
    SeriesKey,
)
from ..rendering import context as ctx
from ..rendering import render
from . import audit, orders, tax_engine
from .money import amount_in_words, q2
from .numbering import NumberingService

_SERIES_FOR_DOC = {
    DocType.PROFORMA: SeriesKey.PROFORMA,
    DocType.TAX_INVOICE: SeriesKey.TAX_INVOICE,
    DocType.CREDIT_NOTE: SeriesKey.CREDIT_NOTE,
}


class InvoiceError(RuntimeError):
    pass


def _company(db: Session) -> Company | None:
    return db.execute(select(Company)).scalars().first()


def _tax_context(
    db: Session, customer: Customer, on: date, *, is_reverse_charge: bool = False
) -> tax_engine.TaxContext:
    company = _company(db)
    return tax_engine.TaxContext(
        document_date=on,
        home_state_code=(company.state_code if company else get_settings().home_state_code),
        place_of_supply_state_code=customer.effective_pos_state_code,
        is_export=customer.is_export,
        is_sez=customer.is_sez,
        lut_no=None,
        is_reverse_charge=is_reverse_charge,
    )


def _order_lines_as_taxable(order: SalesOrder) -> list[tax_engine.TaxableLine]:
    return [
        tax_engine.TaxableLine(
            description=line.description,
            qty=Decimal(line.qty),
            unit_price=Decimal(line.unit_price),
            hsn_code=line.hsn_code,
            discount_percent=Decimal(line.discount_percent),
            uom=line.uom,
            item_id=line.item_id,
        )
        for line in order.lines
    ]


def _persist_lines(db: Session, invoice: Invoice, computation: tax_engine.TaxComputation) -> None:
    for line in computation.lines:
        db.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_no=line.line_no,
                item_id=line.item_id,
                description=line.description,
                hsn_code=line.hsn_code,
                qty=line.qty,
                uom=line.uom,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                taxable_value=line.taxable_value,
                gst_rate_percent=line.gst_rate_percent,
                cgst_amount=line.cgst_amount,
                sgst_amount=line.sgst_amount,
                igst_amount=line.igst_amount,
                cess_amount=line.cess_amount,
                line_total=line.line_total,
            )
        )
    invoice.taxable_value = computation.taxable_value
    invoice.cgst_amount = computation.cgst_amount
    invoice.sgst_amount = computation.sgst_amount
    invoice.igst_amount = computation.igst_amount
    invoice.cess_amount = computation.cess_amount
    invoice.round_off = computation.round_off
    invoice.grand_total = computation.grand_total
    invoice.amount_in_words = amount_in_words(computation.grand_total)


# --- proforma --------------------------------------------------------------

def create_proforma(
    db: Session,
    order: SalesOrder,
    milestone: PaymentMilestone,
    *,
    actor: str,
    invoice_date: date | None = None,
) -> Invoice:
    """A proforma bills one milestone: a percentage of the order, no GST.

    Proformas are *not* tax documents (CLAUDE.md → Domain rules), so no tax is
    computed and they use their own series. They are excluded from GST reports
    and from the Tally export by ``doc_type``.
    """
    if milestone.sales_order_id != order.id:
        raise InvoiceError("milestone does not belong to this order")
    if milestone.status != MilestoneStatus.PENDING:
        raise InvoiceError(
            f"milestone {milestone.seq} is already {milestone.status}; "
            "cancel the existing proforma first"
        )

    on = invoice_date or date.today()
    number, fy = NumberingService(db).allocate(SeriesKey.PROFORMA, on)

    invoice = Invoice(
        number=number,
        series_key=SeriesKey.PROFORMA,
        financial_year=fy,
        doc_type=DocType.PROFORMA,
        invoice_date=on,
        due_date=milestone.due_date,
        customer_id=order.customer_id,
        sales_order_id=order.id,
        payment_milestone_id=milestone.id,
        place_of_supply_state_code=order.customer.effective_pos_state_code,
        taxable_value=Decimal(milestone.amount),
        grand_total=Decimal(milestone.amount),
        amount_in_words=amount_in_words(Decimal(milestone.amount)),
        status=InvoiceStatus.DRAFT,
        notes=order.notes,
    )
    db.add(invoice)
    db.flush()

    db.add(
        InvoiceLine(
            invoice_id=invoice.id,
            line_no=1,
            description=(
                f"{milestone.label} — {q2(milestone.percent)}% of order value "
                f"against PO {order.customer_po_number}"
            ),
            qty=Decimal("1"),
            uom="LOT",
            unit_price=Decimal(milestone.amount),
            taxable_value=Decimal(milestone.amount),
            line_total=Decimal(milestone.amount),
        )
    )

    milestone.status = MilestoneStatus.INVOICED
    milestone.proforma_invoice_id = invoice.id
    db.flush()

    audit.record(
        db,
        entity_type="invoices",
        entity_id=invoice.id,
        action=AuditAction.CREATE,
        actor=actor,
        after={"number": number, "doc_type": "PROFORMA", "amount": str(invoice.grand_total)},
        context={"sales_order_id": order.id, "milestone_seq": milestone.seq},
    )
    return invoice


# --- tax invoice -----------------------------------------------------------

def create_tax_invoice(
    db: Session,
    order: SalesOrder,
    *,
    actor: str,
    invoice_date: date | None = None,
    is_reverse_charge: bool = False,
    lut_no: str | None = None,
    credit_days: int | None = None,
) -> Invoice:
    """The real thing: HSN-driven rates, derived tax split, round-off."""
    if not order.lines:
        raise InvoiceError("cannot raise a tax invoice for an order with no lines")

    on = invoice_date or date.today()
    customer = order.customer
    tax_ctx = _tax_context(db, customer, on, is_reverse_charge=is_reverse_charge)
    if lut_no:
        tax_ctx = tax_engine.TaxContext(
            document_date=tax_ctx.document_date,
            home_state_code=tax_ctx.home_state_code,
            place_of_supply_state_code=tax_ctx.place_of_supply_state_code,
            is_export=tax_ctx.is_export,
            is_sez=tax_ctx.is_sez,
            lut_no=lut_no,
            is_reverse_charge=tax_ctx.is_reverse_charge,
        )

    computation = tax_engine.compute(db, _order_lines_as_taxable(order), tax_ctx)

    number, fy = NumberingService(db).allocate(SeriesKey.TAX_INVOICE, on)
    invoice = Invoice(
        number=number,
        series_key=SeriesKey.TAX_INVOICE,
        financial_year=fy,
        doc_type=DocType.TAX_INVOICE,
        invoice_date=on,
        due_date=(on + timedelta(days=credit_days)) if credit_days else None,
        customer_id=customer.id,
        sales_order_id=order.id,
        place_of_supply_state_code=tax_ctx.place_of_supply_state_code,
        is_reverse_charge=is_reverse_charge,
        is_export=tax_ctx.is_export or tax_ctx.is_sez,
        lut_no=lut_no,
        status=InvoiceStatus.DRAFT,
    )
    db.add(invoice)
    db.flush()
    _persist_lines(db, invoice, computation)
    db.flush()

    audit.record(
        db,
        entity_type="invoices",
        entity_id=invoice.id,
        action=AuditAction.CREATE,
        actor=actor,
        after={
            "number": number,
            "doc_type": "TAX_INVOICE",
            "taxable_value": str(invoice.taxable_value),
            "grand_total": str(invoice.grand_total),
            "split": "CGST+SGST" if computation.is_intra_state else "IGST",
        },
        context={"sales_order_id": order.id},
    )
    return invoice


# --- issue / render / cancel ----------------------------------------------

def issue(
    db: Session,
    invoice: Invoice,
    *,
    actor: str,
    template_name: str | None = None,
    formats: tuple[str, ...] = ("pdf", "docx"),
) -> Invoice:
    """Freeze the render context, produce the files, mark it issued.

    After this point the numbers on the document are what will reprint,
    regardless of later master-data edits.
    """
    if invoice.status == InvoiceStatus.CANCELLED:
        raise InvoiceError(f"invoice {invoice.number} is cancelled")

    order = db.get(SalesOrder, invoice.sales_order_id) if invoice.sales_order_id else None
    milestone = (
        db.get(PaymentMilestone, invoice.payment_milestone_id)
        if invoice.payment_milestone_id
        else None
    )
    already_received = (
        orders.received_total(db, order.id, upto=invoice.invoice_date) if order else Decimal("0")
    )
    hsn_summary = tax_engine.build_hsn_summary(
        [
            tax_engine.ComputedLine(
                line_no=line.line_no,
                description=line.description,
                hsn_code=line.hsn_code,
                qty=Decimal(line.qty),
                uom=line.uom,
                unit_price=Decimal(line.unit_price),
                discount_percent=Decimal(line.discount_percent),
                taxable_value=Decimal(line.taxable_value),
                gst_rate_percent=Decimal(line.gst_rate_percent),
                cgst_amount=Decimal(line.cgst_amount),
                sgst_amount=Decimal(line.sgst_amount),
                igst_amount=Decimal(line.igst_amount),
                cess_amount=Decimal(line.cess_amount),
                line_total=Decimal(line.line_total),
            )
            for line in invoice.lines
            if line.hsn_code
        ]
    )

    context = ctx.invoice_context(
        invoice,
        _company(db),
        db.get(Customer, invoice.customer_id),
        sales_order=order,
        hsn_summary=hsn_summary,
        already_received=already_received,
        milestone=milestone,
        schedule=list(order.milestones) if order else None,
        template_name=template_name or "standard",
        template_version=0,
    )

    rendered: dict[str, Any] = {}
    for fmt in formats:
        target = render.output_path("invoices", invoice.number, fmt)
        result = (
            render.render_pdf("invoice", context, target, name=template_name)
            if fmt == "pdf"
            else render.render_docx("invoice", context, target, name=template_name)
        )
        rendered[fmt] = result
        context["meta"]["template_name"] = result.template_name
        context["meta"]["template_version"] = result.template_version

    if "pdf" in rendered:
        invoice.pdf_path = str(rendered["pdf"].path)
    if "docx" in rendered:
        invoice.docx_path = str(rendered["docx"].path)

    first = next(iter(rendered.values()))
    invoice.template_name = first.template_name
    invoice.template_version = first.template_version
    invoice.render_context_json = context
    invoice.status = InvoiceStatus.ISSUED
    db.flush()

    audit.record(
        db,
        entity_type="invoices",
        entity_id=invoice.id,
        action=AuditAction.ISSUE,
        actor=actor,
        after={
            "number": invoice.number,
            "template": f"{invoice.template_name}-v{invoice.template_version}",
            "formats": list(rendered),
        },
    )
    return invoice


def reprint(db: Session, invoice: Invoice, fmt: str = "pdf") -> str:
    """Re-render from the *frozen* context, at the version originally used."""
    if not invoice.render_context_json:
        raise InvoiceError(
            f"invoice {invoice.number} has no frozen render context; it was never issued"
        )
    target = render.output_path("invoices", f"{invoice.number}-reprint", fmt)
    renderer = render.render_pdf if fmt == "pdf" else render.render_docx
    result = renderer(
        "invoice",
        invoice.render_context_json,
        target,
        name=invoice.template_name,
        version=invoice.template_version,
    )
    return str(result.path)


def cancel(db: Session, invoice: Invoice, *, actor: str, reason: str) -> Invoice:
    """Cancelled invoices keep their number — that is what keeps the series
    gapless and the audit trail honest."""
    if invoice.status == InvoiceStatus.CANCELLED:
        return invoice
    before = {"status": str(invoice.status)}
    invoice.status = InvoiceStatus.CANCELLED
    invoice.cancelled_reason = reason

    if invoice.payment_milestone_id:
        milestone = db.get(PaymentMilestone, invoice.payment_milestone_id)
        if milestone and milestone.proforma_invoice_id == invoice.id:
            milestone.status = MilestoneStatus.PENDING
            milestone.proforma_invoice_id = None

    db.flush()
    audit.record(
        db,
        entity_type="invoices",
        entity_id=invoice.id,
        action=AuditAction.CANCEL,
        actor=actor,
        before=before,
        after={"status": "cancelled", "number": invoice.number},
        context={"reason": reason},
    )
    return invoice
