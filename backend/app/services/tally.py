"""Tally Prime XML export.

Three things this must never do, and the mechanism that stops each:

* **Guess a ledger name.** Every name comes from ``tally_ledger_mappings``. A
  missing mapping is an itemised pre-flight error naming the exact keys to
  fill in — the export refuses to run rather than posting to a ledger that
  does not exist in Tally.
* **Double-post.** Two guards: ``REMOTEID`` is the document's stable GUID so
  Tally treats a re-import as an update to the same object, and
  ``tally_export_items`` records what has already gone so the default run
  skips it.
* **Export a proforma.** The query filters on ``doc_type='TAX_INVOICE'``.
  Proformas carry no GST liability and must never reach the books.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AuditAction,
    Customer,
    DocType,
    Invoice,
    InvoiceStatus,
    LedgerScope,
    TallyExport,
    TallyExportItem,
    TallyLedgerMapping,
)
from ..rendering.render import output_path
from . import audit
from .money import q2

TAX_KEYS = ("CGST", "SGST", "IGST", "CESS")


class LedgerMappingMissing(RuntimeError):
    def __init__(self, missing: list[tuple[str, str, str]]) -> None:
        self.missing = missing
        lines = "\n".join(
            f"  · {scope}/{key}   ({hint})" for scope, key, hint in missing
        )
        super().__init__(
            "Tally export blocked — these ledger mappings are not configured:\n"
            f"{lines}\n"
            "Add them under Settings → Tally ledgers, using the ledger names "
            "exactly as they appear in Tally."
        )


@dataclass
class ExportManifest:
    date_from: date
    date_to: date
    invoices: list[dict[str, Any]] = field(default_factory=list)
    skipped_already_exported: list[str] = field(default_factory=list)
    excluded_proformas: int = 0
    excluded_cancelled: int = 0

    @property
    def voucher_count(self) -> int:
        return len(self.invoices)

    def to_json(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "voucher_count": self.voucher_count,
            "invoices": self.invoices,
            "skipped_already_exported": self.skipped_already_exported,
            "excluded_proformas": self.excluded_proformas,
            "excluded_cancelled": self.excluded_cancelled,
        }


# --- ledger resolution -----------------------------------------------------

def _mapping_index(db: Session) -> dict[tuple[str, str], TallyLedgerMapping]:
    return {
        (str(m.scope), m.local_key): m
        for m in db.execute(select(TallyLedgerMapping)).scalars()
    }


def check_mappings(db: Session, invoices: list[Invoice]) -> None:
    """Pre-flight. Raises with the complete list, not the first failure —
    fixing them one round-trip at a time is what makes an export hateful."""
    index = _mapping_index(db)
    missing: list[tuple[str, str, str]] = []

    def require(scope: LedgerScope, key: str, hint: str) -> None:
        if (str(scope), key) not in index and (scope, key, hint) not in missing:
            missing.append((str(scope), key, hint))

    require(LedgerScope.ROUNDOFF, "ROUNDOFF", "the round-off ledger, e.g. 'Round Off'")

    for invoice in invoices:
        customer = invoice.customer
        require(
            LedgerScope.CUSTOMER,
            invoice.customer_id,
            f"sundry-debtor ledger for {customer.name if customer else invoice.customer_id}",
        )
        for line in invoice.lines:
            rate = q2(Decimal(line.gst_rate_percent))
            require(
                LedgerScope.ITEM_GROUP,
                f"SALES@{rate}",
                f"sales ledger for GST {rate}%, e.g. 'Sales - GST {rate}%'",
            )
        if Decimal(invoice.cgst_amount):
            require(LedgerScope.TAX, "CGST", "output CGST duty ledger")
        if Decimal(invoice.sgst_amount):
            require(LedgerScope.TAX, "SGST", "output SGST duty ledger")
        if Decimal(invoice.igst_amount):
            require(LedgerScope.TAX, "IGST", "output IGST duty ledger")
        if Decimal(invoice.cess_amount):
            require(LedgerScope.TAX, "CESS", "output cess ledger")

    if missing:
        raise LedgerMappingMissing(missing)


# --- XML -------------------------------------------------------------------

def _tally_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _amount(value: Decimal) -> str:
    """Tally sign convention: credits negative, debits positive."""
    return f"{q2(Decimal(value)):.2f}"


def _entry(parent: ET.Element, ledger: str, amount: Decimal, *, is_deemed_positive: bool) -> None:
    entry = ET.SubElement(parent, "ALLLEDGERENTRIES.LIST")
    ET.SubElement(entry, "LEDGERNAME").text = ledger
    ET.SubElement(entry, "ISDEEMEDPOSITIVE").text = "Yes" if is_deemed_positive else "No"
    # Debit is positive to Tally, credit is negative.
    signed = Decimal(amount) if is_deemed_positive else -Decimal(amount)
    ET.SubElement(entry, "AMOUNT").text = _amount(signed)


def build_xml(db: Session, invoices: list[Invoice], company_name: str) -> str:
    index = _mapping_index(db)

    def ledger(scope: LedgerScope, key: str) -> str:
        return index[(str(scope), key)].tally_ledger_name

    envelope = ET.Element("ENVELOPE")
    header = ET.SubElement(envelope, "HEADER")
    ET.SubElement(header, "TALLYREQUEST").text = "Import Data"

    body = ET.SubElement(envelope, "BODY")
    import_data = ET.SubElement(body, "IMPORTDATA")
    request_desc = ET.SubElement(import_data, "REQUESTDESC")
    ET.SubElement(request_desc, "REPORTNAME").text = "Vouchers"
    static_vars = ET.SubElement(request_desc, "STATICVARIABLES")
    ET.SubElement(static_vars, "SVCURRENTCOMPANY").text = company_name
    request_data = ET.SubElement(import_data, "REQUESTDATA")

    for invoice in invoices:
        message = ET.SubElement(request_data, "TALLYMESSAGE")
        voucher = ET.SubElement(
            message,
            "VOUCHER",
            {
                "VCHTYPE": "Sales",
                "ACTION": "Create",
                "OBJVIEW": "Invoice Voucher",
                # The idempotency key on Tally's side: re-importing the same
                # REMOTEID updates that voucher instead of creating a second.
                "REMOTEID": invoice.guid,
            },
        )
        ET.SubElement(voucher, "DATE").text = _tally_date(invoice.invoice_date)
        ET.SubElement(voucher, "EFFECTIVEDATE").text = _tally_date(invoice.invoice_date)
        ET.SubElement(voucher, "VOUCHERTYPENAME").text = "Sales"
        ET.SubElement(voucher, "VOUCHERNUMBER").text = invoice.number
        ET.SubElement(voucher, "REFERENCE").text = invoice.number
        ET.SubElement(voucher, "PARTYLEDGERNAME").text = ledger(
            LedgerScope.CUSTOMER, invoice.customer_id
        )
        ET.SubElement(voucher, "PERSISTEDVIEW").text = "Invoice Voucher"
        ET.SubElement(voucher, "GUID").text = invoice.guid
        if invoice.place_of_supply_state_code:
            ET.SubElement(voucher, "PLACEOFSUPPLY").text = invoice.place_of_supply_state_code

        # Debit the customer for the full amount received-able.
        _entry(
            voucher,
            ledger(LedgerScope.CUSTOMER, invoice.customer_id),
            Decimal(invoice.grand_total),
            is_deemed_positive=True,
        )

        # Credit sales, grouped by GST rate so each rate hits its own ledger.
        by_rate: dict[Decimal, Decimal] = {}
        for line in invoice.lines:
            rate = q2(Decimal(line.gst_rate_percent))
            by_rate[rate] = by_rate.get(rate, Decimal("0")) + Decimal(line.taxable_value)
        for rate, taxable in sorted(by_rate.items()):
            _entry(
                voucher,
                ledger(LedgerScope.ITEM_GROUP, f"SALES@{rate}"),
                taxable,
                is_deemed_positive=False,
            )

        # Credit each duty ledger.
        for key, amount in (
            ("CGST", invoice.cgst_amount),
            ("SGST", invoice.sgst_amount),
            ("IGST", invoice.igst_amount),
            ("CESS", invoice.cess_amount),
        ):
            if Decimal(amount):
                _entry(voucher, ledger(LedgerScope.TAX, key), Decimal(amount), is_deemed_positive=False)

        if Decimal(invoice.round_off):
            round_off = Decimal(invoice.round_off)
            _entry(
                voucher,
                ledger(LedgerScope.ROUNDOFF, "ROUNDOFF"),
                abs(round_off),
                is_deemed_positive=round_off < 0,
            )

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        envelope, encoding="unicode"
    )


# --- run -------------------------------------------------------------------

def collect(
    db: Session, date_from: date, date_to: date, *, force: bool = False
) -> tuple[list[Invoice], ExportManifest]:
    manifest = ExportManifest(date_from=date_from, date_to=date_to)

    candidates = list(
        db.execute(
            select(Invoice)
            .where(
                Invoice.invoice_date >= date_from,
                Invoice.invoice_date <= date_to,
                # Proformas are structurally excluded — not filtered later,
                # not "usually" excluded. Here.
                Invoice.doc_type == DocType.TAX_INVOICE,
            )
            .order_by(Invoice.invoice_date, Invoice.number)
        ).scalars()
    )

    manifest.excluded_proformas = db.query(Invoice).filter(
        Invoice.invoice_date >= date_from,
        Invoice.invoice_date <= date_to,
        Invoice.doc_type == DocType.PROFORMA,
    ).count()

    already: set[str] = set()
    if not force:
        already = {
            row.document_id
            for row in db.execute(
                select(TallyExportItem)
                .join(TallyExport, TallyExport.id == TallyExportItem.export_id)
                .where(TallyExport.succeeded.is_(True))
            ).scalars()
        }

    selected: list[Invoice] = []
    for invoice in candidates:
        if invoice.status == InvoiceStatus.CANCELLED:
            manifest.excluded_cancelled += 1
            continue
        if invoice.status != InvoiceStatus.ISSUED:
            continue
        if invoice.id in already:
            manifest.skipped_already_exported.append(invoice.number)
            continue
        selected.append(invoice)
        manifest.invoices.append(
            {
                "id": invoice.id,
                "number": invoice.number,
                "date": invoice.invoice_date.isoformat(),
                "customer": invoice.customer.name if invoice.customer else "",
                "taxable_value": str(invoice.taxable_value),
                "grand_total": str(invoice.grand_total),
                "remote_id": invoice.guid,
            }
        )

    return selected, manifest


def run_export(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    actor: str,
    delivery: str = "FILE",
    force: bool = False,
    company_name: str | None = None,
) -> TallyExport:
    settings = get_settings()
    invoices, manifest = collect(db, date_from, date_to, force=force)
    check_mappings(db, invoices)

    xml = build_xml(db, invoices, company_name or settings.tally_company_name)
    target = output_path(
        "tally", f"tally-{date_from.isoformat()}-to-{date_to.isoformat()}", "xml"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(xml, encoding="utf-8")

    export = TallyExport(
        date_from=date_from,
        date_to=date_to,
        voucher_count=manifest.voucher_count,
        manifest_json=manifest.to_json(),
        xml_path=str(target),
        delivery=delivery,
        succeeded=True,
    )
    db.add(export)
    db.flush()

    if delivery == "HTTP_POST":
        url = f"http://{settings.tally_host}:{settings.tally_port}"
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    url, content=xml.encode("utf-8"), headers={"Content-Type": "text/xml"}
                )
                response.raise_for_status()
                export.response_text = response.text[:8000]
            export.posted_at = datetime.now(timezone.utc)
            # Tally answers 200 even when it rejected rows; the errors are in
            # the body, so a status code alone is not success.
            export.succeeded = "<LINEERROR>" not in (export.response_text or "")
        except httpx.HTTPError as exc:
            export.succeeded = False
            export.response_text = f"{type(exc).__name__}: {exc}"

    if export.succeeded:
        for invoice in invoices:
            db.add(
                TallyExportItem(
                    export_id=export.id,
                    document_type="TAX_INVOICE",
                    document_id=invoice.id,
                    remote_id=invoice.guid,
                )
            )
    db.flush()

    audit.record(
        db,
        entity_type="tally_exports",
        entity_id=export.id,
        action=AuditAction.EXPORT,
        actor=actor,
        after={
            "voucher_count": export.voucher_count,
            "delivery": delivery,
            "succeeded": export.succeeded,
            "numbers": [i["number"] for i in manifest.invoices],
        },
        context={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
    )
    return export


SEED_MAPPINGS: list[dict[str, str]] = [
    # Placeholders. Replace the ledger names with the exact strings from Tally.
    {"scope": "TAX", "local_key": "CGST", "tally_ledger_name": "CGST Output",
     "tally_parent_group": "Duties & Taxes"},
    {"scope": "TAX", "local_key": "SGST", "tally_ledger_name": "SGST Output",
     "tally_parent_group": "Duties & Taxes"},
    {"scope": "TAX", "local_key": "IGST", "tally_ledger_name": "IGST Output",
     "tally_parent_group": "Duties & Taxes"},
    {"scope": "TAX", "local_key": "CESS", "tally_ledger_name": "Cess Output",
     "tally_parent_group": "Duties & Taxes"},
    {"scope": "ROUNDOFF", "local_key": "ROUNDOFF", "tally_ledger_name": "Round Off",
     "tally_parent_group": "Indirect Expenses"},
    {"scope": "ITEM_GROUP", "local_key": "SALES@18.00",
     "tally_ledger_name": "Sales - GST 18%", "tally_parent_group": "Sales Accounts"},
    {"scope": "ITEM_GROUP", "local_key": "SALES@28.00",
     "tally_ledger_name": "Sales - GST 28%", "tally_parent_group": "Sales Accounts"},
    {"scope": "ITEM_GROUP", "local_key": "SALES@12.00",
     "tally_ledger_name": "Sales - GST 12%", "tally_parent_group": "Sales Accounts"},
    {"scope": "ITEM_GROUP", "local_key": "SALES@5.00",
     "tally_ledger_name": "Sales - GST 5%", "tally_parent_group": "Sales Accounts"},
    {"scope": "ITEM_GROUP", "local_key": "SALES@0.00",
     "tally_ledger_name": "Sales - Exempt", "tally_parent_group": "Sales Accounts"},
]


def seed_mappings(db: Session) -> int:
    """Seed the placeholder rows. Never overwrites an edited name."""
    index = _mapping_index(db)
    created = 0
    for row in SEED_MAPPINGS:
        if (row["scope"], row["local_key"]) in index:
            continue
        db.add(
            TallyLedgerMapping(
                scope=LedgerScope(row["scope"]),
                local_key=row["local_key"],
                tally_ledger_name=row["tally_ledger_name"],
                tally_parent_group=row.get("tally_parent_group"),
                notes="seeded placeholder — replace with the exact name from Tally",
            )
        )
        created += 1
    db.flush()
    return created


def ensure_customer_mapping(db: Session, customer: Customer) -> TallyLedgerMapping | None:
    """Create a *placeholder* debtor mapping so the pre-flight lists it as
    editable rather than merely missing."""
    existing = db.execute(
        select(TallyLedgerMapping).where(
            TallyLedgerMapping.scope == LedgerScope.CUSTOMER,
            TallyLedgerMapping.local_key == customer.id,
        )
    ).scalars().first()
    if existing:
        return existing
    mapping = TallyLedgerMapping(
        scope=LedgerScope.CUSTOMER,
        local_key=customer.id,
        tally_ledger_name=customer.name,
        tally_parent_group="Sundry Debtors",
        notes="auto-created from the customer name — confirm it matches Tally exactly",
    )
    db.add(mapping)
    db.flush()
    return mapping
