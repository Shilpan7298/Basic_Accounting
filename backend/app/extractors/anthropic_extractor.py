"""Anthropic adapter.

Uses the Messages API over plain HTTP (``httpx``) rather than the SDK, so the
container has one less dependency and the import-boundary rule is trivially
satisfied. The response shape is constrained with a tool schema derived from
the Pydantic model, so the API itself enforces the shape and the repair ladder
is a belt-and-braces path rather than the main one.

For a PDF with no text layer, the document is sent as a base64 ``document``
block so a scan still works — which is precisely what the local text-only
adapter cannot do.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from pydantic import ValidationError

from ..config import get_settings
from ..models import ExtractionStatus
from . import prompts
from .base import ExtractionRequest, ExtractionResult, ExtractorHealth, finalise
from .json_repair import JSONRepairFailed
from .json_repair import loads as repair_loads

API_VERSION = "2023-06-01"
TOOL_NAME = "record_extraction"


class AnthropicExtractor:
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.anthropic_model
        self.base_url = (base_url or settings.anthropic_base_url).rstrip("/")
        self.timeout = timeout or settings.extractor_timeout_seconds
        self.review_threshold = settings.extraction_review_threshold

    def _tool_schema(self, schema: type) -> dict[str, Any]:
        json_schema = schema.model_json_schema()
        properties = dict(json_schema.get("properties", {}))
        properties["field_confidence"] = {
            "type": "object",
            "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
            "description": "Per-field certainty that you read the value correctly.",
        }
        return {
            "name": TOOL_NAME,
            "description": "Record the fields extracted from the document.",
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": json_schema.get("required", []),
                "$defs": json_schema.get("$defs", {}),
            },
        }

    def _content_blocks(self, req: ExtractionRequest) -> list[dict[str, Any]]:
        schema = req.schema
        if req.has_text_layer:
            return [
                {
                    "type": "text",
                    "text": prompts.user_prompt(req.schema_name, req.text or "", schema),
                }
            ]
        # No text layer: send the file itself.
        media_type = req.content_type or "application/pdf"
        block_type = "document" if media_type == "application/pdf" else "image"
        return [
            {
                "type": block_type,
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(req.content).decode("ascii"),
                },
            },
            {
                "type": "text",
                "text": prompts.user_prompt(
                    req.schema_name, "(the document is attached above)", schema
                ),
            },
        ]

    def extract(self, req: ExtractionRequest) -> ExtractionResult:
        started = time.perf_counter()
        schema = req.schema

        if not self.api_key:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                error_text="ANTHROPIC_API_KEY is not configured",
                warnings=["falling back to manual entry"],
                extractor_name=self.name,
                model_name=self.model,
            )

        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0,
            "system": prompts.SYSTEM_PROMPT,
            "tools": [self._tool_schema(schema)],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": [{"role": "user", "content": self._content_blocks(req)}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

        raw = ""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/v1/messages", json=payload, headers=headers
                )
                response.raise_for_status()
                body = response.json()

            tool_input = next(
                (
                    block.get("input")
                    for block in body.get("content", [])
                    if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME
                ),
                None,
            )
            if tool_input is None:
                # The model answered in prose instead of calling the tool.
                text = "".join(
                    block.get("text", "")
                    for block in body.get("content", [])
                    if block.get("type") == "text"
                )
                raw = text
                tool_input, _ = repair_loads(text)
            else:
                raw = str(tool_input)

            confidence = tool_input.pop("field_confidence", {}) or {}
            tool_input.pop("schema_name", None)
            data = schema.model_validate(tool_input)
        except (httpx.HTTPError, JSONRepairFailed, ValidationError, KeyError) as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                raw_response=raw or None,
                error_text=f"{type(exc).__name__}: {exc}",
                warnings=["falling back to manual entry"],
                extractor_name=self.name,
                model_name=self.model,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        clean_confidence = {
            key: max(0.0, min(1.0, float(value)))
            for key, value in confidence.items()
            if isinstance(value, (int, float))
        }
        return finalise(
            data,
            clean_confidence,
            extractor_name=self.name,
            model_name=self.model,
            raw_response=raw,
            review_threshold=self.review_threshold,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def health(self) -> ExtractorHealth:
        if not self.api_key:
            return ExtractorHealth(
                name=self.name, available=False, detail="no API key configured",
                model_name=self.model,
            )
        return ExtractorHealth(
            name=self.name,
            available=True,
            detail="API key present (not verified until first call)",
            model_name=self.model,
        )
