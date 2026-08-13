"""OpenAI-compatible adapter — Ollama, vLLM, LM Studio, anything on localhost.

This is the adapter that matters for the "in a year it all runs offline"
assumption, so it is the one written defensively. It talks plain HTTP with
``httpx``; there is no vendor SDK to install, which is itself the point.

Small local models produce malformed JSON. The response goes through the
repair ladder in :mod:`json_repair`, then one bounded re-ask with the
validation error attached, then it gives up honestly and the UI opens the
manual form on the same review screen.
"""

from __future__ import annotations

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


class OpenAICompatibleExtractor:
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.openai_base_url).rstrip("/")
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model
        self.timeout = timeout or settings.extractor_timeout_seconds
        self.review_threshold = settings.extraction_review_threshold

    # -- transport ---------------------------------------------------------
    def _post(self, messages: list[dict[str, Any]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            if response.status_code == 400:
                # Older llama.cpp / LM Studio builds reject response_format.
                payload.pop("response_format", None)
                response = client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
            response.raise_for_status()
            body = response.json()

        return body["choices"][0]["message"]["content"] or ""

    # -- interface ---------------------------------------------------------
    def extract(self, req: ExtractionRequest) -> ExtractionResult:
        started = time.perf_counter()
        schema = req.schema

        if not req.has_text_layer:
            # A text-only local model cannot read a scan. Say so plainly rather
            # than sending it bytes it will hallucinate over.
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                error_text=(
                    "this document has no text layer and the local model is text-only; "
                    "use the manual form or OCR the file first"
                ),
                warnings=["no text layer"],
                extractor_name=self.name,
                model_name=self.model,
            )

        messages = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": prompts.user_prompt(req.schema_name, req.text or "", schema),
            },
        ]

        raw = ""
        notes: list[str] = []
        try:
            raw = self._post(messages)
            payload, notes = repair_loads(raw)
            data, confidence = self._validate(payload, schema)
        except (JSONRepairFailed, ValidationError) as first_error:
            # One bounded re-ask with the actual error. Bounded because a model
            # that failed twice will fail a third time, and a human is cheaper.
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": prompts.reask_prompt(str(first_error))})
            try:
                raw = self._post(messages)
                payload, retry_notes = repair_loads(raw)
                notes = notes + ["re-asked the model after a validation failure"] + retry_notes
                data, confidence = self._validate(payload, schema)
            except (JSONRepairFailed, ValidationError, httpx.HTTPError) as second_error:
                return ExtractionResult(
                    status=ExtractionStatus.FAILED,
                    raw_response=raw or getattr(first_error, "raw", None),
                    error_text=f"model output unusable after one retry: {second_error}",
                    warnings=notes + ["falling back to manual entry"],
                    extractor_name=self.name,
                    model_name=self.model,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
        except httpx.HTTPError as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                error_text=f"could not reach the local model at {self.base_url}: {exc}",
                warnings=["falling back to manual entry"],
                extractor_name=self.name,
                model_name=self.model,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        return finalise(
            data,
            confidence,
            extractor_name=self.name,
            model_name=self.model,
            raw_response=raw,
            review_threshold=self.review_threshold,
            duration_ms=int((time.perf_counter() - started) * 1000),
            extra_warnings=notes,
        )

    def _validate(self, payload: dict[str, Any], schema: type) -> tuple[Any, dict[str, float]]:
        confidence = payload.pop("field_confidence", {}) or {}
        payload.pop("schema_name", None)
        data = schema.model_validate(payload)
        clean = {
            key: max(0.0, min(1.0, float(value)))
            for key, value in confidence.items()
            if isinstance(value, (int, float))
        }
        return data, clean

    def health(self) -> ExtractorHealth:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/models")
                response.raise_for_status()
            return ExtractorHealth(
                name=self.name, available=True, detail=f"reachable at {self.base_url}",
                model_name=self.model,
            )
        except Exception as exc:
            return ExtractorHealth(
                name=self.name,
                available=False,
                detail=f"unreachable at {self.base_url}: {exc}",
                model_name=self.model,
            )
