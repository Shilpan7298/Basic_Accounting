from __future__ import annotations

import logging

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import SessionLocal, engine
from .models import Base
from .services.audit import install_session_listener

logger = logging.getLogger(__name__)

# The catch-all audit hook is installed at import time, on the sessionmaker
# itself. That is deliberate: there is no configuration that turns it off and
# no code path that gets a session without it.
install_session_listener(SessionLocal)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Order and invoicing system for Urjapod Energy Private Limited — customer PO "
        "extraction, proforma milestones, GST tax invoices, supplier purchase orders, "
        "Tally export and management reports."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    settings.ensure_dirs()
    # Alembic owns the schema in a real deployment; this keeps a fresh dev or
    # test database usable without a migration step.
    Base.metadata.create_all(engine)


@app.get("/api/health", tags=["health"])
def health():
    from .extractors import health_all

    return {
        "status": "ok",
        "app": settings.app_name,
        "database": engine.dialect.name,
        "extractor_selected": settings.extractor,
        "extractors": [
            {"name": h.name, "available": h.available, "detail": h.detail}
            for h in health_all()
        ],
    }


from .api.routes import (  # noqa: E402
    amendments,
    auth,
    extraction,
    invoices,
    masters,
    orders,
    purchase,
    reports,
    tally,
)

for router in (
    auth.router,
    masters.router,
    extraction.router,
    orders.router,
    invoices.router,
    purchase.router,
    tally.router,
    reports.router,
    # Registered last: its /{entity_type}/… paths would otherwise
    # shadow the specific routes above.
    amendments.router,
):
    app.include_router(router, prefix="/api")


# --- the front end ---------------------------------------------------------
# In a packaged install there is no nginx: the built React app sits next to the
# backend and FastAPI serves it, so the whole thing is one process listening on
# one port. In development Vite serves it instead and this directory is absent.
_STATIC = Path(__file__).resolve().parent / "static"
if _STATIC.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Anything that is not an API route is a client-side route."""
        candidate = _STATIC / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC / "index.html")
