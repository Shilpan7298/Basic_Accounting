"""Users, sessions, and the versioning of issued documents.

Two ideas here, and they are the whole reason this file exists:

* A **user** has a role, and the role decides what the API will let them do.
  Before this, the actor was a self-declared header — the accountant could
  type "owner" and the audit log believed him.

* An issued document is **immutable**. Changing one does not update a row; it
  writes a new :class:`DocumentVersion` carrying a complete snapshot. The
  ledger reads the *current* version, and every earlier one survives intact so
  a reprint reproduces exactly what was sent to the customer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPkMixin, UtcDateTime, utcnow
from .enums import AmendmentStatus, Role
from .json_type import JSONText


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    username: Mapped[str] = mapped_column(String(60), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(160))
    # scrypt, salted per user. Never a reversible form of the password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(String(20), nullable=False, default=Role.ACCOUNTANT)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    # Rate limiting. An accountant's workstation is on an office LAN, but the
    # host may be reachable over Tailscale, so brute force is worth blunting.
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime)

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER


class UserSession(UUIDPkMixin, Base):
    """A login session.

    Only the *hash* of the token is stored: a stolen database backup must not
    hand over live sessions.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    client_ip: Mapped[str | None] = mapped_column(String(60))

    user = relationship("User", lazy="joined")

    def is_live(self, now: datetime | None = None) -> bool:
        moment = now or utcnow()
        return self.revoked_at is None and self.expires_at > moment


class DocumentVersion(UUIDPkMixin, Base):
    """One immutable snapshot of a document.

    ``version_no`` 1 is what was originally issued and is never modified.
    An amendment adds version 2 and so on. Which one the ledger believes is
    decided by ``status``:

    ``pending``    proposed by someone who cannot approve their own change
    ``current``    the live version
    ``superseded`` a previous current version
    ``rejected``   a proposal the owner turned down
    """

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "version_no", name="uq_version_no"),
    )

    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)  # "invoices" | …
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AmendmentStatus] = mapped_column(String(20), nullable=False)

    # The complete document as at this version — header and lines. Self-
    # contained on purpose: reconstructing an old version must never depend on
    # the current state of any other table.
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONText, nullable=False)
    # Field-level diff against the version this one supersedes, for the review
    # screen. Derived data — the snapshot is the source of truth.
    diff_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)

    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )
    approved_by: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
