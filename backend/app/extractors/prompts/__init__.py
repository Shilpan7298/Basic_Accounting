"""Provider prompt text lives here and nowhere else (CLAUDE.md → Do not)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

_HERE = Path(__file__).resolve().parent

SYSTEM_PROMPT = (_HERE / "system.txt").read_text(encoding="utf-8")
CUSTOMER_PO_PROMPT = (_HERE / "customer_po.txt").read_text(encoding="utf-8")
SUPPLIER_OFFER_PROMPT = (_HERE / "supplier_offer.txt").read_text(encoding="utf-8")
REASK_PROMPT = (_HERE / "reask.txt").read_text(encoding="utf-8")

_BY_SCHEMA = {
    "customer_po": CUSTOMER_PO_PROMPT,
    "supplier_offer": SUPPLIER_OFFER_PROMPT,
}


def user_prompt(schema_name: str, document_text: str, schema: type[BaseModel]) -> str:
    """Task prompt + the JSON shape, derived from the Pydantic model itself so
    the prompt can never drift from what validation will accept."""
    body = _BY_SCHEMA[schema_name]
    return (
        f"{body}\n\n"
        f"Return JSON matching exactly this schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}\n\n"
        f"--- BEGIN DOCUMENT TEXT ---\n{document_text}\n--- END DOCUMENT TEXT ---\n"
    )


def reask_prompt(error: str) -> str:
    return REASK_PROMPT.format(error=error)
