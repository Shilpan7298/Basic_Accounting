"""Payment terms prose → a proforma milestone schedule.

The dialect this targets (confirmed with the business) is percentage splits
tied to named events:

    "30% advance along with PO, 60% before dispatch, 10% after commissioning"

Everything about this module is built around the assumption that it will
sometimes be wrong. It returns a confidence and a list of warnings; it never
silently produces a schedule it is not sure about. Anything that does not
resolve to exactly 100% is handed to the manual schedule builder instead.
That is a design decision, not a limitation — a wrong milestone split becomes
a wrong proforma, which becomes a customer conversation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..models import MilestoneTrigger
from .money import q2

HUNDRED = Decimal("100")

_WORD_NUMBERS = {
    "ten": 10, "fifteen": 15, "twenty": 20, "twenty five": 25, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "seventy five": 75,
    "eighty": 80, "ninety": 90, "hundred": 100, "full": 100, "entire": 100,
}

# Ordered most-specific first: "before dispatch" must beat a bare "delivery"
# appearing later in the same clause.
_TRIGGER_RULES: list[tuple[MilestoneTrigger, tuple[str, ...]]] = [
    (
        MilestoneTrigger.AFTER_COMMISSIONING,
        ("after commissioning", "on commissioning", "post commissioning",
         "commissioning", "installation", "successful testing", "site acceptance"),
    ),
    (
        MilestoneTrigger.BEFORE_DISPATCH,
        ("before dispatch", "prior to dispatch", "before despatch",
         "prior to despatch", "against proforma", "against pro-forma",
         "before shipment", "prior to shipment", "against pi", "ready for dispatch"),
    ),
    (
        MilestoneTrigger.ON_DELIVERY,
        ("on delivery", "against delivery", "after delivery", "on receipt of material",
         "upon delivery", "delivery at site", "on dispatch", "after dispatch",
         "against lr", "against gr", "delivery"),
    ),
    (
        MilestoneTrigger.ON_PO,
        ("along with po", "with po", "advance along", "advance with", "advance against po",
         "advance", "on order", "with order", "along with order", "at the time of order",
         "token advance", "booking amount"),
    ),
]

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PERCENT_WORD_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:percent|pct|per cent)\b", re.I)
_NET_DAYS_RE = re.compile(
    r"(?:with ?in|net|after)\s*(\d{1,3})\s*(?:days?|d\b)", re.I
)
_BALANCE_RE = re.compile(r"\b(balance|remaining|rest|thereafter)\b", re.I)
_SPLIT_RE = re.compile(
    r"[;\n]"
    r"|,(?![^()]*\))"
    r"|\band then\b"
    r"|\bthereafter\b"
    # "…dispatch and 10% on delivery" — split on 'and' only when a new
    # milestone clearly follows, so "packing and forwarding" stays intact.
    r"|\s+and\s+(?=(?:\d+(?:\.\d+)?\s*(?:%|percent|per cent)|(?:balance|remaining|rest)\b))",
    re.I,
)


@dataclass
class ParsedMilestone:
    seq: int
    label: str
    trigger_event: MilestoneTrigger
    percent: Decimal
    net_days: int | None = None
    source_clause: str = ""


@dataclass
class ParsedTerms:
    milestones: list[ParsedMilestone]
    confidence: float
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def total_percent(self) -> Decimal:
        return q2(sum((m.percent for m in self.milestones), Decimal("0")))

    @property
    def is_usable(self) -> bool:
        """Usable means: it parsed, and it sums to exactly 100."""
        return bool(self.milestones) and self.total_percent == HUNDRED


def _classify(clause: str) -> tuple[MilestoneTrigger, int | None]:
    lowered = clause.lower()
    net_days_match = _NET_DAYS_RE.search(lowered)

    for trigger, keywords in _TRIGGER_RULES:
        for keyword in keywords:
            if keyword in lowered:
                # "within 30 days of commissioning" keeps the event *and* the
                # offset — the event decides when it becomes due, the days
                # decide the due date.
                return trigger, int(net_days_match.group(1)) if net_days_match else None

    if net_days_match:
        return MilestoneTrigger.NET_DAYS, int(net_days_match.group(1))
    return MilestoneTrigger.MANUAL, None


def _find_percent(clause: str) -> Decimal | None:
    match = _PERCENT_RE.search(clause) or _PERCENT_WORD_RE.search(clause)
    if match:
        return q2(Decimal(match.group(1)))
    lowered = clause.lower()
    for word, value in sorted(_WORD_NUMBERS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b\s*(?:percent|per cent|%)", lowered):
            return Decimal(value)
    return None


def _label_for(trigger: MilestoneTrigger, clause: str) -> str:
    cleaned = re.sub(r"\s+", " ", clause).strip(" .,-")
    if cleaned:
        return cleaned[:1].upper() + cleaned[1:] if len(cleaned) <= 120 else cleaned[:120]
    return {
        MilestoneTrigger.ON_PO: "Advance along with PO",
        MilestoneTrigger.BEFORE_DISPATCH: "Before dispatch",
        MilestoneTrigger.ON_DELIVERY: "On delivery",
        MilestoneTrigger.AFTER_COMMISSIONING: "After commissioning",
        MilestoneTrigger.NET_DAYS: "Credit period",
        MilestoneTrigger.MANUAL: "Milestone",
    }[trigger]


def parse(text: str | None) -> ParsedTerms:
    """Parse payment-terms prose. Never raises; reports confidence instead."""
    if not text or not text.strip():
        return ParsedTerms([], 0.0, ["no payment terms text supplied"], raw_text=text or "")

    raw = text.strip()
    clauses = [c.strip() for c in _SPLIT_RE.split(raw) if c and c.strip()]
    warnings: list[str] = []
    milestones: list[ParsedMilestone] = []
    balance_slots: list[int] = []

    for clause in clauses:
        percent = _find_percent(clause)
        is_balance = percent is None and bool(_BALANCE_RE.search(clause))
        if percent is None and not is_balance:
            # A clause with no number is usually boilerplate ("payment by NEFT",
            # "subject to satisfactory performance"). Skipping is right, but say so.
            if len(clause) > 3:
                warnings.append(f"ignored clause with no percentage: {clause[:80]!r}")
            continue

        trigger, net_days = _classify(clause)
        if trigger is MilestoneTrigger.MANUAL:
            warnings.append(f"could not identify a trigger event in: {clause[:80]!r}")

        milestones.append(
            ParsedMilestone(
                seq=len(milestones) + 1,
                label=_label_for(trigger, clause),
                trigger_event=trigger,
                percent=percent if percent is not None else Decimal("0"),
                net_days=net_days,
                source_clause=clause,
            )
        )
        if is_balance:
            balance_slots.append(len(milestones) - 1)

    # "balance on delivery" — resolve to whatever is left, but only if exactly
    # one such clause exists. Two would be ambiguous and we do not guess.
    if len(balance_slots) == 1:
        known = sum(
            (m.percent for i, m in enumerate(milestones) if i != balance_slots[0]),
            Decimal("0"),
        )
        remainder = q2(HUNDRED - known)
        if remainder > 0:
            milestones[balance_slots[0]].percent = remainder
        else:
            warnings.append("a 'balance' clause was found but nothing is left to allocate")
    elif len(balance_slots) > 1:
        warnings.append("more than one 'balance' clause — cannot resolve unambiguously")

    if not milestones:
        return ParsedTerms([], 0.0, warnings + ["no percentage milestones found"], raw_text=raw)

    total = q2(sum((m.percent for m in milestones), Decimal("0")))
    confidence = 0.95
    if total != HUNDRED:
        warnings.append(f"milestone percentages sum to {total}%, not 100%")
        confidence = 0.25
    if any(m.trigger_event is MilestoneTrigger.MANUAL for m in milestones):
        confidence = min(confidence, 0.55)
    if warnings and confidence > 0.5:
        confidence -= 0.05 * min(len(warnings), 4)

    for index, milestone in enumerate(milestones, start=1):
        milestone.seq = index

    return ParsedTerms(milestones, round(confidence, 2), warnings, raw_text=raw)


def allocate_amounts(order_value: Decimal, percents: list[Decimal]) -> list[Decimal]:
    """Split a value by percentages so the parts sum to the whole, exactly.

    The last milestone absorbs the rounding remainder. Without this, three
    milestones of 33.33% on ₹100 quietly lose a paisa and the final invoice
    never closes the order.
    """
    total = q2(Decimal(order_value))
    amounts: list[Decimal] = []
    running = Decimal("0")
    for percent in percents[:-1]:
        amount = q2(total * Decimal(percent) / HUNDRED)
        amounts.append(amount)
        running += amount
    if percents:
        amounts.append(q2(total - running))
    return amounts


def due_date_for(
    trigger: MilestoneTrigger,
    *,
    po_date: date | None,
    delivery_due_date: date | None,
    net_days: int | None,
) -> date | None:
    """Only computed when it is genuinely derivable. ``None`` is honest."""
    offset = timedelta(days=net_days or 0)
    if trigger is MilestoneTrigger.ON_PO and po_date:
        return po_date + offset
    if trigger in (MilestoneTrigger.BEFORE_DISPATCH, MilestoneTrigger.ON_DELIVERY):
        return delivery_due_date + offset if delivery_due_date else None
    if trigger is MilestoneTrigger.NET_DAYS and net_days is not None:
        base = delivery_due_date or po_date
        return base + offset if base else None
    # AFTER_COMMISSIONING has no knowable date until commissioning happens.
    return None
