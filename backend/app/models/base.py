from __future__ import annotations

import uuid
from datetime import datetime, timezone

from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class UtcDateTime(TypeDecorator):
    """A timestamp that is always tz-aware UTC on the way out.

    Postgres honours ``DateTime(timezone=True)``; SQLite has no timezone
    concept and hands back a naive value. Comparing the two raises
    ``TypeError: can't compare offset-naive and offset-aware datetimes`` —
    which is exactly what happened the first time a session expiry was
    checked. Normalising here means no caller has to remember.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Any, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class UUIDPkMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
