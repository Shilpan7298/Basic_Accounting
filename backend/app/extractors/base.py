"""The one interface all document understanding goes through.

Nothing outside this package may import a model-provider SDK. Nothing outside
this package may hold provider prompt text. Adding a fourth backend means
adding a module here and a line in the registry — no caller changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from ..models import ExtractionStatus
from ..schemas.extraction import schema_for

T = TypeVar("T", bound=BaseModel)


@dataclass
class ExtractionRequest:
    """Both the bytes and the text layer travel together, always.

    A text-layer PDF should never cost a vision call; a scanned one has no
    text and needs the bytes. Carrying both from day one is why swapping in a
    text-only local model later does not require re-plumbing.
    """

    content: bytes
    content_type: str
    schema_name: str
    text: str | None = None
    filename: str | None = None
    hints: dict[str, Any] = field(default_factory=dict)

    @property
    def schema(self) -> type[BaseModel]:
        return schema_for(self.schema_name)

    @property
    def has_text_layer(self) -> bool:
        return bool(self.text and len(self.text.strip()) > 80)


@dataclass
class ExtractionResult(Generic[T]):
    status: ExtractionStatus
    data: T | None = None
    field_confidence: dict[str, float] = field(default_factory=dict)
    overall_confidence: float = 0.0
    raw_response: str | None = None
    warnings: list[str] = field(default_factory=list)
    error_text: str | None = None
    extractor_name: str = ""
    model_name: str | None = None
    duration_ms: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in (ExtractionStatus.SUCCEEDED, ExtractionStatus.NEEDS_REVIEW)


@dataclass
class ExtractorHealth:
    name: str
    available: bool
    detail: str = ""
    model_name: str | None = None


class DocumentExtractor(Protocol):
    name: str

    def extract(self, req: ExtractionRequest) -> ExtractionResult: ...

    def health(self) -> ExtractorHealth: ...


# --- shared post-validation ------------------------------------------------
# Applied to every adapter's output. A model claiming 0.99 confidence on a
# total that does not add up gets overruled here.

TOTALS_TOLERANCE = Decimal("1")


def cross_check(data: BaseModel, confidence: dict[str, float]) -> tuple[list[str], float]:
    """Arithmetic sanity checks. Returns ``(warnings, confidence_penalty)``."""
    warnings: list[str] = []
    penalty = 0.0

    lines = getattr(data, "lines", None) or []
    if not lines:
        warnings.append("no line items were extracted")
        penalty += 0.4

    line_sum = sum((line.line_total or Decimal("0") for line in lines), Decimal("0"))
    subtotal = getattr(data, "subtotal", None)
    if subtotal is not None and lines and abs(line_sum - subtotal) > TOTALS_TOLERANCE:
        warnings.append(
            f"line totals sum to {line_sum} but the stated subtotal is {subtotal}"
        )
        penalty += 0.35

    grand_total = getattr(data, "grand_total", None)
    if grand_total is not None and subtotal is not None and grand_total < subtotal:
        warnings.append(f"grand total {grand_total} is below the subtotal {subtotal}")
        penalty += 0.2

    for line_no, line in enumerate(lines, start=1):
        if line.qty <= 0:
            warnings.append(f"line {line_no} has a non-positive quantity")
            penalty += 0.1
        if line.unit_price < 0:
            warnings.append(f"line {line_no} has a negative unit price")
            penalty += 0.1
        if not line.hsn_code:
            warnings.append(f"line {line_no} has no usable HSN code — set it on review")
            penalty += 0.05

    date_field = "po_date" if hasattr(data, "po_date") else "offer_date"
    if getattr(data, date_field, None) is None:
        warnings.append(f"{date_field} could not be parsed")
        penalty += 0.2

    gstin_field = "customer_gstin" if hasattr(data, "customer_gstin") else "supplier_gstin"
    if getattr(data, gstin_field, None) is None:
        warnings.append(f"{gstin_field} missing or failed checksum — dropped rather than guessed")
        penalty += 0.1
        confidence[gstin_field] = 0.0

    return warnings, min(penalty, 1.0)


def finalise(
    data: BaseModel,
    confidence: dict[str, float],
    *,
    extractor_name: str,
    model_name: str | None,
    raw_response: str | None,
    review_threshold: float,
    duration_ms: int | None = None,
    extra_warnings: list[str] | None = None,
) -> ExtractionResult:
    """Apply cross-checks and decide succeeded vs needs_review."""
    warnings = list(extra_warnings or [])
    checks, penalty = cross_check(data, confidence)
    warnings.extend(checks)

    claimed = (
        sum(confidence.values()) / len(confidence) if confidence else 0.6
    )
    overall = max(0.0, min(1.0, claimed - penalty))

    status = (
        ExtractionStatus.SUCCEEDED
        if overall >= review_threshold and not warnings
        else ExtractionStatus.NEEDS_REVIEW
    )
    return ExtractionResult(
        status=status,
        data=data,
        field_confidence=confidence,
        overall_confidence=round(overall, 3),
        raw_response=raw_response,
        warnings=warnings,
        extractor_name=extractor_name,
        model_name=model_name,
        duration_ms=duration_ms,
    )
