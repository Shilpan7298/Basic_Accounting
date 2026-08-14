"""End-to-end through HTTP.

The two flows a person actually performs: PO in → proforma out, and supplier
offer in → Word purchase order out. If these pass, the vertical slices work.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import DocumentKind

from .conftest import FIXTURES


def test_health_reports_the_selected_extractor(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["extractor_selected"] == "manual"
    assert any(a["name"] == "manual" and a["available"] for a in body["extractors"])


def test_gstin_validation_endpoint(client):
    good = client.get("/api/gstin/validate", params={"value": "27AAACS7409B1ZN"}).json()
    assert good["valid"] and good["state_name"] == "Maharashtra"

    bad = client.get("/api/gstin/validate", params={"value": "27AAACS7409B1ZZ"}).json()
    assert not bad["valid"] and "checksum" in bad["reason"]


def test_a_customer_with_a_bad_gstin_is_rejected(client):
    response = client.post(
        "/api/customers", json={"name": "Typo Ltd", "gstin": "27AAACS7409B1ZZ"}
    )
    assert response.status_code == 422
    assert "GSTIN rejected" in response.json()["detail"]


def test_creating_a_customer_derives_the_state_from_the_gstin(client):
    body = client.post(
        "/api/customers", json={"name": "Solaris", "gstin": "27AAACS7409B1ZN"}
    ).json()
    assert body["state_code"] == "27"
    assert body["state_name"] == "Maharashtra"
    assert body["place_of_supply_state_code"] == "27"


def test_uploading_a_po_returns_a_reviewable_draft(client):
    """M1: the review screen's data, including the source file to display."""
    with open(FIXTURES / "customer_po_solaris.pdf", "rb") as handle:
        response = client.post(
            "/api/uploads",
            files={"file": ("po.pdf", handle, "application/pdf")},
            data={"schema_name": "customer_po"},
        )
    assert response.status_code == 201
    body = response.json()

    assert body["page_count"] == 1
    assert body["has_text_layer"] is True
    # The manual extractor is configured, so this lands on the review form.
    assert body["extraction"]["status"] == "needs_review"
    assert body["extraction"]["overall_confidence"] == 0.0

    source = client.get(f"/api/documents/{body['document_id']}/file")
    assert source.status_code == 200
    assert source.content[:4] == b"%PDF"


def test_approving_an_extraction_creates_the_order(client, db):
    """Nothing financial exists until this call."""
    from app.models import SalesOrder

    with open(FIXTURES / "customer_po_solaris.pdf", "rb") as handle:
        upload = client.post(
            "/api/uploads",
            files={"file": ("po.pdf", handle, "application/pdf")},
            data={"schema_name": "customer_po"},
        ).json()

    assert db.query(SalesOrder).count() == 0, "an order existed before approval"

    corrected = {
        "customer_name": "Solaris Infra Projects Private Limited",
        "customer_gstin": "27AAACS7409B1ZN",
        "customer_address": "412, Sunrise Business Park\nMumbai 400093",
        "po_number": "SIPL/PO/2025-26/0187",
        "po_date": "2025-08-04",
        "delivery_due_date": "2025-09-30",
        "payment_terms_text": (
            "30% advance along with PO, 60% before dispatch, 10% after commissioning"
        ),
        "lines": [
            {"description": "BESS 100 kWh", "hsn_code": "8507", "qty": "4",
             "uom": "NOS", "unit_price": "1250000"},
            {"description": "PCS 50 kW", "hsn_code": "8504", "qty": "4",
             "uom": "NOS", "unit_price": "375000"},
        ],
    }
    response = client.post(
        f"/api/extractions/{upload['extraction']['id']}/approve",
        json={"corrected": corrected},
        # A spoofed actor header must be ignored — the audit trail takes the
        # name from the signed-in session, not from anything the caller sends.
        headers={"X-Actor": "someone-else"},
    )
    assert response.status_code == 200
    order = response.json()["sales_order"]

    assert order["customer_po_number"] == "SIPL/PO/2025-26/0187"
    assert Decimal(order["order_value"]) == Decimal("6500000.00")
    assert len(order["lines"]) == 2

    audit = client.get(
        "/api/audit", params={"entity_type": "sales_orders", "entity_id": order["id"]}
    ).json()
    actors = {e["actor"] for e in audit}
    assert "shilpan" in actors, "the signed-in user should own the audit rows"
    assert "someone-else" not in actors, "a spoofed X-Actor header was believed"


def test_approving_twice_is_refused(client):
    with open(FIXTURES / "customer_po_solaris.pdf", "rb") as handle:
        upload = client.post(
            "/api/uploads",
            files={"file": ("po.pdf", handle, "application/pdf")},
            data={"schema_name": "customer_po"},
        ).json()

    payload = {
        "corrected": {
            "customer_name": "X", "po_number": "1", "po_date": "2025-08-04",
            "lines": [{"description": "a", "hsn_code": "8507", "qty": "1",
                       "unit_price": "100"}],
        }
    }
    first = client.post(f"/api/extractions/{upload['extraction']['id']}/approve", json=payload)
    assert first.status_code == 200

    second = client.post(f"/api/extractions/{upload['extraction']['id']}/approve", json=payload)
    assert second.status_code == 409


def test_a_corrected_payload_is_revalidated(client):
    """A typo in the review form is as damaging as a bad extraction."""
    with open(FIXTURES / "customer_po_solaris.pdf", "rb") as handle:
        upload = client.post(
            "/api/uploads",
            files={"file": ("po.pdf", handle, "application/pdf")},
            data={"schema_name": "customer_po"},
        ).json()

    response = client.post(
        f"/api/extractions/{upload['extraction']['id']}/approve",
        json={"corrected": {"po_number": "1"}},  # customer_name missing
    )
    assert response.status_code == 422
    assert "not valid" in response.json()["detail"]


def test_terms_preview_shows_the_split_before_committing(client):
    body = client.post(
        "/api/orders/preview-terms",
        params={"text": "30% advance along with PO, 60% before dispatch, 10% after commissioning"},
    ).json()

    assert body["is_usable"] is True
    assert Decimal(body["total_percent"]) == Decimal("100.00")
    assert len(body["milestones"]) == 3


def test_unusable_terms_ask_for_the_manual_builder(
    client, db, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer, terms="50% advance, 40% before dispatch")
    body = client.post(f"/api/orders/{order.id}/milestones", json={}).json()

    assert body["needs_manual_schedule"] is True
    assert body["created"] == []
    assert body["parsed"]["is_usable"] is False


def test_manual_milestones_must_sum_to_100(client, db, gujarat_customer, order_factory):
    order = order_factory(gujarat_customer)
    response = client.post(
        f"/api/orders/{order.id}/milestones",
        json={"manual": [{"label": "Half", "trigger_event": "ON_PO", "percent": "50"}]},
    )
    assert response.status_code == 422
    assert "100%" in response.json()["detail"]


def test_the_full_sales_slice_po_to_proforma_to_tax_invoice(
    client, db, company, tax_rates, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)

    milestones = client.post(f"/api/orders/{order.id}/milestones", json={}).json()
    assert len(milestones["created"]) == 3

    proforma = client.post(
        f"/api/orders/{order.id}/proforma",
        json={"milestone_id": milestones["created"][0]["id"], "issue": True},
    ).json()
    assert proforma["doc_type"] == "PROFORMA"
    assert proforma["number"].startswith("URJ/PI/")
    assert Decimal(proforma["igst_amount"]) == Decimal("0")

    pdf = client.get(f"/api/invoices/{proforma['id']}/download", params={"fmt": "pdf"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    docx = client.get(f"/api/invoices/{proforma['id']}/download", params={"fmt": "docx"})
    assert docx.status_code == 200 and docx.content[:2] == b"PK"

    tax_invoice = client.post(
        f"/api/orders/{order.id}/tax-invoice", json={"issue": True}
    ).json()
    assert tax_invoice["doc_type"] == "TAX_INVOICE"
    assert Decimal(tax_invoice["cgst_amount"]) == Decimal("450000.00")
    assert Decimal(tax_invoice["sgst_amount"]) == Decimal("450000.00")
    assert tax_invoice["amount_in_words"].startswith("Rupees")


def test_an_invoice_for_an_unpriced_hsn_names_the_gap(
    client, db, company, gujarat_customer, order_factory
):
    """No tax rate configured — the error must say which HSN, not 500."""
    order = order_factory(gujarat_customer)
    response = client.post(f"/api/orders/{order.id}/tax-invoice", json={"issue": False})

    assert response.status_code == 422
    assert "8507" in response.json()["detail"]


def test_the_dashboard_feed_carries_a_delivery_countdown(
    client, db, company, tax_rates, gujarat_customer, order_factory
):
    order_factory(gujarat_customer, delivery_due_date=date(2025, 9, 30))
    rows = client.get("/api/orders", params={"today": "2025-09-01"}).json()

    assert rows
    assert rows[0]["days_to_delivery"] == 29
    assert rows[0]["next_milestone"] is None  # no milestones built yet


def test_recording_a_receipt_updates_the_order_total(
    client, db, company, tax_rates, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    response = client.post(
        f"/api/orders/{order.id}/receipts",
        json={"amount": "1500000", "received_on": "2025-08-20", "mode": "RTGS",
              "reference": "UTR123"},
    )
    assert response.status_code == 201
    assert Decimal(response.json()["received_total"]) == Decimal("1500000.00")


def test_an_illegal_status_transition_is_a_409(client, db, gujarat_customer, order_factory):
    order = order_factory(gujarat_customer)
    response = client.post(f"/api/orders/{order.id}/transition", json={"target": "paid"})
    assert response.status_code == 409
    assert "allowed" in response.json()["detail"]


def test_the_full_purchase_slice_offer_to_word_po(client, db, company, tax_rates, supplier):
    """M5: upload an offer, negotiate, get a Word PO."""
    with open(FIXTURES / "supplier_offer_voltcell.pdf", "rb") as handle:
        upload = client.post(
            "/api/uploads",
            files={"file": ("offer.pdf", handle, "application/pdf")},
            data={"schema_name": "supplier_offer"},
        ).json()

    approved = client.post(
        f"/api/extractions/{upload['extraction']['id']}/approve",
        json={
            "corrected": {
                "supplier_name": "Voltcell Components Private Limited",
                "supplier_gstin": "29AAGCV2233P1ZT",
                "offer_ref": "VCP/QT/25-26/0311",
                "offer_date": "2025-07-22",
                "lead_time_days": 28,
                "payment_terms_text": "50% advance along with order, balance 50% before dispatch",
                "freight_terms": "Ex-works Bengaluru",
                "warranty_text": "24 months from date of supply",
                "lines": [
                    {"description": "LFP prismatic cell 3.2V 280Ah", "hsn_code": "8507",
                     "qty": "320", "uom": "NOS", "unit_price": "4950"},
                ],
            }
        },
    ).json()
    offer = approved["supplier_offer"]
    assert offer["offer_ref"] == "VCP/QT/25-26/0311"
    assert Decimal(offer["lines"][0]["quoted_unit_price"]) == Decimal("4950.0000")

    negotiated = client.post(
        f"/api/offers/{offer['id']}/negotiate",
        json={"rates": {offer["lines"][0]["id"]: "4700"}},
    ).json()
    assert Decimal(negotiated["lines"][0]["negotiated_unit_price"]) == Decimal("4700.0000")

    po = client.post(
        f"/api/offers/{offer['id']}/purchase-order",
        json={"po_date": "2025-08-01", "delivery_due_date": "2025-09-01", "issue": True},
    ).json()

    assert po["number"].startswith("URJ/PO/")
    # Karnataka supplier → inter-state → IGST, at the negotiated rate.
    assert Decimal(po["taxable_value"]) == Decimal("1504000.00")
    assert Decimal(po["igst_amount"]) == Decimal("270720.00")
    assert Decimal(po["cgst_amount"]) == Decimal("0")

    word = client.get(f"/api/purchase-orders/{po['id']}/download", params={"fmt": "docx"})
    assert word.status_code == 200
    assert word.content[:2] == b"PK"
    assert "URJ-PO-" in word.headers["content-disposition"]


def test_templates_are_listed_and_validated_over_http(client):
    listed = client.get("/api/templates").json()
    assert {t["doc_type"] for t in listed} == {"invoice", "purchase_order"}

    result = client.post("/api/templates/invoice/html/standard/validate").json()
    assert result["ok"] is True
    assert result["undefined_names"] == []


def test_tally_preview_lists_the_missing_mappings_before_you_export(
    client, db, company, tax_rates, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    client.post(f"/api/orders/{order.id}/tax-invoice",
                json={"invoice_date": "2025-08-12", "issue": True})

    preview = client.post(
        "/api/tally/preview", json={"date_from": "2025-08-01", "date_to": "2025-08-31"}
    ).json()

    assert preview["voucher_count"] == 1
    assert preview["missing_mappings"]
    assert all("hint" in m for m in preview["missing_mappings"])

    export = client.post(
        "/api/tally/export", json={"date_from": "2025-08-01", "date_to": "2025-08-31"}
    )
    assert export.status_code == 422


def test_tally_export_over_http_once_mappings_exist(
    client, db, company, tax_rates, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    client.post(f"/api/orders/{order.id}/tax-invoice",
                json={"invoice_date": "2025-08-12", "issue": True})

    client.post("/api/tally/mappings/seed")
    client.post("/api/tally/mappings", json={
        "scope": "CUSTOMER", "local_key": gujarat_customer.id,
        "tally_ledger_name": "Greenfield Textiles Limited",
        "tally_parent_group": "Sundry Debtors",
    })

    export = client.post(
        "/api/tally/export", json={"date_from": "2025-08-01", "date_to": "2025-08-31"}
    )
    assert export.status_code == 201
    assert export.json()["voucher_count"] == 1

    xml = client.get(f"/api/tally/exports/{export.json()['id']}/xml")
    assert xml.status_code == 200
    assert b"<ENVELOPE>" in xml.content


def test_reports_render_and_export_to_excel(
    client, db, company, tax_rates, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    client.post(f"/api/orders/{order.id}/tax-invoice",
                json={"invoice_date": "2025-08-12", "issue": True})

    register = client.get(
        "/api/reports/sales-register",
        params={"date_from": "2025-08-01", "date_to": "2025-08-31"},
    ).json()
    assert len(register["rows"]) == 1
    assert Decimal(register["totals"]["taxable_value"]) == Decimal("5000000.00")

    hsn = client.get(
        "/api/reports/hsn-summary",
        params={"date_from": "2025-08-01", "date_to": "2025-08-31"},
    ).json()
    assert hsn["rows"][0][0] == "8507"

    excel = client.get(
        "/api/reports/all/excel",
        params={"date_from": "2025-08-01", "date_to": "2025-08-31"},
    )
    assert excel.status_code == 200
    assert excel.content[:2] == b"PK"
    assert "all.xlsx" in excel.headers["content-disposition"]


def test_an_unknown_report_name_lists_what_is_available(client):
    response = client.get("/api/reports/not-a-report")
    assert response.status_code == 404
    assert "sales-register" in response.json()["detail"]
