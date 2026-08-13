"""The no-model extractor.

It returns an empty draft with zero confidence. That sounds useless; it is
the opposite. Because it satisfies the same interface, the review screen has
exactly one code path, and the whole application is usable with no model, no
GPU and no internet — which is also what every failure of the other two
adapters degrades into.
"""

from __future__ import annotations

from ..models import ExtractionStatus
from .base import ExtractionRequest, ExtractionResult, ExtractorHealth


class ManualExtractor:
    name = "manual"

    def extract(self, req: ExtractionRequest) -> ExtractionResult:
        schema = req.schema
        # Build the emptiest instance the schema will accept, so the review
        # form renders every field rather than special-casing "no data".
        stub: dict[str, object] = {}
        for field_name, field in schema.model_fields.items():
            if field.is_required():
                stub[field_name] = "" if field.annotation is str else None
        try:
            data = schema.model_construct(**{**{k: None for k in schema.model_fields}, **stub})
        except Exception:  # pragma: no cover - model_construct does not validate
            data = None

        return ExtractionResult(
            status=ExtractionStatus.NEEDS_REVIEW,
            data=data,
            field_confidence={},
            overall_confidence=0.0,
            raw_response=None,
            warnings=["manual entry: no automatic extraction was attempted"],
            extractor_name=self.name,
            model_name=None,
        )

    def health(self) -> ExtractorHealth:
        return ExtractorHealth(
            name=self.name, available=True, detail="manual entry is always available"
        )
