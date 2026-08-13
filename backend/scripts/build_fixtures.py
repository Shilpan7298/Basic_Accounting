"""Generate the anonymised sample documents used by the tests.

Real customer POs go in ``tests/fixtures/`` and replace these — that is the
point of the folder. Until they arrive, these give the extraction tests
something with a genuine text layer, realistic Indian formatting (dd/mm/yyyy
dates, 1,23,456.78 grouping, ₹ symbols) and the payment-terms dialect the
parser targets.

Run:  python backend/scripts/build_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

_styles = getSampleStyleSheet()
_normal = ParagraphStyle("n", parent=_styles["Normal"], fontSize=9, leading=12)
_title = ParagraphStyle("t", parent=_styles["Heading1"], fontSize=14, spaceAfter=6)
_small = ParagraphStyle("s", parent=_normal, fontSize=8)


def _table(rows, widths):
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, "#666666"),
                ("BACKGROUND", (0, 0), (-1, 0), "#dddddd"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def build_po_1(path: Path) -> None:
    """The clean case: three-way percentage split, GST-registered, inter-state."""
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph("SOLARIS INFRA PROJECTS PRIVATE LIMITED", _title),
        Paragraph(
            "412, Sunrise Business Park, Andheri East, Mumbai 400093, Maharashtra<br/>"
            "GSTIN: 27AAACS7409B1ZN &nbsp;|&nbsp; PAN: AAACS7409B &nbsp;|&nbsp; "
            "Tel: +91 22 4000 1234",
            _small,
        ),
        Spacer(1, 8 * mm),
        Paragraph("<b>PURCHASE ORDER</b>", _title),
        Paragraph(
            "PO No.: SIPL/PO/2025-26/0187<br/>"
            "PO Date: 04/08/2025<br/>"
            "Required Delivery Date: 30/09/2025",
            _normal,
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            "<b>To,</b><br/>Urjapod Energy Private Limited<br/>"
            "Plot 12, GIDC Estate, Ahmedabad 382445, Gujarat<br/>"
            "GSTIN: 24AAACC1206D1ZM",
            _normal,
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            "<b>Delivery Location:</b> Solaris Infra Site Office, Plot 7, MIDC Ranjangaon, "
            "Pune 412220, Maharashtra",
            _normal,
        ),
        Spacer(1, 4 * mm),
        _table(
            [
                ["Sr", "Description", "HSN", "Qty", "UOM", "Rate (Rs.)", "Amount (Rs.)"],
                [
                    "1",
                    "Battery Energy Storage System, 100 kWh,\nLFP chemistry, outdoor rated IP54,\n"
                    "including BMS and enclosure",
                    "8507",
                    "4",
                    "NOS",
                    "12,50,000.00",
                    "50,00,000.00",
                ],
                [
                    "2",
                    "Power Conversion System 50 kW,\nbidirectional, grid-tie",
                    "8504",
                    "4",
                    "NOS",
                    "3,75,000.00",
                    "15,00,000.00",
                ],
                [
                    "3",
                    "Installation, commissioning and\nsite acceptance testing",
                    "998719",
                    "1",
                    "LOT",
                    "2,50,000.00",
                    "2,50,000.00",
                ],
            ],
            [12 * mm, 62 * mm, 16 * mm, 12 * mm, 14 * mm, 28 * mm, 30 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            "Sub Total: Rs. 67,50,000.00<br/>"
            "GST @ 18%: Rs. 12,15,000.00<br/>"
            "<b>Grand Total: Rs. 79,65,000.00</b>",
            _normal,
        ),
        Spacer(1, 5 * mm),
        Paragraph(
            "<b>Payment Terms:</b> 30% advance along with PO, 60% before dispatch, "
            "10% after commissioning.",
            _normal,
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            "<b>Other Conditions:</b> Prices are firm and inclusive of packing and forwarding. "
            "Freight to be borne by supplier. Warranty 60 months from date of commissioning. "
            "Liquidated damages @ 0.5% per week of delay, capped at 5% of order value.",
            _small,
        ),
        Spacer(1, 8 * mm),
        Paragraph("For Solaris Infra Projects Private Limited<br/><br/>Authorised Signatory",
                  _normal),
    ]
    doc.build(story)


def build_po_2(path: Path) -> None:
    """The awkward case: 'balance' wording, intra-state Gujarat customer."""
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph("GREENFIELD TEXTILES LIMITED", _title),
        Paragraph(
            "Survey 220, Naroda GIDC, Ahmedabad 382330, Gujarat<br/>"
            "GSTIN: 24AABCG1234M1ZU &nbsp;|&nbsp; Tel: +91 79 2281 5566",
            _small,
        ),
        Spacer(1, 8 * mm),
        Paragraph("<b>PURCHASE ORDER</b>", _title),
        Paragraph(
            "Order No: GTL-PO-25-0442 &nbsp;&nbsp; Dated: 18-07-2025<br/>"
            "Delivery required by: 15-10-2025",
            _normal,
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            "<b>Supplier:</b><br/>Urjapod Energy Private Limited<br/>"
            "Plot 12, GIDC Estate, Ahmedabad 382445, Gujarat<br/>GSTIN: 24AAACC1206D1ZM",
            _normal,
        ),
        Spacer(1, 4 * mm),
        _table(
            [
                ["Sr", "Item Description", "HSN", "Qty", "UOM", "Unit Rate", "Value"],
                [
                    "1",
                    "Lithium-ion battery pack 215 kWh\nfor peak shaving application",
                    "8507",
                    "2",
                    "NOS",
                    "24,80,000.00",
                    "49,60,000.00",
                ],
                [
                    "2",
                    "Annual maintenance contract - year 1",
                    "998719",
                    "1",
                    "LOT",
                    "1,40,000.00",
                    "1,40,000.00",
                ],
            ],
            [12 * mm, 66 * mm, 16 * mm, 12 * mm, 14 * mm, 28 * mm, 28 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph("Total Basic Value: Rs. 51,00,000.00", _normal),
        Spacer(1, 5 * mm),
        Paragraph(
            "<b>Terms of Payment:</b> 70% against proforma invoice prior to despatch, "
            "balance 30% within 30 days of commissioning.",
            _normal,
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            "Note: Kindly mention our PO number on all invoices. Material to be delivered "
            "at our Naroda works during working hours only.",
            _small,
        ),
        Spacer(1, 8 * mm),
        Paragraph("For Greenfield Textiles Limited<br/><br/>Purchase Manager", _normal),
    ]
    doc.build(story)


def build_po_3(path: Path) -> None:
    """The one the parser should refuse: terms that do not sum to 100."""
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph("NORTHPOINT ENERGY SOLUTIONS LLP", _title),
        Paragraph(
            "22 Industrial Area Phase II, Chandigarh 160002<br/>GSTIN: 04AAJFN8899K1ZE",
            _small,
        ),
        Spacer(1, 8 * mm),
        Paragraph("<b>PURCHASE ORDER</b>", _title),
        Paragraph("PO Number: NES/2025/0091<br/>Date: 01-09-2025", _normal),
        Spacer(1, 4 * mm),
        Paragraph(
            "<b>Vendor:</b> Urjapod Energy Private Limited, Ahmedabad, Gujarat<br/>"
            "GSTIN: 24AAACC1206D1ZM",
            _normal,
        ),
        Spacer(1, 4 * mm),
        _table(
            [
                ["Sr", "Description", "HSN", "Qty", "UOM", "Rate", "Amount"],
                [
                    "1",
                    "Battery module 50 kWh with rack",
                    "8507",
                    "6",
                    "NOS",
                    "6,20,000.00",
                    "37,20,000.00",
                ],
            ],
            [12 * mm, 66 * mm, 16 * mm, 12 * mm, 14 * mm, 28 * mm, 28 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph("Total: Rs. 37,20,000.00", _normal),
        Spacer(1, 5 * mm),
        Paragraph(
            "<b>Payment:</b> 50% advance, 40% before dispatch. "
            "Retention to be discussed separately.",
            _normal,
        ),
    ]
    doc.build(story)


def build_offer_1(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph("VOLTCELL COMPONENTS PRIVATE LIMITED", _title),
        Paragraph(
            "Plot 45, Electronic City Phase 1, Bengaluru 560100, Karnataka<br/>"
            "GSTIN: 29AAGCV2233P1ZT &nbsp;|&nbsp; sales@example-voltcell.in",
            _small,
        ),
        Spacer(1, 8 * mm),
        Paragraph("<b>QUOTATION</b>", _title),
        Paragraph(
            "Quotation Ref: VCP/QT/25-26/0311<br/>Date: 22/07/2025<br/>"
            "Validity: 30/08/2025",
            _normal,
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            "<b>To:</b> Urjapod Energy Private Limited, Plot 12, GIDC Estate, "
            "Ahmedabad 382445, Gujarat",
            _normal,
        ),
        Spacer(1, 4 * mm),
        _table(
            [
                ["Sr", "Description", "HSN", "Qty", "UOM", "Rate", "Amount"],
                [
                    "1",
                    "LFP prismatic cell 3.2V 280Ah, Grade A\nwith matched IR",
                    "8507",
                    "320",
                    "NOS",
                    "4,950.00",
                    "15,84,000.00",
                ],
                [
                    "2",
                    "BMS 16S 200A with CAN interface",
                    "8537",
                    "20",
                    "NOS",
                    "18,500.00",
                    "3,70,000.00",
                ],
                [
                    "3",
                    "Busbar and interconnect kit",
                    "8544",
                    "20",
                    "SET",
                    "6,200.00",
                    "1,24,000.00",
                ],
            ],
            [12 * mm, 62 * mm, 16 * mm, 14 * mm, 14 * mm, 26 * mm, 28 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph("Basic Value: Rs. 20,78,000.00 (GST 18% extra)", _normal),
        Spacer(1, 5 * mm),
        Paragraph(
            "<b>Payment Terms:</b> 50% advance along with order, balance 50% before dispatch.<br/>"
            "<b>Delivery:</b> 4 weeks from receipt of confirmed order and advance.<br/>"
            "<b>Freight:</b> Ex-works Bengaluru. Freight and insurance at buyer's account.<br/>"
            "<b>Warranty:</b> 24 months from date of supply against manufacturing defects.",
            _normal,
        ),
    ]
    doc.build(story)


def build_offer_2(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph("SHAKTI ENCLOSURES & FABRICATION", _title),
        Paragraph(
            "Shed 8, Odhav Industrial Estate, Ahmedabad 382415, Gujarat<br/>"
            "GSTIN: 24AAKFS6677L1ZG",
            _small,
        ),
        Spacer(1, 8 * mm),
        Paragraph("<b>OFFER / QUOTATION</b>", _title),
        Paragraph("Our Ref: SEF-Q-0925-14<br/>Dated: 05-09-2025<br/>Valid up to: 05-10-2025",
                  _normal),
        Spacer(1, 4 * mm),
        Paragraph("<b>Kind Attn:</b> Purchase Dept, Urjapod Energy Private Limited", _normal),
        Spacer(1, 4 * mm),
        _table(
            [
                ["Sr", "Description", "HSN", "Qty", "UOM", "Rate", "Amount"],
                [
                    "1",
                    "IP54 outdoor enclosure 1800x900x600,\npowder coated, with door and gland plate",
                    "7326",
                    "12",
                    "NOS",
                    "42,000.00",
                    "5,04,000.00",
                ],
                [
                    "2",
                    "Mounting rack assembly, galvanised",
                    "7308",
                    "12",
                    "SET",
                    "11,500.00",
                    "1,38,000.00",
                ],
            ],
            [12 * mm, 62 * mm, 16 * mm, 14 * mm, 14 * mm, 26 * mm, 28 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph("Total Basic: Rs. 6,42,000.00 plus GST as applicable", _normal),
        Spacer(1, 5 * mm),
        Paragraph(
            "<b>Payment:</b> 100% within 30 days of delivery.<br/>"
            "<b>Delivery:</b> 3 weeks from PO.<br/>"
            "<b>Freight:</b> Delivered at your Ahmedabad works, freight included.<br/>"
            "<b>Warranty:</b> 12 months against workmanship.",
            _normal,
        ),
    ]
    doc.build(story)


BUILDERS = {
    "customer_po_solaris.pdf": build_po_1,
    "customer_po_greenfield.pdf": build_po_2,
    "customer_po_northpoint_bad_terms.pdf": build_po_3,
    "supplier_offer_voltcell.pdf": build_offer_1,
    "supplier_offer_shakti.pdf": build_offer_2,
}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, builder in BUILDERS.items():
        target = FIXTURES / name
        builder(target)
        print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
