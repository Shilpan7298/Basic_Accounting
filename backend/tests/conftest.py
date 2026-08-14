from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Point the app at a throwaway database and storage dir *before* importing it —
# the engine is built at import time.
_TMP = Path(os.environ.setdefault("PYTEST_STORAGE", "/tmp/urjapod-tests"))
_TMP.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_TMP)
os.environ["EXTRACTOR"] = "manual"

from app.db import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    Company,
    Customer,
    Item,
    SalesOrder,
    SalesOrderLine,
    Supplier,
    TaxRate,
)
from app.models import Role  # noqa: E402
from app.services import security  # noqa: E402
from app.services.audit import install_session_listener  # noqa: E402

install_session_listener(SessionLocal)

FIXTURES = BACKEND / "tests" / "fixtures"


@pytest.fixture()
def db():
    """A clean database per test. Financial invariants are order-dependent —
    a leaked row from a previous test would hide a numbering bug."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def company(db):
    row = Company(
        legal_name="Urjapod Energy Private Limited",
        gstin="24AAACC1206D1ZM",
        state_code="24",
        state_name="Gujarat",
        pan="AAACC1206D",
        address_line1="Plot 12, GIDC Estate",
        city="Ahmedabad",
        pincode="382445",
        bank_name="HDFC Bank",
        bank_account_no="50200012345678",
        bank_ifsc="HDFC0000123",
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture()
def tax_rates(db):
    rows = [
        TaxRate(hsn_code=hsn, effective_from=date(2020, 4, 1), rate_percent=Decimal(rate))
        for hsn, rate in [
            ("8507", "18"), ("8504", "18"), ("998719", "18"), ("7326", "18"),
        ]
    ]
    db.add_all(rows)
    db.commit()
    return rows


@pytest.fixture()
def gujarat_customer(db):
    """Intra-state: CGST + SGST."""
    row = Customer(
        name="Greenfield Textiles Limited",
        gstin="24AABCG1234M1ZU",
        state_code="24",
        place_of_supply_state_code="24",
        state_name="Gujarat",
        billing_address="Survey 220, Naroda GIDC\nAhmedabad 382330",
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture()
def maharashtra_customer(db):
    """Inter-state: IGST."""
    row = Customer(
        name="Solaris Infra Projects Private Limited",
        gstin="27AAACS7409B1ZN",
        state_code="27",
        place_of_supply_state_code="27",
        state_name="Maharashtra",
        billing_address="412, Sunrise Business Park\nMumbai 400093",
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture()
def supplier(db):
    row = Supplier(
        name="Voltcell Components Private Limited",
        gstin="29AAGCV2233P1ZT",
        state_code="29",
        state_name="Karnataka",
        billing_address="Plot 45, Electronic City\nBengaluru 560100",
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture()
def items(db):
    rows = [
        Item(sku="BESS-100", description="BESS 100 kWh", hsn_code="8507",
             uom="NOS", default_unit_price=Decimal("1250000")),
        Item(sku="PCS-50", description="PCS 50 kW", hsn_code="8504",
             uom="NOS", default_unit_price=Decimal("375000")),
    ]
    db.add_all(rows)
    db.commit()
    return rows


def make_order(
    db,
    customer,
    *,
    po_number="TEST/PO/001",
    terms="30% advance along with PO, 60% before dispatch, 10% after commissioning",
    lines=(("BESS 100 kWh", "8507", "4", "1250000"),),
    po_date=None,
    delivery_due_date=None,
) -> SalesOrder:
    order = SalesOrder(
        customer_id=customer.id,
        customer_po_number=po_number,
        customer_po_date=po_date or date(2025, 8, 4),
        delivery_due_date=delivery_due_date or date(2025, 9, 30),
        payment_terms_text=terms,
        status="received",
    )
    db.add(order)
    db.flush()

    total = Decimal("0")
    for index, (description, hsn, qty, price) in enumerate(lines, start=1):
        line_total = Decimal(qty) * Decimal(price)
        db.add(
            SalesOrderLine(
                sales_order_id=order.id,
                line_no=index,
                description=description,
                hsn_code=hsn,
                qty=Decimal(qty),
                uom="NOS",
                unit_price=Decimal(price),
                line_total=line_total,
            )
        )
        total += line_total
    order.order_value = total
    db.commit()
    db.refresh(order)
    return order


@pytest.fixture()
def order_factory(db):
    def _make(customer, **kwargs):
        return make_order(db, customer, **kwargs)

    return _make


# --- users -----------------------------------------------------------------
# Passwords are intentionally long enough to satisfy the strength check.
OWNER_PASSWORD = "owner-password-1"
ACCOUNTANT_PASSWORD = "accountant-password-1"
VIEWER_PASSWORD = "viewer-password-1"


@pytest.fixture()
def owner(db):
    user = security.create_user(
        db, username="shilpan", full_name="Shilpan Shukla", password=OWNER_PASSWORD,
        role=Role.OWNER, actor="test-setup",
    )
    db.commit()
    return user


@pytest.fixture()
def accountant(db):
    user = security.create_user(
        db, username="ca", full_name="Office Accountant", password=ACCOUNTANT_PASSWORD,
        role=Role.ACCOUNTANT, actor="test-setup",
    )
    db.commit()
    return user


@pytest.fixture()
def viewer(db):
    user = security.create_user(
        db, username="auditor", full_name="External Auditor", password=VIEWER_PASSWORD,
        role=Role.VIEWER, actor="test-setup",
    )
    db.commit()
    return user


def _make_client(db):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app), app


@pytest.fixture()
def anon_client(db):
    """Not signed in. Used to prove endpoints actually refuse."""
    test_client, app = _make_client(db)
    with test_client as c:
        yield c
    app.dependency_overrides.clear()


def _sign_in(test_client, username, password):
    response = test_client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return test_client


@pytest.fixture()
def client(db, owner):
    """Signed in as the owner — the default for most tests."""
    test_client, app = _make_client(db)
    with test_client as c:
        _sign_in(c, owner.username, OWNER_PASSWORD)
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def accountant_client(db, accountant):
    test_client, app = _make_client(db)
    with test_client as c:
        _sign_in(c, accountant.username, ACCOUNTANT_PASSWORD)
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def viewer_client(db, viewer):
    test_client, app = _make_client(db)
    with test_client as c:
        _sign_in(c, viewer.username, VIEWER_PASSWORD)
        yield c
    app.dependency_overrides.clear()
