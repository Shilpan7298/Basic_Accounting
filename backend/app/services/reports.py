"""Management reports and their Excel export.

Every report here filters proformas out of anything GST-shaped, because a
proforma carries no liability. The sales register lists them separately so
the office can still see what has been billed for collection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    DocType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    OrderStatus,
    PurchaseOrder,
    PurchaseOrderStatus,
    Receipt,
    SalesOrder,
)
from .money import q2
from .orders import days_to_delivery

ZERO = Decimal("0")


@dataclass
class Report:
    title: str
    columns: list[str]
    rows: list[list[Any]]
    totals: dict[str, Decimal] | None = None


def sales_register(db: Session, date_from: date, date_to: date) -> Report:
    invoices = db.execute(
        select(Invoice)
        .where(
            Invoice.invoice_date >= date_from,
            Invoice.invoice_date <= date_to,
            Invoice.doc_type == DocType.TAX_INVOICE,
            Invoice.status != InvoiceStatus.CANCELLED,
        )
        .order_by(Invoice.invoice_date, Invoice.number)
    ).scalars().all()

    rows = [
        [
            inv.number,
            inv.invoice_date.isoformat(),
            inv.customer.name if inv.customer else "",
            inv.customer.gstin if inv.customer else "",
            inv.place_of_supply_state_code or "",
            Decimal(inv.taxable_value),
            Decimal(inv.cgst_amount),
            Decimal(inv.sgst_amount),
            Decimal(inv.igst_amount),
            Decimal(inv.cess_amount),
            Decimal(inv.round_off),
            Decimal(inv.grand_total),
        ]
        for inv in invoices
    ]
    totals = {
        "taxable_value": q2(sum((r[5] for r in rows), ZERO)),
        "cgst": q2(sum((r[6] for r in rows), ZERO)),
        "sgst": q2(sum((r[7] for r in rows), ZERO)),
        "igst": q2(sum((r[8] for r in rows), ZERO)),
        "grand_total": q2(sum((r[11] for r in rows), ZERO)),
    }
    return Report(
        title=f"Sales Register {date_from} to {date_to}",
        columns=[
            "Invoice No", "Date", "Customer", "GSTIN", "POS", "Taxable",
            "CGST", "SGST", "IGST", "Cess", "Round Off", "Total",
        ],
        rows=rows,
        totals=totals,
    )


def hsn_summary(db: Session, date_from: date, date_to: date) -> Report:
    """GSTR-1 shaped: per HSN and rate, across issued tax invoices only."""
    rows_raw = db.execute(
        select(InvoiceLine, Invoice)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(
            Invoice.invoice_date >= date_from,
            Invoice.invoice_date <= date_to,
            Invoice.doc_type == DocType.TAX_INVOICE,
            Invoice.status != InvoiceStatus.CANCELLED,
        )
    ).all()

    buckets: dict[tuple[str, Decimal], dict[str, Decimal]] = {}
    for line, _invoice in rows_raw:
        key = (line.hsn_code or "", q2(Decimal(line.gst_rate_percent)))
        bucket = buckets.setdefault(
            key,
            {"qty": ZERO, "taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "cess": ZERO},
        )
        bucket["qty"] += Decimal(line.qty)
        bucket["taxable"] += Decimal(line.taxable_value)
        bucket["cgst"] += Decimal(line.cgst_amount)
        bucket["sgst"] += Decimal(line.sgst_amount)
        bucket["igst"] += Decimal(line.igst_amount)
        bucket["cess"] += Decimal(line.cess_amount)

    rows = [
        [hsn, rate, q2(b["qty"]), q2(b["taxable"]), q2(b["cgst"]), q2(b["sgst"]),
         q2(b["igst"]), q2(b["cess"]),
         q2(b["taxable"] + b["cgst"] + b["sgst"] + b["igst"] + b["cess"])]
        for (hsn, rate), b in sorted(buckets.items())
    ]
    return Report(
        title=f"HSN Summary {date_from} to {date_to}",
        columns=["HSN", "Rate %", "Qty", "Taxable", "CGST", "SGST", "IGST", "Cess", "Total"],
        rows=rows,
        totals={"taxable_value": q2(sum((r[3] for r in rows), ZERO))},
    )


def purchase_register(db: Session, date_from: date, date_to: date) -> Report:
    pos = db.execute(
        select(PurchaseOrder)
        .where(
            PurchaseOrder.po_date >= date_from,
            PurchaseOrder.po_date <= date_to,
            PurchaseOrder.status != PurchaseOrderStatus.CANCELLED,
        )
        .order_by(PurchaseOrder.po_date, PurchaseOrder.number)
    ).scalars().all()

    rows = [
        [
            po.number, po.po_date.isoformat(),
            po.supplier.name if po.supplier else "",
            po.supplier.gstin if po.supplier else "",
            Decimal(po.taxable_value),
            Decimal(po.cgst_amount) + Decimal(po.sgst_amount) + Decimal(po.igst_amount),
            Decimal(po.grand_total),
            str(po.status),
        ]
        for po in pos
    ]
    return Report(
        title=f"Purchase Register {date_from} to {date_to}",
        columns=["PO No", "Date", "Supplier", "GSTIN", "Taxable", "Tax", "Total", "Status"],
        rows=rows,
        totals={"grand_total": q2(sum((r[6] for r in rows), ZERO))},
    )


def outstanding_receivables(db: Session, as_on: date | None = None) -> Report:
    """Issued tax invoices less receipts, oldest first."""
    on = as_on or date.today()
    invoices = db.execute(
        select(Invoice).where(
            Invoice.doc_type == DocType.TAX_INVOICE,
            Invoice.status == InvoiceStatus.ISSUED,
            Invoice.invoice_date <= on,
        ).order_by(Invoice.invoice_date)
    ).scalars().all()

    receipts_by_invoice: dict[str, Decimal] = {}
    receipts_by_order: dict[str, Decimal] = {}
    for receipt in db.execute(
        select(Receipt).where(Receipt.received_on <= on)
    ).scalars():
        if receipt.invoice_id:
            receipts_by_invoice[receipt.invoice_id] = receipts_by_invoice.get(
                receipt.invoice_id, ZERO
            ) + Decimal(receipt.amount)
        elif receipt.sales_order_id:
            receipts_by_order[receipt.sales_order_id] = receipts_by_order.get(
                receipt.sales_order_id, ZERO
            ) + Decimal(receipt.amount)

    rows = []
    for invoice in invoices:
        received = receipts_by_invoice.get(invoice.id, ZERO)
        # Advances collected against the order (via proformas) count too.
        if invoice.sales_order_id:
            received += receipts_by_order.pop(invoice.sales_order_id, ZERO)
        balance = q2(Decimal(invoice.grand_total) - received)
        if balance <= 0:
            continue
        rows.append(
            [
                invoice.number,
                invoice.invoice_date.isoformat(),
                invoice.customer.name if invoice.customer else "",
                Decimal(invoice.grand_total),
                q2(received),
                balance,
                (on - invoice.invoice_date).days,
            ]
        )
    return Report(
        title=f"Outstanding Receivables as on {on}",
        columns=["Invoice No", "Date", "Customer", "Invoiced", "Received", "Balance", "Age (days)"],
        rows=rows,
        totals={"balance": q2(sum((r[5] for r in rows), ZERO))},
    )


def order_pipeline(db: Session, today: date | None = None) -> Report:
    """The dashboard's data: open orders with delivery countdown."""
    on = today or date.today()
    open_states = {
        OrderStatus.RECEIVED, OrderStatus.IN_PRODUCTION,
        OrderStatus.DISPATCHED, OrderStatus.INVOICED, OrderStatus.PARTIALLY_PAID,
    }
    orders = db.execute(
        select(SalesOrder).where(SalesOrder.status.in_([str(s) for s in open_states]))
        .order_by(SalesOrder.delivery_due_date.is_(None), SalesOrder.delivery_due_date)
    ).scalars().all()

    rows = []
    for order in orders:
        countdown = days_to_delivery(order, on)
        billed = q2(
            sum(
                (Decimal(m.amount) for m in order.milestones if str(m.status) != "pending"),
                ZERO,
            )
        )
        rows.append(
            [
                order.customer_po_number,
                order.customer.name if order.customer else "",
                str(order.status),
                order.delivery_due_date.isoformat() if order.delivery_due_date else "",
                countdown if countdown is not None else "",
                Decimal(order.order_value),
                billed,
                q2(Decimal(order.order_value) - billed),
            ]
        )
    return Report(
        title=f"Order Pipeline as on {on}",
        columns=[
            "Customer PO", "Customer", "Status", "Delivery Due", "Days Left",
            "Order Value", "Billed", "Unbilled",
        ],
        rows=rows,
    )


# --- Excel -----------------------------------------------------------------

_HEADER_FILL = PatternFill("solid", fgColor="1F2937")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)


def to_excel(reports: list[Report]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)

    for report in reports:
        # Excel sheet names cap at 31 chars and reject several punctuation marks.
        safe = "".join(c for c in report.title if c not in "[]:*?/\\")[:31]
        sheet = workbook.create_sheet(safe)
        sheet.append(report.columns)
        for cell in sheet[1]:
            cell.fill = _HEADER_FILL
            cell.font = _HEADER_FONT
            cell.alignment = Alignment(horizontal="center")

        for row in report.rows:
            sheet.append([float(v) if isinstance(v, Decimal) else v for v in row])

        if report.totals:
            sheet.append([])
            sheet.append(
                ["TOTAL"]
                + [""] * (len(report.columns) - 2)
                + [float(list(report.totals.values())[-1])]
            )
            for cell in sheet[sheet.max_row]:
                cell.font = Font(bold=True)

        for index, column in enumerate(report.columns, start=1):
            width = max(len(str(column)) + 2, 12)
            for row in report.rows[:200]:
                width = max(width, min(len(str(row[index - 1])) + 2, 45))
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "A2"

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
