from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Money
from .base import Base, TimestampMixin, UUIDPkMixin, new_uuid
from .enums import OfferStatus, PurchaseOrderStatus
from .json_type import JSONText


class SupplierOffer(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "supplier_offers"

    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    offer_ref: Mapped[str | None] = mapped_column(String(80))
    offer_date: Mapped[date | None] = mapped_column(Date)
    validity_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    payment_terms_text: Mapped[str | None] = mapped_column(Text)
    freight_terms: Mapped[str | None] = mapped_column(Text)
    warranty_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[OfferStatus] = mapped_column(
        String(20), nullable=False, default=OfferStatus.DRAFT
    )
    source_document_id: Mapped[str | None] = mapped_column(String(36))
    extraction_id: Mapped[str | None] = mapped_column(String(36))
    notes: Mapped[str | None] = mapped_column(Text)

    supplier = relationship("Supplier", lazy="joined")
    lines = relationship(
        "SupplierOfferLine",
        back_populates="offer",
        cascade="all, delete-orphan",
        order_by="SupplierOfferLine.line_no",
        lazy="selectin",
    )


class SupplierOfferLine(UUIDPkMixin, TimestampMixin, Base):
    """The quoted rate and the negotiated rate sit side by side on purpose —
    the delta is the thing purchasing actually wants to see."""

    __tablename__ = "supplier_offer_lines"

    supplier_offer_id: Mapped[str] = mapped_column(ForeignKey("supplier_offers.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    qty: Mapped[Decimal] = mapped_column(Money, nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="NOS")
    quoted_unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    negotiated_unit_price: Mapped[Decimal | None] = mapped_column(Money)
    discount_percent: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    offer = relationship("SupplierOffer", back_populates="lines")

    @property
    def effective_unit_price(self) -> Decimal:
        return (
            self.negotiated_unit_price
            if self.negotiated_unit_price is not None
            else self.quoted_unit_price
        )


class PurchaseOrder(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("number", name="uq_purchase_orders_number"),)

    number: Mapped[str] = mapped_column(String(60), nullable=False)
    financial_year: Mapped[str] = mapped_column(String(7), nullable=False)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    supplier_offer_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_offers.id"))
    po_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_due_date: Mapped[date | None] = mapped_column(Date)
    delivery_location: Mapped[str | None] = mapped_column(Text)
    payment_terms_text: Mapped[str | None] = mapped_column(Text)
    freight_terms: Mapped[str | None] = mapped_column(Text)
    warranty_text: Mapped[str | None] = mapped_column(Text)

    place_of_supply_state_code: Mapped[str | None] = mapped_column(String(2))
    is_reverse_charge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    taxable_value: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    cgst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    sgst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    igst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    cess_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    round_off: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_in_words: Mapped[str | None] = mapped_column(String(400))

    status: Mapped[PurchaseOrderStatus] = mapped_column(
        String(20), nullable=False, default=PurchaseOrderStatus.DRAFT
    )
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    template_name: Mapped[str | None] = mapped_column(String(120))
    template_version: Mapped[int | None] = mapped_column(Integer)
    render_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)
    docx_path: Mapped[str | None] = mapped_column(String(600))
    pdf_path: Mapped[str | None] = mapped_column(String(600))
    guid: Mapped[str] = mapped_column(String(36), nullable=False, default=new_uuid)
    notes: Mapped[str | None] = mapped_column(Text)

    supplier = relationship("Supplier", lazy="joined")
    lines = relationship(
        "PurchaseOrderLine",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderLine.line_no",
        lazy="selectin",
    )


class PurchaseOrderLine(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "purchase_order_lines"

    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    qty: Mapped[Decimal] = mapped_column(Money, nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="NOS")
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    taxable_value: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    gst_rate_percent: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    cgst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    sgst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    igst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    cess_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    purchase_order = relationship("PurchaseOrder", back_populates="lines")
