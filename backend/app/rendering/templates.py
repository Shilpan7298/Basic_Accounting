"""Template discovery.

Templates are found by scanning ``templates/documents/<doc_type>/``. Adding one
is a file drop — no registry edit, no code change (CLAUDE.md constraint #2).

Naming is ``<name>-v<N>.<ext>``. The ``N`` is what gets frozen onto the
document row, so a reprint knows which template drew the original.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

_NAME_RE = re.compile(r"^(?P<name>.+?)-v(?P<version>\d+)$")
_EXTENSIONS = {".docx": "docx", ".html": "html", ".htm": "html"}


class TemplateNotFound(LookupError):
    pass


@dataclass(frozen=True)
class TemplateFile:
    doc_type: str
    name: str
    version: int
    kind: str  # "docx" | "html"
    path: Path

    @property
    def label(self) -> str:
        return f"{self.name}-v{self.version}"


def _root() -> Path:
    return get_settings().template_dir


def discover(doc_type: str | None = None) -> list[TemplateFile]:
    root = _root()
    if not root.exists():
        return []
    found: list[TemplateFile] = []
    directories = [root / doc_type] if doc_type else [d for d in root.iterdir() if d.is_dir()]
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            kind = _EXTENSIONS.get(path.suffix.lower())
            if kind is None or path.name.startswith("~$"):
                continue
            match = _NAME_RE.match(path.stem)
            if not match:
                continue
            found.append(
                TemplateFile(
                    doc_type=directory.name,
                    name=match.group("name"),
                    version=int(match.group("version")),
                    kind=kind,
                    path=path,
                )
            )
    return found


def resolve(
    doc_type: str, kind: str, name: str | None = None, version: int | None = None
) -> TemplateFile:
    """Pick a template. Without a version, the highest wins."""
    candidates = [t for t in discover(doc_type) if t.kind == kind]
    if name:
        candidates = [t for t in candidates if t.name == name]
    if version is not None:
        candidates = [t for t in candidates if t.version == version]
    if not candidates:
        raise TemplateNotFound(
            f"no {kind} template for doc_type={doc_type!r} "
            f"name={name!r} version={version!r} under {_root()}"
        )
    return max(candidates, key=lambda t: t.version)


def list_names(doc_type: str) -> list[str]:
    return sorted({t.name for t in discover(doc_type)})
