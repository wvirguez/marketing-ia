"""Impulso API — application factory.

Intentionally small: this module wires together configuration, logging,
middleware, error handlers, and routers. No business logic lives here.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.root import router as root_router
from app.api.v1.router import api_v1_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.persistence.session import configure_database


def configure_app(app: FastAPI, settings: Settings) -> None:
    """Wires middleware and exception handlers onto an already-created
    FastAPI instance.

    Extracted from ``create_app`` so tests can build a fully isolated app
    instance — e.g. to exercise a deliberately-crashing route through the
    real middleware/handler stack — using the exact same production
    wiring, without ever touching the real ``app`` object below.
    """
    # Outermost first: RequestContextMiddleware must see every response,
    # including CORS preflight short-circuits, so every response carries
    # an X-Request-ID and is captured in the access log.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    register_exception_handlers(app)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.DEBUG)

    app = FastAPI(
        title=settings.APP_NAME,
        debug=False,  # error responses always go through our own handlers
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    configure_app(app, settings)

    # Optional: most of this API's own test suite runs with no database
    # configured at all, and that must keep working (see BACKEND-03 §19).
    # `/api/v1/readiness` reports "not_ready" rather than the app failing
    # to start when DATABASE_URL is unset outside production (production
    # already fails closed at the Settings level — see core/config.py).
    if settings.DATABASE_URL:
        configure_database(settings.DATABASE_URL, echo=settings.DEBUG)

    app.include_router(root_router)
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_app()
