"""Adapter selection.

This is the only module that knows all three adapters exist. Callers ask for
``get_extractor()`` and get whatever ``EXTRACTOR`` says — which is how the
"selected by config, never by import" rule is actually enforced rather than
merely stated.
"""

from __future__ import annotations

from typing import Callable

from ..config import get_settings
from .base import DocumentExtractor, ExtractorHealth
from .manual import ManualExtractor

_BUILDERS: dict[str, Callable[[], DocumentExtractor]] = {}


def _build_anthropic() -> DocumentExtractor:
    from .anthropic_extractor import AnthropicExtractor

    return AnthropicExtractor()


def _build_openai_compatible() -> DocumentExtractor:
    from .openai_compatible import OpenAICompatibleExtractor

    return OpenAICompatibleExtractor()


def _build_manual() -> DocumentExtractor:
    return ManualExtractor()


_BUILDERS.update(
    {
        "anthropic": _build_anthropic,
        "openai_compatible": _build_openai_compatible,
        "manual": _build_manual,
    }
)


def available_names() -> list[str]:
    return sorted(_BUILDERS)


def get_extractor(name: str | None = None) -> DocumentExtractor:
    key = (name or get_settings().extractor).lower()
    try:
        return _BUILDERS[key]()
    except KeyError as exc:
        raise ValueError(
            f"unknown extractor {key!r}; available: {', '.join(available_names())}"
        ) from exc


def get_fallback() -> DocumentExtractor:
    """What every failure degrades into. Always available, never a stack trace."""
    return ManualExtractor()


def health_all() -> list[ExtractorHealth]:
    report: list[ExtractorHealth] = []
    for name in available_names():
        try:
            report.append(get_extractor(name).health())
        except Exception as exc:  # pragma: no cover - defensive
            report.append(ExtractorHealth(name=name, available=False, detail=str(exc)))
    return report
