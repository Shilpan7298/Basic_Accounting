"""Tally export: no guessed ledgers, no double-posts, no proformas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from app.models import LedgerScope, TallyLedgerMapping
from app.services import invoices as invoice_service
from app.services import orders as order_service
from app.services import tally as tally_service


@pytest.fixture()
def mappings(db, gujarat_customer, maharashtra_customer):
    tally_service.seed_mappings(db)
    for customer in (gujarat_customer, maharashtra_customer):
        db.add(
            TallyLedgerMapping(
                scope=LedgerScope.CUSTOMER,
                local_key=customer.id,
                tally_ledger_name=f"{customer.name} - Debtors",
                tally_parent_group="Sundry Debtors",
            )
        )
    db.commit()


def _issue_invoice(db, order, on=date(2025, 8, 12)):
    invoice = invoice_service.create_tax_invoice(db, order, actor="t", invoice_date=on)
    invoice_service.issue(db, invoice, actor="t")
    db.commit()
    return invoice


# --- ledger mapping is mandatory ------------------------------------------

def test_a_missing_mapping_blocks_the_export_and_lists_every_gap(
    db, company, tax_rates, gujarat_customer, order_factory
):
    """No guessed ledger names, ever — and all the gaps at once, not one per round trip."""
    order = order_factory(gujarat_customer)
    _issue_invoice(db, order)

    with pytest.raises(tally_service.LedgerMappingMissing) as exc:
        tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")

    scopes = {scope for scope, _key, _hint in exc.value.missing}
    assert "CUSTOMER" in scopes
    assert "ITEM_GROUP" in scopes
    assert "TAX" in scopes
    assert "Settings" in str(exc.value)


def test_seeding_never_overwrites_an_edited_ledger_name(db):
    tally_service.seed_mappings(db)
    db.commit()

    from sqlalchemy import select

    row = db.execute(
        select(TallyLedgerMapping).where(TallyLedgerMapping.local_key == "CGST")
    ).scalar_one()
    row.tally_ledger_name = "Output CGST @ Ahmedabad"
    db.commit()

    assert tally_service.seed_mappings(db) == 0
    db.commit()
    db.refresh(row)
    assert row.tally_ledger_name == "Output CGST @ Ahmedabad"


# --- the XML ---------------------------------------------------------------

def test_the_xml_is_a_valid_tally_import_envelope(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    _issue_invoice(db, order)

    export = tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()

    root = ET.fromstring(open(export.xml_path, encoding="utf-8").read())
    assert root.tag == "ENVELOPE"
    assert root.findtext("HEADER/TALLYREQUEST") == "Import Data"
    assert root.findtext("BODY/IMPORTDATA/REQUESTDESC/REPORTNAME") == "Vouchers"
    vouchers = root.findall(".//VOUCHER")
    assert len(vouchers) == 1
    assert vouchers[0].get("VCHTYPE") == "Sales"


def test_ledger_names_come_only_from_the_mapping_table(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    _issue_invoice(db, order)
    export = tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()

    root = ET.fromstring(open(export.xml_path, encoding="utf-8").read())
    names = {e.text for e in root.findall(".//LEDGERNAME")}

    assert "Greenfield Textiles Limited - Debtors" in names
    assert "CGST Output" in names
    assert "SGST Output" in names
    assert "Sales - GST 18.00%" in names or "Sales - GST 18%" in names


def test_the_voucher_balances_to_zero(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    """Debits and credits must net out or Tally rejects the voucher."""
    order = order_factory(gujarat_customer)
    invoice = _issue_invoice(db, order)
    export = tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()

    root = ET.fromstring(open(export.xml_path, encoding="utf-8").read())
    amounts = [Decimal(e.text) for e in root.findall(".//AMOUNT")]

    assert sum(amounts) == Decimal("0.00")
    # The debtor is debited the full invoice value.
    assert max(amounts) == Decimal(invoice.grand_total)


def test_the_remote_id_is_the_documents_stable_guid(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    invoice = _issue_invoice(db, order)
    export = tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()

    root = ET.fromstring(open(export.xml_path, encoding="utf-8").read())
    voucher = root.find(".//VOUCHER")
    assert voucher.get("REMOTEID") == invoice.guid
    assert voucher.findtext("GUID") == invoice.guid


# --- exclusions ------------------------------------------------------------

def test_a_proforma_never_reaches_the_export(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    """Proformas carry no GST liability. They must not touch the books."""
    order = order_factory(gujarat_customer)
    order_service.build_milestones(db, order, actor="t")
    db.commit()
    db.refresh(order)
    proforma = invoice_service.create_proforma(
        db, order, order.milestones[0], actor="t", invoice_date=date(2025, 8, 12)
    )
    invoice_service.issue(db, proforma, actor="t")
    db.commit()

    invoices, manifest = tally_service.collect(db, date(2025, 8, 1), date(2025, 8, 31))

    assert invoices == []
    assert manifest.voucher_count == 0
    assert manifest.excluded_proformas == 1


def test_a_cancelled_invoice_is_excluded(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    invoice = _issue_invoice(db, order)
    invoice_service.cancel(db, invoice, actor="t", reason="void")
    db.commit()

    _invoices, manifest = tally_service.collect(db, date(2025, 8, 1), date(2025, 8, 31))
    assert manifest.voucher_count == 0
    assert manifest.excluded_cancelled == 1


def test_a_draft_invoice_is_excluded_until_issued(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    invoice_service.create_tax_invoice(db, order, actor="t", invoice_date=date(2025, 8, 12))
    db.commit()

    _invoices, manifest = tally_service.collect(db, date(2025, 8, 1), date(2025, 8, 31))
    assert manifest.voucher_count == 0


def test_the_date_range_is_respected(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order_a = order_factory(gujarat_customer, po_number="A")
    order_b = order_factory(gujarat_customer, po_number="B")
    _issue_invoice(db, order_a, on=date(2025, 8, 12))
    _issue_invoice(db, order_b, on=date(2025, 9, 3))

    _invoices, manifest = tally_service.collect(db, date(2025, 8, 1), date(2025, 8, 31))
    assert manifest.voucher_count == 1


# --- idempotency -----------------------------------------------------------

def test_re_running_an_export_does_not_double_post(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    """The guard that stops the CA getting the same voucher twice."""
    order = order_factory(gujarat_customer)
    _issue_invoice(db, order)

    first = tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()
    assert first.voucher_count == 1

    second = tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()
    assert second.voucher_count == 0
    assert second.manifest_json["skipped_already_exported"] == [
        first.manifest_json["invoices"][0]["number"]
    ]


def test_force_re_exports_deliberately(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    _issue_invoice(db, order)
    tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()

    forced = tally_service.run_export(
        db, date(2025, 8, 1), date(2025, 8, 31), actor="t", force=True
    )
    db.commit()
    assert forced.voucher_count == 1


def test_a_new_invoice_after_an_export_is_picked_up_next_time(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    order_a = order_factory(gujarat_customer, po_number="A")
    _issue_invoice(db, order_a)
    tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()

    order_b = order_factory(gujarat_customer, po_number="B")
    _issue_invoice(db, order_b, on=date(2025, 8, 20))
    second = tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="t")
    db.commit()

    assert second.voucher_count == 1


def test_the_export_is_recorded_in_the_audit_log(
    db, company, tax_rates, mappings, gujarat_customer, order_factory
):
    from sqlalchemy import select

    from app.models import AuditAction, AuditEvent

    order = order_factory(gujarat_customer)
    _issue_invoice(db, order)
    tally_service.run_export(db, date(2025, 8, 1), date(2025, 8, 31), actor="ca-export")
    db.commit()

    events = list(
        db.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.EXPORT)
        ).scalars()
    )
    assert events
    assert events[-1].actor == "ca-export"
    assert events[-1].after_json["voucher_count"] == 1
