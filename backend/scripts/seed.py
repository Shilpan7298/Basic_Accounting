"""Seed a usable database: company, masters, GST rates, Tally placeholders.

Idempotent — run it as often as you like. It never overwrites a value you
have edited, which matters most for the Tally ledger names.

Run:  python backend/scripts/seed.py [--demo]

``--demo`` additionally walks one order through the whole chain so there is
something to click on the dashboard.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    Company,
    Customer,
    Item,
    Supplier,
    TaxRate,
)
from app.services import invoices as invoice_service  # noqa: E402
from app.services import orders as order_service  # noqa: E402
from app.services import purchase as purchase_service  # noqa: E402
from app.services import security  # noqa: E402
from app.services import tally as tally_service  # noqa: E402
from app.services.audit import install_session_listener  # noqa: E402

install_session_listener(SessionLocal)

ACTOR = "seed-script"

COMPANY = {
    "legal_name": "Urjapod Energy Private Limited",
    "trade_name": "Urjapod",
    "gstin": "24AAACC1206D1ZM",
    "state_code": "24",
    "state_name": "Gujarat",
    "pan": "AAACC1206D",
    "address_line1": "Plot 12, GIDC Estate",
    "address_line2": "Odhav",
    "city": "Ahmedabad",
    "pincode": "382445",
    "email": "accounts@urjapod.example",
    "phone": "+91 79 4000 0000",
    "bank_name": "HDFC Bank",
    "bank_account_no": "50200012345678",
    "bank_ifsc": "HDFC0000123",
    "bank_branch": "Odhav, Ahmedabad",
}

# Rates as at the start of FY 25-26. Keyed by HSN and date so an older invoice
# still reprints at the rate that applied on its own date.
TAX_RATES = [
    ("8507", "18", "Lithium-ion accumulators — batteries and packs"),
    ("8504", "18", "Static converters / PCS / inverters"),
    ("8537", "18", "Boards and panels with switching apparatus (BMS)"),
    ("8544", "18", "Insulated wire, cable and busbar assemblies"),
    ("7326", "18", "Other articles of iron or steel — enclosures"),
    ("7308", "18", "Structures of iron or steel — racks and mounting"),
    ("998719", "18", "Installation, commissioning and maintenance services"),
]

ITEMS = [
    ("BESS-100", "Battery Energy Storage System 100 kWh, LFP, IP54", "8507", "NOS", "1250000"),
    ("BESS-215", "Battery Energy Storage System 215 kWh, LFP", "8507", "NOS", "2480000"),
    ("PCS-50", "Power Conversion System 50 kW bidirectional", "8504", "NOS", "375000"),
    ("CELL-280", "LFP prismatic cell 3.2V 280Ah Grade A", "8507", "NOS", "4950"),
    ("BMS-16S", "BMS 16S 200A with CAN interface", "8537", "NOS", "18500"),
    ("ENC-1800", "IP54 outdoor enclosure 1800x900x600", "7326", "NOS", "42000"),
    ("SVC-COMM", "Installation, commissioning and site acceptance testing", "998719", "LOT", "250000"),
    ("SVC-AMC", "Annual maintenance contract", "998719", "LOT", "140000"),
]

CUSTOMERS = [
    ("Solaris Infra Projects Private Limited", "27AAACS7409B1ZN",
     "412, Sunrise Business Park\nAndheri East\nMumbai 400093"),
    ("Greenfield Textiles Limited", "24AABCG1234M1ZU",
     "Survey 220, Naroda GIDC\nAhmedabad 382330"),
    ("Northpoint Energy Solutions LLP", "04AAJFN8899K1ZE",
     "22 Industrial Area Phase II\nChandigarh 160002"),
]

SUPPLIERS = [
    ("Voltcell Components Private Limited", "29AAGCV2233P1ZT",
     "Plot 45, Electronic City Phase 1\nBengaluru 560100"),
    ("Shakti Enclosures & Fabrication", "24AAKFS6677L1ZG",
     "Shed 8, Odhav Industrial Estate\nAhmedabad 382415"),
]


def seed_users(db) -> None:
    """A development owner and accountant, so both roles can be tried out.

    The passwords are obvious on purpose — this is the dev seed. A real
    install creates its owner through the first-run screen or
    ``urjapod create-owner``, never from a script with a password in it.
    """
    from app.models import Role, User

    # Note: the strength check refuses a password containing the username,
    # so these deliberately do not echo it — including as a substring.
    for username, full_name, role, password in [
        ("shilpan", "Development Owner", Role.OWNER, "urjapod-dev-9812"),
        ("bookkeeper", "Development Accountant", Role.ACCOUNTANT, "urjapod-dev-4471"),
    ]:
        if db.execute(select(User).where(User.username == username)).scalars().first():
            continue
        security.create_user(
            db, username=username, full_name=full_name, password=password,
            role=role, actor=ACTOR,
        )
        print(f"  + user {username} / {password}  ({role})")


def seed_masters(db) -> None:
    if db.execute(select(Company)).scalars().first() is None:
        db.add(Company(**COMPANY))
        print("  + company profile")

    fy_start = date(2020, 4, 1)
    for hsn, rate, description in TAX_RATES:
        exists = db.execute(
            select(TaxRate).where(TaxRate.hsn_code == hsn, TaxRate.effective_to.is_(None))
        ).scalars().first()
        if exists:
            continue
        db.add(
            TaxRate(
                hsn_code=hsn,
                effective_from=fy_start,
                rate_percent=Decimal(rate),
                cess_percent=Decimal("0"),
                description=description,
            )
        )
        print(f"  + tax rate {hsn} @ {rate}%")

    for sku, description, hsn, uom, price in ITEMS:
        if db.execute(select(Item).where(Item.sku == sku)).scalars().first():
            continue
        db.add(
            Item(
                sku=sku,
                description=description,
                hsn_code=hsn,
                uom=uom,
                default_unit_price=Decimal(price),
                is_service=hsn.startswith("99"),
            )
        )
        print(f"  + item {sku}")

    for name, gstin, address in CUSTOMERS:
        if db.execute(select(Customer).where(Customer.gstin == gstin)).scalars().first():
            continue
        order_service.find_or_create_customer(db, name, gstin, address, actor=ACTOR)
        print(f"  + customer {name}")

    for name, gstin, address in SUPPLIERS:
        if db.execute(select(Supplier).where(Supplier.gstin == gstin)).scalars().first():
            continue
        purchase_service.find_or_create_supplier(db, name, gstin, address, actor=ACTOR)
        print(f"  + supplier {name}")

    created = tally_service.seed_mappings(db)
    if created:
        print(f"  + {created} Tally ledger placeholders (edit them with your real names)")

    for customer in db.execute(select(Customer)).scalars():
        tally_service.ensure_customer_mapping(db, customer)


def seed_demo(db) -> None:
    """One order walked all the way through, so the dashboard is not empty."""
    from app.models import SalesOrder

    po_number = "SIPL/PO/2025-26/0187"
    if db.execute(
        select(SalesOrder).where(SalesOrder.customer_po_number == po_number)
    ).scalars().first():
        print("  = demo order already present")
        return

    today = date.today()
    payload = {
        "customer_name": "Solaris Infra Projects Private Limited",
        "customer_gstin": "27AAACS7409B1ZN",
        "customer_address": "412, Sunrise Business Park\nAndheri East\nMumbai 400093",
        "po_number": po_number,
        "po_date": today - timedelta(days=8),
        "delivery_due_date": today + timedelta(days=45),
        "delivery_location": "Solaris Infra Site Office, MIDC Ranjangaon, Pune 412220",
        "payment_terms_text": (
            "30% advance along with PO, 60% before dispatch, 10% after commissioning"
        ),
        "currency": "INR",
        "lines": [
            {"description": "Battery Energy Storage System 100 kWh, LFP, IP54",
             "hsn_code": "8507", "qty": "4", "uom": "NOS", "unit_price": "1250000"},
            {"description": "Power Conversion System 50 kW bidirectional",
             "hsn_code": "8504", "qty": "4", "uom": "NOS", "unit_price": "375000"},
            {"description": "Installation, commissioning and site acceptance testing",
             "hsn_code": "998719", "qty": "1", "uom": "LOT", "unit_price": "250000"},
        ],
    }
    order = order_service.create_from_approved_extraction(db, payload, actor=ACTOR)
    print(f"  + demo sales order {order.customer_po_number} ({order.order_value})")

    milestones, parsed = order_service.build_milestones(db, order, actor=ACTOR)
    print(f"  + {len(milestones)} milestones parsed at confidence {parsed.confidence}")

    proforma = invoice_service.create_proforma(db, order, milestones[0], actor=ACTOR)
    invoice_service.issue(db, proforma, actor=ACTOR)
    print(f"  + proforma {proforma.number} for {proforma.grand_total}")

    order_service.transition(db, order, "in_production", actor=ACTOR)
    print(f"  = order moved to {order.status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="also create a worked example order")
    args = parser.parse_args()

    Base.metadata.create_all(engine)
    print(f"database: {engine.url}")

    db = SessionLocal()
    try:
        print("seeding users:")
        seed_users(db)
        db.commit()
        print("seeding masters:")
        seed_masters(db)
        db.commit()
        if args.demo:
            print("seeding demo data:")
            seed_demo(db)
            db.commit()
    finally:
        db.close()

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
