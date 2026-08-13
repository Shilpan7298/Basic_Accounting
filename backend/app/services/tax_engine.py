"""The single place where a rate is ever multiplied by an amount.

Nothing else in this codebase may compute GST. If you find yourself writing
``* rate / 100`` anywhere outside this module, the answer is a function here.

The rules it enforces (CLAUDE.md → Domain rules):

* the CGST+SGST vs IGST split is *derived* from place of supply vs the home
  state, never chosen by a user;
* the rate comes from ``tax_rates`` keyed by (HSN, document date), so a
  reprint of a 2024 invoice uses the 2024 rate;
* exports under LUT and reverse-charge supplies are zero-rated on the face of
  the invoice but keep their notional rate for reporting;
* the invoice total is rounded to the nearest rupee with the delta on an
  explicit round-off line, so the parts always sum to the whole.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TaxRate
from .money import q2, q4, round_to_rupee

ZERO = Decimal("0")


class MissingTaxRate(LookupError):
    """No rate row covers this HSN on this date. Never guess one."""


@dataclass(frozen=True)
class TaxContext:
    document_date: date
    home_state_code: str
    place_of_supply_state_code: str | None
    is_export: bool = False
    is_sez: bool = False
    lut_no: str | None = None
    is_reverse_charge: bool = False

    @property
    def is_intra_state(self) -> bool:
        """Gujarat → Gujarat. Exports and SEZ are inter-state by definition."""
        if self.is_export or self.is_sez:
            return False
        if not self.place_of_supply_state_code:
            # Unknown place of supply is not an excuse to guess intra-state,
            # which would under-collect. Treat as inter-state.
            return False
        return self.place_of_supply_state_code == self.home_state_code

    @property
    def is_zero_rated(self) -> bool:
        """Export/SEZ under LUT carries no tax; without an LUT it does."""
        return (self.is_export or self.is_sez) and bool(self.lut_no)


@dataclass
class TaxableLine:
    description: str
    qty: Decimal
    unit_price: Decimal
    hsn_code: str | None = None
    discount_percent: Decimal = ZERO
    uom: str = "NOS"
    item_id: str | None = None
    # Escape hatch for a line whose rate is known but not in the master
    # (e.g. freight recovered at the goods rate). Still never hardcoded here.
    rate_percent_override: Decimal | None = None


@dataclass
class ComputedLine:
    line_no: int
    description: str
    hsn_code: str | None
    qty: Decimal
    uom: str
    unit_price: Decimal
    discount_percent: Decimal
    taxable_value: Decimal
    gst_rate_percent: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal
    line_total: Decimal
    item_id: str | None = None


@dataclass
class HsnSummaryRow:
    hsn_code: str
    taxable_value: Decimal
    rate_percent: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal


@dataclass
class TaxComputation:
    lines: list[ComputedLine]
    taxable_value: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal
    round_off: Decimal
    grand_total: Decimal
    is_intra_state: bool
    hsn_summary: list[HsnSummaryRow] = field(default_factory=list)

    @property
    def tax_total(self) -> Decimal:
        return q2(self.cgst_amount + self.sgst_amount + self.igst_amount + self.cess_amount)


def resolve_rate(db: Session, hsn_code: str, on: date) -> tuple[Decimal, Decimal]:
    """``(gst_rate_percent, cess_percent)`` effective for this HSN on this date."""
    row = db.execute(
        select(TaxRate)
        .where(
            TaxRate.hsn_code == hsn_code,
            TaxRate.effective_from <= on,
            (TaxRate.effective_to.is_(None)) | (TaxRate.effective_to >= on),
        )
        .order_by(TaxRate.effective_from.desc())
    ).scalars().first()
    if row is None:
        raise MissingTaxRate(
            f"no GST rate configured for HSN {hsn_code} effective {on.isoformat()}"
        )
    return Decimal(row.rate_percent), Decimal(row.cess_percent or ZERO)


def compute_line_taxable_value(line: TaxableLine) -> Decimal:
    gross = Decimal(line.qty) * Decimal(line.unit_price)
    discount = gross * Decimal(line.discount_percent or ZERO) / Decimal("100")
    return q2(gross - discount)


def compute(
    db: Session,
    lines: list[TaxableLine],
    ctx: TaxContext,
    *,
    apply_round_off: bool = True,
) -> TaxComputation:
    """Compute a full document. This is the entry point; use nothing else."""
    computed: list[ComputedLine] = []

    for index, line in enumerate(lines, start=1):
        taxable = compute_line_taxable_value(line)

        if line.rate_percent_override is not None:
            rate, cess_rate = Decimal(line.rate_percent_override), ZERO
        elif line.hsn_code:
            rate, cess_rate = resolve_rate(db, line.hsn_code, ctx.document_date)
        else:
            raise MissingTaxRate(
                f"line {index} ({line.description!r}) has no HSN code; "
                "every invoiceable line needs one"
            )

        # Zero-rated and reverse-charge supplies carry no tax on the face of
        # the document, but we keep the notional rate for GSTR reporting.
        charge_tax = not (ctx.is_zero_rated or ctx.is_reverse_charge)
        cgst = sgst = igst = cess = ZERO
        if charge_tax:
            if ctx.is_intra_state:
                half = q4(taxable * rate / Decimal("200"))
                cgst = q2(half)
                sgst = q2(half)
            else:
                igst = q2(taxable * rate / Decimal("100"))
            cess = q2(taxable * cess_rate / Decimal("100"))

        computed.append(
            ComputedLine(
                line_no=index,
                description=line.description,
                hsn_code=line.hsn_code,
                qty=q4(Decimal(line.qty)),
                uom=line.uom,
                unit_price=q4(Decimal(line.unit_price)),
                discount_percent=q2(Decimal(line.discount_percent or ZERO)),
                taxable_value=taxable,
                gst_rate_percent=q2(rate),
                cgst_amount=cgst,
                sgst_amount=sgst,
                igst_amount=igst,
                cess_amount=cess,
                line_total=q2(taxable + cgst + sgst + igst + cess),
                item_id=line.item_id,
            )
        )

    taxable_total = q2(sum((c.taxable_value for c in computed), ZERO))
    cgst_total = q2(sum((c.cgst_amount for c in computed), ZERO))
    sgst_total = q2(sum((c.sgst_amount for c in computed), ZERO))
    igst_total = q2(sum((c.igst_amount for c in computed), ZERO))
    cess_total = q2(sum((c.cess_amount for c in computed), ZERO))

    exact_total = q2(taxable_total + cgst_total + sgst_total + igst_total + cess_total)
    if apply_round_off:
        grand_total, round_off = round_to_rupee(exact_total)
    else:
        grand_total, round_off = exact_total, ZERO

    return TaxComputation(
        lines=computed,
        taxable_value=taxable_total,
        cgst_amount=cgst_total,
        sgst_amount=sgst_total,
        igst_amount=igst_total,
        cess_amount=cess_total,
        round_off=round_off,
        grand_total=grand_total,
        is_intra_state=ctx.is_intra_state,
        hsn_summary=build_hsn_summary(computed),
    )


def build_hsn_summary(lines: list[ComputedLine]) -> list[HsnSummaryRow]:
    """Per-(HSN, rate) rollup — the shape GSTR-1 and the invoice footer want."""
    buckets: dict[tuple[str, Decimal], HsnSummaryRow] = {}
    for line in lines:
        key = (line.hsn_code or "", line.gst_rate_percent)
        row = buckets.get(key)
        if row is None:
            buckets[key] = HsnSummaryRow(
                hsn_code=line.hsn_code or "",
                taxable_value=line.taxable_value,
                rate_percent=line.gst_rate_percent,
                cgst_amount=line.cgst_amount,
                sgst_amount=line.sgst_amount,
                igst_amount=line.igst_amount,
                cess_amount=line.cess_amount,
            )
        else:
            row.taxable_value = q2(row.taxable_value + line.taxable_value)
            row.cgst_amount = q2(row.cgst_amount + line.cgst_amount)
            row.sgst_amount = q2(row.sgst_amount + line.sgst_amount)
            row.igst_amount = q2(row.igst_amount + line.igst_amount)
            row.cess_amount = q2(row.cess_amount + line.cess_amount)
    return sorted(buckets.values(), key=lambda r: (r.hsn_code, r.rate_percent))
