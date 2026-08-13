from __future__ import annotations

import logging

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


from .api.routes import extraction, invoices, masters, orders, purchase, reports, tally  # noqa: E402

for router in (
    masters.router,
    extraction.router,
    orders.router,
    invoices.router,
    purchase.router,
    tally.router,
    reports.router,
):
    app.include_router(router, prefix="/api")
