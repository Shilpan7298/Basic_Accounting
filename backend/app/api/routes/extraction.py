"""Upload → extract → review → approve.

This is the M1 spine. The approve endpoint is the *only* route in the app that
turns extracted data into a financial record, which is how "AI never commits
financial data unattended" is enforced rather than merely intended.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ...extractors import health_all
from ...extractors.pdf_text import has_text_layer, page_count
from ...models import DocumentKind, Extraction, ExtractionStatus, StoredDocument
from ...schemas.api import ApproveExtractionIn, ExtractionOut, OfferOut, OrderOut, UploadResponse
from ...schemas.extraction import schema_for
from ...services import documents as doc_service
from ...services import orders as order_service
from ...services import purchase as purchase_service
from ...models import User
from ...services.permissions import Permission
from ..deps import Actor, DbSession, requires

router = APIRouter(tags=["extraction"])

_KIND_FOR_SCHEMA = {
    "customer_po": DocumentKind.UPLOAD_CUSTOMER_PO,
    "supplier_offer": DocumentKind.UPLOAD_SUPPLIER_OFFER,
}


@router.get("/extractors/health")
def extractor_health():
    from ...config import get_settings

    return {
        "selected": get_settings().extractor,
        "adapters": [
            {
                "name": h.name,
                "available": h.available,
                "detail": h.detail,
                "model_name": h.model_name,
            }
            for h in health_all()
        ],
    }


@router.post("/uploads", response_model=UploadResponse, status_code=201)
async def upload_and_extract(
    db: DbSession, _perm: Annotated[User, Depends(requires(Permission.EXTRACTION_RUN))],
    file: UploadFile = File(...),
    schema_name: str = Form("customer_po"),
    extractor: str | None = Form(None),
):
    if schema_name not in _KIND_FOR_SCHEMA:
        raise HTTPException(422, f"unknown schema_name {schema_name!r}")

    content = await file.read()
    if not content:
        raise HTTPException(422, "the uploaded file is empty")

    document = doc_service.store_upload(
        db,
        content=content,
        filename=file.filename or "upload.pdf",
        content_type=file.content_type or "application/pdf",
        kind=_KIND_FOR_SCHEMA[schema_name],
    )
    extraction = doc_service.run_extraction(
        db, document, schema_name, extractor_name=extractor
    )
    db.commit()
    db.refresh(extraction)

    return UploadResponse(
        document_id=document.id,
        filename=document.filename,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        page_count=page_count(content),
        has_text_layer=has_text_layer(content, document.content_type),
        extraction=ExtractionOut.model_validate(extraction),
    )


@router.get("/documents/{document_id}/file")
def download_source(document_id: str, db: DbSession):
    """The left-hand pane of the review screen."""
    document = db.get(StoredDocument, document_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return FileResponse(
        document.storage_path, media_type=document.content_type, filename=document.filename
    )


@router.get("/extractions/{extraction_id}", response_model=ExtractionOut)
def get_extraction(extraction_id: str, db: DbSession):
    extraction = db.get(Extraction, extraction_id)
    if extraction is None:
        raise HTTPException(404, "extraction not found")
    return extraction


@router.post("/extractions/{extraction_id}/retry", response_model=ExtractionOut)
def retry_extraction(extraction_id: str, db: DbSession, _perm: Annotated[User, Depends(requires(Permission.EXTRACTION_RUN))], extractor: str | None = None):
    """Re-run against a different adapter without re-uploading the file."""
    extraction = db.get(Extraction, extraction_id)
    if extraction is None:
        raise HTTPException(404, "extraction not found")
    document = db.get(StoredDocument, extraction.document_id)
    if document is None:
        raise HTTPException(404, "source document is missing")
    fresh = doc_service.run_extraction(
        db, document, extraction.schema_name, extractor_name=extractor
    )
    db.commit()
    db.refresh(fresh)
    return fresh


@router.post("/extractions/{extraction_id}/approve")
def approve_extraction(
    extraction_id: str, payload: ApproveExtractionIn, db: DbSession, actor: Actor, _perm: Annotated[User, Depends(requires(Permission.EXTRACTION_APPROVE))]
):
    """The human has reviewed and corrected. Now — and only now — records exist."""
    extraction = db.get(Extraction, extraction_id)
    if extraction is None:
        raise HTTPException(404, "extraction not found")
    if extraction.status == ExtractionStatus.APPROVED:
        raise HTTPException(409, "this extraction has already been approved")

    # Re-validate the human's payload against the same schema the model had to
    # satisfy. A typo in the review form is as damaging as a bad extraction.
    schema = schema_for(extraction.schema_name)
    try:
        validated = schema.model_validate(payload.corrected)
    except Exception as exc:
        raise HTTPException(422, f"corrected payload is not valid: {exc}") from exc

    data = validated.model_dump()
    doc_service.approve(db, extraction, validated.model_dump(mode="json"))

    if extraction.schema_name == "customer_po":
        order = order_service.create_from_approved_extraction(
            db,
            data,
            actor=actor,
            source_document_id=extraction.document_id,
            extraction_id=extraction.id,
        )
        db.commit()
        db.refresh(order)
        return {"kind": "sales_order", "sales_order": OrderOut.model_validate(order)}

    offer = purchase_service.create_offer_from_extraction(
        db,
        data,
        actor=actor,
        source_document_id=extraction.document_id,
        extraction_id=extraction.id,
    )
    db.commit()
    db.refresh(offer)
    return {"kind": "supplier_offer", "supplier_offer": OfferOut.model_validate(offer)}
