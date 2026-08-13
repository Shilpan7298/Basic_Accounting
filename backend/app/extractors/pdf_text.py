"""Text-layer extraction.

Runs before any model is called. A PO with a text layer never needs a vision
call, which is the difference between "works on a laptop with Ollama" and
"needs a GPU".
"""

from __future__ import annotations

import io

from pypdf import PdfReader


def extract_text(content: bytes, content_type: str = "application/pdf") -> str:
    if content_type == "text/plain":
        return content.decode("utf-8", errors="replace")
    if content_type != "application/pdf":
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception:
        return ""
    return "\n\n".join(pages).strip()


def page_count(content: bytes) -> int:
    try:
        return len(PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return 0


def has_text_layer(content: bytes, content_type: str = "application/pdf") -> bool:
    return len(extract_text(content, content_type).strip()) > 80
