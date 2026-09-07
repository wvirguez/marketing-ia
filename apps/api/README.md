# Impulso API

FastAPI + PostgreSQL + authentication/tenancy foundation for the
Impulso backend — **BACKEND-02** (application skeleton), **BACKEND-03**
(persistence foundation), and **BACKEND-04** (identity, authentication,
workspace tenancy).

> **CAMPAIGNS NOT IMPLEMENTED. AI AGENTS NOT IMPLEMENTED. FRONTEND NOT WIRED.**
> This stage adds real identity/auth/tenancy only. No `Campaign`,
> `Content`, `Agent`, or billing table or route exists. `apps/web`
> has not been touched and does not call this API yet — see
> `docs/backend/BACKEND-01-ARCHITECTURE.md` §13 for the proposed phase
> sequence.

## Purpose

Provide the first real business/security domain: a user can register,
log in, log out, and see their current session/workspace, with
first-class multi-tenant isolation — following the modular-monolith
bounded contexts approved in BACKEND-01.

## Python requirements

- **Required:** Python `>=3.11,<3.14` (declared in `pyproject.toml`).
- This host has Python `3.10.12` (default `python3`) **and** Python
  `3.12.13` (`python3.12`) installed. **3.10.12 does not satisfy the
  `>=3.11` floor** — use `python3.12` (or newer, below 3.14) when
  creating the virtual environment below.

## PostgreSQL requirement

This application targets **PostgreSQL specifically** — no other
database is supported, and tests never fall back to SQLite.

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

### Dedicated test databases — do not skip this

Tests **never** reuse `DATABASE_URL`. `TEST_DATABASE_URL` (transaction/
auth/tenancy tests) and `TEST_MIGRATIONS_DATABASE_URL` (Alembic
round-trip tests) must be separate databases, both validated by
`app/persistence/testing.py` before any destructive operation: the
database **name must contain `_test`** and the **host must be local**.

> **Destructive test warning:** the migration tests run
> `alembic downgrade base` against `TEST_MIGRATIONS_DATABASE_URL`, and
> the shared test fixture drops/recreates all tables on
> `TEST_DATABASE_URL` at session start/end. Never point either at a
> database you want to keep — **never development, never production.**

## Development run

```bash
uvicorn app.main:app --reload
```

- `GET /`, `GET /health`, `GET /api/v1/health` — unchanged since BACKEND-02
- `GET /api/v1/readiness` — unchanged since BACKEND-03
- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`,
  `POST /api/v1/auth/logout`, `GET /api/v1/auth/session`,
  `GET /api/v1/auth/csrf` — **new**
- `GET /api/v1/users/me`, `GET /api/v1/workspaces/current` — **new**
- `GET /docs`, `GET /redoc`, `GET /openapi.json` — OpenAPI (enabled)

If `DATABASE_URL` has migrations applied (`alembic upgrade head`), the
full register → session → csrf → logout flow works with real cookies —
verified live with `curl` (see delivery notes) and in
`tests/test_auth_session.py`/`test_auth_csrf.py`/`test_registration.py`.

## Authentication model

**Opaque server-side sessions, not JWT.** Browser flow:

```
browser --(HttpOnly cookie: opaque random token)--> API
API --(SHA-256 hash of the token)--> AuthSession row in PostgreSQL
AuthSession --> User --> Membership --> Workspace
```

- The browser only ever holds a random, high-entropy, opaque token
  (`secrets.token_urlsafe(32)`, ~256 bits) — never a user id, public or
  internal.
- **The raw token is never written to the database** — only
  `token_hash` (SHA-256 hex digest, one-way). A stolen database backup
  cannot be used to forge a session.
- Passwords and session tokens use **different** hashing tools on
  purpose: passwords go through Argon2id (`pwdlib[argon2]`), which is
  deliberately slow to resist offline guessing of a human-chosen,
  low-entropy secret. Session tokens (and the CSRF secret) are already
  maximally random — hashing them with a slow KDF would only waste CPU;
  a single fast SHA-256 digest is the correct, standard tool for "a
  stolen DB dump shouldn't hand out a working session." See
  `app/auth/security.py` for the full reasoning in code.
- Invariants preserved throughout: SESSION TOKEN ≠ USER ID, SESSION ≠
  AUTHORIZATION, AUTHENTICATED ≠ AUTHORIZED FOR WORKSPACE (see
  `app/auth/dependencies.py`).

## Session model

`AuthSession` (`app/auth/models.py`): `id` (internal UUID), `public_id`
(`SES-...`), `user_id`, `token_hash`, `csrf_secret`, `created_at`,
`last_seen_at` (set at creation only — see the known limitation below),
`expires_at`, `revoked_at`, `user_agent_summary` (first 120 chars only —
never a full fingerprint, never an IP address).

- Expiration (`SESSION_TTL_SECONDS`, default 14 days) is enforced
  server-side on every authenticated request (`get_current_session`).
- Logout revokes (`revoked_at = now()`) rather than deletes — an audit
  trail of "this session existed and was ended" survives.
- A missing cookie, an unrecognized token hash, and a revoked session
  are **deliberately indistinguishable** (`AUTHENTICATION_REQUIRED`,
  401) — revealing "this session used to exist" is unnecessary
  information. A **naturally expired** session gets its own code
  (`SESSION_EXPIRED`, 401) instead, because "please log in again, your
  session timed out" is useful, non-sensitive information about the
  caller's own request (unlike login, which is about account
  enumeration — see below).
- Each login creates a **new** session; multiple concurrent sessions per
  user are allowed by design (no "single session" policy in this
  stage).

**Known limitation:** `last_seen_at` is set once at creation and not
live-updated on every request — doing so would require `get_current_session`
(a pure-read dependency, used everywhere) to write, which would break
the "reads never commit" half of the session contract for the sake of a
nice-to-have observability field. Deferred.

## Cookie contract

Centralized in `app/auth/cookies.py`, configured via `Settings`:

| Setting | Default | Production requirement |
|---|---|---|
| `SESSION_COOKIE_NAME` | `impulso_session` | — |
| `SESSION_COOKIE_SECURE` | `false` | **must be `true`** — the app refuses to start otherwise |
| `SESSION_COOKIE_SAMESITE` | `lax` | if set to `none`, `SECURE` must also be `true` (browser requirement, enforced at Settings validation) |
| `SESSION_TTL_SECONDS` | 1209600 (14 days) | — |

Always `HttpOnly=true`, `Path=/`. The token is never exposed to
JavaScript.

## CSRF model

Because authentication uses a cookie, `SameSite=Lax` alone is not
treated as a complete CSRF architecture. Synchronizer-token pattern:

1. Each `AuthSession` gets its own random `csrf_secret` at creation
   (plaintext in the database — see `app/auth/csrf.py` for why this,
   unlike the session token, is safe: knowing it alone grants nothing
   without also having the session cookie).
2. `GET /api/v1/auth/csrf` returns it to the frontend (requires an
   authenticated session; never exposes the session token itself).
3. Every authenticated state-changing request (`POST`/`PUT`/`PATCH`/
   `DELETE`) must send it back in an `X-CSRF-Token` header
   (`require_csrf` dependency); constant-time comparison
   (`hmac.compare_digest`) against the session's stored secret.
4. `GET`/`HEAD`/`OPTIONS` never require it.

**Pre-auth exemption:** `register`/`login` are exempt from this check —
no session/CSRF secret exists yet before they succeed. The residual
"login CSRF" risk (forcing a victim to log in as someone else's account)
is mitigated by `SameSite=Lax` and by the fact that a successful login
does not act on behalf of an already-authenticated identity. Documented
trade-off, not an oversight — see `app/auth/service.py`.

## Workspace tenancy

`User -> Membership -> Organization -> Workspace`. Registration
bootstraps exactly one Organization + one Workspace + one `OWNER`
Membership per new user (`app/auth/service.py::AuthService.register`) —
the schema supports multiple memberships per user; only the "current
workspace" convenience lookup (earliest active membership) is
single-workspace-shaped for this stage. A real workspace switcher is a
later concern.

No route in this stage accepts a client-supplied `workspace_id` —
`get_current_workspace` (`app/auth/dependencies.py`) is the only path to
a workspace, and it is derived entirely from the authenticated session's
membership. `WorkspaceAccessService.get_authorized_workspace`
(`app/workspaces/service.py`) is the reusable pattern every future
by-public-id endpoint must use; it is exercised directly by
`tests/test_tenancy.py`, proving a non-existent workspace and a
workspace-you're-not-a-member-of return the identical `FORBIDDEN` error.

## Setup for local development, continued

### DATABASE_URL / migrations

```bash
alembic upgrade head        # apply all migrations
alembic current              # show the currently applied revision
alembic revision --autogenerate -m "description"
```

`alembic.ini` leaves `sqlalchemy.url` blank; `alembic/env.py` resolves
it at runtime from `Settings().DATABASE_URL`.

## Tests

```bash
pytest                    # everything; DB tests skip if no test DB configured
pytest -m "not postgres"  # unit tests only, no database needed

TEST_DATABASE_URL=postgresql+psycopg://user@localhost/impulso_dev_test \
TEST_MIGRATIONS_DATABASE_URL=postgresql+psycopg://user@localhost/impulso_dev_test_migrations \
pytest                    # full acceptance, including real PostgreSQL
```

New in BACKEND-04: `tests/test_auth_passwords.py` (hashing, no DB),
`tests/test_authorization.py` (`require_role`, no DB),
`tests/test_registration.py`, `tests/test_auth_session.py`,
`tests/test_auth_csrf.py`, `tests/test_tenancy.py` (all `postgres`-marked).

## Security limitations (explicit, not implicit)

- **No rate limiting.** Login/register are documented future rate-limit
  targets (`app/auth/router.py`); **there is no brute-force protection
  of any kind in this stage.** Do not deploy this publicly without it.
- **No email verification, no password reset flow.**
- **No account lockout** after repeated failed logins.
- Registration reveals whether an email is already registered
  (`EMAIL_ALREADY_REGISTERED`) — a deliberate, common trade-off, unlike
  login, which never reveals anything about account existence.
- `last_seen_at` is not live-updated (see Session model above).
- UUIDv7 vs. UUIDv4: BACKEND-01 specifies UUIDv7 for internal primary
  keys; `UUIDPrimaryKeyMixin` still generates UUIDv4 (no stdlib UUIDv7
  before Python 3.14, no UUIDv7 package approved yet) — unchanged,
  reported gap from BACKEND-03.
- No Row-Level Security, no production pool tuning, no secret-manager
  integration — see `docs/backend/BACKEND-01-ARCHITECTURE.md` §6.

## Explicitly deferred (not in this stage)

- `Campaign`, `Content`, `Agent`, `Subscription`, or any other
  business/billing table
- `AGENT-00`…`AGENT-10`, `AOL-00`, Handoffs, Returns, Gates, or any
  orchestration/governance runtime
- Any AI provider integration (OpenRouter, OpenAI, Anthropic, etc.)
- Any external integration (Meta, Google Ads, Stripe, etc.)
- Frontend wiring — `apps/web` remains mock/static
- Redis, Celery, rate-limiting infrastructure, async SQLAlchemy

## Future module layout

`app/auth/`, `app/users/`, `app/workspaces/` (new in BACKEND-04) join
`app/api`, `app/core`, `app/shared`, `app/persistence` alongside future
`app/campaigns/`, `app/orchestration/`, `app/agents/`, `app/content/`,
`app/measurement/`, etc. — each with its own `models.py`, `schemas.py`,
`repository.py`, `service.py`, and `router.py`, included into
`app/api/v1/router.py` one line at a time.
