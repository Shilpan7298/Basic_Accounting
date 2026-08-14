"""Passwords, sessions and login.

Password hashing is ``hashlib.scrypt`` from the standard library. That is a
deliberate choice over bcrypt/argon2: it is a memory-hard KDF of the same
class, and it needs no compiled third-party wheel — which matters a great deal
when the whole application has to be frozen into a Windows .exe and a macOS
.dmg that install without a toolchain.

Only the hash of a session token is stored, so a stolen database backup does
not hand over live sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditAction, Role, User, UserSession
from ..models.base import utcnow
from . import audit

# scrypt parameters. n=2**15 is roughly 100 ms on the office-PC class of
# hardware this runs on — slow enough to make guessing expensive, fast enough
# that nobody notices logging in.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 64
_SALT_BYTES = 16

SESSION_LIFETIME = timedelta(hours=12)
MAX_FAILED_ATTEMPTS = 8
LOCKOUT = timedelta(minutes=15)
MIN_PASSWORD_LENGTH = 10


class AuthError(RuntimeError):
    """Login failed. The message is deliberately vague — see :func:`authenticate`."""


class WeakPassword(ValueError):
    pass


# --- passwords -------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_KEY_LEN, maxmem=_SCRYPT_N * _SCRYPT_R * 200,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, expected_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=n, r=r, p=p,
            dklen=len(expected_hex) // 2, maxmem=n * r * 200,
        )
    except (ValueError, TypeError):
        return False
    # Constant time: a timing difference here leaks the hash prefix.
    return hmac.compare_digest(derived.hex(), expected_hex)


def check_password_strength(password: str, username: str = "") -> None:
    """Deliberately modest rules — this is a two-person office, not a bank.

    Length does more work than character-class rules, so that is what is
    enforced, plus the two mistakes people actually make.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    lowered = password.lower()
    if username and username.lower() in lowered:
        raise WeakPassword("password must not contain the username")
    if lowered in {"password12", "1234567890", "qwertyuiop", "urjapod123"}:
        raise WeakPassword("that password is too easy to guess")


# --- users -----------------------------------------------------------------

def create_user(
    db: Session,
    *,
    username: str,
    full_name: str,
    password: str,
    role: Role,
    actor: str,
    email: str | None = None,
    must_change_password: bool = False,
) -> User:
    username = username.strip().lower()
    if not username:
        raise ValueError("username is required")
    if db.execute(select(User).where(User.username == username)).scalars().first():
        raise ValueError(f"a user named {username!r} already exists")
    check_password_strength(password, username)

    user = User(
        username=username,
        full_name=full_name,
        email=email,
        password_hash=hash_password(password),
        role=Role(role),
        must_change_password=must_change_password,
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        entity_type="users",
        entity_id=user.id,
        action=AuditAction.CREATE,
        actor=actor,
        # Never the password, and never the hash.
        after={"username": username, "full_name": full_name, "role": str(role)},
    )
    return user


def set_password(db: Session, user: User, new_password: str, *, actor: str) -> User:
    check_password_strength(new_password, user.username)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.flush()
    audit.record(
        db,
        entity_type="users",
        entity_id=user.id,
        action=AuditAction.UPDATE,
        actor=actor,
        after={"password": "changed"},
    )
    return user


def set_role(db: Session, user: User, role: Role, *, actor: str) -> User:
    before = str(user.role)
    user.role = Role(role)
    db.flush()
    audit.record(
        db,
        entity_type="users",
        entity_id=user.id,
        action=AuditAction.UPDATE,
        actor=actor,
        before={"role": before},
        after={"role": str(role)},
    )
    return user


def owner_count(db: Session) -> int:
    return len(
        db.execute(
            select(User).where(User.role == Role.OWNER, User.is_active.is_(True))
        ).scalars().all()
    )


# --- login -----------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(
    db: Session,
    username: str,
    password: str,
    *,
    user_agent: str | None = None,
    client_ip: str | None = None,
) -> tuple[User, str]:
    """Return ``(user, session_token)`` or raise :class:`AuthError`.

    Every failure raises the same message. Telling an attacker "no such user"
    versus "wrong password" hands them a list of valid usernames.
    """
    username = (username or "").strip().lower()
    user = db.execute(select(User).where(User.username == username)).scalars().first()
    now = utcnow()

    def _reject(reason: str) -> AuthError:
        audit.record(
            db,
            entity_type="users",
            entity_id=user.id if user else "",
            action=AuditAction.LOGIN_FAILED,
            actor=username or "(unknown)",
            context={"reason": reason, "client_ip": client_ip},
        )
        return AuthError("incorrect username or password")

    if user is None:
        # Spend roughly the same time as a real verification, so response time
        # does not reveal whether the username exists.
        verify_password(password, hash_password("timing-equaliser"))
        raise _reject("no such user")

    if not user.is_active:
        raise _reject("account disabled")

    if user.locked_until and user.locked_until > now:
        raise _reject("account temporarily locked")

    if not verify_password(password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + LOCKOUT
            user.failed_attempts = 0
        db.flush()
        raise _reject("bad password")

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now

    token = secrets.token_urlsafe(48)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=_token_hash(token),
            expires_at=now + SESSION_LIFETIME,
            user_agent=(user_agent or "")[:300] or None,
            client_ip=client_ip,
        )
    )
    db.flush()
    audit.record(
        db,
        entity_type="users",
        entity_id=user.id,
        action=AuditAction.LOGIN,
        actor=user.username,
        context={"client_ip": client_ip},
    )
    return user, token


def resolve_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    session = db.execute(
        select(UserSession).where(UserSession.token_hash == _token_hash(token))
    ).scalars().first()
    if session is None or not session.is_live():
        return None
    user = session.user
    if user is None or not user.is_active:
        return None
    return user


def revoke_session(db: Session, token: str | None, *, actor: str) -> None:
    if not token:
        return
    session = db.execute(
        select(UserSession).where(UserSession.token_hash == _token_hash(token))
    ).scalars().first()
    if session is None or session.revoked_at is not None:
        return
    session.revoked_at = utcnow()
    db.flush()
    audit.record(
        db,
        entity_type="users",
        entity_id=session.user_id,
        action=AuditAction.LOGOUT,
        actor=actor,
    )


def revoke_all_sessions(db: Session, user_id: str) -> int:
    """Used when a password or role changes — old sessions must not outlive it."""
    now = utcnow()
    sessions = db.execute(
        select(UserSession).where(
            UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
        )
    ).scalars().all()
    for session in sessions:
        session.revoked_at = now
    db.flush()
    return len(sessions)


def purge_expired_sessions(db: Session, older_than: datetime | None = None) -> int:
    cutoff = older_than or (datetime.now(timezone.utc) - timedelta(days=30))
    stale = db.execute(select(UserSession).where(UserSession.expires_at < cutoff)).scalars().all()
    for session in stale:
        db.delete(session)
    db.flush()
    return len(stale)
