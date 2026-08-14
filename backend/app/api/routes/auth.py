"""Login, logout, and user administration.

First-run bootstrap lives here too: an empty database has no users, so exactly
one unauthenticated call is allowed — creating the first owner. After that the
endpoint refuses, which is what stops it becoming a back door.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_db
from ...models import Role, User
from ...schemas.api import ORMModel
from ...services import permissions, security
from ...services.permissions import Permission
from ..deps import SESSION_COOKIE, CurrentUser, DbSession, requires

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(ORMModel):
    id: str
    username: str
    full_name: str
    email: str | None
    role: str
    is_active: bool
    must_change_password: bool


class MeOut(BaseModel):
    user: UserOut
    permissions: list[str]


class CreateUserIn(BaseModel):
    username: str
    full_name: str
    password: str = Field(min_length=security.MIN_PASSWORD_LENGTH)
    role: str = "accountant"
    email: str | None = None
    must_change_password: bool = True


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=security.MIN_PASSWORD_LENGTH)


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=security.MIN_PASSWORD_LENGTH)


class SetRoleIn(BaseModel):
    role: str


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,          # not readable from JavaScript
        samesite="lax",         # blunts cross-site request forgery
        secure=settings.session_cookie_secure,
        max_age=int(security.SESSION_LIFETIME.total_seconds()),
        path="/",
    )


def _me(user: User) -> MeOut:
    return MeOut(
        user=UserOut.model_validate(user),
        permissions=sorted(str(p) for p in permissions.permissions_for(user.role)),
    )


# --- first run -------------------------------------------------------------

@router.get("/setup-required")
def setup_required(db: Annotated[Session, Depends(get_db)]):
    """Lets the login screen offer 'create the first account' on a fresh install."""
    return {"setup_required": db.execute(select(User)).scalars().first() is None}


@router.post("/bootstrap", response_model=MeOut, status_code=201)
def bootstrap_owner(
    payload: CreateUserIn,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """Create the first owner. Only works while no user exists."""
    if db.execute(select(User)).scalars().first() is not None:
        raise HTTPException(409, "setup has already been completed; sign in instead")

    try:
        user = security.create_user(
            db,
            username=payload.username,
            full_name=payload.full_name,
            password=payload.password,
            role=Role.OWNER,
            email=payload.email,
            actor="first-run-setup",
            must_change_password=False,
        )
    except (ValueError, security.WeakPassword) as exc:
        raise HTTPException(422, str(exc)) from exc

    _, token = security.authenticate(
        db, user.username, payload.password,
        user_agent=request.headers.get("user-agent"), client_ip=_client_ip(request),
    )
    db.commit()
    _set_cookie(response, token)
    return _me(user)


# --- session ---------------------------------------------------------------

@router.post("/login", response_model=MeOut)
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        user, token = security.authenticate(
            db, payload.username, payload.password,
            user_agent=request.headers.get("user-agent"), client_ip=_client_ip(request),
        )
    except security.AuthError as exc:
        db.commit()  # keep the failed-login audit row
        raise HTTPException(401, str(exc)) from exc
    db.commit()
    _set_cookie(response, token)
    return _me(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession, user: CurrentUser):
    token = request.cookies.get(SESSION_COOKIE)
    security.revoke_session(db, token, actor=user.username)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser):
    return _me(user)


@router.post("/change-password")
def change_password(payload: ChangePasswordIn, db: DbSession, user: CurrentUser):
    if not security.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(401, "current password is incorrect")
    try:
        security.set_password(db, user, payload.new_password, actor=user.username)
    except security.WeakPassword as exc:
        raise HTTPException(422, str(exc)) from exc
    # Other sessions must not outlive the old password.
    security.revoke_all_sessions(db, user.id)
    db.commit()
    return {"ok": True, "note": "all sessions signed out; sign in again"}


# --- user administration (owner only) --------------------------------------

@router.get("/users", response_model=list[UserOut])
def list_users(
    db: DbSession,
    _user: Annotated[User, Depends(requires(Permission.USER_MANAGE))],
):
    return list(db.execute(select(User).order_by(User.username)).scalars())


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: CreateUserIn,
    db: DbSession,
    actor: Annotated[User, Depends(requires(Permission.USER_MANAGE))],
):
    try:
        role = Role(payload.role)
    except ValueError as exc:
        raise HTTPException(422, f"unknown role {payload.role!r}") from exc
    try:
        user = security.create_user(
            db,
            username=payload.username,
            full_name=payload.full_name,
            password=payload.password,
            role=role,
            email=payload.email,
            actor=actor.username,
            must_change_password=payload.must_change_password,
        )
    except (ValueError, security.WeakPassword) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-password", response_model=UserOut)
def reset_password(
    user_id: str,
    payload: ResetPasswordIn,
    db: DbSession,
    actor: Annotated[User, Depends(requires(Permission.USER_MANAGE))],
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    try:
        security.set_password(db, target, payload.new_password, actor=actor.username)
    except security.WeakPassword as exc:
        raise HTTPException(422, str(exc)) from exc
    target.must_change_password = True
    security.revoke_all_sessions(db, target.id)
    db.commit()
    db.refresh(target)
    return target


@router.post("/users/{user_id}/role", response_model=UserOut)
def set_role(
    user_id: str,
    payload: SetRoleIn,
    db: DbSession,
    actor: Annotated[User, Depends(requires(Permission.USER_MANAGE))],
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    try:
        role = Role(payload.role)
    except ValueError as exc:
        raise HTTPException(422, f"unknown role {payload.role!r}") from exc

    # Locking yourself out of your own books is not a recoverable mistake.
    if target.role == Role.OWNER and role != Role.OWNER and security.owner_count(db) <= 1:
        raise HTTPException(409, "this is the only owner; promote someone else first")

    security.set_role(db, target, role, actor=actor.username)
    security.revoke_all_sessions(db, target.id)
    db.commit()
    db.refresh(target)
    return target


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate(
    user_id: str,
    db: DbSession,
    actor: Annotated[User, Depends(requires(Permission.USER_MANAGE))],
):
    """Users are deactivated, never deleted — their name is on audit rows."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.role == Role.OWNER and security.owner_count(db) <= 1:
        raise HTTPException(409, "this is the only owner; promote someone else first")
    target.is_active = False
    security.revoke_all_sessions(db, target.id)
    db.commit()
    db.refresh(target)
    return target
