"""Upload storage and extraction orchestration.

The rule this enforces is CLAUDE.md constraint #5: extraction produces a
*draft* with confidence, and nothing financial exists until a human approves
it. So this module writes to ``documents`` and ``extractions`` and stops. The
step that creates a SalesOrder lives in ``orders`` and is only reachable from
the approve endpoint.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..extractors import ExtractionRequest, get_extractor
from ..extractors.pdf_text import extract_text
from ..models import DocumentKind, Extraction, ExtractionStatus, StoredDocument


def store_upload(
    db: Session, *, content: bytes, filename: str, content_type: str, kind: DocumentKind
) -> StoredDocument:
    """Content-addressed, so re-uploading the same PO is one blob, not two."""
    digest = hashlib.sha256(content).hexdigest()
    existing = db.execute(
        select(StoredDocument).where(
            StoredDocument.kind == kind, StoredDocument.sha256 == digest
        )
    ).scalars().first()
    if existing:
        return existing

    settings = get_settings()
    suffix = "".join(c for c in filename[-8:] if c.isalnum() or c == ".") or ".bin"
    target = settings.upload_dir / f"{digest[:16]}{suffix if suffix.startswith('.') else ''}"
    if not target.suffix:
        target = target.with_suffix(".pdf" if content_type == "application/pdf" else ".bin")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    document = StoredDocument(
        kind=kind,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        storage_path=str(target),
    )
    db.add(document)
    db.flush()
    return document


def run_extraction(
    db: Session,
    document: StoredDocument,
    schema_name: str,
    *,
    extractor_name: str | None = None,
) -> Extraction:
    """Run the configured extractor and persist the attempt — success or not.

    A failure is stored, not raised: the review screen opens on the manual
    form either way, and the raw response is the only evidence of what a local
    model actually said.
    """
    content = _read(document)
    text = extract_text(content, document.content_type)

    extractor = get_extractor(extractor_name)
    request = ExtractionRequest(
        content=content,
        content_type=document.content_type,
        schema_name=schema_name,
        text=text,
        filename=document.filename,
    )
    result = extractor.extract(request)

    parsed: dict[str, Any] | None = None
    if result.data is not None:
        parsed = result.data.model_dump(mode="json")

    extraction = Extraction(
        document_id=document.id,
        extractor_name=result.extractor_name or extractor.name,
        model_name=result.model_name,
        schema_name=schema_name,
        status=result.status,
        raw_response=result.raw_response,
        parsed_json=parsed,
        field_confidence_json=result.field_confidence or None,
        overall_confidence=result.overall_confidence,
        warnings_json=result.warnings or None,
        error_text=result.error_text,
        duration_ms=result.duration_ms,
    )
    db.add(extraction)
    db.flush()
    return extraction


def approve(db: Session, extraction: Extraction, corrected: dict[str, Any]) -> Extraction:
    """Record the human's corrected payload and mark the extraction approved.

    The corrected values are stored *over* the model's, so the audit trail
    shows both what the model said (``raw_response``) and what the human
    signed off (``parsed_json``).
    """
    extraction.parsed_json = corrected
    extraction.status = ExtractionStatus.APPROVED
    db.flush()
    return extraction


def _read(document: StoredDocument) -> bytes:
    from pathlib import Path

    return Path(document.storage_path).read_bytes()


def health_snapshot() -> list[dict[str, Any]]:
    from ..extractors import health_all

    return [asdict(h) for h in health_all()]
