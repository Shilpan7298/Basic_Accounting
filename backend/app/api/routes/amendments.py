"""Amending, voiding and deleting issued documents.

The accountant reaches `propose`; the owner reaches everything. Which of those
happens is decided by the permission on each route, not by anything the client
sends.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...models import AmendmentStatus, DocumentVersion, Invoice, PurchaseOrder, User
from ...schemas.api import ORMModel
from ...services import versioning
from ...services.permissions import Permission
from ..deps import CurrentUser, DbSession, requires

router = APIRouter(tags=["amendments"])

_ENTITY_MODELS = {"invoices": Invoice, "purchase_orders": PurchaseOrder}


class AmendIn(BaseModel):
    reason: str = Field(min_length=3)
    header: dict[str, Any] | None = None
    lines: list[dict[str, Any]] | None = None


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3)


class VersionOut(ORMModel):
    id: str
    entity_type: str
    entity_id: str
    version_no: int
    status: str
    reason: str | None
    diff_json: dict[str, Any] | None
    created_by: str
    created_at: Any
    approved_by: str | None
    approved_at: Any | None
    rejected_reason: str | None


class PendingOut(VersionOut):
    document_number: str | None = None
    document_type: str | None = None


def _check_entity_type(entity_type: str) -> None:
    if entity_type not in _ENTITY_MODELS:
        raise HTTPException(
            422,
            f"cannot version {entity_type!r}; "
            f"supported: {', '.join(sorted(_ENTITY_MODELS))}",
        )


# --- history ---------------------------------------------------------------

@router.get("/{entity_type}/{entity_id}/versions", response_model=list[VersionOut])
def list_versions(entity_type: str, entity_id: str, db: DbSession, _user: CurrentUser):
    _check_entity_type(entity_type)
    return versioning.versions(db, entity_type, entity_id)


@router.get("/{entity_type}/{entity_id}/versions/{version_no}")
def get_version(
    entity_type: str, entity_id: str, version_no: int, db: DbSession, _user: CurrentUser
):
    """The document exactly as it stood at this version."""
    _check_entity_type(entity_type)
    version = db.execute(
        select(DocumentVersion).where(
            DocumentVersion.entity_type == entity_type,
            DocumentVersion.entity_id == entity_id,
            DocumentVersion.version_no == version_no,
        )
    ).scalars().first()
    if version is None:
        raise HTTPException(404, f"version {version_no} not found")
    return {
        "version_no": version.version_no,
        "status": str(version.status),
        "reason": version.reason,
        "created_by": version.created_by,
        "created_at": version.created_at,
        "approved_by": version.approved_by,
        "snapshot": version.snapshot_json,
        "diff": version.diff_json,
    }


# --- proposing -------------------------------------------------------------

@router.post("/{entity_type}/{entity_id}/amend", response_model=VersionOut, status_code=201)
def amend(
    entity_type: str,
    entity_id: str,
    payload: AmendIn,
    db: DbSession,
    user: Annotated[User, Depends(requires(Permission.DOCUMENT_AMEND))],
):
    """Propose a change.

    The owner's amendment applies immediately; anyone else's waits for
    approval, and the ledger keeps showing the previous version meanwhile.
    """
    _check_entity_type(entity_type)
    try:
        version = versioning.propose_amendment(
            db,
            entity_type,
            entity_id,
            user=user,
            reason=payload.reason,
            header_changes=payload.header,
            line_changes=payload.lines,
        )
    except versioning.NotAmendable as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except versioning.VersioningError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(version)
    return version


# --- approving -------------------------------------------------------------

@router.get("/amendments/pending", response_model=list[PendingOut])
def list_pending(db: DbSession, _user: CurrentUser):
    """The owner's approval queue."""
    out: list[PendingOut] = []
    for version in versioning.pending_amendments(db):
        model = _ENTITY_MODELS.get(version.entity_type)
        entity = db.get(model, version.entity_id) if model else None
        row = PendingOut.model_validate(version)
        row.document_number = getattr(entity, "number", None)
        row.document_type = str(getattr(entity, "doc_type", version.entity_type))
        out.append(row)
    return out


@router.post("/amendments/{version_id}/approve", response_model=VersionOut)
def approve(
    version_id: str,
    db: DbSession,
    user: Annotated[User, Depends(requires(Permission.DOCUMENT_AMEND_APPROVE))],
):
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise HTTPException(404, "amendment not found")
    try:
        versioning.approve_amendment(db, version, user=user)
    except versioning.VersioningError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    db.refresh(version)
    return version


@router.post("/amendments/{version_id}/reject", response_model=VersionOut)
def reject(
    version_id: str,
    payload: ReasonIn,
    db: DbSession,
    user: Annotated[User, Depends(requires(Permission.DOCUMENT_AMEND_APPROVE))],
):
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise HTTPException(404, "amendment not found")
    try:
        versioning.reject_amendment(db, version, user=user, reason=payload.reason)
    except versioning.VersioningError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    db.refresh(version)
    return version


@router.post("/{entity_type}/{entity_id}/restore/{version_no}", response_model=VersionOut)
def restore(
    entity_type: str,
    entity_id: str,
    version_no: int,
    payload: ReasonIn,
    db: DbSession,
    user: Annotated[User, Depends(requires(Permission.DOCUMENT_AMEND))],
):
    """Roll back to an earlier version — itself recorded as a new version."""
    _check_entity_type(entity_type)
    try:
        version = versioning.restore_version(
            db, entity_type, entity_id, version_no, user=user, reason=payload.reason
        )
    except versioning.NotAmendable as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except versioning.VersioningError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(version)
    return version


# --- void and delete (owner) ----------------------------------------------

@router.post("/{entity_type}/{entity_id}/void")
def void(
    entity_type: str,
    entity_id: str,
    payload: ReasonIn,
    db: DbSession,
    user: Annotated[User, Depends(requires(Permission.DOCUMENT_VOID))],
):
    """Mark voided. The number is kept, so the series stays gapless."""
    _check_entity_type(entity_type)
    try:
        entity = versioning.void_document(
            db, entity_type, entity_id, user=user, reason=payload.reason
        )
    except versioning.VersioningError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return {
        "voided": entity_type,
        "id": entity_id,
        "number": getattr(entity, "number", None),
        "status": str(entity.status),
    }


@router.delete("/{entity_type}/{entity_id}")
def hard_delete(
    entity_type: str,
    entity_id: str,
    reason: str,
    confirm: str,
    db: DbSession,
    user: Annotated[User, Depends(requires(Permission.DOCUMENT_DELETE))],
):
    """Physically remove a document. Owner only, and deliberately awkward.

    ``confirm`` must be the document's own number, typed out — the same guard
    GitHub uses before deleting a repository. The full pre-delete snapshot goes
    into the audit log first.
    """
    _check_entity_type(entity_type)
    entity = db.get(_ENTITY_MODELS[entity_type], entity_id)
    if entity is None:
        raise HTTPException(404, f"{entity_type} not found")

    number = getattr(entity, "number", None)
    if confirm != number:
        raise HTTPException(
            400,
            f"to delete this permanently, pass confirm={number!r}. "
            "Voiding is almost always the right choice instead — it keeps the "
            "number and leaves no gap in the series.",
        )
    try:
        result = versioning.hard_delete_document(
            db, entity_type, entity_id, user=user, reason=reason
        )
    except versioning.VersioningError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return {
        "deleted": result["deleted"],
        "number": result["number"],
        "note": "the full pre-delete snapshot is in the audit log",
    }
