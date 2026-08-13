from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ...models import MilestoneStatus, OrderStatus, Receipt, SalesOrder
from ...schemas.api import (
    BuildMilestonesIn,
    BuildMilestonesOut,
    MilestoneOut,
    OrderOut,
    OrderSummary,
    ParsedTermsOut,
    ReceiptIn,
    TransitionIn,
)
from ...services import audit, orders as order_service, payment_terms
from ...services.money import q2
from ..deps import Actor, DbSession

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderSummary])
def list_orders(db: DbSession, status: str | None = None, today: date | None = None):
    """The dashboard feed: countdown, billed vs unbilled, next milestone."""
    stmt = select(SalesOrder).order_by(
        SalesOrder.delivery_due_date.is_(None), SalesOrder.delivery_due_date
    )
    if status:
        stmt = stmt.where(SalesOrder.status == status)

    on = today or date.today()
    summaries: list[OrderSummary] = []
    for order in db.execute(stmt).scalars():
        billed = q2(
            sum(
                (Decimal(m.amount) for m in order.milestones
                 if m.status != MilestoneStatus.PENDING),
                Decimal("0"),
            )
        )
        pending = [m for m in order.milestones if m.status == MilestoneStatus.PENDING]
        summaries.append(
            OrderSummary(
                id=order.id,
                customer_po_number=order.customer_po_number,
                customer_name=order.customer.name if order.customer else "",
                status=str(order.status),
                order_value=Decimal(order.order_value),
                delivery_due_date=order.delivery_due_date,
                days_to_delivery=order_service.days_to_delivery(order, on),
                billed=billed,
                unbilled=q2(Decimal(order.order_value) - billed),
                received=order_service.received_total(db, order.id),
                next_milestone=pending[0].label if pending else None,
            )
        )
    return summaries


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: str, db: DbSession):
    try:
        return order_service.get(db, order_id)
    except order_service.OrderNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{order_id}/transition", response_model=OrderOut)
def transition_order(order_id: str, payload: TransitionIn, db: DbSession, actor: Actor):
    try:
        order = order_service.get(db, order_id)
        order_service.transition(
            db, order, OrderStatus(payload.target), actor=actor, reason=payload.reason
        )
    except order_service.OrderNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except order_service.IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, f"unknown status {payload.target!r}") from exc
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/milestones", response_model=BuildMilestonesOut)
def build_milestones(
    order_id: str, payload: BuildMilestonesIn, db: DbSession, actor: Actor
):
    """Parse the terms text, or accept a manually built schedule.

    When the parse is not usable, nothing is persisted and
    ``needs_manual_schedule`` tells the UI to open the builder. We never save a
    schedule we are unsure about.
    """
    try:
        order = order_service.get(db, order_id)
    except order_service.OrderNotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    manual = [m.model_dump() for m in payload.manual] if payload.manual else None
    if manual:
        total = q2(sum((Decimal(m["percent"]) for m in manual), Decimal("0")))
        if total != Decimal("100"):
            raise HTTPException(
                422, f"manual milestones must sum to 100%, got {total}%"
            )

    created, parsed = order_service.build_milestones(db, order, actor=actor, manual=manual)
    db.commit()

    parsed_out = None
    if parsed is not None:
        parsed_out = ParsedTermsOut(
            confidence=parsed.confidence,
            total_percent=parsed.total_percent,
            is_usable=parsed.is_usable,
            warnings=parsed.warnings,
            milestones=[
                {
                    "seq": m.seq,
                    "label": m.label,
                    "trigger_event": str(m.trigger_event),
                    "percent": str(m.percent),
                    "net_days": m.net_days,
                    "source_clause": m.source_clause,
                }
                for m in parsed.milestones
            ],
        )

    return BuildMilestonesOut(
        created=[MilestoneOut.model_validate(m) for m in created],
        parsed=parsed_out,
        needs_manual_schedule=not created,
    )


@router.post("/preview-terms", response_model=ParsedTermsOut)
def preview_terms(text: str):
    """Dry-run the parser so the UI can show the split before committing."""
    parsed = payment_terms.parse(text)
    return ParsedTermsOut(
        confidence=parsed.confidence,
        total_percent=parsed.total_percent,
        is_usable=parsed.is_usable,
        warnings=parsed.warnings,
        milestones=[
            {
                "seq": m.seq,
                "label": m.label,
                "trigger_event": str(m.trigger_event),
                "percent": str(m.percent),
                "net_days": m.net_days,
                "source_clause": m.source_clause,
            }
            for m in parsed.milestones
        ],
    )


@router.post("/{order_id}/receipts", status_code=201)
def record_receipt(order_id: str, payload: ReceiptIn, db: DbSession, actor: Actor):
    try:
        order = order_service.get(db, order_id)
    except order_service.OrderNotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    receipt = Receipt(
        customer_id=order.customer_id,
        sales_order_id=order.id,
        invoice_id=payload.invoice_id,
        amount=payload.amount,
        received_on=payload.received_on,
        mode=payload.mode,
        reference=payload.reference,
        notes=payload.notes,
    )
    db.add(receipt)
    db.flush()

    # A receipt against a proforma settles that milestone.
    if payload.invoice_id:
        for milestone in order.milestones:
            if milestone.proforma_invoice_id == payload.invoice_id:
                milestone.status = MilestoneStatus.RECEIVED

    audit.record(
        db,
        entity_type="receipts",
        entity_id=receipt.id,
        action=audit.AuditAction.CREATE,
        actor=actor,
        after={"amount": str(receipt.amount), "received_on": receipt.received_on.isoformat()},
        context={"sales_order_id": order.id, "invoice_id": payload.invoice_id},
    )
    db.commit()
    return {
        "id": receipt.id,
        "received_total": str(order_service.received_total(db, order.id)),
    }
