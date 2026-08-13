"""The schemas every extractor must satisfy.

These validators run on *every* adapter's output — cloud or local, big model
or small — so a 7B model on a laptop is held to exactly the same bar as a
frontier one. That is what makes the "in a year this runs offline" assumption
survivable.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..services.gstin import is_valid as gstin_is_valid
from ..services.money import to_decimal

# dd/mm/yyyy first, always. mm/dd would silently corrupt every Indian PO date.
_DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%d %b %y", "%d-%b-%y",
    "%b %d, %Y", "%B %d, %Y",
)

_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:p\.?\s?o\.?|purchase\s+order|order|ref(?:erence)?|offer|quotation|quote)"
    r"\s*(?:no\.?|number|#|ref\.?)?\s*[:\-–]?\s*",
    re.I,
)


def parse_date(value: Any) -> date | None:
    """Parse Indian-format dates. Returns ``None`` rather than a wrong date."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        # Two-digit years: a PO is not from 1925.
        if parsed.year < 100:
            parsed = parsed.replace(year=parsed.year + 2000)
        return parsed
    return None


def strip_label(value: str | None) -> str | None:
    """'PO No.: 4500123' → '4500123'."""
    if not value:
        return None
    cleaned = _LABEL_PREFIX_RE.sub("", str(value)).strip().strip(":-–").strip()
    return cleaned or None


class ExtractedLine(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str
    hsn_code: str | None = None
    qty: Decimal = Decimal("1")
    uom: str = "NOS"
    unit_price: Decimal = Decimal("0")
    discount_percent: Decimal = Decimal("0")
    line_total: Decimal | None = None

    @field_validator("qty", "unit_price", "discount_percent", "line_total", mode="before")
    @classmethod
    def _money(cls, v: Any) -> Any:
        return to_decimal(v) if v is not None else None

    @field_validator("hsn_code", mode="before")
    @classmethod
    def _hsn(cls, v: Any) -> str | None:
        if not v:
            return None
        digits = re.sub(r"\D", "", str(v))
        # HSN is 4, 6 or 8 digits; SAC is 6. Anything else is model noise.
        return digits if len(digits) in (4, 6, 8) else None

    @field_validator("uom", mode="before")
    @classmethod
    def _uom(cls, v: Any) -> str:
        return (str(v).strip().upper() or "NOS") if v else "NOS"

    @model_validator(mode="after")
    def _fill_line_total(self) -> ExtractedLine:
        if self.line_total is None:
            gross = self.qty * self.unit_price
            self.line_total = gross - (gross * self.discount_percent / Decimal("100"))
        return self


class _PartyDocBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    currency: str = "INR"
    payment_terms_text: str | None = None
    lines: list[ExtractedLine] = Field(default_factory=list)
    subtotal: Decimal | None = None
    grand_total: Decimal | None = None
    notes: str | None = None

    @field_validator("subtotal", "grand_total", mode="before")
    @classmethod
    def _money(cls, v: Any) -> Any:
        return to_decimal(v) if v is not None else None

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v: Any) -> str:
        return (str(v).strip().upper()[:3] or "INR") if v else "INR"


class CustomerPOExtraction(_PartyDocBase):
    """What a customer purchase order extraction must return."""

    schema_name: Literal["customer_po"] = "customer_po"

    customer_name: str
    customer_gstin: str | None = None
    customer_address: str | None = None
    po_number: str
    po_date: date | None = None
    delivery_due_date: date | None = None
    delivery_location: str | None = None

    @field_validator("po_date", "delivery_due_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> Any:
        return parse_date(v)

    @field_validator("po_number", mode="before")
    @classmethod
    def _po_number(cls, v: Any) -> Any:
        return strip_label(v) or v

    @field_validator("customer_gstin", mode="before")
    @classmethod
    def _gstin(cls, v: Any) -> str | None:
        # A GSTIN that fails the checksum is dropped, not saved wrong — a bad
        # state code silently flips the whole invoice's tax split.
        if not v:
            return None
        candidate = str(v).strip().upper().replace(" ", "")
        return candidate if gstin_is_valid(candidate) else None


class SupplierOfferExtraction(_PartyDocBase):
    """What a supplier offer / quotation extraction must return."""

    schema_name: Literal["supplier_offer"] = "supplier_offer"

    supplier_name: str
    supplier_gstin: str | None = None
    supplier_address: str | None = None
    offer_ref: str | None = None
    offer_date: date | None = None
    validity_date: date | None = None
    lead_time_days: int | None = None
    freight_terms: str | None = None
    warranty_text: str | None = None

    @field_validator("offer_date", "validity_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> Any:
        return parse_date(v)

    @field_validator("offer_ref", mode="before")
    @classmethod
    def _ref(cls, v: Any) -> Any:
        return strip_label(v)

    @field_validator("supplier_gstin", mode="before")
    @classmethod
    def _gstin(cls, v: Any) -> str | None:
        if not v:
            return None
        candidate = str(v).strip().upper().replace(" ", "")
        return candidate if gstin_is_valid(candidate) else None

    @field_validator("lead_time_days", mode="before")
    @classmethod
    def _lead_time(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        match = re.search(r"\d+", str(v))
        return int(match.group()) if match else None


SCHEMAS: dict[str, type[BaseModel]] = {
    "customer_po": CustomerPOExtraction,
    "supplier_offer": SupplierOfferExtraction,
}


def schema_for(name: str) -> type[BaseModel]:
    try:
        return SCHEMAS[name]
    except KeyError as exc:
        raise ValueError(f"unknown extraction schema {name!r}") from exc
