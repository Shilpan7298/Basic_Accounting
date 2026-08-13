"""Getting JSON out of a model that was only mostly asked for JSON.

A 7B model running on a laptop will wrap its answer in prose, fence it in
markdown, leave a trailing comma, emit ``NaN``, or return two objects. None of
that should reach a financial record, and none of it should crash the app.

The ladder, in order of how much we're guessing:

1. parse the whole body
2. strip markdown fences
3. brace-match the first balanced object
4. mechanical repairs (trailing commas, quotes, NaN/Infinity, ``None``/``True``)

Anything still unparsed is a failure, not a guess. The caller then either
re-asks once with the error attached, or falls through to the manual form.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_PY_LITERALS_RE = re.compile(r"(?<![\"\w])(None|True|False)(?![\"\w])")
_NON_JSON_NUMBERS_RE = re.compile(r"(?<![\"\w])(NaN|-?Infinity)(?![\"\w])")
_UNQUOTED_KEY_RE = re.compile(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)")


class JSONRepairFailed(ValueError):
    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


def _balanced_object(text: str) -> str | None:
    """First balanced ``{...}``, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _mechanical_repairs(text: str) -> str:
    text = _NON_JSON_NUMBERS_RE.sub("null", text)
    text = _PY_LITERALS_RE.sub(
        lambda m: {"None": "null", "True": "true", "False": "false"}[m.group(1)], text
    )
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    text = _UNQUOTED_KEY_RE.sub(r'\1"\2"\3', text)
    # Smart quotes from a model that has been reading too many PDFs.
    return text.replace("“", '"').replace("”", '"').replace("‘", "'").replace(
        "’", "'"
    )


def loads(raw: str) -> tuple[dict[str, Any], list[str]]:
    """Return ``(parsed, notes)``. ``notes`` says which rung of the ladder ran."""
    if not raw or not raw.strip():
        raise JSONRepairFailed("model returned an empty response", raw or "")

    notes: list[str] = []

    # 1. straight parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed, notes
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            notes.append("model returned a list; used the first object")
            return parsed[0], notes
    except json.JSONDecodeError:
        pass

    # 2. markdown fences
    fenced = _FENCE_RE.search(raw)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                notes.append("stripped markdown code fences")
                return parsed, notes
        except json.JSONDecodeError:
            pass

    # 3. brace matching — handles prose before and after the object
    candidate = _balanced_object(fenced.group(1) if fenced else raw)
    if candidate:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                notes.append("extracted the first balanced JSON object from surrounding prose")
                return parsed, notes
        except json.JSONDecodeError:
            # 4. mechanical repairs on the candidate
            try:
                parsed = json.loads(_mechanical_repairs(candidate))
                if isinstance(parsed, dict):
                    notes.append("applied mechanical JSON repairs (commas/quotes/NaN)")
                    return parsed, notes
            except json.JSONDecodeError as exc:
                raise JSONRepairFailed(
                    f"could not repair model JSON: {exc}", raw
                ) from exc

    raise JSONRepairFailed("no JSON object found in the model response", raw)
