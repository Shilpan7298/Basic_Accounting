from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Money
from .base import Base, TimestampMixin, UUIDPkMixin


class Company(UUIDPkMixin, TimestampMixin, Base):
    """The single Urjapod row. ``state_code`` drives every intra/inter decision."""

    __tablename__ = "company"

    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str] = mapped_column(String(2), nullable=False, default="24")
    pan: Mapped[str | None] = mapped_column(String(10))
    cin: Mapped[str | None] = mapped_column(String(30))
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state_name: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40))
    bank_name: Mapped[str | None] = mapped_column(String(160))
    bank_account_no: Mapped[str | None] = mapped_column(String(40))
    bank_ifsc: Mapped[str | None] = mapped_column(String(20))
    bank_branch: Mapped[str | None] = mapped_column(String(160))
    logo_path: Mapped[str | None] = mapped_column(String(400))
    fy_start_month: Mapped[int] = mapped_column(default=4, nullable=False)


class Customer(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_name", "name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str | None] = mapped_column(String(2))
    place_of_supply_state_code: Mapped[str | None] = mapped_column(String(2))
    billing_address: Mapped[str | None] = mapped_column(Text)
    shipping_address: Mapped[str | None] = mapped_column(Text)
    state_name: Mapped[str | None] = mapped_column(String(100))
    payment_terms_default: Mapped[str | None] = mapped_column(Text)
    is_sez: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_export: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def effective_pos_state_code(self) -> str | None:
        return self.place_of_supply_state_code or self.state_code


class Supplier(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str | None] = mapped_column(String(2))
    state_name: Mapped[str | None] = mapped_column(String(100))
    billing_address: Mapped[str | None] = mapped_column(Text)
    msme_registration_no: Mapped[str | None] = mapped_column(String(40))
    payment_terms_default: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Item(UUIDPkMixin, TimestampMixin, Base):
    """Nothing can be invoiced without an HSN, so ``hsn_code`` is NOT NULL."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("sku", name="uq_items_sku"),)

    sku: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_code: Mapped[str] = mapped_column(String(10), nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="NOS")
    default_unit_price: Mapped[Decimal | None] = mapped_column(Money)
    is_service: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spec_json: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TaxRate(UUIDPkMixin, TimestampMixin, Base):
    """GST rate for an HSN, valid over a date window.

    Lookup is always by *document date*, which is what makes an old invoice
    reprint at the rate that applied when it was raised.
    """

    __tablename__ = "tax_rates"
    __table_args__ = (Index("ix_tax_rates_hsn_from", "hsn_code", "effective_from"),)

    hsn_code: Mapped[str] = mapped_column(String(10), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    rate_percent: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cess_percent: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    description: Mapped[str | None] = mapped_column(String(200))
