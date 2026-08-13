from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from ...models import PurchaseOrder, SupplierOffer
from ...schemas.api import (
    CancelIn,
    CreatePOIn,
    NegotiateIn,
    OfferOut,
    PurchaseOrderOut,
)
from ...services import purchase as purchase_service
from ...services import tax_engine
from ..deps import Actor, DbSession

router = APIRouter(tags=["purchase"])


@router.get("/offers", response_model=list[OfferOut])
def list_offers(db: DbSession, status: str | None = None):
    stmt = select(SupplierOffer).order_by(SupplierOffer.created_at.desc())
    if status:
        stmt = stmt.where(SupplierOffer.status == status)
    return list(db.execute(stmt).scalars())


@router.get("/offers/{offer_id}", response_model=OfferOut)
def get_offer(offer_id: str, db: DbSession):
    offer = db.get(SupplierOffer, offer_id)
    if offer is None:
        raise HTTPException(404, "offer not found")
    return offer


@router.post("/offers/{offer_id}/negotiate", response_model=OfferOut)
def negotiate(offer_id: str, payload: NegotiateIn, db: DbSession, actor: Actor):
    offer = db.get(SupplierOffer, offer_id)
    if offer is None:
        raise HTTPException(404, "offer not found")
    try:
        purchase_service.negotiate(db, offer, payload.rates, actor=actor)
    except purchase_service.PurchaseError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(offer)
    return offer


@router.post("/offers/{offer_id}/purchase-order", response_model=PurchaseOrderOut, status_code=201)
def create_purchase_order(offer_id: str, payload: CreatePOIn, db: DbSession, actor: Actor):
    offer = db.get(SupplierOffer, offer_id)
    if offer is None:
        raise HTTPException(404, "offer not found")
    try:
        po = purchase_service.create_purchase_order(
            db,
            offer,
            actor=actor,
            po_date=payload.po_date,
            delivery_due_date=payload.delivery_due_date,
            delivery_location=payload.delivery_location,
            is_reverse_charge=payload.is_reverse_charge,
            notes=payload.notes,
        )
        if payload.issue:
            purchase_service.issue_purchase_order(db, po, actor=actor)
    except tax_engine.MissingTaxRate as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except purchase_service.PurchaseError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    db.refresh(po)
    return po


@router.get("/purchase-orders", response_model=list[PurchaseOrderOut])
def list_purchase_orders(db: DbSession, status: str | None = None):
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.po_date.desc(), PurchaseOrder.number.desc())
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    return list(db.execute(stmt).scalars())


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
def get_purchase_order(po_id: str, db: DbSession):
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, "purchase order not found")
    return po


@router.post("/purchase-orders/{po_id}/issue", response_model=PurchaseOrderOut)
def issue_purchase_order(
    po_id: str, db: DbSession, actor: Actor, template_name: str | None = None
):
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, "purchase order not found")
    try:
        purchase_service.issue_purchase_order(db, po, actor=actor, template_name=template_name)
    except purchase_service.PurchaseError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    db.refresh(po)
    return po


@router.get("/purchase-orders/{po_id}/download")
def download_purchase_order(
    po_id: str, db: DbSession, fmt: str = Query("docx", pattern="^(pdf|docx)$")
):
    """Word by default — a PO is a document the supplier expects to edit."""
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, "purchase order not found")
    path = po.docx_path if fmt == "docx" else po.pdf_path
    if not path:
        raise HTTPException(409, f"purchase order {po.number} has no {fmt}; issue it first")
    media = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if fmt == "docx"
        else "application/pdf"
    )
    return FileResponse(path, media_type=media, filename=f"{po.number.replace('/', '-')}.{fmt}")


@router.post("/purchase-orders/{po_id}/cancel", response_model=PurchaseOrderOut)
def cancel_purchase_order(po_id: str, payload: CancelIn, db: DbSession, actor: Actor):
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, "purchase order not found")
    purchase_service.cancel_purchase_order(db, po, actor=actor, reason=payload.reason)
    db.commit()
    db.refresh(po)
    return po
