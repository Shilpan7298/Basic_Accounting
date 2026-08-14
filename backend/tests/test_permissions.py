"""Authentication and the role split.

These are the tests that make the deployment story true rather than claimed.
The accountant must be *unable* to change history, not merely un-shown the
button.
"""

from __future__ import annotations

import pytest

from app.main import app
from app.models import Role, User
from app.services import permissions, security
from app.services.permissions import Permission

from .conftest import ACCOUNTANT_PASSWORD, OWNER_PASSWORD


# --- the permission table --------------------------------------------------

def test_the_owner_has_every_permission():
    """Defined as the whole enum, so a permission added later cannot leave the
    owner locked out of their own books."""
    assert permissions.permissions_for(Role.OWNER) == frozenset(Permission)


def test_the_accountant_cannot_undo_history():
    accountant = permissions.permissions_for(Role.ACCOUNTANT)

    assert Permission.DOCUMENT_CREATE in accountant
    assert Permission.DOCUMENT_ISSUE in accountant
    assert Permission.DOCUMENT_AMEND in accountant  # may propose

    # The four that matter.
    assert Permission.DOCUMENT_AMEND_APPROVE not in accountant
    assert Permission.DOCUMENT_VOID not in accountant
    assert Permission.DOCUMENT_DELETE not in accountant
    assert Permission.SETTINGS_EDIT not in accountant
    assert Permission.USER_MANAGE not in accountant
    assert Permission.TAXRATE_EDIT not in accountant


def test_the_viewer_can_only_read():
    viewer = permissions.permissions_for(Role.VIEWER)
    assert viewer == frozenset({Permission.REPORT_READ, Permission.AUDIT_READ})


def test_every_mutating_endpoint_declares_a_permission():
    """A route without a guard is a hole. This is the test that finds one."""
    import inspect

    exempt = {
        # Authentication itself cannot require being authenticated.
        ("/api/auth/login", "POST"),
        ("/api/auth/logout", "POST"),
        ("/api/auth/bootstrap", "POST"),
        ("/api/auth/change-password", "POST"),
        # Guarded inside the handler, against the acting user's own role.
        ("/api/{entity_type}/{entity_id}/amend", "POST"),
        ("/api/{entity_type}/{entity_id}/void", "POST"),
        ("/api/{entity_type}/{entity_id}", "DELETE"),
        ("/api/{entity_type}/{entity_id}/restore/{version_no}", "POST"),
        ("/api/amendments/{version_id}/approve", "POST"),
        ("/api/amendments/{version_id}/reject", "POST"),
        ("/api/auth/users", "POST"),
        ("/api/auth/users/{user_id}/role", "POST"),
        ("/api/auth/users/{user_id}/reset-password", "POST"),
        ("/api/auth/users/{user_id}/deactivate", "POST"),
        # Pure computation, no data touched.
        ("/api/orders/preview-terms", "POST"),
        ("/api/templates/{doc_type}/{kind}/{name}/validate", "POST"),
        ("/api/tally/preview", "POST"),
    }

    unguarded: list[str] = []
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        mutating = methods & {"POST", "PUT", "PATCH", "DELETE"}
        endpoint = getattr(route, "endpoint", None)
        if not mutating or endpoint is None:
            continue
        path = getattr(route, "path", "")
        if any((path, m) in exempt for m in mutating):
            continue
        source = inspect.getsource(endpoint)
        if "requires(Permission." not in source:
            unguarded.append(f"{sorted(mutating)} {path}")

    assert not unguarded, "these mutating endpoints have no permission guard:\n" + "\n".join(
        unguarded
    )


# --- passwords -------------------------------------------------------------

def test_a_password_hash_is_salted_and_not_reversible():
    first = security.hash_password("correct-horse-battery")
    second = security.hash_password("correct-horse-battery")

    assert first != second, "identical passwords must not produce identical hashes"
    assert "correct-horse-battery" not in first
    assert security.verify_password("correct-horse-battery", first)
    assert security.verify_password("correct-horse-battery", second)
    assert not security.verify_password("wrong", first)


def test_a_corrupted_hash_fails_closed():
    for broken in ["", "nonsense", "scrypt$bad", "md5$1$1$1$aa$bb"]:
        assert not security.verify_password("anything", broken)


@pytest.mark.parametrize(
    "password,reason",
    [("short", "at least"), ("shilpan123", "username"), ("password12", "guess")],
)
def test_weak_passwords_are_refused(password, reason):
    with pytest.raises(security.WeakPassword, match=reason):
        security.check_password_strength(password, username="shilpan")


# --- login -----------------------------------------------------------------

def test_login_succeeds_and_issues_a_session(db, owner):
    user, token = security.authenticate(db, "shilpan", OWNER_PASSWORD)
    db.commit()

    assert user.id == owner.id
    assert token
    assert security.resolve_session(db, token).id == owner.id


def test_the_session_token_is_not_stored_in_the_clear(db, owner):
    from app.models import UserSession

    _user, token = security.authenticate(db, "shilpan", OWNER_PASSWORD)
    db.commit()

    stored = db.query(UserSession).one()
    assert stored.token_hash != token
    assert token not in stored.token_hash


def test_a_bad_password_is_rejected(db, owner):
    with pytest.raises(security.AuthError):
        security.authenticate(db, "shilpan", "not-the-password")


def test_an_unknown_user_and_a_bad_password_are_indistinguishable(db, owner):
    """Different messages would hand an attacker a list of valid usernames."""
    with pytest.raises(security.AuthError) as unknown:
        security.authenticate(db, "nobody", "whatever-long-enough")
    with pytest.raises(security.AuthError) as bad:
        security.authenticate(db, "shilpan", "wrong-password-here")
    assert str(unknown.value) == str(bad.value)


def test_repeated_failures_lock_the_account(db, owner):
    for _ in range(security.MAX_FAILED_ATTEMPTS):
        with pytest.raises(security.AuthError):
            security.authenticate(db, "shilpan", "wrong")
    db.commit()

    # Even the correct password is refused while locked.
    with pytest.raises(security.AuthError):
        security.authenticate(db, "shilpan", OWNER_PASSWORD)


def test_failed_logins_are_audited(db, owner):
    from app.models import AuditAction, AuditEvent

    with pytest.raises(security.AuthError):
        security.authenticate(db, "shilpan", "wrong")
    db.commit()

    events = db.query(AuditEvent).filter(AuditEvent.action == AuditAction.LOGIN_FAILED).all()
    assert events


def test_changing_a_password_kills_existing_sessions(db, owner):
    _user, token = security.authenticate(db, "shilpan", OWNER_PASSWORD)
    db.commit()
    assert security.resolve_session(db, token) is not None

    security.set_password(db, owner, "a-brand-new-password", actor="shilpan")
    security.revoke_all_sessions(db, owner.id)
    db.commit()

    assert security.resolve_session(db, token) is None


def test_a_deactivated_user_cannot_use_an_existing_session(db, accountant):
    _user, token = security.authenticate(db, "ca", ACCOUNTANT_PASSWORD)
    db.commit()
    assert security.resolve_session(db, token) is not None

    accountant.is_active = False
    db.commit()
    assert security.resolve_session(db, token) is None


# --- over HTTP -------------------------------------------------------------

def test_an_unauthenticated_request_is_refused(anon_client):
    for method, path in [
        ("get", "/api/orders"),
        ("get", "/api/customers"),
        ("get", "/api/audit"),
        ("post", "/api/customers"),
    ]:
        response = getattr(anon_client, method)(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_health_stays_public_so_the_installer_can_check_it(anon_client):
    assert anon_client.get("/api/health").status_code == 200


def test_login_over_http_sets_an_httponly_cookie(anon_client, owner):
    response = anon_client.post(
        "/api/auth/login", json={"username": "shilpan", "password": OWNER_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "owner"

    cookie = response.headers.get("set-cookie", "")
    assert "urjapod_session=" in cookie
    assert "HttpOnly" in cookie, "the session cookie must not be readable from JavaScript"


def test_me_reports_the_permission_set_the_ui_uses(client):
    body = client.get("/api/auth/me").json()
    assert body["user"]["username"] == "shilpan"
    assert "document.void" in body["permissions"]


def test_the_accountant_sees_a_smaller_permission_set(accountant_client):
    body = accountant_client.get("/api/auth/me").json()
    assert body["user"]["role"] == "accountant"
    assert "document.create" in body["permissions"]
    assert "document.void" not in body["permissions"]
    assert "document.delete" not in body["permissions"]


# --- the refusals that matter ---------------------------------------------

def test_the_accountant_cannot_void_an_invoice(
    accountant_client, db, company, tax_rates, gujarat_customer, order_factory
):
    from app.services import invoices as invoice_service

    order = order_factory(gujarat_customer)
    invoice = invoice_service.create_tax_invoice(db, order, actor="setup")
    invoice_service.issue(db, invoice, actor="setup")
    db.commit()

    response = accountant_client.post(
        f"/api/invoices/{invoice.id}/void", json={"reason": "I changed my mind"}
    )
    assert response.status_code == 403
    assert "cannot document.void" in response.json()["detail"]

    db.refresh(invoice)
    assert str(invoice.status) == "issued", "the invoice was voided despite the refusal"


def test_the_accountant_cannot_delete_an_invoice(
    accountant_client, db, company, tax_rates, gujarat_customer, order_factory
):
    from app.services import invoices as invoice_service

    order = order_factory(gujarat_customer)
    invoice = invoice_service.create_tax_invoice(db, order, actor="setup")
    invoice_service.issue(db, invoice, actor="setup")
    db.commit()

    response = accountant_client.request(
        "DELETE",
        f"/api/invoices/{invoice.id}",
        params={"reason": "oops", "confirm": invoice.number},
    )
    assert response.status_code == 403
    assert db.get(type(invoice), invoice.id) is not None


def test_the_accountant_cannot_edit_gst_rates(accountant_client):
    """Changing a rate changes what every future invoice charges."""
    response = accountant_client.post(
        "/api/tax-rates",
        json={"hsn_code": "8507", "effective_from": "2025-04-01", "rate_percent": "5"},
    )
    assert response.status_code == 403


def test_the_accountant_cannot_manage_users(accountant_client):
    assert accountant_client.get("/api/auth/users").status_code == 403
    response = accountant_client.post(
        "/api/auth/users",
        json={
            "username": "backdoor", "full_name": "Back Door",
            "password": "a-long-enough-password", "role": "owner",
        },
    )
    assert response.status_code == 403


def test_the_accountant_cannot_change_tally_ledgers(accountant_client):
    assert accountant_client.post("/api/tally/mappings/seed").status_code == 403


def test_the_viewer_cannot_create_anything(viewer_client):
    assert viewer_client.post("/api/customers", json={"name": "X"}).status_code == 403
    assert viewer_client.get("/api/reports/order-pipeline").status_code == 200


def test_a_refusal_is_itself_recorded(accountant_client, db):
    from app.models import AuditAction, AuditEvent

    accountant_client.post("/api/tally/mappings/seed")

    denials = db.query(AuditEvent).filter(
        AuditEvent.action == AuditAction.PERMISSION_DENIED
    ).all()
    assert denials, "a refused attempt left no trace"
    assert denials[-1].actor == "ca"
    assert denials[-1].context_json["permission"] == "settings.edit"


def test_the_accountant_can_still_do_the_day_job(
    accountant_client, db, company, tax_rates, gujarat_customer, order_factory
):
    """The restrictions must not stop him working."""
    order = order_factory(gujarat_customer)

    assert accountant_client.post(f"/api/orders/{order.id}/milestones", json={}).status_code == 200
    invoice = accountant_client.post(
        f"/api/orders/{order.id}/tax-invoice", json={"issue": True}
    )
    assert invoice.status_code == 201
    assert accountant_client.post(
        f"/api/orders/{order.id}/receipts",
        json={"amount": "1000", "received_on": "2025-08-20"},
    ).status_code == 201
    assert accountant_client.get("/api/reports/order-pipeline").status_code == 200


# --- bootstrap -------------------------------------------------------------

def test_the_first_run_creates_an_owner_then_closes_the_door(anon_client, db):
    assert anon_client.get("/api/auth/setup-required").json()["setup_required"] is True

    payload = {
        "username": "shilpan", "full_name": "Shilpan Shukla",
        "password": "first-owner-password", "role": "owner",
    }
    first = anon_client.post("/api/auth/bootstrap", json=payload)
    assert first.status_code == 201
    assert first.json()["user"]["role"] == "owner"

    assert anon_client.get("/api/auth/setup-required").json()["setup_required"] is False
    # The door is shut: a second call cannot mint another owner.
    second = anon_client.post(
        "/api/auth/bootstrap", json={**payload, "username": "intruder"}
    )
    assert second.status_code == 409


def test_bootstrap_always_creates_an_owner_even_if_asked_for_less(anon_client, db):
    response = anon_client.post(
        "/api/auth/bootstrap",
        json={
            "username": "shilpan", "full_name": "Shilpan Shukla",
            "password": "first-owner-password", "role": "viewer",
        },
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "owner"


def test_the_last_owner_cannot_be_demoted(client, db, owner):
    response = client.post(f"/api/auth/users/{owner.id}/role", json={"role": "accountant"})
    assert response.status_code == 409
    assert "only owner" in response.json()["detail"]


def test_the_owner_can_create_an_accountant(client, db):
    response = client.post(
        "/api/auth/users",
        json={
            "username": "newca", "full_name": "New Accountant",
            "password": "another-long-password", "role": "accountant",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "accountant"
    assert response.json()["must_change_password"] is True
    assert "password" not in response.text.lower().split("must_change_password")[0]


def test_a_user_is_deactivated_not_deleted(client, db, accountant):
    """Their name is on audit rows; deleting the row would orphan history."""
    response = client.post(f"/api/auth/users/{accountant.id}/deactivate")
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert db.get(User, accountant.id) is not None
