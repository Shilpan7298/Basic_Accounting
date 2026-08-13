"""Engine, session and the money type.

Two things here are load-bearing:

* :class:`Money` guarantees a ``Decimal`` comes back out of the database on
  both Postgres and SQLite. SQLite has no NUMERIC affinity, so without the
  type decorator a test could pass on SQLite with floats and then lose
  paise in production.
* The SQLite ``BEGIN IMMEDIATE`` listener plus :func:`supports_row_locks` are
  the only places that know how write serialisation differs between the two
  backends, so ``NumberingService`` doesn't have to.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import get_settings

MONEY_PRECISION = 18
MONEY_SCALE = 4


class Money(TypeDecorator):
    """NUMERIC(18,4) that always round-trips as ``Decimal``."""

    impl = Numeric(MONEY_PRECISION, MONEY_SCALE, asdecimal=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            # SQLite would hand back a float; store the exact text instead.
            return dialect.type_descriptor(_SqliteDecimal())
        return dialect.type_descriptor(Numeric(MONEY_PRECISION, MONEY_SCALE, asdecimal=True))

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        return Decimal(str(value))

    def process_result_value(self, value: Any, dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))


class _SqliteDecimal(TypeDecorator):
    """Store decimals as zero-padded text on SQLite so ORDER BY still works."""

    impl = Numeric(MONEY_PRECISION, MONEY_SCALE, asdecimal=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        from sqlalchemy import String

        return dialect.type_descriptor(String(40))

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return f"{Decimal(str(value)):+021.4f}"

    def process_result_value(self, value: Any, dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))


_settings = get_settings()

_connect_args: dict[str, Any] = {}
if _settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}

engine: Engine = create_engine(
    _settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
        # Hand transaction control to SQLAlchemy. pysqlite otherwise defers
        # BEGIN until the first write, which leaves a read-modify-write (the
        # numbering sequence) unprotected: two sessions read the same
        # ``next_seq`` and both commit, issuing a duplicate invoice number.
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin_immediate(conn):  # pragma: no cover - trivial
        # BEGIN IMMEDIATE takes the write lock up front, so concurrent
        # allocations serialise exactly as ``SELECT ... FOR UPDATE`` makes
        # them serialise on Postgres. Blunter than a row lock, but SQLite is
        # the dev/test backend and correctness beats concurrency here.
        conn.exec_driver_sql("BEGIN IMMEDIATE")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def supports_row_locks(db: Session) -> bool:
    """Postgres locks the series row; SQLite serialises the whole transaction
    via the BEGIN IMMEDIATE listener above. Same invariant, different lever."""
    return db.bind.dialect.name != "sqlite"
