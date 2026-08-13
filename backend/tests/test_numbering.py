"""Numbering: gapless, per financial year, no reuse, no races.

CLAUDE.md constraint #4 in full.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date

import pytest
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Base, DocumentSeries, SeriesKey
from app.services.money import financial_year
from app.services.numbering import NumberingService, SeriesLocked


def test_format_matches_the_agreed_pattern(db):
    service = NumberingService(db)
    number, fy = service.allocate(SeriesKey.TAX_INVOICE, date(2025, 8, 12))

    assert number == "URJ/25-26/0001"
    assert fy == "25-26"


def test_each_document_type_has_its_own_series(db):
    service = NumberingService(db)
    on = date(2025, 8, 12)

    assert service.allocate(SeriesKey.TAX_INVOICE, on)[0] == "URJ/25-26/0001"
    assert service.allocate(SeriesKey.PROFORMA, on)[0] == "URJ/PI/25-26/0001"
    assert service.allocate(SeriesKey.CREDIT_NOTE, on)[0] == "URJ/CN/25-26/0001"
    assert service.allocate(SeriesKey.PURCHASE_ORDER, on)[0] == "URJ/PO/25-26/0001"


def test_sequence_is_gapless(db):
    service = NumberingService(db)
    on = date(2025, 8, 12)
    numbers = [service.allocate(SeriesKey.TAX_INVOICE, on)[0] for _ in range(25)]

    assert numbers == [f"URJ/25-26/{i:04d}" for i in range(1, 26)]
    assert len(set(numbers)) == 25


def test_the_counter_resets_each_financial_year(db):
    service = NumberingService(db)

    assert service.allocate(SeriesKey.TAX_INVOICE, date(2025, 8, 12))[0] == "URJ/25-26/0001"
    assert service.allocate(SeriesKey.TAX_INVOICE, date(2026, 1, 5))[0] == "URJ/25-26/0002"
    # 1 April starts a new year and a new counter.
    assert service.allocate(SeriesKey.TAX_INVOICE, date(2026, 4, 1))[0] == "URJ/26-27/0001"


def test_financial_year_boundaries():
    assert financial_year(date(2025, 3, 31)) == "24-25"
    assert financial_year(date(2025, 4, 1)) == "25-26"
    assert financial_year(date(2026, 3, 31)) == "25-26"
    assert financial_year(date(2026, 4, 1)) == "26-27"


def test_peek_does_not_consume(db):
    service = NumberingService(db)
    on = date(2025, 8, 12)

    assert service.peek(SeriesKey.TAX_INVOICE, on) == "URJ/25-26/0001"
    assert service.peek(SeriesKey.TAX_INVOICE, on) == "URJ/25-26/0001"
    assert service.allocate(SeriesKey.TAX_INVOICE, on)[0] == "URJ/25-26/0001"
    assert service.peek(SeriesKey.TAX_INVOICE, on) == "URJ/25-26/0002"


def test_a_rolled_back_transaction_does_not_burn_a_number(db):
    """The number and the document commit together or not at all."""
    service = NumberingService(db)
    on = date(2025, 8, 12)
    assert service.allocate(SeriesKey.TAX_INVOICE, on)[0] == "URJ/25-26/0001"
    db.commit()

    service.allocate(SeriesKey.TAX_INVOICE, on)
    db.rollback()

    assert NumberingService(db).allocate(SeriesKey.TAX_INVOICE, on)[0] == "URJ/25-26/0002"


def test_the_pattern_is_data_not_code(db):
    """Changing the format must not require a code change."""
    service = NumberingService(db)
    service.allocate(SeriesKey.TAX_INVOICE, date(2025, 8, 12))
    db.commit()

    series = db.execute(
        select(DocumentSeries).where(DocumentSeries.series_key == SeriesKey.TAX_INVOICE)
    ).scalar_one()
    series.pattern = "UEPL-{fy}-{seq:05d}"
    db.commit()

    assert (
        NumberingService(db).allocate(SeriesKey.TAX_INVOICE, date(2025, 8, 12))[0]
        == "UEPL-25-26-00002"
    )


def test_a_locked_series_refuses_to_allocate(db):
    service = NumberingService(db)
    service.allocate(SeriesKey.TAX_INVOICE, date(2025, 8, 12))
    db.commit()

    series = db.execute(select(DocumentSeries)).scalar_one()
    series.is_locked = True
    db.commit()

    with pytest.raises(SeriesLocked):
        NumberingService(db).allocate(SeriesKey.TAX_INVOICE, date(2025, 8, 12))


def test_concurrent_allocation_never_issues_a_duplicate(db):
    """The race the row lock exists to prevent.

    Twelve threads allocate at once. On Postgres the ``SELECT ... FOR UPDATE``
    serialises them; on SQLite ``BEGIN IMMEDIATE`` does. Either way the
    invariant is the same and is asserted the same way: twelve distinct,
    contiguous numbers.
    """
    Base.metadata.create_all(engine)
    on = date(2025, 8, 12)
    thread_count = 12

    allocated: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    start = threading.Barrier(thread_count)

    def worker() -> None:
        start.wait()
        session = SessionLocal()
        try:
            for _attempt in range(30):
                try:
                    # One transaction per allocation: take the number and
                    # commit it, exactly as a document insert would.
                    number, _fy = NumberingService(session).allocate(
                        SeriesKey.TAX_INVOICE, on
                    )
                    session.commit()
                    with lock:
                        allocated.append(number)
                    return
                except Exception as exc:  # SQLite raises on write contention
                    session.rollback()
                    if not isinstance(
                        getattr(exc, "orig", exc), sqlite3.OperationalError
                    ):
                        raise
            raise RuntimeError("gave up after 30 contended attempts")
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"threads failed: {errors[:3]}"
    assert len(allocated) == thread_count
    # No duplicates, and no gaps.
    assert len(set(allocated)) == thread_count
    assert sorted(allocated) == [f"URJ/25-26/{i:04d}" for i in range(1, thread_count + 1)]
