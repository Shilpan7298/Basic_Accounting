from __future__ import annotations

from typing import Annotated

from datetime import date

from fastapi import Depends, APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from ...models import AuditEvent
from ...rendering import render, templates
from ...schemas.api import AuditEventOut, TemplateOut, TemplateValidationOut
from ...services import reports as report_service
from ...models import User
from ...services.permissions import Permission
from ..deps import DbSession, requires

router = APIRouter(tags=["reports"])

_BUILDERS = {
    "sales-register": report_service.sales_register,
    "hsn-summary": report_service.hsn_summary,
    "purchase-register": report_service.purchase_register,
}


def _serialise(report: report_service.Report) -> dict:
    return {
        "title": report.title,
        "columns": report.columns,
        "rows": [[str(v) for v in row] for row in report.rows],
        "totals": {k: str(v) for k, v in (report.totals or {}).items()},
    }


@router.get("/reports/{name}")
def get_report(
    name: str,
    db: DbSession, _perm: Annotated[User, Depends(requires(Permission.REPORT_READ))],
    date_from: date | None = None,
    date_to: date | None = None,
    as_on: date | None = None,
):
    if name == "outstanding-receivables":
        return _serialise(report_service.outstanding_receivables(db, as_on))
    if name == "order-pipeline":
        return _serialise(report_service.order_pipeline(db, as_on))

    builder = _BUILDERS.get(name)
    if builder is None:
        raise HTTPException(
            404,
            f"unknown report {name!r}; available: "
            + ", ".join([*_BUILDERS, "outstanding-receivables", "order-pipeline"]),
        )
    if not date_from or not date_to:
        raise HTTPException(422, "date_from and date_to are required for this report")
    return _serialise(builder(db, date_from, date_to))


@router.get("/reports/{name}/excel")
def get_report_excel(
    name: str,
    db: DbSession, _perm: Annotated[User, Depends(requires(Permission.REPORT_READ))],
    date_from: date | None = None,
    date_to: date | None = None,
    as_on: date | None = None,
):
    if name == "all":
        if not date_from or not date_to:
            raise HTTPException(422, "date_from and date_to are required")
        reports = [
            report_service.sales_register(db, date_from, date_to),
            report_service.hsn_summary(db, date_from, date_to),
            report_service.purchase_register(db, date_from, date_to),
            report_service.outstanding_receivables(db, as_on),
            report_service.order_pipeline(db, as_on),
        ]
    elif name == "outstanding-receivables":
        reports = [report_service.outstanding_receivables(db, as_on)]
    elif name == "order-pipeline":
        reports = [report_service.order_pipeline(db, as_on)]
    else:
        builder = _BUILDERS.get(name)
        if builder is None:
            raise HTTPException(404, f"unknown report {name!r}")
        if not date_from or not date_to:
            raise HTTPException(422, "date_from and date_to are required for this report")
        reports = [builder(db, date_from, date_to)]

    payload = report_service.to_excel(reports)
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


@router.get("/audit", response_model=list[AuditEventOut])
def list_audit_events(
    db: DbSession, _perm: Annotated[User, Depends(requires(Permission.AUDIT_READ))],
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = Query(200, le=1000),
):
    """Read-only by design. There is no endpoint that deletes from this table."""
    stmt = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    return list(db.execute(stmt).scalars())


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(doc_type: str | None = None):
    return [
        TemplateOut(
            doc_type=t.doc_type, name=t.name, version=t.version, kind=t.kind, path=str(t.path)
        )
        for t in templates.discover(doc_type)
    ]


@router.post("/templates/{doc_type}/{kind}/{name}/validate", response_model=TemplateValidationOut)
def validate_template(doc_type: str, kind: str, name: str, version: int | None = None):
    """Render the template against the golden sample and report what's undefined.

    This is the 'did I break it?' button for whoever edits a template in Word.
    """
    if kind not in ("html", "docx"):
        raise HTTPException(422, "kind must be 'html' or 'docx'")
    result = render.validate_template(doc_type, kind, name, version)
    return TemplateValidationOut(
        ok=result.ok,
        template=result.template,
        undefined_names=result.undefined_names,
        error=result.error,
    )
