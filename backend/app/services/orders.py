"""Sales order lifecycle.

The state machine is a table, not scattered ``if`` statements, so "can this
order move there?" has exactly one answer and one place to change it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AuditAction,
    Customer,
    MilestoneStatus,
    OrderStatus,
    PaymentMilestone,
    Receipt,
    SalesOrder,
    SalesOrderLine,
)
from . import audit, payment_terms
from .gstin import state_code_of, state_name
from .money import q2

# The only legal moves. Anything not listed raises.
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.RECEIVED, OrderStatus.CANCELLED},
    OrderStatus.RECEIVED: {OrderStatus.IN_PRODUCTION, OrderStatus.CANCELLED},
    OrderStatus.IN_PRODUCTION: {OrderStatus.DISPATCHED, OrderStatus.CANCELLED},
    OrderStatus.DISPATCHED: {OrderStatus.INVOICED, OrderStatus.CANCELLED},
    OrderStatus.INVOICED: {OrderStatus.PARTIALLY_PAID, OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PARTIALLY_PAID: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: set(),
    OrderStatus.CANCELLED: set(),
}


class IllegalTransition(ValueError):
    pass


class OrderNotFound(LookupError):
    pass


def get(db: Session, order_id: str) -> SalesOrder:
    order = db.get(SalesOrder, order_id)
    if order is None:
        raise OrderNotFound(f"sales order {order_id} not found")
    return order


def transition(
    db: Session, order: SalesOrder, target: OrderStatus, *, actor: str, reason: str | None = None
) -> SalesOrder:
    current = OrderStatus(order.status)
    target = OrderStatus(target)
    if target not in TRANSITIONS[current]:
        allowed = ", ".join(sorted(TRANSITIONS[current])) or "nothing (terminal state)"
        raise IllegalTransition(
            f"cannot move order {order.customer_po_number} from {current} to {target}; "
            f"allowed: {allowed}"
        )
    before = {"status": str(current)}
    order.status = target
    audit.record(
        db,
        entity_type="sales_orders",
        entity_id=order.id,
        action=AuditAction.STATUS_CHANGE,
        actor=actor,
        before=before,
        after={"status": str(target)},
        context={"reason": reason} if reason else None,
    )
    db.flush()
    return order


def find_or_create_customer(
    db: Session, name: str, gstin: str | None, address: str | None, *, actor: str
) -> Customer:
    """Match on GSTIN first (authoritative), then on an exact name."""
    if gstin:
        existing = db.execute(select(Customer).where(Customer.gstin == gstin)).scalars().first()
        if existing:
            return existing
    existing = db.execute(select(Customer).where(Customer.name == name)).scalars().first()
    if existing:
        return existing

    code = state_code_of(gstin)
    customer = Customer(
        name=name,
        gstin=gstin,
        state_code=code,
        place_of_supply_state_code=code,
        state_name=state_name(code),
        billing_address=address,
        shipping_address=address,
    )
    db.add(customer)
    db.flush()
    audit.record(
        db,
        entity_type="customers",
        entity_id=customer.id,
        action=AuditAction.CREATE,
        actor=actor,
        after={"name": name, "gstin": gstin},
        context={"source": "po_extraction"},
    )
    return customer


def create_from_approved_extraction(
    db: Session,
    payload: dict[str, Any],
    *,
    actor: str,
    source_document_id: str | None = None,
    extraction_id: str | None = None,
) -> SalesOrder:
    """Turn an approved (human-reviewed) PO extraction into a SalesOrder.

    This is the *only* path from extraction to a financial record — nothing
    upstream of a human approval reaches here.
    """
    customer = find_or_create_customer(
        db,
        name=payload["customer_name"],
        gstin=payload.get("customer_gstin"),
        address=payload.get("customer_address"),
        actor=actor,
    )

    lines_payload = payload.get("lines") or []
    order = SalesOrder(
        customer_id=customer.id,
        customer_po_number=payload["po_number"],
        customer_po_date=payload.get("po_date") or date.today(),
        currency=payload.get("currency") or "INR",
        delivery_due_date=payload.get("delivery_due_date"),
        delivery_location=payload.get("delivery_location"),
        payment_terms_text=payload.get("payment_terms_text"),
        status=OrderStatus.RECEIVED,
        source_document_id=source_document_id,
        extraction_id=extraction_id,
        notes=payload.get("notes"),
    )
    db.add(order)
    db.flush()

    total = Decimal("0")
    for index, raw in enumerate(lines_payload, start=1):
        qty = Decimal(str(raw.get("qty", 1)))
        unit_price = Decimal(str(raw.get("unit_price", 0)))
        discount = Decimal(str(raw.get("discount_percent", 0) or 0))
        gross = qty * unit_price
        line_total = q2(gross - gross * discount / Decimal("100"))
        db.add(
            SalesOrderLine(
                sales_order_id=order.id,
                line_no=index,
                item_id=raw.get("item_id"),
                description=raw.get("description", ""),
                hsn_code=raw.get("hsn_code"),
                qty=qty,
                uom=raw.get("uom") or "NOS",
                unit_price=unit_price,
                discount_percent=discount,
                line_total=line_total,
            )
        )
        total += line_total

    order.order_value = q2(total)
    db.flush()

    audit.record(
        db,
        entity_type="sales_orders",
        entity_id=order.id,
        action=AuditAction.CREATE,
        actor=actor,
        after={
            "customer_po_number": order.customer_po_number,
            "order_value": str(order.order_value),
            "line_count": len(lines_payload),
        },
        context={"extraction_id": extraction_id, "source": "approved_extraction"},
    )
    return order


def build_milestones(
    db: Session,
    order: SalesOrder,
    *,
    actor: str,
    manual: list[dict[str, Any]] | None = None,
) -> tuple[list[PaymentMilestone], payment_terms.ParsedTerms | None]:
    """Create the proforma schedule for an order.

    With ``manual`` supplied, that is used verbatim (the schedule builder).
    Otherwise the terms text is parsed — and if the parse is not usable, no
    milestones are created and the caller is told to open the builder. We do
    not persist a schedule we are unsure about.
    """
    existing = list(order.milestones)
    if existing:
        for milestone in existing:
            db.delete(milestone)
        db.flush()

    parsed: payment_terms.ParsedTerms | None = None
    if manual:
        specs = [
            {
                "label": m["label"],
                "trigger_event": m.get("trigger_event", "MANUAL"),
                "percent": Decimal(str(m["percent"])),
                "net_days": m.get("net_days"),
            }
            for m in manual
        ]
        source = "manual"
    else:
        parsed = payment_terms.parse(order.payment_terms_text)
        if not parsed.is_usable:
            return [], parsed
        specs = [
            {
                "label": m.label,
                "trigger_event": m.trigger_event,
                "percent": m.percent,
                "net_days": m.net_days,
            }
            for m in parsed.milestones
        ]
        source = "parsed"

    amounts = payment_terms.allocate_amounts(
        Decimal(order.order_value), [s["percent"] for s in specs]
    )

    created: list[PaymentMilestone] = []
    for index, (spec, amount) in enumerate(zip(specs, amounts), start=1):
        trigger = spec["trigger_event"]
        milestone = PaymentMilestone(
            sales_order_id=order.id,
            seq=index,
            label=spec["label"],
            trigger_event=trigger,
            percent=q2(spec["percent"]),
            amount=amount,
            net_days=spec.get("net_days"),
            due_date=payment_terms.due_date_for(
                payment_terms.MilestoneTrigger(trigger),
                po_date=order.customer_po_date,
                delivery_due_date=order.delivery_due_date,
                net_days=spec.get("net_days"),
            ),
            status=MilestoneStatus.PENDING,
            source=source,
        )
        db.add(milestone)
        created.append(milestone)

    db.flush()
    audit.record(
        db,
        entity_type="sales_orders",
        entity_id=order.id,
        action=AuditAction.UPDATE,
        actor=actor,
        after={
            "milestones": [
                {"seq": m.seq, "label": m.label, "percent": str(m.percent), "amount": str(m.amount)}
                for m in created
            ]
        },
        context={"source": source, "replaced": len(existing)},
    )
    return created, parsed


def received_total(db: Session, order_id: str, upto: date | None = None) -> Decimal:
    stmt = select(Receipt).where(Receipt.sales_order_id == order_id)
    if upto:
        stmt = stmt.where(Receipt.received_on <= upto)
    return q2(sum((Decimal(r.amount) for r in db.execute(stmt).scalars()), Decimal("0")))


def days_to_delivery(order: SalesOrder, today: date | None = None) -> int | None:
    if not order.delivery_due_date:
        return None
    return (order.delivery_due_date - (today or date.today())).days
