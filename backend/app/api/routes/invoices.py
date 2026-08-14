from __future__ import annotations

from typing import Annotated

from datetime import date

from fastapi import Depends, APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from ...models import Invoice, PaymentMilestone
from ...schemas.api import CancelIn, CreateProformaIn, CreateTaxInvoiceIn, InvoiceOut
from ...services import invoices as invoice_service
from ...services import orders as order_service
from ...services import tax_engine
from ...models import User
from ...services.permissions import Permission
from ..deps import Actor, DbSession, requires

router = APIRouter(tags=["invoices"])


@router.get("/invoices", response_model=list[InvoiceOut])
def list_invoices(
    db: DbSession,
    doc_type: str | None = None,
    order_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    stmt = select(Invoice).order_by(Invoice.invoice_date.desc(), Invoice.number.desc())
    if doc_type:
        stmt = stmt.where(Invoice.doc_type == doc_type)
    if order_id:
        stmt = stmt.where(Invoice.sales_order_id == order_id)
    if date_from:
        stmt = stmt.where(Invoice.invoice_date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.invoice_date <= date_to)
    return list(db.execute(stmt).scalars())


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: str, db: DbSession):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    return invoice


@router.post("/orders/{order_id}/proforma", response_model=InvoiceOut, status_code=201)
def create_proforma(order_id: str, payload: CreateProformaIn, db: DbSession, actor: Actor, _perm: Annotated[User, Depends(requires(Permission.DOCUMENT_CREATE))]):
    try:
        order = order_service.get(db, order_id)
    except order_service.OrderNotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    milestone = db.get(PaymentMilestone, payload.milestone_id)
    if milestone is None:
        raise HTTPException(404, "milestone not found")

    try:
        invoice = invoice_service.create_proforma(
            db, order, milestone, actor=actor, invoice_date=payload.invoice_date
        )
        if payload.issue:
            invoice_service.issue(db, invoice, actor=actor)
    except invoice_service.InvoiceError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc

    db.commit()
    db.refresh(invoice)
    return invoice


@router.post("/orders/{order_id}/tax-invoice", response_model=InvoiceOut, status_code=201)
def create_tax_invoice(order_id: str, payload: CreateTaxInvoiceIn, db: DbSession, actor: Actor, _perm: Annotated[User, Depends(requires(Permission.DOCUMENT_CREATE))]):
    try:
        order = order_service.get(db, order_id)
    except order_service.OrderNotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    try:
        invoice = invoice_service.create_tax_invoice(
            db,
            order,
            actor=actor,
            invoice_date=payload.invoice_date,
            is_reverse_charge=payload.is_reverse_charge,
            lut_no=payload.lut_no,
            credit_days=payload.credit_days,
        )
        if payload.issue:
            invoice_service.issue(db, invoice, actor=actor)
    except tax_engine.MissingTaxRate as exc:
        db.rollback()
        # This is a configuration gap, not a bad request — name the HSN.
        raise HTTPException(422, str(exc)) from exc
    except invoice_service.InvoiceError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc

    db.commit()
    db.refresh(invoice)
    return invoice


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceOut)
def issue_invoice(
    invoice_id: str, db: DbSession, actor: Actor, _perm: Annotated[User, Depends(requires(Permission.DOCUMENT_ISSUE))], template_name: str | None = None
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    try:
        invoice_service.issue(db, invoice, actor=actor, template_name=template_name)
    except invoice_service.InvoiceError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    db.refresh(invoice)
    return invoice


@router.get("/invoices/{invoice_id}/download")
def download_invoice(invoice_id: str, db: DbSession, fmt: str = Query("pdf", pattern="^(pdf|docx)$")):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    path = invoice.pdf_path if fmt == "pdf" else invoice.docx_path
    if not path:
        raise HTTPException(409, f"invoice {invoice.number} has no {fmt} rendered; issue it first")
    media = (
        "application/pdf"
        if fmt == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(
        path, media_type=media, filename=f"{invoice.number.replace('/', '-')}.{fmt}"
    )


@router.post("/invoices/{invoice_id}/reprint")
def reprint_invoice(invoice_id: str, db: DbSession, fmt: str = Query("pdf", pattern="^(pdf|docx)$")):
    """Re-render from the frozen context at the original template version."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    try:
        path = invoice_service.reprint(db, invoice, fmt)
    except invoice_service.InvoiceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"path": path, "template": f"{invoice.template_name}-v{invoice.template_version}"}


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(invoice_id: str, payload: CancelIn, db: DbSession, actor: Actor, _perm: Annotated[User, Depends(requires(Permission.DOCUMENT_VOID))]):
    """The number is kept — that is what keeps the series gapless."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    invoice_service.cancel(db, invoice, actor=actor, reason=payload.reason)
    db.commit()
    db.refresh(invoice)
    return invoice
