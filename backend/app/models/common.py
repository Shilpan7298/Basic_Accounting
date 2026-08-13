from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDPkMixin, utcnow
from .enums import AuditAction, DocumentKind, ExtractionStatus, LedgerScope
from .json_type import JSONText


class StoredDocument(UUIDPkMixin, TimestampMixin, Base):
    """Every file that enters or leaves the system, content-addressed."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("kind", "sha256", name="uq_documents_kind_sha"),)

    kind: Mapped[DocumentKind] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(600), nullable=False)


class Extraction(UUIDPkMixin, TimestampMixin, Base):
    """One extraction attempt.

    ``raw_response`` is kept deliberately: when a local model starts emitting
    junk six months from now, this is the only evidence of what it said.
    """

    __tablename__ = "extractions"

    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(60), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(120))
    schema_name: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[ExtractionStatus] = mapped_column(String(20), nullable=False)
    raw_response: Mapped[str | None] = mapped_column(Text)
    parsed_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)
    field_confidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)
    overall_confidence: Mapped[float | None] = mapped_column()
    warnings_json: Mapped[list[Any] | None] = mapped_column(JSONText)
    error_text: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class AuditEvent(UUIDPkMixin, Base):
    """Append-only. There is no API that deletes from this table."""

    __tablename__ = "audit_events"

    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[AuditAction] = mapped_column(String(30), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)
    context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)


class TallyLedgerMapping(UUIDPkMixin, TimestampMixin, Base):
    """local_key → the exact ledger name that already exists in Tally."""

    __tablename__ = "tally_ledger_mappings"
    __table_args__ = (UniqueConstraint("scope", "local_key", name="uq_tally_scope_key"),)

    scope: Mapped[LedgerScope] = mapped_column(String(30), nullable=False)
    local_key: Mapped[str] = mapped_column(String(120), nullable=False)
    tally_ledger_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tally_parent_group: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)


class TallyExport(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "tally_exports"

    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    voucher_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)
    xml_path: Mapped[str | None] = mapped_column(String(600))
    delivery: Mapped[str] = mapped_column(String(20), nullable=False, default="FILE")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_text: Mapped[str | None] = mapped_column(Text)
    succeeded: Mapped[bool] = mapped_column(default=True, nullable=False)


class TallyExportItem(UUIDPkMixin, Base):
    __tablename__ = "tally_export_items"
    __table_args__ = (
        UniqueConstraint("export_id", "document_type", "document_id", name="uq_tally_item"),
    )

    export_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    remote_id: Mapped[str] = mapped_column(String(64), nullable=False)
