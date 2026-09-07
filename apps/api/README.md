# Impulso API

FastAPI + PostgreSQL persistence foundation for the Impulso backend —
**BACKEND-02** (application skeleton) and **BACKEND-03** (persistence
foundation).

This stage establishes the Python project structure, the FastAPI
application factory, configuration, health/readiness endpoints, a
structured error envelope, request correlation IDs, structured logging,
a CORS foundation, a test foundation, and — new in BACKEND-03 — a
PostgreSQL persistence foundation: SQLAlchemy 2.x engine/session
lifecycle, Alembic migrations, and a metadata naming convention. It does
**not** implement any business domain (campaigns, content,
orchestration, etc.).

> **NO AUTHENTICATION. NO AI EXECUTION. NO DOMAIN TABLES.**
> These are all explicitly deferred to later, separately authorized
> stages (see `docs/backend/BACKEND-01-ARCHITECTURE.md` §13 for the
> proposed phase sequence). BACKEND-03 adds the database *foundation*
> only — no `User`, `Workspace`, `Campaign`, `Content`, or `Agent`
> tables exist yet.

## Purpose

Provide a clean, minimal, production-oriented FastAPI + PostgreSQL
skeleton that later phases (auth, orchestration, specialist agents,
content, metrics) can extend without restructuring — following the
modular-monolith bounded contexts approved in BACKEND-01.

## Python requirements

- **Required:** Python `>=3.11,<3.14` (declared in `pyproject.toml`).
- This host has Python `3.10.12` (default `python3`) **and** Python
  `3.12.13` (`python3.12`) installed. **3.10.12 does not satisfy the
  `>=3.11` floor** — use `python3.12` (or newer, below 3.14) when
  creating the virtual environment below.

## PostgreSQL requirement

This application targets **PostgreSQL specifically** — no other
database is supported, and tests never fall back to SQLite. You need a
running PostgreSQL server (14+ is what this stage was verified against)
reachable from wherever you run the app or its tests.

## Setup

From `apps/api/`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# edit .env: set DATABASE_URL, TEST_DATABASE_URL, and
# TEST_MIGRATIONS_DATABASE_URL to real databases on your own PostgreSQL
# server (see .env.example for the required naming/host safety rules)
```

`.venv/` and `.env` are git-ignored — never commit either.

### DATABASE_URL

A SQLAlchemy + psycopg 3 connection string, e.g.:

```
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database>
```

No default is provided anywhere in the code. In `APP_ENV=production`,
the app refuses to start at all without it (`app/core/config.py`). In
`development`/`test`, it is optional — `/api/v1/readiness` simply
reports `not_ready` if it is unset or unreachable; every non-database
endpoint keeps working normally either way.

### Dedicated test databases — do not skip this

Tests **never** reuse `DATABASE_URL`. Two additional, separate databases
are required to run the database-dependent tests:

- `TEST_DATABASE_URL` — used by the transaction/session tests
  (`tests/test_transactions.py`) and the "database available" readiness
  test. Its schema is dropped and recreated at the start of the test
  session.
- `TEST_MIGRATIONS_DATABASE_URL` — used only by the Alembic migration
  round-trip tests (`tests/test_migrations.py`), kept **separate** from
  `TEST_DATABASE_URL` so Alembic's own version bookkeeping never
  collides with the other tests' direct table management of the same
  schema.

Both are validated by `app/persistence/testing.py` before any
destructive operation runs: the database **name must contain `_test`**
and the **host must be local** (`localhost`/`127.0.0.1`/`::1`). A URL
that fails either check is refused, not silently allowed — this is a
fail-closed safeguard, not just a naming convention.

> **Destructive test warning:** the migration tests run
> `alembic downgrade base` (drops every table) against
> `TEST_MIGRATIONS_DATABASE_URL`, and the transaction-test fixture drops
> and recreates all tables on `TEST_DATABASE_URL` at session start. Never
> point either at a database that holds anything you want to keep —
> **never your development database, and never production.**

## Development run

From `apps/api/`, with the virtual environment activated:

```bash
uvicorn app.main:app --reload
```

The app listens on `http://127.0.0.1:8000` by default. With it running:

- `GET /` → service identity
- `GET /health` → process liveness only (unchanged meaning from BACKEND-02)
- `GET /api/v1/health` → the same, under the versioned prefix
- `GET /api/v1/readiness` → **new in BACKEND-03**: `{"status": "ready", "database": "ok"}` (200) if the database is configured and reachable, `{"status": "not_ready", "database": "unavailable"}` (503) otherwise. Never leaks hostnames, usernames, DSNs, passwords, or SQL error text.
- `GET /docs`, `GET /redoc`, `GET /openapi.json` → OpenAPI (enabled)

## Migrations

From `apps/api/`, with `DATABASE_URL` set (in `.env` or the environment):

```bash
alembic upgrade head        # apply all migrations
alembic current              # show the currently applied revision
alembic revision --autogenerate -m "description"   # generate a new migration
alembic downgrade base       # drop everything this project's migrations created — destructive, test databases only
```

`alembic.ini` deliberately leaves `sqlalchemy.url` blank — `alembic/env.py`
resolves it at runtime from `Settings().DATABASE_URL`, the same
environment variable the FastAPI app itself reads, so the connection
string is never duplicated or hardcoded in two places.

## Tests

From `apps/api/`, with the virtual environment activated:

```bash
pytest
```

Most of the test suite requires **no** database, no network access, and
no running server — it exercises the app in-process via
`fastapi.testclient.TestClient`. Tests that need a real PostgreSQL
database are marked `@pytest.mark.postgres` and are **skipped (not
faked, never run against SQLite)** unless `TEST_DATABASE_URL` /
`TEST_MIGRATIONS_DATABASE_URL` are set to real, reachable, test-scoped
databases — each skip states the exact reason.

```bash
# unit tests only (always run, no database needed)
pytest -m "not postgres"

# full suite, including real PostgreSQL acceptance
TEST_DATABASE_URL=postgresql+psycopg://user@localhost/impulso_dev_test \
TEST_MIGRATIONS_DATABASE_URL=postgresql+psycopg://user@localhost/impulso_dev_test_migrations \
pytest
```

## Current implemented scope (BACKEND-02 + BACKEND-03)

**BACKEND-02** (see prior delivery for full detail): application
factory, typed settings, versioned routing, health endpoints, error
envelope, request-ID middleware, structured logging, CORS foundation,
OpenAPI, test foundation.

**BACKEND-03 additions:**

- `app/persistence/base.py` — shared `Base`/`metadata` with a
  deterministic naming convention (`pk`/`fk`/`uq`/`ck`/`ix`) so Alembic
  autogenerate produces stable constraint names; `UUIDPrimaryKeyMixin`
  and `TimestampMixin` reusable mixins for future models
- `app/persistence/session.py` — engine factory (conservative pool
  defaults: `pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`),
  `configure_database()`/`get_engine()`/`dispose_engine()`, and the
  `get_db()` FastAPI dependency implementing the session contract below
- `app/persistence/probe.py` — `PersistenceProbe`, an explicitly
  **non-domain** table that exists only to prove the foundation works
  end-to-end (migrated, inserted into, committed, rolled back) without
  prematurely creating `User`/`Workspace`/`Campaign`/etc.
- `app/persistence/testing.py` — the test-database safety guard
  (`assert_safe_test_database_url`)
- `app/api/v1/readiness.py` — `GET /api/v1/readiness`
- Alembic configured at `apps/api/alembic/`, importing the project's own
  metadata (never a second `Base`); one migration, hand-verified to
  round-trip (`upgrade head` → `current` → `downgrade base` → `upgrade
  head`) against a real PostgreSQL 14 database
- 29 new tests across `tests/test_persistence_foundation.py` (unit,
  always run), `tests/test_transactions.py`, `tests/test_migrations.py`,
  and `tests/test_readiness.py` (mixed; DB-only cases marked `postgres`)

### Session / transaction contract

- **One `Session` per request.** `get_db` is a generator dependency —
  FastAPI calls it fresh every time; there is no global mutable
  `Session`, only a stateless session factory.
- **No implicit commit.** `get_db` never calls `session.commit()` — a
  read-only request does nothing beyond closing its session. Only
  application/service-layer code that explicitly needs to persist a
  write calls `commit()` on the session it was given. SESSION lifecycle
  (this module's job) is a distinct concern from TRANSACTION AUTHORITY
  (the caller's job) — and both are distinct from GOVERNANCE AUTHORITY,
  which belongs to a later, separately authorized stage: a database
  write is not itself a governance decision.
- **Rollback on exception**, always closed, never reused across
  requests — see the docstring in `app/persistence/session.py` for the
  full contract and the tests in `tests/test_transactions.py` that prove
  each clause against a real database.

## Explicitly deferred (not in this stage)

- `User`, `Workspace`, `Campaign`, `Content`, `Agent`, or any other
  domain/business table — see BACKEND-05 onward
- Authentication, password hashing, sessions, JWTs, RBAC, CSRF — see
  BACKEND-04
- `AGENT-00`…`AGENT-10`, `AOL-00`, Handoffs, Returns, Gates, or any
  other orchestration/governance runtime — architecture-only until a
  dedicated, separately authorized stage
- Any AI provider integration (OpenRouter, OpenAI, Anthropic, etc.)
- Any external integration (Meta, Google Ads, Stripe, etc.)
- Async SQLAlchemy (`asyncpg`) — this stage uses synchronous SQLAlchemy;
  revisit only if a concrete, reported justification emerges
- Production connection-pool tuning, PgBouncer, and Postgres Row-Level
  Security — conservative development defaults only for now; see
  `docs/backend/BACKEND-01-ARCHITECTURE.md` §5/§6
- Rate limiting, secret-manager integration, and other hardening —
  principles are documented in `docs/backend/BACKEND-01-ARCHITECTURE.md`
  §6, not implemented here

### Known gap: UUIDv7 vs. UUIDv4

BACKEND-01 specifies **UUIDv7** (time-ordered) for internal primary
keys. `UUIDPrimaryKeyMixin` currently generates **UUIDv4** instead:
Python's stdlib has no UUIDv7 generator before 3.14 (this project
targets 3.11–3.13), and no UUIDv7 package is among BACKEND-03's approved
dependencies. This is reported, not silent — revisit once Python 3.14
is the floor, or a UUIDv7 dependency is explicitly authorized.

## Future module layout

Per BACKEND-01's modular-monolith boundaries, future domain packages
will sit alongside `app/api`, `app/core`, `app/shared`, and
`app/persistence` — e.g. `app/auth/`, `app/users/`, `app/workspaces/`,
`app/campaigns/`, `app/orchestration/`, `app/agents/`, `app/content/`,
`app/measurement/`, etc. — each with its own `models.py`, `schemas.py`,
`service.py`, and `router.py`, included into `app/api/v1/router.py` one
line at a time. None of those packages exist yet; they are created only
when their implementation stage is explicitly authorized.
