"""Purchase side: supplier offer → negotiated rates → Word purchase order.

Tax on a PO is what we *expect* to be charged, so it runs through the same
``tax_engine`` as a sales invoice — same rules, same rate table, no second
implementation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AuditAction,
    Company,
    OfferStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SeriesKey,
    Supplier,
    SupplierOffer,
    SupplierOfferLine,
)
from ..rendering import context as ctx
from ..rendering import render
from . import audit, tax_engine, versioning
from .gstin import state_code_of, state_name
from .money import amount_in_words
from .numbering import NumberingService


class PurchaseError(RuntimeError):
    pass


def find_or_create_supplier(
    db: Session, name: str, gstin: str | None, address: str | None, *, actor: str
) -> Supplier:
    if gstin:
        existing = db.execute(select(Supplier).where(Supplier.gstin == gstin)).scalars().first()
        if existing:
            return existing
    existing = db.execute(select(Supplier).where(Supplier.name == name)).scalars().first()
    if existing:
        return existing

    code = state_code_of(gstin)
    supplier = Supplier(
        name=name,
        gstin=gstin,
        state_code=code,
        state_name=state_name(code),
        billing_address=address,
    )
    db.add(supplier)
    db.flush()
    audit.record(
        db,
        entity_type="suppliers",
        entity_id=supplier.id,
        action=AuditAction.CREATE,
        actor=actor,
        after={"name": name, "gstin": gstin},
        context={"source": "offer_extraction"},
    )
    return supplier


def create_offer_from_extraction(
    db: Session,
    payload: dict[str, Any],
    *,
    actor: str,
    source_document_id: str | None = None,
    extraction_id: str | None = None,
) -> SupplierOffer:
    supplier = find_or_create_supplier(
        db,
        name=payload["supplier_name"],
        gstin=payload.get("supplier_gstin"),
        address=payload.get("supplier_address"),
        actor=actor,
    )

    offer = SupplierOffer(
        supplier_id=supplier.id,
        offer_ref=payload.get("offer_ref"),
        offer_date=payload.get("offer_date"),
        validity_date=payload.get("validity_date"),
        currency=payload.get("currency") or "INR",
        lead_time_days=payload.get("lead_time_days"),
        payment_terms_text=payload.get("payment_terms_text"),
        freight_terms=payload.get("freight_terms"),
        warranty_text=payload.get("warranty_text"),
        status=OfferStatus.DRAFT,
        source_document_id=source_document_id,
        extraction_id=extraction_id,
        notes=payload.get("notes"),
    )
    db.add(offer)
    db.flush()

    for index, raw in enumerate(payload.get("lines") or [], start=1):
        db.add(
            SupplierOfferLine(
                supplier_offer_id=offer.id,
                line_no=index,
                item_id=raw.get("item_id"),
                description=raw.get("description", ""),
                hsn_code=raw.get("hsn_code"),
                qty=Decimal(str(raw.get("qty", 1))),
                uom=raw.get("uom") or "NOS",
                quoted_unit_price=Decimal(str(raw.get("unit_price", 0))),
                discount_percent=Decimal(str(raw.get("discount_percent", 0) or 0)),
            )
        )
    db.flush()

    audit.record(
        db,
        entity_type="supplier_offers",
        entity_id=offer.id,
        action=AuditAction.CREATE,
        actor=actor,
        after={"offer_ref": offer.offer_ref, "supplier": supplier.name},
        context={"extraction_id": extraction_id},
    )
    return offer


def negotiate(
    db: Session, offer: SupplierOffer, rates: dict[str, Decimal], *, actor: str
) -> SupplierOffer:
    """Set the final rate per line. Keyed by line id, so a re-ordered UI
    cannot mis-assign a price to the wrong item."""
    before = {
        line.id: str(line.negotiated_unit_price or line.quoted_unit_price) for line in offer.lines
    }
    by_id = {line.id: line for line in offer.lines}
    for line_id, price in rates.items():
        line = by_id.get(line_id)
        if line is None:
            raise PurchaseError(f"line {line_id} is not part of offer {offer.id}")
        line.negotiated_unit_price = Decimal(str(price))
    db.flush()
    audit.record(
        db,
        entity_type="supplier_offers",
        entity_id=offer.id,
        action=AuditAction.UPDATE,
        actor=actor,
        before=before,
        after={line_id: str(price) for line_id, price in rates.items()},
        context={"field": "negotiated_unit_price"},
    )
    return offer


def create_purchase_order(
    db: Session,
    offer: SupplierOffer,
    *,
    actor: str,
    po_date: date | None = None,
    delivery_due_date: date | None = None,
    delivery_location: str | None = None,
    is_reverse_charge: bool = False,
    notes: str | None = None,
) -> PurchaseOrder:
    if not offer.lines:
        raise PurchaseError("cannot raise a purchase order from an offer with no lines")

    on = po_date or date.today()
    supplier = offer.supplier
    company = db.execute(select(Company)).scalars().first()

    tax_ctx = tax_engine.TaxContext(
        document_date=on,
        home_state_code=(company.state_code if company else get_settings().home_state_code),
        # For a purchase, place of supply is where *we* are — the supplier's
        # state versus ours is what decides whether they charge us IGST.
        place_of_supply_state_code=supplier.state_code,
        is_reverse_charge=is_reverse_charge,
    )
    computation = tax_engine.compute(
        db,
        [
            tax_engine.TaxableLine(
                description=line.description,
                qty=Decimal(line.qty),
                unit_price=line.effective_unit_price,
                hsn_code=line.hsn_code,
                discount_percent=Decimal(line.discount_percent),
                uom=line.uom,
                item_id=line.item_id,
            )
            for line in offer.lines
        ],
        tax_ctx,
    )

    number, fy = NumberingService(db).allocate(SeriesKey.PURCHASE_ORDER, on)
    po = PurchaseOrder(
        number=number,
        financial_year=fy,
        supplier_id=supplier.id,
        supplier_offer_id=offer.id,
        po_date=on,
        delivery_due_date=delivery_due_date,
        delivery_location=delivery_location,
        payment_terms_text=offer.payment_terms_text,
        freight_terms=offer.freight_terms,
        warranty_text=offer.warranty_text,
        place_of_supply_state_code=supplier.state_code,
        is_reverse_charge=is_reverse_charge,
        taxable_value=computation.taxable_value,
        cgst_amount=computation.cgst_amount,
        sgst_amount=computation.sgst_amount,
        igst_amount=computation.igst_amount,
        cess_amount=computation.cess_amount,
        round_off=computation.round_off,
        grand_total=computation.grand_total,
        amount_in_words=amount_in_words(computation.grand_total),
        status=PurchaseOrderStatus.DRAFT,
        notes=notes,
    )
    db.add(po)
    db.flush()

    for line in computation.lines:
        db.add(
            PurchaseOrderLine(
                purchase_order_id=po.id,
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

    offer.status = OfferStatus.ORDERED
    db.flush()

    audit.record(
        db,
        entity_type="purchase_orders",
        entity_id=po.id,
        action=AuditAction.CREATE,
        actor=actor,
        after={"number": number, "supplier": supplier.name, "grand_total": str(po.grand_total)},
        context={"supplier_offer_id": offer.id},
    )
    return po


def issue_purchase_order(
    db: Session,
    po: PurchaseOrder,
    *,
    actor: str,
    template_name: str | None = None,
    formats: tuple[str, ...] = ("docx", "pdf"),
) -> PurchaseOrder:
    """Word first — the PO is the document a supplier expects to be able to
    edit and sign back."""
    if po.status == PurchaseOrderStatus.CANCELLED:
        raise PurchaseError(f"purchase order {po.number} is cancelled")

    company = db.execute(select(Company)).scalars().first()
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
            for line in po.lines
            if line.hsn_code
        ]
    )
    context = ctx.purchase_order_context(
        po,
        company,
        po.supplier,
        hsn_summary=hsn_summary,
        template_name=template_name or "standard",
        template_version=0,
    )

    rendered: dict[str, Any] = {}
    for fmt in formats:
        target = render.output_path("purchase_orders", po.number, fmt)
        result = (
            render.render_docx("purchase_order", context, target, name=template_name)
            if fmt == "docx"
            else render.render_pdf("purchase_order", context, target, name=template_name)
        )
        rendered[fmt] = result
        context["meta"]["template_name"] = result.template_name
        context["meta"]["template_version"] = result.template_version

    if "docx" in rendered:
        po.docx_path = str(rendered["docx"].path)
    if "pdf" in rendered:
        po.pdf_path = str(rendered["pdf"].path)

    first = next(iter(rendered.values()))
    po.template_name = first.template_name
    po.template_version = first.template_version
    po.render_context_json = context
    po.status = PurchaseOrderStatus.ISSUED
    db.flush()

    if versioning.current_version(db, "purchase_orders", po.id) is None:
        versioning.record_initial_version(db, "purchase_orders", po.id, actor=actor)

    audit.record(
        db,
        entity_type="purchase_orders",
        entity_id=po.id,
        action=AuditAction.ISSUE,
        actor=actor,
        after={"number": po.number, "formats": list(rendered)},
    )
    return po


def cancel_purchase_order(
    db: Session, po: PurchaseOrder, *, actor: str, reason: str
) -> PurchaseOrder:
    if po.status == PurchaseOrderStatus.CANCELLED:
        return po
    before = {"status": str(po.status)}
    po.status = PurchaseOrderStatus.CANCELLED
    po.cancelled_reason = reason
    db.flush()
    audit.record(
        db,
        entity_type="purchase_orders",
        entity_id=po.id,
        action=AuditAction.CANCEL,
        actor=actor,
        before=before,
        after={"status": "cancelled", "number": po.number},
        context={"reason": reason},
    )
    return po
