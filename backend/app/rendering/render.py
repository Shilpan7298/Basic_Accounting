"""Rendering a context through a template.

Word goes through ``docxtpl``; PDF goes through Jinja → HTML → WeasyPrint.
Both take the identical context dict, so the two outputs cannot drift.

Undefined variables render empty rather than raising — a half-filled document
is more useful to the person editing a template than a 500 — but
:func:`validate_template` renders against the golden sample and *reports*
every undefined name, so "did I break it?" is a button rather than a support
call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, meta

from ..config import get_settings
from .context import GOLDEN_SAMPLE
from .templates import TemplateFile, resolve


@dataclass
class RenderedDocument:
    path: Path
    template_name: str
    template_version: int
    kind: str


@dataclass
class TemplateValidation:
    ok: bool
    template: str
    undefined_names: list[str] = field(default_factory=list)
    error: str | None = None


class _RecordingUndefined(ChainableUndefined):
    """Renders empty, but remembers what was missing."""

    seen: set[str] = set()

    def _fail_with_undefined_error(self, *args, **kwargs):  # pragma: no cover
        return ""

    def __str__(self) -> str:
        if self._undefined_name:
            _RecordingUndefined.seen.add(str(self._undefined_name))
        return ""


def _jinja_env(search_path: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(search_path)),
        undefined=_RecordingUndefined,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


def render_html(template: TemplateFile, context: dict[str, Any]) -> str:
    env = _jinja_env(template.path.parent)
    return env.get_template(template.path.name).render(**context)


def render_pdf(
    doc_type: str,
    context: dict[str, Any],
    out_path: Path,
    *,
    name: str | None = None,
    version: int | None = None,
) -> RenderedDocument:
    from weasyprint import HTML  # imported lazily; heavy at import time

    template = resolve(doc_type, "html", name=name, version=version)
    html = render_html(template, context)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(template.path.parent)).write_pdf(str(out_path))
    return RenderedDocument(out_path, template.name, template.version, "pdf")


def render_docx(
    doc_type: str,
    context: dict[str, Any],
    out_path: Path,
    *,
    name: str | None = None,
    version: int | None = None,
) -> RenderedDocument:
    from docxtpl import DocxTemplate

    template = resolve(doc_type, "docx", name=name, version=version)
    doc = DocxTemplate(str(template.path))
    doc.render(context, autoescape=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return RenderedDocument(out_path, template.name, template.version, "docx")


def validate_template(doc_type: str, kind: str, name: str, version: int | None = None) -> TemplateValidation:
    """Render against the golden sample and report undefined names."""
    try:
        template = resolve(doc_type, kind, name=name, version=version)
    except Exception as exc:
        return TemplateValidation(ok=False, template=f"{name}", error=str(exc))

    _RecordingUndefined.seen = set()
    try:
        if kind == "html":
            render_html(template, GOLDEN_SAMPLE)
        else:
            from docxtpl import DocxTemplate

            doc = DocxTemplate(str(template.path))
            doc.render(GOLDEN_SAMPLE, autoescape=True)
    except Exception as exc:
        return TemplateValidation(ok=False, template=template.label, error=str(exc))

    missing = sorted(_RecordingUndefined.seen)
    return TemplateValidation(ok=not missing, template=template.label, undefined_names=missing)


def declared_variables(template: TemplateFile) -> list[str]:
    """Top-level names an HTML template references — used by the docs page."""
    if template.kind != "html":
        return []
    env = _jinja_env(template.path.parent)
    source = template.path.read_text(encoding="utf-8")
    return sorted(meta.find_undeclared_variables(env.parse(source)))


def output_path(kind: str, doc_number: str, extension: str) -> Path:
    """Deterministic on-disk location, safe for a number containing slashes."""
    settings = get_settings()
    safe = doc_number.replace("/", "-").replace(" ", "_")
    return settings.generated_dir / kind / f"{safe}.{extension}"
