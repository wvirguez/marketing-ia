# Impulso API

FastAPI application foundation for the Impulso backend — **BACKEND-02**.

This stage establishes the Python project structure, the FastAPI
application factory, configuration, health endpoints, a structured error
envelope, request correlation IDs, structured logging, a CORS
foundation, and a test foundation. It does **not** implement any business
domain (campaigns, content, orchestration, etc.).

> **NO DATABASE. NO AUTHENTICATION. NO AI EXECUTION.**
> These are all explicitly deferred to later, separately authorized
> stages (see `docs/backend/BACKEND-01-ARCHITECTURE.md` §13 for the
> proposed phase sequence).

## Purpose

Provide a clean, minimal, production-oriented FastAPI skeleton that
later phases (persistence, auth, orchestration, specialist agents,
content, metrics) can extend without restructuring — following the
modular-monolith bounded contexts approved in BACKEND-01.

## Python requirements

- **Required:** Python `>=3.11,<3.14` (declared in `pyproject.toml`).
- This host has Python `3.10.12` (default `python3`) **and** Python
  `3.12.13` (`python3.12`) installed. **3.10.12 does not satisfy the
  `>=3.11` floor** — use `python3.12` (or newer, below 3.14) when
  creating the virtual environment below.

## Setup

From `apps/api/`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # optional; defaults already work for local dev
```

`.venv/` and `.env` are git-ignored — never commit either.

## Development run

From `apps/api/`, with the virtual environment activated:

```bash
uvicorn app.main:app --reload
```

The app listens on `http://127.0.0.1:8000` by default. With it running:

- `GET /` → service identity
- `GET /health` and `GET /api/v1/health` → process liveness
- `GET /docs`, `GET /redoc`, `GET /openapi.json` → OpenAPI (enabled)

## Tests

From `apps/api/`, with the virtual environment activated:

```bash
pytest
```

No database, no network access, and no running server are required —
tests exercise the app in-process via `fastapi.testclient.TestClient`.

## Current implemented scope (BACKEND-02)

- FastAPI application factory (`app/main.py`), kept intentionally small
- Typed settings via `pydantic-settings` (`app/core/config.py`):
  `APP_NAME`, `APP_ENV` (`development` | `test` | `production`),
  `API_V1_PREFIX`, `DEBUG`, `CORS_ALLOWED_ORIGINS`
- Centralized API versioning under `settings.API_V1_PREFIX` (`/api/v1`
  by default) — never hardcoded as a string literal per route
- `GET /` and `GET /health` (unversioned) + `GET /api/v1/health`
  (versioned) — process liveness only
- Structured JSON error envelope for HTTP errors, validation errors,
  and unhandled exceptions (`app/core/errors.py`) — client responses
  never include a stack trace; server-side logs retain it
- `X-Request-ID` correlation middleware (`app/core/middleware.py`) —
  accepts a safe incoming ID or generates one, echoes it in the
  response, and is available to logging; it carries no authentication
  or authorization meaning
- Minimal structured (JSON-lines) logging to stdout
  (`app/core/logging.py`) — timestamp, level, logger, and per-request
  method/path/status/duration/request_id; never logs headers, cookies,
  bodies, or credentials
- CORS foundation via `CORS_ALLOWED_ORIGINS` (defaults to
  `http://localhost:3000`); wildcard origins are rejected by
  configuration validation
- A minimal, real dependency-injection example: `Depends(get_settings)`
  is used in `app/api/root.py`, demonstrating the pattern later
  dependencies (current user, workspace context, database session,
  services) will follow — none of those are implemented yet
- Test foundation: `pytest` + `httpx`/`TestClient`, covering the root
  and health endpoints, the error envelope (including a direct
  unit-level check that unhandled exception details never reach the
  client), request-ID behavior, and CORS allow/deny behavior

## Explicitly deferred (not in this stage)

- PostgreSQL, SQLAlchemy, Alembic, migrations, repositories, any
  `DATABASE_URL` — see BACKEND-03
- Authentication, password hashing, sessions, JWTs, RBAC, CSRF — see
  BACKEND-04
- `AGENT-00`…`AGENT-10`, `AOL-00`, Handoffs, Returns, Gates, or any
  other orchestration/governance runtime — architecture-only until a
  dedicated, separately authorized stage
- Any AI provider integration (OpenRouter, OpenAI, Anthropic, etc.)
- Any external integration (Meta, Google Ads, Stripe, etc.)
- Rate limiting, secret-manager integration, and other hardening —
  principles are documented in `docs/backend/BACKEND-01-ARCHITECTURE.md`
  §6, not implemented here

## Future module layout

Per BACKEND-01's modular-monolith boundaries, future domain packages
will sit alongside `app/api`, `app/core`, and `app/shared` — e.g.
`app/auth/`, `app/users/`, `app/workspaces/`, `app/campaigns/`,
`app/orchestration/`, `app/agents/`, `app/content/`, `app/measurement/`,
etc. — each with its own `models.py`, `schemas.py`, `service.py`, and
`router.py`, included into `app/api/v1/router.py` one line at a time.
None of those packages exist yet; they are created only when their
implementation stage is explicitly authorized.
