from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Money
from .base import Base, TimestampMixin, UUIDPkMixin, new_uuid
from .enums import (
    DocType,
    InvoiceStatus,
    MilestoneStatus,
    MilestoneTrigger,
    OrderStatus,
    SeriesKey,
)
from .json_type import JSONText


class DocumentSeries(UUIDPkMixin, TimestampMixin, Base):
    """One counter per (series, financial year).

    ``pattern`` is a format string rather than an f-string in code, so the
    number format is data the business can change.
    """

    __tablename__ = "document_series"
    __table_args__ = (UniqueConstraint("series_key", "financial_year", name="uq_series_fy"),)

    series_key: Mapped[SeriesKey] = mapped_column(String(30), nullable=False)
    financial_year: Mapped[str] = mapped_column(String(7), nullable=False)  # "25-26"
    pattern: Mapped[str] = mapped_column(String(120), nullable=False)
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SalesOrder(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "sales_orders"
    __table_args__ = (Index("ix_sales_orders_status", "status"),)

    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    customer_po_number: Mapped[str] = mapped_column(String(80), nullable=False)
    customer_po_date: Mapped[date] = mapped_column(Date, nullable=False)
    order_value: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    delivery_due_date: Mapped[date | None] = mapped_column(Date)
    delivery_location: Mapped[str | None] = mapped_column(Text)
    payment_terms_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[OrderStatus] = mapped_column(
        String(20), nullable=False, default=OrderStatus.DRAFT
    )
    source_document_id: Mapped[str | None] = mapped_column(String(36))
    extraction_id: Mapped[str | None] = mapped_column(String(36))
    notes: Mapped[str | None] = mapped_column(Text)

    customer = relationship("Customer", lazy="joined")
    lines = relationship(
        "SalesOrderLine",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="SalesOrderLine.line_no",
        lazy="selectin",
    )
    milestones = relationship(
        "PaymentMilestone",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="PaymentMilestone.seq",
        lazy="selectin",
    )


class SalesOrderLine(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "sales_order_lines"

    sales_order_id: Mapped[str] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    qty: Mapped[Decimal] = mapped_column(Money, nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="NOS")
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    order = relationship("SalesOrder", back_populates="lines")


class PaymentMilestone(UUIDPkMixin, TimestampMixin, Base):
    """A slice of the order value that becomes one proforma invoice."""

    __tablename__ = "payment_milestones"

    sales_order_id: Mapped[str] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger_event: Mapped[MilestoneTrigger] = mapped_column(String(30), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Money, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    net_days: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[MilestoneStatus] = mapped_column(
        String(20), nullable=False, default=MilestoneStatus.PENDING
    )
    proforma_invoice_id: Mapped[str | None] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="parsed")

    order = relationship("SalesOrder", back_populates="milestones")


class Invoice(UUIDPkMixin, TimestampMixin, Base):
    """Proforma, tax invoice and credit note share this table.

    They differ in ``doc_type`` and in which series allocated the number.
    Keeping them together means every report has exactly one place to look,
    and the "proformas are not tax documents" rule is one WHERE clause that
    can be tested rather than a convention spread across queries.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("number", name="uq_invoices_number"),
        Index("ix_invoices_type_date", "doc_type", "invoice_date"),
    )

    number: Mapped[str] = mapped_column(String(60), nullable=False)
    series_key: Mapped[SeriesKey] = mapped_column(String(30), nullable=False)
    financial_year: Mapped[str] = mapped_column(String(7), nullable=False)
    doc_type: Mapped[DocType] = mapped_column(String(20), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)

    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    sales_order_id: Mapped[str | None] = mapped_column(ForeignKey("sales_orders.id"))
    payment_milestone_id: Mapped[str | None] = mapped_column(String(36))

    place_of_supply_state_code: Mapped[str | None] = mapped_column(String(2))
    is_reverse_charge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_export: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lut_no: Mapped[str | None] = mapped_column(String(60))

    taxable_value: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    cgst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    sgst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    igst_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    cess_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    round_off: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_in_words: Mapped[str | None] = mapped_column(String(400))

    status: Mapped[InvoiceStatus] = mapped_column(
        String(20), nullable=False, default=InvoiceStatus.DRAFT
    )
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    template_name: Mapped[str | None] = mapped_column(String(120))
    template_version: Mapped[int | None] = mapped_column(Integer)
    # The exact dict handed to the template, frozen. This is what makes a
    # reprint reproduce the original rather than today's data.
    render_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONText)
    pdf_path: Mapped[str | None] = mapped_column(String(600))
    docx_path: Mapped[str | None] = mapped_column(String(600))

    guid: Mapped[str] = mapped_column(String(36), nullable=False, default=new_uuid)
    notes: Mapped[str | None] = mapped_column(Text)

    # E-invoicing placeholders — deliberately unused for now (see CLAUDE.md).
    irn: Mapped[str | None] = mapped_column(String(80))
    ack_no: Mapped[str | None] = mapped_column(String(40))
    qr_payload: Mapped[str | None] = mapped_column(Text)
    eway_bill_no: Mapped[str | None] = mapped_column(String(30))

    customer = relationship("Customer", lazy="joined")
    lines = relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.line_no",
        lazy="selectin",
    )

    @property
    def is_tax_document(self) -> bool:
        return self.doc_type in (DocType.TAX_INVOICE, DocType.CREDIT_NOTE)


class InvoiceLine(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), nullable=False)
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

    invoice = relationship("Invoice", back_populates="lines")


class Receipt(UUIDPkMixin, TimestampMixin, Base):
    """Money actually in the bank."""

    __tablename__ = "receipts"

    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    sales_order_id: Mapped[str | None] = mapped_column(ForeignKey("sales_orders.id"))
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"))
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False, default="NEFT")
    reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
