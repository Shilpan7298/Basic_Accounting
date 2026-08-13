"""Document numbering.

The contract (CLAUDE.md constraint #4):

* separate series per document type, per financial year (Apr–Mar)
* sequential, gapless, never reused
* allocated inside the *same* transaction that creates the document, under a
  row lock, so two concurrent requests cannot receive the same number

The format lives in ``document_series.pattern`` as a ``str.format`` template,
so changing ``URJ/{fy}/{seq:04d}`` to something else is a data edit, not a
code change.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import supports_row_locks
from ..models import DocumentSeries, SeriesKey
from .money import financial_year

# The formats confirmed by the business. Seeded per financial year on demand.
DEFAULT_PATTERNS: dict[SeriesKey, str] = {
    SeriesKey.TAX_INVOICE: "URJ/{fy}/{seq:04d}",
    SeriesKey.PROFORMA: "URJ/PI/{fy}/{seq:04d}",
    SeriesKey.CREDIT_NOTE: "URJ/CN/{fy}/{seq:04d}",
    SeriesKey.PURCHASE_ORDER: "URJ/PO/{fy}/{seq:04d}",
}


class SeriesLocked(RuntimeError):
    pass


class NumberingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -- internals ---------------------------------------------------------
    def _get_or_create_series(self, series_key: SeriesKey, fy: str) -> DocumentSeries:
        stmt = select(DocumentSeries).where(
            DocumentSeries.series_key == series_key,
            DocumentSeries.financial_year == fy,
        )
        if supports_row_locks(self.db):
            # Postgres: hold the row until this transaction commits, so a
            # concurrent allocation blocks here instead of duplicating a number.
            stmt = stmt.with_for_update()
        series = self.db.execute(stmt).scalar_one_or_none()
        if series is not None:
            return series

        series = DocumentSeries(
            series_key=series_key,
            financial_year=fy,
            pattern=DEFAULT_PATTERNS[series_key],
            next_seq=1,
        )
        self.db.add(series)
        try:
            self.db.flush()
        except Exception:
            # Another transaction created it between our SELECT and INSERT.
            self.db.rollback()
            series = self.db.execute(stmt).scalar_one()
        return series

    # -- public API --------------------------------------------------------
    def peek(self, series_key: SeriesKey, on: date) -> str:
        """What the next number *would* be. Does not consume it."""
        fy = financial_year(on)
        series = self.db.execute(
            select(DocumentSeries).where(
                DocumentSeries.series_key == series_key,
                DocumentSeries.financial_year == fy,
            )
        ).scalar_one_or_none()
        pattern = series.pattern if series else DEFAULT_PATTERNS[series_key]
        seq = series.next_seq if series else 1
        return pattern.format(fy=fy, seq=seq)

    def allocate(self, series_key: SeriesKey, on: date) -> tuple[str, str]:
        """Consume and return ``(number, financial_year)``.

        Must be called inside the transaction that inserts the document. The
        caller commits; if the caller rolls back, the sequence rolls back with
        it and no number is burnt.
        """
        fy = financial_year(on)
        series = self._get_or_create_series(series_key, fy)
        if series.is_locked:
            raise SeriesLocked(f"series {series_key} for FY {fy} is locked")

        number = series.pattern.format(fy=fy, seq=series.next_seq)
        series.next_seq += 1
        self.db.flush()
        return number, fy
