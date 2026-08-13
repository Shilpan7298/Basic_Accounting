"""The tamper-evident edit log.

Two layers, deliberately:

* :func:`record` is the explicit call a service makes when it knows what
  happened ("this invoice was issued", "this order moved to dispatched").
* :func:`install_session_listener` is the safety net — a SQLAlchemy flush
  hook that logs any INSERT/UPDATE to an audited table that nobody logged
  explicitly. That is what turns "we remembered to log it" into "it is
  logged whether we remembered or not".

There is no ``delete`` here and no setting that turns this off. That is the
requirement, not an oversight.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from ..models import AuditAction, AuditEvent, new_uuid
from ..models.base import Base

# Tables whose every mutation must leave a trace.
AUDITED_TABLES = {
    "sales_orders",
    "sales_order_lines",
    "payment_milestones",
    "invoices",
    "invoice_lines",
    "receipts",
    "purchase_orders",
    "purchase_order_lines",
    "supplier_offers",
    "supplier_offer_lines",
    "document_series",
    "customers",
    "suppliers",
    "items",
    "tax_rates",
    "tally_ledger_mappings",
}

_SKIP_COLUMNS = {"created_at", "updated_at"}


def _serialise(obj: Base) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for column in inspect(obj).mapper.column_attrs:
        if column.key in _SKIP_COLUMNS:
            continue
        value = getattr(obj, column.key, None)
        out[column.key] = str(value) if isinstance(value, Decimal) else value
    return out


def _changed_columns(obj: Base) -> tuple[dict[str, Any], dict[str, Any]]:
    """Before/after limited to the columns that actually changed."""
    state = inspect(obj)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        if attr.key in _SKIP_COLUMNS:
            continue
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        before[attr.key] = str(old) if isinstance(old, Decimal) else old
        after[attr.key] = str(new) if isinstance(new, Decimal) else new
    return before, after


def record(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    action: AuditAction,
    actor: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> AuditEvent:
    """Write an explicit audit row. Flushed immediately so it cannot be lost
    to a later partial rollback of the caller's own bookkeeping."""
    entry = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        before_json=before,
        after_json=after,
        context_json=context,
    )
    db.add(entry)
    db.flush()
    return entry


def snapshot(obj: Base) -> dict[str, Any]:
    """Public helper so a service can grab 'before' prior to mutating."""
    return _serialise(obj)


def install_session_listener(session_factory: Any, actor_getter: Any = None) -> None:
    """Attach the catch-all flush hook to a sessionmaker."""

    @event.listens_for(session_factory, "before_flush")
    def _before_flush(session: Session, _ctx, _instances):  # pragma: no cover - hook
        actor = (actor_getter() if actor_getter else None) or getattr(
            session, "audit_actor", "system"
        )
        pending: list[AuditEvent] = []

        for obj in session.new:
            table = getattr(obj, "__tablename__", None)
            if table not in AUDITED_TABLES:
                continue
            # The UUID default is only applied at flush time, so a snapshot
            # taken now would carry an empty entity_id and the create event
            # could never be looked up. Assign it early instead — the default
            # is a plain Python callable, so this is the same value it would
            # have got a moment later.
            if getattr(obj, "id", None) is None:
                obj.id = new_uuid()
            pending.append(
                AuditEvent(
                    entity_type=table,
                    entity_id=obj.id,
                    action=AuditAction.CREATE,
                    actor=actor,
                    after_json=_serialise(obj),
                    context_json={"source": "session_hook"},
                )
            )

        for obj in session.dirty:
            table = getattr(obj, "__tablename__", None)
            if table not in AUDITED_TABLES or not session.is_modified(obj):
                continue
            before, after = _changed_columns(obj)
            if not after:
                continue
            action = (
                AuditAction.STATUS_CHANGE if "status" in after else AuditAction.UPDATE
            )
            pending.append(
                AuditEvent(
                    entity_type=table,
                    entity_id=getattr(obj, "id", "") or "",
                    action=action,
                    actor=actor,
                    before_json=before,
                    after_json=after,
                    context_json={"source": "session_hook"},
                )
            )

        for entry in pending:
            session.add(entry)


def set_actor(db: Session, actor: str) -> None:
    db.audit_actor = actor  # type: ignore[attr-defined]
