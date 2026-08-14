"""Immutable documents: amend by version, void, and (owner only) delete.

Once a document is issued it is frozen. Changing it never updates the row in
place — it writes a new :class:`DocumentVersion` holding a complete snapshot,
and the live row is only touched when that version becomes *current*.

Who may make a change take effect is the whole point:

* the **accountant** proposes; the version sits ``pending`` and the ledger
  keeps showing the old one until the owner approves;
* the **owner** amends and it applies at once, still as a new version.

Voiding keeps the row and its number — that is what keeps the invoice series
gapless. Hard deletion is available to the owner because it was explicitly
asked for; it writes the complete pre-delete snapshot into the audit log
first, so the record of what was removed outlives the removal.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Date as SqlDate
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Money
from ..models import (
    AmendmentStatus,
    AuditAction,
    DocumentVersion,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Role,
    User,
)
from ..models.base import utcnow
from . import audit, tax_engine
from .money import amount_in_words, q2
from .permissions import Permission, has

# Entity types that can be versioned, and how to reach their lines.
_REGISTRY: dict[str, dict[str, Any]] = {
    "invoices": {"model": Invoice, "line_model": InvoiceLine, "line_fk": "invoice_id"},
    "purchase_orders": {
        "model": PurchaseOrder,
        "line_model": PurchaseOrderLine,
        "line_fk": "purchase_order_id",
    },
}

# Fields an amendment may touch. Everything else — the number, the series, the
# financial year, the GUID — is structural and must never move.
EDITABLE_HEADER_FIELDS: dict[str, set[str]] = {
    "invoices": {
        "invoice_date", "due_date", "notes", "is_reverse_charge", "lut_no",
        "place_of_supply_state_code", "irn", "ack_no", "qr_payload", "eway_bill_no",
    },
    "purchase_orders": {
        "po_date", "delivery_due_date", "delivery_location", "payment_terms_text",
        "freight_terms", "warranty_text", "notes", "is_reverse_charge",
    },
}

# Never amendable and never restored: these identify the row or record when it
# was written. Rolling `created_at` back to an old version's value would be a
# lie about when the row was made.
FROZEN_FIELDS = {
    "id", "number", "series_key", "financial_year", "guid", "doc_type",
    "created_at", "updated_at",
}

EDITABLE_LINE_FIELDS = {
    "description", "hsn_code", "qty", "uom", "unit_price", "discount_percent",
}


class VersioningError(RuntimeError):
    pass


class NotAmendable(VersioningError):
    pass


# --- snapshots -------------------------------------------------------------

def _serialise(obj: Any, skip: set[str] = frozenset()) -> dict[str, Any]:
    from sqlalchemy import inspect as sa_inspect

    out: dict[str, Any] = {}
    for column in sa_inspect(obj).mapper.column_attrs:
        if column.key in skip:
            continue
        value = getattr(obj, column.key, None)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, date):
            value = value.isoformat()
        out[column.key] = value
    return out


def snapshot(db: Session, entity_type: str, entity_id: str) -> dict[str, Any]:
    """The complete document, self-contained.

    Self-contained matters: rebuilding an old version must never depend on the
    current state of any other table.
    """
    spec = _REGISTRY[entity_type]
    entity = db.get(spec["model"], entity_id)
    if entity is None:
        raise VersioningError(f"{entity_type} {entity_id} not found")

    lines = db.execute(
        select(spec["line_model"])
        .where(getattr(spec["line_model"], spec["line_fk"]) == entity_id)
        .order_by(spec["line_model"].line_no)
    ).scalars().all()

    return {
        "header": _serialise(entity, skip={"render_context_json"}),
        "lines": [_serialise(line) for line in lines],
        "render_context_json": entity.render_context_json,
    }


def _unchanged(old: Any, new: Any) -> bool:
    """Compare snapshot values, treating money numerically.

    A stored NUMERIC(18,4) serialises as "5000000.0000" while the recomputed
    value quantises to "5000000.00". Comparing those as strings reports a
    change that did not happen, which would fill every approval screen with
    noise.
    """
    if old is None and new is None:
        return True
    try:
        return Decimal(str(old)) == Decimal(str(new))
    except (InvalidOperation, ValueError, TypeError):
        return str(old) == str(new)


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Field-level differences, for the approval screen."""
    header_changes: dict[str, Any] = {}
    for key, new_value in after.get("header", {}).items():
        old_value = before.get("header", {}).get(key)
        if not _unchanged(old_value, new_value):
            header_changes[key] = {"from": old_value, "to": new_value}

    line_changes: list[dict[str, Any]] = []
    old_lines = {line.get("line_no"): line for line in before.get("lines", [])}
    new_lines = {line.get("line_no"): line for line in after.get("lines", [])}
    for line_no in sorted(set(old_lines) | set(new_lines)):
        old_line, new_line = old_lines.get(line_no), new_lines.get(line_no)
        if old_line is None:
            line_changes.append({"line_no": line_no, "change": "added", "to": new_line})
        elif new_line is None:
            line_changes.append({"line_no": line_no, "change": "removed", "from": old_line})
        else:
            fields = {
                key: {"from": old_line.get(key), "to": new_line.get(key)}
                for key in EDITABLE_LINE_FIELDS | {"taxable_value", "line_total"}
                if not _unchanged(old_line.get(key), new_line.get(key))
            }
            if fields:
                line_changes.append({"line_no": line_no, "change": "modified", "fields": fields})

    return {"header": header_changes, "lines": line_changes}


def current_version(db: Session, entity_type: str, entity_id: str) -> DocumentVersion | None:
    return db.execute(
        select(DocumentVersion).where(
            DocumentVersion.entity_type == entity_type,
            DocumentVersion.entity_id == entity_id,
            DocumentVersion.status == AmendmentStatus.CURRENT,
        )
    ).scalars().first()


def versions(db: Session, entity_type: str, entity_id: str) -> list[DocumentVersion]:
    return list(
        db.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.entity_type == entity_type,
                DocumentVersion.entity_id == entity_id,
            )
            .order_by(DocumentVersion.version_no)
        ).scalars()
    )


def _next_version_no(db: Session, entity_type: str, entity_id: str) -> int:
    existing = versions(db, entity_type, entity_id)
    return max((v.version_no for v in existing), default=0) + 1


def record_initial_version(
    db: Session, entity_type: str, entity_id: str, *, actor: str
) -> DocumentVersion:
    """Version 1, written when a document is issued. Never modified again."""
    if current_version(db, entity_type, entity_id):
        raise VersioningError(f"{entity_type} {entity_id} already has a current version")
    version = DocumentVersion(
        entity_type=entity_type,
        entity_id=entity_id,
        version_no=1,
        status=AmendmentStatus.CURRENT,
        snapshot_json=snapshot(db, entity_type, entity_id),
        reason="original issue",
        created_by=actor,
        approved_by=actor,
        approved_at=utcnow(),
    )
    db.add(version)
    db.flush()
    return version


# --- proposing an amendment ------------------------------------------------

def _guard_amendable(db: Session, entity_type: str, entity_id: str) -> Any:
    spec = _REGISTRY[entity_type]
    entity = db.get(spec["model"], entity_id)
    if entity is None:
        raise VersioningError(f"{entity_type} {entity_id} not found")
    if str(entity.status) == "cancelled":
        raise NotAmendable(
            f"{getattr(entity, 'number', entity_id)} is voided; raise a fresh document instead"
        )
    if str(entity.status) == "draft":
        raise NotAmendable(
            "this document is still a draft — edit it directly; versioning starts at issue"
        )
    pending = db.execute(
        select(DocumentVersion).where(
            DocumentVersion.entity_type == entity_type,
            DocumentVersion.entity_id == entity_id,
            DocumentVersion.status == AmendmentStatus.PENDING,
        )
    ).scalars().first()
    if pending:
        raise NotAmendable(
            f"an amendment is already awaiting approval (version {pending.version_no}); "
            "approve or reject it first"
        )
    return entity


def _build_amended_snapshot(
    db: Session,
    entity_type: str,
    entity: Any,
    header_changes: dict[str, Any],
    line_changes: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply the proposed edits to a copy of the snapshot, recomputing tax.

    Tax is recomputed through ``tax_engine`` rather than carried over, because
    a changed quantity, rate or place of supply changes the tax — and there is
    exactly one module allowed to work that out.
    """
    base = snapshot(db, entity_type, entity.id)
    proposed = {"header": dict(base["header"]), "lines": [dict(l) for l in base["lines"]]}

    allowed = EDITABLE_HEADER_FIELDS[entity_type]
    for key, value in (header_changes or {}).items():
        if key in FROZEN_FIELDS:
            raise VersioningError(f"{key} cannot be amended — it identifies the document")
        if key not in allowed:
            raise VersioningError(
                f"{key} is not an amendable field on {entity_type}; "
                f"allowed: {', '.join(sorted(allowed))}"
            )
        proposed["header"][key] = value

    if line_changes is not None:
        rebuilt = []
        for index, raw in enumerate(line_changes, start=1):
            unknown = set(raw) - EDITABLE_LINE_FIELDS - {"line_no", "item_id"}
            if unknown:
                raise VersioningError(f"line fields cannot be set directly: {sorted(unknown)}")
            rebuilt.append({**raw, "line_no": index})
        proposed["lines"] = rebuilt

    # Recompute through the tax engine so the amended document is internally
    # consistent rather than merely edited.
    document_date = proposed["header"].get(
        "invoice_date" if entity_type == "invoices" else "po_date"
    )
    if isinstance(document_date, str):
        document_date = date.fromisoformat(document_date)

    is_proforma = str(proposed["header"].get("doc_type", "")) == "PROFORMA"
    if is_proforma:
        # A proforma carries no GST, so there is nothing to recompute — just
        # keep the line amounts consistent with qty x rate.
        total = Decimal("0")
        for line in proposed["lines"]:
            value = q2(Decimal(str(line.get("qty", 1))) * Decimal(str(line.get("unit_price", 0))))
            line["taxable_value"] = str(value)
            line["line_total"] = str(value)
            total += value
        proposed["header"]["taxable_value"] = str(total)
        proposed["header"]["grand_total"] = str(total)
        proposed["header"]["amount_in_words"] = amount_in_words(total)
        return proposed

    ctx = tax_engine.TaxContext(
        document_date=document_date,
        home_state_code=_home_state_code(db),
        place_of_supply_state_code=proposed["header"].get("place_of_supply_state_code"),
        is_export=bool(proposed["header"].get("is_export")),
        lut_no=proposed["header"].get("lut_no"),
        is_reverse_charge=bool(proposed["header"].get("is_reverse_charge")),
    )
    computation = tax_engine.compute(
        db,
        [
            tax_engine.TaxableLine(
                description=line.get("description", ""),
                qty=Decimal(str(line.get("qty", 1))),
                unit_price=Decimal(str(line.get("unit_price", 0))),
                hsn_code=line.get("hsn_code"),
                discount_percent=Decimal(str(line.get("discount_percent", 0) or 0)),
                uom=line.get("uom") or "NOS",
                item_id=line.get("item_id"),
            )
            for line in proposed["lines"]
        ],
        ctx,
    )

    proposed["lines"] = [
        {
            "line_no": computed.line_no,
            "item_id": computed.item_id,
            "description": computed.description,
            "hsn_code": computed.hsn_code,
            "qty": str(computed.qty),
            "uom": computed.uom,
            "unit_price": str(computed.unit_price),
            "discount_percent": str(computed.discount_percent),
            "taxable_value": str(computed.taxable_value),
            "gst_rate_percent": str(computed.gst_rate_percent),
            "cgst_amount": str(computed.cgst_amount),
            "sgst_amount": str(computed.sgst_amount),
            "igst_amount": str(computed.igst_amount),
            "cess_amount": str(computed.cess_amount),
            "line_total": str(computed.line_total),
        }
        for computed in computation.lines
    ]
    proposed["header"].update(
        {
            "taxable_value": str(computation.taxable_value),
            "cgst_amount": str(computation.cgst_amount),
            "sgst_amount": str(computation.sgst_amount),
            "igst_amount": str(computation.igst_amount),
            "cess_amount": str(computation.cess_amount),
            "round_off": str(computation.round_off),
            "grand_total": str(computation.grand_total),
            "amount_in_words": amount_in_words(computation.grand_total),
        }
    )
    return proposed


def _home_state_code(db: Session) -> str:
    from ..config import get_settings
    from ..models import Company

    company = db.execute(select(Company)).scalars().first()
    return company.state_code if company else get_settings().home_state_code


def propose_amendment(
    db: Session,
    entity_type: str,
    entity_id: str,
    *,
    user: User,
    reason: str,
    header_changes: dict[str, Any] | None = None,
    line_changes: list[dict[str, Any]] | None = None,
) -> DocumentVersion:
    """Write a new version.

    If the user may approve their own amendments (the owner), it is applied
    immediately. Otherwise it waits, and the ledger keeps showing the old
    version in the meantime.
    """
    if not reason or not reason.strip():
        raise VersioningError("an amendment needs a reason — it goes in the audit trail")

    entity = _guard_amendable(db, entity_type, entity_id)
    before = snapshot(db, entity_type, entity_id)
    proposed = _build_amended_snapshot(db, entity_type, entity, header_changes or {}, line_changes)
    changes = diff(before, proposed)
    if not changes["header"] and not changes["lines"]:
        raise VersioningError("nothing would change")

    auto_approve = has(user.role, Permission.DOCUMENT_AMEND_APPROVE)
    version = DocumentVersion(
        entity_type=entity_type,
        entity_id=entity_id,
        version_no=_next_version_no(db, entity_type, entity_id),
        status=AmendmentStatus.PENDING,
        snapshot_json=proposed,
        diff_json=changes,
        reason=reason.strip(),
        created_by=user.username,
    )
    db.add(version)
    db.flush()

    audit.record(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=AuditAction.AMEND_PROPOSED,
        actor=user.username,
        before=changes["header"] and {k: v["from"] for k, v in changes["header"].items()} or None,
        after=changes["header"] and {k: v["to"] for k, v in changes["header"].items()} or None,
        context={
            "version_no": version.version_no,
            "reason": reason,
            "line_changes": changes["lines"],
            "auto_approved": auto_approve,
        },
    )

    if auto_approve:
        approve_amendment(db, version, user=user)
    return version


# --- approving and rejecting ----------------------------------------------

def _apply_snapshot(db: Session, entity_type: str, entity_id: str, snap: dict[str, Any]) -> None:
    spec = _REGISTRY[entity_type]
    entity = db.get(spec["model"], entity_id)
    if entity is None:
        raise VersioningError(f"{entity_type} {entity_id} not found")

    from sqlalchemy import inspect as sa_inspect

    columns = {c.key: c for c in sa_inspect(spec["model"]).mapper.column_attrs}
    for key, value in snap["header"].items():
        if key in FROZEN_FIELDS or key not in columns:
            continue
        # The snapshot is JSON, so money and dates arrive as strings. Coerce by
        # inspecting the column type directly — ``python_type`` *raises* on the
        # custom Money/JSONText decorators rather than being absent, so asking
        # for it is not safe.
        column_type = columns[key].expression.type
        if value is not None and isinstance(value, str):
            if isinstance(column_type, Money):
                value = Decimal(value)
            elif isinstance(column_type, SqlDate):
                value = date.fromisoformat(value)
        setattr(entity, key, value)

    # Lines are replaced wholesale — a positional patch is how a line ends up
    # priced against the wrong item.
    for line in db.execute(
        select(spec["line_model"]).where(
            getattr(spec["line_model"], spec["line_fk"]) == entity_id
        )
    ).scalars().all():
        db.delete(line)
    db.flush()

    line_columns = {c.key for c in sa_inspect(spec["line_model"]).mapper.column_attrs}
    for raw in snap["lines"]:
        payload = {
            key: (Decimal(str(value)) if key in _DECIMAL_LINE_FIELDS and value is not None else value)
            for key, value in raw.items()
            if key in line_columns and key not in {"id", "created_at", "updated_at"}
        }
        payload[spec["line_fk"]] = entity_id
        db.add(spec["line_model"](**payload))
    db.flush()


_DECIMAL_LINE_FIELDS = {
    "qty", "unit_price", "discount_percent", "taxable_value", "gst_rate_percent",
    "cgst_amount", "sgst_amount", "igst_amount", "cess_amount", "line_total",
}


def approve_amendment(db: Session, version: DocumentVersion, *, user: User) -> DocumentVersion:
    if version.status != AmendmentStatus.PENDING:
        raise VersioningError(f"version {version.version_no} is {version.status}, not pending")
    if not has(user.role, Permission.DOCUMENT_AMEND_APPROVE):
        raise VersioningError(f"{user.username} cannot approve amendments")

    live = current_version(db, version.entity_type, version.entity_id)
    if live:
        live.status = AmendmentStatus.SUPERSEDED

    _apply_snapshot(db, version.entity_type, version.entity_id, version.snapshot_json)

    version.status = AmendmentStatus.CURRENT
    version.approved_by = user.username
    version.approved_at = utcnow()
    db.flush()

    audit.record(
        db,
        entity_type=version.entity_type,
        entity_id=version.entity_id,
        action=AuditAction.AMEND_APPROVED,
        actor=user.username,
        after={"version_no": version.version_no},
        context={"proposed_by": version.created_by, "reason": version.reason},
    )
    return version


def reject_amendment(
    db: Session, version: DocumentVersion, *, user: User, reason: str
) -> DocumentVersion:
    if version.status != AmendmentStatus.PENDING:
        raise VersioningError(f"version {version.version_no} is {version.status}, not pending")
    if not has(user.role, Permission.DOCUMENT_AMEND_APPROVE):
        raise VersioningError(f"{user.username} cannot reject amendments")

    version.status = AmendmentStatus.REJECTED
    version.rejected_reason = reason
    version.approved_by = user.username
    version.approved_at = utcnow()
    db.flush()

    audit.record(
        db,
        entity_type=version.entity_type,
        entity_id=version.entity_id,
        action=AuditAction.AMEND_REJECTED,
        actor=user.username,
        context={
            "version_no": version.version_no,
            "proposed_by": version.created_by,
            "reason": reason,
        },
    )
    return version


def pending_amendments(db: Session) -> list[DocumentVersion]:
    return list(
        db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.status == AmendmentStatus.PENDING)
            .order_by(DocumentVersion.created_at)
        ).scalars()
    )


# --- void and delete -------------------------------------------------------

def void_document(
    db: Session, entity_type: str, entity_id: str, *, user: User, reason: str
) -> Any:
    """Mark voided. The row and its number survive — that is what keeps the
    invoice series gapless and the auditor happy."""
    if not has(user.role, Permission.DOCUMENT_VOID):
        raise VersioningError(f"{user.username} cannot void documents")
    if not reason or not reason.strip():
        raise VersioningError("voiding needs a reason")

    spec = _REGISTRY[entity_type]
    entity = db.get(spec["model"], entity_id)
    if entity is None:
        raise VersioningError(f"{entity_type} {entity_id} not found")

    before = {"status": str(entity.status)}
    entity.status = (
        InvoiceStatus.CANCELLED if entity_type == "invoices" else PurchaseOrderStatus.CANCELLED
    )
    entity.cancelled_reason = reason

    # Any amendment still waiting becomes moot.
    for version in db.execute(
        select(DocumentVersion).where(
            DocumentVersion.entity_type == entity_type,
            DocumentVersion.entity_id == entity_id,
            DocumentVersion.status == AmendmentStatus.PENDING,
        )
    ).scalars():
        version.status = AmendmentStatus.REJECTED
        version.rejected_reason = "document was voided"

    # Free the milestone so it can be billed again.
    if entity_type == "invoices" and entity.payment_milestone_id:
        from ..models import MilestoneStatus, PaymentMilestone

        milestone = db.get(PaymentMilestone, entity.payment_milestone_id)
        if milestone and milestone.proforma_invoice_id == entity.id:
            milestone.status = MilestoneStatus.PENDING
            milestone.proforma_invoice_id = None

    db.flush()
    audit.record(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=AuditAction.VOID,
        actor=user.username,
        before=before,
        after={"status": "cancelled", "number": getattr(entity, "number", None)},
        context={"reason": reason},
    )
    return entity


def hard_delete_document(
    db: Session, entity_type: str, entity_id: str, *, user: User, reason: str
) -> dict[str, Any]:
    """Physically remove the document. Owner only.

    This is deliberately available because the business asked for it, and it is
    deliberately noisy. The complete pre-delete snapshot — header, every line,
    and every version — goes into the audit log *before* anything is removed,
    so the record of what was deleted outlives the deletion. The audit rows
    themselves are never touched.

    Note for the auditor's benefit: this leaves a gap in the document series.
    Voiding does not, and is the right tool in almost every case.
    """
    if not has(user.role, Permission.DOCUMENT_DELETE):
        raise VersioningError(
            f"{user.username} cannot delete documents — only the owner can"
        )
    if not reason or not reason.strip():
        raise VersioningError("deletion needs a reason")

    spec = _REGISTRY[entity_type]
    entity = db.get(spec["model"], entity_id)
    if entity is None:
        raise VersioningError(f"{entity_type} {entity_id} not found")

    full = snapshot(db, entity_type, entity_id)
    history = versions(db, entity_type, entity_id)
    full["versions"] = [
        {
            "version_no": v.version_no,
            "status": str(v.status),
            "reason": v.reason,
            "created_by": v.created_by,
            "created_at": v.created_at,
            "snapshot": v.snapshot_json,
        }
        for v in history
    ]

    number = getattr(entity, "number", None)
    audit.record(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=AuditAction.HARD_DELETE,
        actor=user.username,
        before=full,
        after=None,
        context={
            "reason": reason,
            "number": number,
            "warning": "physically deleted; this leaves a gap in the document series",
        },
    )

    for version in history:
        db.delete(version)
    for line in db.execute(
        select(spec["line_model"]).where(
            getattr(spec["line_model"], spec["line_fk"]) == entity_id
        )
    ).scalars().all():
        db.delete(line)
    db.delete(entity)
    db.flush()

    return {"deleted": entity_type, "id": entity_id, "number": number, "snapshot": full}


def restore_version(
    db: Session, entity_type: str, entity_id: str, version_no: int, *, user: User, reason: str
) -> DocumentVersion:
    """Roll back to an earlier version by writing it forward as a new one.

    Rolling back is itself a change, so it gets a version rather than deleting
    history.
    """
    target = db.execute(
        select(DocumentVersion).where(
            DocumentVersion.entity_type == entity_type,
            DocumentVersion.entity_id == entity_id,
            DocumentVersion.version_no == version_no,
        )
    ).scalars().first()
    if target is None:
        raise VersioningError(f"version {version_no} not found")

    _guard_amendable(db, entity_type, entity_id)
    version = DocumentVersion(
        entity_type=entity_type,
        entity_id=entity_id,
        version_no=_next_version_no(db, entity_type, entity_id),
        status=AmendmentStatus.PENDING,
        snapshot_json=target.snapshot_json,
        diff_json=diff(snapshot(db, entity_type, entity_id), target.snapshot_json),
        reason=f"restore of version {version_no}: {reason}",
        created_by=user.username,
    )
    db.add(version)
    db.flush()
    if has(user.role, Permission.DOCUMENT_AMEND_APPROVE):
        approve_amendment(db, version, user=user)
    return version
