"""Request dependencies: who is calling, and may they do this.

Before authentication existed the actor was a self-declared header, which meant
the accountant could type "owner" and the audit log believed him. Now the
identity comes from a signed-in session and the role comes from the database.

Every mutating route declares a permission via :func:`requires`. A route that
declares none is a bug, and ``test_permissions.py`` asserts there are none.
"""

from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AuditAction, User
from ..services import audit, permissions, security
from ..services.permissions import Permission

SESSION_COOKIE = "urjapod_session"


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    urjapod_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """The signed-in user, or 401.

    The token arrives as an HttpOnly cookie from the browser; the Bearer header
    is there for scripts and the Tally scheduled export.
    """
    token = urjapod_session or _bearer(authorization)
    user = security.resolve_session(db, token)
    if user is None:
        raise HTTPException(
            401,
            "not signed in, or the session has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Everything written in this request is attributed to this user.
    audit.set_actor(db, user.username)
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def db_session(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
) -> Session:
    """A session that knows who to blame in the audit log."""
    audit.set_actor(db, user.username)
    return db


DbSession = Annotated[Session, Depends(db_session)]


def current_actor(user: CurrentUser) -> str:
    """The name recorded on audit rows.

    It now comes from the signed-in session rather than a header the caller
    chose, which is the whole point of adding authentication.
    """
    return user.username


Actor = Annotated[str, Depends(current_actor)]


def requires(permission: Permission) -> Callable[..., User]:
    """Dependency factory: ``user: Annotated[User, Depends(requires(P.X))]``.

    A refusal is itself recorded. An accountant repeatedly trying to void
    invoices is something the owner should be able to see.
    """

    def _check(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        user: CurrentUser,
    ) -> User:
        if not permissions.has(user.role, permission):
            audit.record(
                db,
                entity_type="users",
                entity_id=user.id,
                action=AuditAction.PERMISSION_DENIED,
                actor=user.username,
                context={
                    "permission": str(permission),
                    "role": str(user.role),
                    "path": request.url.path,
                    "method": request.method,
                },
            )
            db.commit()
            raise HTTPException(
                403,
                f"your role ({user.role}) cannot {permission}. "
                "Ask the owner to do this, or to change your role.",
            )
        return user

    return _check


def owner_only() -> Callable[..., User]:
    return requires(Permission.SETTINGS_EDIT)


# Kept so the audit log still has a name during first-run setup, before any
# user exists. Not reachable once a user has been created.
def bootstrap_actor() -> str:
    return get_settings().default_actor
