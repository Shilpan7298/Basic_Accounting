"""Issued documents are immutable; changes become versions.

The behaviour the business asked for, stated as tests: the accountant's edit
does not take effect until the owner approves it, nothing is ever overwritten,
and a deletion still leaves a record of what was deleted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import AmendmentStatus, DocumentVersion, Invoice, InvoiceLine
from app.services import invoices as invoice_service
from app.services import versioning
from app.services.versioning import NotAmendable, VersioningError


@pytest.fixture()
def issued_invoice(db, company, tax_rates, gujarat_customer, order_factory):
    order = order_factory(gujarat_customer)
    invoice = invoice_service.create_tax_invoice(
        db, order, actor="setup", invoice_date=date(2025, 8, 12)
    )
    invoice_service.issue(db, invoice, actor="setup")
    db.commit()
    db.refresh(invoice)
    return invoice


# --- version 1 -------------------------------------------------------------

def test_issuing_writes_version_one(db, issued_invoice):
    history = versioning.versions(db, "invoices", issued_invoice.id)

    assert len(history) == 1
    assert history[0].version_no == 1
    assert history[0].status == AmendmentStatus.CURRENT
    assert history[0].reason == "original issue"


def test_the_snapshot_is_self_contained(db, issued_invoice):
    """Rebuilding an old version must not depend on any other table's current state."""
    snap = versioning.versions(db, "invoices", issued_invoice.id)[0].snapshot_json

    assert snap["header"]["number"] == issued_invoice.number
    assert Decimal(snap["header"]["grand_total"]) == Decimal(issued_invoice.grand_total)
    assert len(snap["lines"]) == len(issued_invoice.lines)
    assert snap["render_context_json"]["document"]["number"] == issued_invoice.number


def test_a_draft_is_edited_directly_not_versioned(
    db, owner, company, tax_rates, gujarat_customer, order_factory
):
    order = order_factory(gujarat_customer)
    draft = invoice_service.create_tax_invoice(db, order, actor="setup")
    db.commit()

    assert versioning.versions(db, "invoices", draft.id) == []
    with pytest.raises(NotAmendable, match="draft"):
        versioning.propose_amendment(
            db, "invoices", draft.id, user=_owner(db), reason="x",
            header_changes={"notes": "y"},
        )


def _owner(db):
    from app.models import Role, User

    return db.query(User).filter(User.role == Role.OWNER).one()


def _accountant(db):
    from app.models import Role, User

    return db.query(User).filter(User.role == Role.ACCOUNTANT).one()


# --- the accountant proposes ----------------------------------------------

def test_an_accountants_amendment_does_not_take_effect_immediately(
    db, accountant, issued_invoice
):
    """The core requirement: he can propose, but the ledger does not move."""
    original_notes = issued_invoice.notes

    version = versioning.propose_amendment(
        db, "invoices", issued_invoice.id,
        user=_accountant(db),
        reason="customer asked for their reference on the invoice",
        header_changes={"notes": "Customer ref: GTL/2025/889"},
    )
    db.commit()
    db.refresh(issued_invoice)

    assert version.status == AmendmentStatus.PENDING
    assert version.version_no == 2
    # The live document is untouched.
    assert issued_invoice.notes == original_notes
    # And version 1 is still the current one.
    assert versioning.current_version(db, "invoices", issued_invoice.id).version_no == 1


def test_approving_applies_the_change_and_supersedes_the_old_version(
    db, accountant, owner, issued_invoice
):
    version = versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_accountant(db),
        reason="add customer reference", header_changes={"notes": "Customer ref: 889"},
    )
    db.commit()

    versioning.approve_amendment(db, version, user=_owner(db))
    db.commit()
    db.refresh(issued_invoice)

    assert issued_invoice.notes == "Customer ref: 889"
    history = versioning.versions(db, "invoices", issued_invoice.id)
    assert [v.status for v in history] == [
        AmendmentStatus.SUPERSEDED, AmendmentStatus.CURRENT
    ]
    assert history[1].approved_by == "shilpan"


def test_rejecting_leaves_the_document_alone(db, accountant, owner, issued_invoice):
    before = issued_invoice.notes
    version = versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_accountant(db),
        reason="speculative", header_changes={"notes": "should not stick"},
    )
    db.commit()

    versioning.reject_amendment(db, version, user=_owner(db), reason="not agreed with customer")
    db.commit()
    db.refresh(issued_invoice)

    assert issued_invoice.notes == before
    assert version.status == AmendmentStatus.REJECTED
    assert version.rejected_reason == "not agreed with customer"
    assert versioning.current_version(db, "invoices", issued_invoice.id).version_no == 1


def test_the_accountant_cannot_approve_his_own_amendment(db, accountant, issued_invoice):
    version = versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_accountant(db),
        reason="self-service", header_changes={"notes": "nope"},
    )
    db.commit()

    with pytest.raises(VersioningError, match="cannot approve"):
        versioning.approve_amendment(db, version, user=_accountant(db))


def test_only_one_amendment_can_be_pending_at_a_time(db, accountant, issued_invoice):
    versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_accountant(db),
        reason="first", header_changes={"notes": "a"},
    )
    db.commit()

    with pytest.raises(NotAmendable, match="awaiting approval"):
        versioning.propose_amendment(
            db, "invoices", issued_invoice.id, user=_accountant(db),
            reason="second", header_changes={"notes": "b"},
        )


# --- the owner amends ------------------------------------------------------

def test_the_owners_amendment_applies_at_once_but_is_still_a_version(
    db, owner, issued_invoice
):
    version = versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_owner(db),
        reason="correcting the invoice date", header_changes={"notes": "corrected"},
    )
    db.commit()
    db.refresh(issued_invoice)

    assert version.status == AmendmentStatus.CURRENT
    assert issued_invoice.notes == "corrected"
    # Nothing was overwritten — version 1 is still there.
    assert len(versioning.versions(db, "invoices", issued_invoice.id)) == 2
    assert versioning.versions(db, "invoices", issued_invoice.id)[0].version_no == 1


def test_amending_lines_recomputes_tax_through_the_tax_engine(db, owner, issued_invoice):
    """A changed quantity must change the GST — not be edited around it."""
    before_total = Decimal(issued_invoice.grand_total)

    versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_owner(db),
        reason="customer reduced the quantity",
        line_changes=[
            {"description": "BESS 100 kWh", "hsn_code": "8507", "qty": "2",
             "uom": "NOS", "unit_price": "1250000", "discount_percent": "0"}
        ],
    )
    db.commit()
    db.refresh(issued_invoice)

    assert Decimal(issued_invoice.taxable_value) == Decimal("2500000.00")
    assert Decimal(issued_invoice.cgst_amount) == Decimal("225000.00")
    assert Decimal(issued_invoice.sgst_amount) == Decimal("225000.00")
    assert Decimal(issued_invoice.grand_total) == Decimal("2950000")
    assert Decimal(issued_invoice.grand_total) < before_total
    assert len(issued_invoice.lines) == 1


def test_the_identifying_fields_can_never_be_amended(db, owner, issued_invoice):
    for field in ["number", "series_key", "financial_year", "guid"]:
        with pytest.raises(VersioningError):
            versioning.propose_amendment(
                db, "invoices", issued_invoice.id, user=_owner(db),
                reason="try it", header_changes={field: "TAMPERED"},
            )


def test_an_amendment_needs_a_reason(db, owner, issued_invoice):
    with pytest.raises(VersioningError, match="reason"):
        versioning.propose_amendment(
            db, "invoices", issued_invoice.id, user=_owner(db),
            reason="   ", header_changes={"notes": "x"},
        )


def test_an_amendment_that_changes_nothing_is_refused(db, owner, issued_invoice):
    with pytest.raises(VersioningError, match="nothing would change"):
        versioning.propose_amendment(
            db, "invoices", issued_invoice.id, user=_owner(db),
            reason="no-op", header_changes={"notes": issued_invoice.notes},
        )


def test_the_diff_shows_what_changed(db, owner, issued_invoice):
    version = versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_owner(db),
        reason="add a note", header_changes={"notes": "please pay by NEFT"},
    )
    db.commit()

    assert version.diff_json["header"]["notes"]["to"] == "please pay by NEFT"


# --- history survives ------------------------------------------------------

def test_every_version_is_retrievable_afterwards(db, owner, issued_invoice):
    for index in range(3):
        versioning.propose_amendment(
            db, "invoices", issued_invoice.id, user=_owner(db),
            reason=f"change {index}", header_changes={"notes": f"note {index}"},
        )
        db.commit()

    history = versioning.versions(db, "invoices", issued_invoice.id)
    assert [v.version_no for v in history] == [1, 2, 3, 4]
    assert [v.snapshot_json["header"]["notes"] for v in history[1:]] == [
        "note 0", "note 1", "note 2"
    ]
    assert sum(1 for v in history if v.status == AmendmentStatus.CURRENT) == 1


def test_restoring_an_earlier_version_moves_forward_not_backward(db, owner, issued_invoice):
    """Rolling back is itself a change, so it gets a new version."""
    versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_owner(db),
        reason="a change we later regret", header_changes={"notes": "regrettable"},
    )
    db.commit()
    db.refresh(issued_invoice)
    assert issued_invoice.notes == "regrettable"

    versioning.restore_version(
        db, "invoices", issued_invoice.id, 1, user=_owner(db), reason="undo"
    )
    db.commit()
    db.refresh(issued_invoice)

    history = versioning.versions(db, "invoices", issued_invoice.id)
    assert len(history) == 3, "restoring must add a version, not delete one"
    assert history[1].status == AmendmentStatus.SUPERSEDED


# --- void ------------------------------------------------------------------

def test_voiding_keeps_the_number(db, owner, issued_invoice):
    number = issued_invoice.number
    versioning.void_document(
        db, "invoices", issued_invoice.id, user=_owner(db), reason="raised in error"
    )
    db.commit()
    db.refresh(issued_invoice)

    assert str(issued_invoice.status) == "cancelled"
    assert issued_invoice.number == number
    assert db.get(Invoice, issued_invoice.id) is not None


def test_a_voided_document_cannot_be_amended(db, owner, issued_invoice):
    versioning.void_document(
        db, "invoices", issued_invoice.id, user=_owner(db), reason="void"
    )
    db.commit()

    with pytest.raises(NotAmendable, match="voided"):
        versioning.propose_amendment(
            db, "invoices", issued_invoice.id, user=_owner(db),
            reason="too late", header_changes={"notes": "x"},
        )


def test_voiding_rejects_any_pending_amendment(db, accountant, owner, issued_invoice):
    version = versioning.propose_amendment(
        db, "invoices", issued_invoice.id, user=_accountant(db),
        reason="pending change", header_changes={"notes": "x"},
    )
    db.commit()

    versioning.void_document(
        db, "invoices", issued_invoice.id, user=_owner(db), reason="void it"
    )
    db.commit()
    db.refresh(version)

    assert version.status == AmendmentStatus.REJECTED
    assert version.rejected_reason == "document was voided"


def test_voiding_needs_a_reason(db, owner, issued_invoice):
    with pytest.raises(VersioningError, match="reason"):
        versioning.void_document(
            db, "invoices", issued_invoice.id, user=_owner(db), reason=""
        )


# --- hard delete -----------------------------------------------------------

def test_hard_delete_removes_the_row_but_not_the_record_of_it(db, owner, issued_invoice):
    from app.models import AuditAction, AuditEvent

    invoice_id = issued_invoice.id
    number = issued_invoice.number
    total = str(issued_invoice.grand_total)

    versioning.hard_delete_document(
        db, "invoices", invoice_id, user=_owner(db), reason="duplicate entry before go-live"
    )
    db.commit()

    # Gone from the ledger.
    assert db.get(Invoice, invoice_id) is None
    assert db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice_id).count() == 0
    assert db.query(DocumentVersion).filter(
        DocumentVersion.entity_id == invoice_id
    ).count() == 0

    # But fully recoverable from the audit log.
    event = db.query(AuditEvent).filter(
        AuditEvent.action == AuditAction.HARD_DELETE
    ).one()
    assert event.actor == "shilpan"
    assert event.context_json["reason"] == "duplicate entry before go-live"
    assert event.before_json["header"]["number"] == number
    assert event.before_json["header"]["grand_total"] == total
    assert event.before_json["lines"], "the deleted lines were not preserved"
    assert event.before_json["versions"], "the version history was not preserved"


def test_only_the_owner_can_hard_delete(db, accountant, issued_invoice):
    with pytest.raises(VersioningError, match="only the owner"):
        versioning.hard_delete_document(
            db, "invoices", issued_invoice.id, user=_accountant(db), reason="try"
        )
    assert db.get(Invoice, issued_invoice.id) is not None


def test_hard_delete_needs_a_reason(db, owner, issued_invoice):
    with pytest.raises(VersioningError, match="reason"):
        versioning.hard_delete_document(
            db, "invoices", issued_invoice.id, user=_owner(db), reason=""
        )


# --- over HTTP -------------------------------------------------------------

def test_the_approval_queue_shows_what_is_waiting(
    accountant_client, client, db, accountant, issued_invoice
):
    proposed = accountant_client.post(
        f"/api/invoices/{issued_invoice.id}/amend",
        json={"reason": "customer reference", "header": {"notes": "ref 889"}},
    )
    assert proposed.status_code == 201
    assert proposed.json()["status"] == "pending"

    queue = client.get("/api/amendments/pending").json()
    assert len(queue) == 1
    assert queue[0]["document_number"] == issued_invoice.number
    assert queue[0]["created_by"] == "ca"
    assert queue[0]["reason"] == "customer reference"

    approved = client.post(f"/api/amendments/{queue[0]['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "current"

    db.refresh(issued_invoice)
    assert issued_invoice.notes == "ref 889"
    assert client.get("/api/amendments/pending").json() == []


def test_hard_delete_over_http_requires_typing_the_number(client, db, issued_invoice):
    wrong = client.request(
        "DELETE",
        f"/api/invoices/{issued_invoice.id}",
        params={"reason": "test", "confirm": "not-the-number"},
    )
    assert wrong.status_code == 400
    assert "Voiding is almost always the right choice" in wrong.json()["detail"]
    assert db.get(Invoice, issued_invoice.id) is not None

    right = client.request(
        "DELETE",
        f"/api/invoices/{issued_invoice.id}",
        params={"reason": "created twice by mistake", "confirm": issued_invoice.number},
    )
    assert right.status_code == 200
    assert db.get(Invoice, issued_invoice.id) is None


def test_version_history_is_visible_over_http(client, db, issued_invoice):
    client.post(
        f"/api/invoices/{issued_invoice.id}/amend",
        json={"reason": "note it", "header": {"notes": "hello"}},
    )
    history = client.get(f"/api/invoices/{issued_invoice.id}/versions").json()
    assert [v["version_no"] for v in history] == [1, 2]

    v1 = client.get(f"/api/invoices/{issued_invoice.id}/versions/1").json()
    assert v1["snapshot"]["header"]["number"] == issued_invoice.number
    assert v1["status"] == "superseded"
