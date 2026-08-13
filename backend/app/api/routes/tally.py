from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from ...models import LedgerScope, TallyExport, TallyLedgerMapping
from ...schemas.api import (
    LedgerMappingIn,
    LedgerMappingOut,
    TallyExportIn,
    TallyExportOut,
    TallyPreviewOut,
)
from ...services import tally as tally_service
from ..deps import Actor, DbSession

router = APIRouter(prefix="/tally", tags=["tally"])


@router.get("/mappings", response_model=list[LedgerMappingOut])
def list_mappings(db: DbSession, scope: str | None = None):
    stmt = select(TallyLedgerMapping).order_by(
        TallyLedgerMapping.scope, TallyLedgerMapping.local_key
    )
    if scope:
        stmt = stmt.where(TallyLedgerMapping.scope == scope)
    return list(db.execute(stmt).scalars())


@router.post("/mappings", response_model=LedgerMappingOut, status_code=201)
def upsert_mapping(payload: LedgerMappingIn, db: DbSession):
    try:
        scope = LedgerScope(payload.scope)
    except ValueError as exc:
        raise HTTPException(422, f"unknown scope {payload.scope!r}") from exc

    existing = db.execute(
        select(TallyLedgerMapping).where(
            TallyLedgerMapping.scope == scope,
            TallyLedgerMapping.local_key == payload.local_key,
        )
    ).scalars().first()

    if existing:
        existing.tally_ledger_name = payload.tally_ledger_name
        existing.tally_parent_group = payload.tally_parent_group
        existing.notes = payload.notes
        mapping = existing
    else:
        mapping = TallyLedgerMapping(**{**payload.model_dump(), "scope": scope})
        db.add(mapping)

    db.commit()
    db.refresh(mapping)
    return mapping


@router.post("/mappings/seed")
def seed_mappings(db: DbSession):
    """Create the placeholder rows. Never overwrites a name you have edited."""
    created = tally_service.seed_mappings(db)
    db.commit()
    return {"created": created}


@router.post("/preview", response_model=TallyPreviewOut)
def preview(payload: TallyExportIn, db: DbSession):
    """Dry run: what would go, and which ledgers are still unmapped."""
    invoices, manifest = tally_service.collect(
        db, payload.date_from, payload.date_to, force=payload.force
    )
    missing: list[dict[str, str]] = []
    try:
        tally_service.check_mappings(db, invoices)
    except tally_service.LedgerMappingMissing as exc:
        missing = [
            {"scope": scope, "local_key": key, "hint": hint} for scope, key, hint in exc.missing
        ]
    return TallyPreviewOut(
        voucher_count=manifest.voucher_count,
        manifest=manifest.to_json(),
        missing_mappings=missing,
    )


@router.post("/export", response_model=TallyExportOut, status_code=201)
def run_export(payload: TallyExportIn, db: DbSession, actor: Actor):
    try:
        export = tally_service.run_export(
            db,
            payload.date_from,
            payload.date_to,
            actor=actor,
            delivery=payload.delivery,
            force=payload.force,
        )
    except tally_service.LedgerMappingMissing as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(export)
    return export


@router.get("/exports", response_model=list[TallyExportOut])
def list_exports(db: DbSession):
    return list(
        db.execute(select(TallyExport).order_by(TallyExport.created_at.desc())).scalars()
    )


@router.get("/exports/{export_id}/xml")
def download_xml(export_id: str, db: DbSession):
    export = db.get(TallyExport, export_id)
    if export is None or not export.xml_path:
        raise HTTPException(404, "export or its XML file not found")
    return FileResponse(
        export.xml_path,
        media_type="application/xml",
        filename=f"tally-{export.date_from}-{export.date_to}.xml",
    )
