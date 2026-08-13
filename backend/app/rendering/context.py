"""Building the dict a template receives.

The Word template and the HTML template for a document type get the *same*
context, so the .docx and the PDF cannot disagree.

Every money value appears twice: ``x`` as a raw string Decimal and ``x_fmt``
pre-grouped for printing. Templates print ``_fmt`` and never do arithmetic —
that is what stops a non-technical editor from producing a wrong invoice by
touching a template.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..models import Company, Customer, Invoice, PurchaseOrder, SalesOrder, Supplier
from ..services.gstin import state_name
from ..services.money import amount_in_words, fmt

IST = timezone(timedelta(hours=5, minutes=30))


def money(value: Decimal | None) -> dict[str, Any]:
    """Both forms of a number, so the template never has to choose."""
    amount = Decimal(value or 0)
    return {"raw": str(amount), "fmt": fmt(amount), "value": amount}


def _addr_lines(text: str | None) -> list[str]:
    if not text:
        return []
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _fmt_date(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


def company_block(company: Company | None) -> dict[str, Any]:
    if company is None:
        return {"legal_name": "", "address_lines": [], "gstin": ""}
    return {
        "legal_name": company.legal_name,
        "trade_name": company.trade_name or company.legal_name,
        "gstin": company.gstin or "",
        "pan": company.pan or "",
        "cin": company.cin or "",
        "state_code": company.state_code,
        "state_name": company.state_name or state_name(company.state_code) or "",
        "address_lines": _addr_lines(
            "\n".join(
                filter(
                    None,
                    [
                        company.address_line1,
                        company.address_line2,
                        " ".join(filter(None, [company.city, company.pincode])),
                        company.state_name,
                    ],
                )
            )
        ),
        "email": company.email or "",
        "phone": company.phone or "",
        "bank_name": company.bank_name or "",
        "bank_account_no": company.bank_account_no or "",
        "bank_ifsc": company.bank_ifsc or "",
        "bank_branch": company.bank_branch or "",
        "logo_path": company.logo_path or "",
    }


def party_block(party: Customer | Supplier | None) -> dict[str, Any]:
    if party is None:
        return {"name": "", "billing_address_lines": [], "gstin": ""}
    billing = getattr(party, "billing_address", None)
    shipping = getattr(party, "shipping_address", None) or billing
    code = getattr(party, "state_code", None)
    return {
        "name": party.name,
        "gstin": party.gstin or "Unregistered",
        "state_code": code or "",
        "state_name": getattr(party, "state_name", None) or state_name(code) or "",
        "billing_address_lines": _addr_lines(billing),
        "shipping_address_lines": _addr_lines(shipping),
        "contact_name": getattr(party, "contact_name", None) or "",
        "email": getattr(party, "email", None) or "",
        "phone": getattr(party, "phone", None) or "",
    }


def _line_block(line: Any, index: int) -> dict[str, Any]:
    return {
        "no": index,
        "description": line.description,
        "hsn_code": line.hsn_code or "",
        "qty": money(line.qty),
        "uom": line.uom,
        "unit_price": money(line.unit_price),
        "discount_percent": money(line.discount_percent),
        "taxable_value": money(getattr(line, "taxable_value", None) or 0),
        "gst_rate_percent": money(getattr(line, "gst_rate_percent", None) or 0),
        "cgst": money(getattr(line, "cgst_amount", None) or 0),
        "sgst": money(getattr(line, "sgst_amount", None) or 0),
        "igst": money(getattr(line, "igst_amount", None) or 0),
        "cess": money(getattr(line, "cess_amount", None) or 0),
        "total": money(getattr(line, "line_total", None) or 0),
    }


def _tax_block(doc: Invoice | PurchaseOrder, hsn_summary: list[Any] | None) -> dict[str, Any]:
    is_intra = bool(doc.cgst_amount or doc.sgst_amount) or (
        not doc.igst_amount and not doc.cgst_amount
    )
    return {
        "is_intra_state": is_intra,
        "cgst_total": money(doc.cgst_amount),
        "sgst_total": money(doc.sgst_amount),
        "igst_total": money(doc.igst_amount),
        "cess_total": money(doc.cess_amount),
        "hsn_summary": [
            {
                "hsn": row.hsn_code,
                "taxable_value": money(row.taxable_value),
                "rate": money(row.rate_percent),
                "cgst": money(row.cgst_amount),
                "sgst": money(row.sgst_amount),
                "igst": money(row.igst_amount),
                "cess": money(row.cess_amount),
            }
            for row in (hsn_summary or [])
        ],
    }


def invoice_context(
    invoice: Invoice,
    company: Company | None,
    customer: Customer | None,
    *,
    sales_order: SalesOrder | None = None,
    hsn_summary: list[Any] | None = None,
    already_received: Decimal = Decimal("0"),
    milestone: Any = None,
    schedule: list[Any] | None = None,
    template_name: str = "",
    template_version: int = 0,
) -> dict[str, Any]:
    is_proforma = invoice.doc_type == "PROFORMA"
    tax_total = (
        Decimal(invoice.cgst_amount)
        + Decimal(invoice.sgst_amount)
        + Decimal(invoice.igst_amount)
        + Decimal(invoice.cess_amount)
    )
    balance_due = Decimal(invoice.grand_total) - Decimal(already_received or 0)

    return {
        "company": company_block(company),
        "document": {
            "type": str(invoice.doc_type),
            "number": invoice.number,
            "date": _fmt_date(invoice.invoice_date),
            "due_date": _fmt_date(invoice.due_date),
            "is_proforma": is_proforma,
            # The label a proforma must carry so it is never mistaken for a
            # tax document.
            "title": "PROFORMA INVOICE" if is_proforma else "TAX INVOICE",
            "subtitle": (
                "This is not a tax invoice. No GST liability arises on this document."
                if is_proforma
                else "(Original for Recipient)"
            ),
            "po_reference": sales_order.customer_po_number if sales_order else "",
            "po_date": _fmt_date(sales_order.customer_po_date) if sales_order else "",
            "notes": invoice.notes or "",
            "status": str(invoice.status),
        },
        "party": party_block(customer),
        "lines": [_line_block(line, i) for i, line in enumerate(invoice.lines, start=1)],
        "tax": _tax_block(invoice, hsn_summary),
        "totals": {
            "taxable_value": money(invoice.taxable_value),
            "tax_total": money(tax_total),
            "round_off": money(invoice.round_off),
            "grand_total": money(invoice.grand_total),
            "amount_in_words": invoice.amount_in_words
            or amount_in_words(Decimal(invoice.grand_total)),
        },
        "payment": {
            "milestone_label": getattr(milestone, "label", "") if milestone else "",
            "milestone_percent": money(getattr(milestone, "percent", 0) if milestone else 0),
            "already_received": money(already_received),
            "balance_due": money(balance_due),
            "terms_text": (sales_order.payment_terms_text if sales_order else "") or "",
            "schedule": [
                {
                    "seq": m.seq,
                    "label": m.label,
                    "percent": money(m.percent),
                    "amount": money(m.amount),
                    "due_date": _fmt_date(m.due_date),
                    "status": str(m.status),
                }
                for m in (schedule or [])
            ],
        },
        "flags": {
            "is_reverse_charge": invoice.is_reverse_charge,
            "is_export": invoice.is_export,
            "lut_no": invoice.lut_no or "",
        },
        "meta": {
            "template_name": template_name,
            "template_version": template_version,
            "generated_at_ist": datetime.now(IST).strftime("%d-%m-%Y %H:%M"),
            "page_footer": f"{invoice.number} — page {{page}}",
        },
    }


def purchase_order_context(
    po: PurchaseOrder,
    company: Company | None,
    supplier: Supplier | None,
    *,
    hsn_summary: list[Any] | None = None,
    template_name: str = "",
    template_version: int = 0,
) -> dict[str, Any]:
    tax_total = (
        Decimal(po.cgst_amount) + Decimal(po.sgst_amount)
        + Decimal(po.igst_amount) + Decimal(po.cess_amount)
    )
    return {
        "company": company_block(company),
        "document": {
            "type": "PURCHASE_ORDER",
            "number": po.number,
            "date": _fmt_date(po.po_date),
            "due_date": _fmt_date(po.delivery_due_date),
            "is_proforma": False,
            "title": "PURCHASE ORDER",
            "subtitle": "",
            "delivery_location": po.delivery_location or "",
            "notes": po.notes or "",
            "status": str(po.status),
        },
        "party": party_block(supplier),
        "lines": [_line_block(line, i) for i, line in enumerate(po.lines, start=1)],
        "tax": _tax_block(po, hsn_summary),
        "totals": {
            "taxable_value": money(po.taxable_value),
            "tax_total": money(tax_total),
            "round_off": money(po.round_off),
            "grand_total": money(po.grand_total),
            "amount_in_words": po.amount_in_words or amount_in_words(Decimal(po.grand_total)),
        },
        "terms": {
            "payment_terms_text": po.payment_terms_text or "",
            "freight_terms": po.freight_terms or "",
            "warranty_text": po.warranty_text or "",
        },
        "flags": {"is_reverse_charge": po.is_reverse_charge},
        "meta": {
            "template_name": template_name,
            "template_version": template_version,
            "generated_at_ist": datetime.now(IST).strftime("%d-%m-%Y %H:%M"),
            "page_footer": f"{po.number} — page {{page}}",
        },
    }


GOLDEN_SAMPLE: dict[str, Any] = {
    "company": {
        "legal_name": "Urjapod Energy Private Limited",
        "trade_name": "Urjapod",
        "gstin": "24AAACC1206D1ZM",
        "pan": "AAACC1206D",
        "cin": "U31900GJ2021PTC000000",
        "state_code": "24",
        "state_name": "Gujarat",
        "address_lines": ["Plot 12, GIDC Estate", "Ahmedabad 382445", "Gujarat"],
        "email": "accounts@example.com",
        "phone": "+91 79 0000 0000",
        "bank_name": "Sample Bank",
        "bank_account_no": "000000000000",
        "bank_ifsc": "SMPL0000001",
        "bank_branch": "Ahmedabad",
        "logo_path": "",
    },
    "document": {
        "type": "TAX_INVOICE", "number": "URJ/25-26/0042", "date": "12-08-2025",
        "due_date": "11-09-2025", "is_proforma": False, "title": "TAX INVOICE",
        "subtitle": "(Original for Recipient)", "po_reference": "4500123456",
        "po_date": "01-08-2025", "notes": "", "status": "issued",
    },
    "party": {
        "name": "Sample Customer Pvt Ltd", "gstin": "27AAACC1206D1ZP",
        "state_code": "27", "state_name": "Maharashtra",
        "billing_address_lines": ["1 Sample Road", "Mumbai 400001"],
        "shipping_address_lines": ["1 Sample Road", "Mumbai 400001"],
        "contact_name": "", "email": "", "phone": "",
    },
    "lines": [
        {
            "no": 1, "description": "BESS 100 kWh lithium-ion pack", "hsn_code": "8507",
            "qty": money(Decimal("2")), "uom": "NOS",
            "unit_price": money(Decimal("1250000")), "discount_percent": money(Decimal("0")),
            "taxable_value": money(Decimal("2500000")), "gst_rate_percent": money(Decimal("18")),
            "cgst": money(Decimal("0")), "sgst": money(Decimal("0")),
            "igst": money(Decimal("450000")), "cess": money(Decimal("0")),
            "total": money(Decimal("2950000")),
        }
    ],
    "tax": {
        "is_intra_state": False,
        "cgst_total": money(Decimal("0")), "sgst_total": money(Decimal("0")),
        "igst_total": money(Decimal("450000")), "cess_total": money(Decimal("0")),
        "hsn_summary": [
            {
                "hsn": "8507", "taxable_value": money(Decimal("2500000")),
                "rate": money(Decimal("18")), "cgst": money(Decimal("0")),
                "sgst": money(Decimal("0")), "igst": money(Decimal("450000")),
                "cess": money(Decimal("0")),
            }
        ],
    },
    "totals": {
        "taxable_value": money(Decimal("2500000")), "tax_total": money(Decimal("450000")),
        "round_off": money(Decimal("0")), "grand_total": money(Decimal("2950000")),
        "amount_in_words": "Rupees Twenty Nine Lakh Fifty Thousand Only",
    },
    "payment": {
        "milestone_label": "Advance along with PO", "milestone_percent": money(Decimal("30")),
        "already_received": money(Decimal("0")), "balance_due": money(Decimal("2950000")),
        "terms_text": "30% advance along with PO, 60% before dispatch, 10% after commissioning",
        "schedule": [],
    },
    "terms": {"payment_terms_text": "", "freight_terms": "", "warranty_text": ""},
    "flags": {"is_reverse_charge": False, "is_export": False, "lut_no": ""},
    "meta": {
        "template_name": "sample", "template_version": 1,
        "generated_at_ist": "12-08-2025 10:00", "page_footer": "",
    },
}
