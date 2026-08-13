from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..services import audit


def current_actor(x_actor: Annotated[str | None, Header()] = None) -> str:
    """Who is making the change.

    There is no auth provider (CLAUDE.md → Do not), so the actor arrives as a
    header from the front end. It is still recorded on every audit row — the
    log needs a name whether or not a password was involved.
    """
    return x_actor or get_settings().default_actor


def db_session(
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[str, Depends(current_actor)],
) -> Session:
    """A session that knows who to blame in the audit log."""
    audit.set_actor(db, actor)
    return db


DbSession = Annotated[Session, Depends(db_session)]
Actor = Annotated[str, Depends(current_actor)]
