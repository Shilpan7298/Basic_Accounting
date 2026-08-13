"""Money helpers: quantisation, Indian digit grouping, amount in words.

Templates never do arithmetic, so every number a template can print is
produced here first. ``fmt`` is what a template uses; the raw ``Decimal`` is
kept alongside it for anything that needs to compute.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
RUPEE = Decimal("1")


def to_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    """Parse anything invoice-ish into a Decimal: '₹ 1,23,456.78' → 123456.78."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int,)):
        return Decimal(value)
    if isinstance(value, float):
        # Route through str so 0.1 doesn't become 0.1000000000000000055511151231.
        return Decimal(str(value))
    text = str(value).strip()
    for junk in ("₹", "Rs.", "Rs", "INR", ",", " "):
        text = text.replace(junk, "")
    text = text.replace("(", "-").replace(")", "")
    if not text or text in {"-", "."}:
        return default
    try:
        return Decimal(text)
    except Exception:
        return default


def q2(value: Decimal) -> Decimal:
    """Quantise to paise, half-up (the convention Indian invoices use)."""
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return Decimal(value).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def round_to_rupee(value: Decimal) -> tuple[Decimal, Decimal]:
    """Return ``(rounded_total, round_off_delta)``.

    The delta is what goes on the round-off line, and it is signed so
    ``taxable + tax + round_off == rounded_total`` exactly.
    """
    exact = q2(value)
    rounded = exact.quantize(RUPEE, rounding=ROUND_HALF_UP)
    return rounded, q2(rounded - exact)


def fmt(value: Decimal | None, places: int = 2) -> str:
    """Indian digit grouping: 12345678.9 → '1,23,45,678.90'."""
    if value is None:
        return ""
    amount = Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    digits = f"{abs(amount):.{places}f}"
    whole, _, frac = digits.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{sign}{whole}.{frac}" if places else f"{sign}{whole}"


_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
    "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
    "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _under_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _under_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_under_hundred(rest))
    return " ".join(parts)


def amount_in_words(value: Decimal, currency: str = "INR") -> str:
    """Indian numbering system: crore / lakh / thousand, plus paise."""
    amount = q2(abs(Decimal(value)))
    rupees = int(amount)
    paise = int((amount - rupees) * 100)

    unit_word = "Rupees" if currency == "INR" else currency
    if rupees == 0:
        words = "Zero"
    else:
        chunks: list[str] = []
        crore, rest = divmod(rupees, 10_000_000)
        lakh, rest = divmod(rest, 100_000)
        thousand, hundred = divmod(rest, 1_000)
        if crore:
            # Above 99 crore the Indian system keeps counting in crore.
            chunks.append(
                f"{_under_thousand(crore) if crore >= 100 else _under_hundred(crore)} Crore"
            )
        if lakh:
            chunks.append(f"{_under_hundred(lakh)} Lakh")
        if thousand:
            chunks.append(f"{_under_hundred(thousand)} Thousand")
        if hundred:
            chunks.append(_under_thousand(hundred))
        words = " ".join(chunks)

    text = f"{unit_word} {words}"
    if paise:
        text += f" and {_under_hundred(paise)} Paise"
    text += " Only"
    if Decimal(value) < 0:
        text = "Minus " + text
    return text


def financial_year(on: date, start_month: int = 4) -> str:
    """'25-26' for any date in Apr-2025 .. Mar-2026."""
    start = on.year if on.month >= start_month else on.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"
