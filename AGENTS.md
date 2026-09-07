# Impulso — repository instructions

Impulso is a digital marketing SaaS assisted by multiple internal AI agents.
These repository-wide instructions apply to Codex and Claude Code. Frontend
stage status lives in `apps/web/AGENTS.md`; overall project status (frontend
and backend) is tracked below.

## Project status

| Stage | Status |
| --- | --- |
| FRONTEND-01 through FRONTEND-09 | COMPLETE |
| FRONTEND-MVP-REVIEW | APPROVED WITH RESERVATIONS |
| BACKEND-01 — Architecture & Domain Model | COMPLETE WITH RESERVATIONS |
| BACKEND-02 — FastAPI Foundation | COMPLETE |
| BACKEND-02R — HTTP Failure Path & Request Correlation Hardening | VERIFIED |
| BACKEND-03 — PostgreSQL + SQLAlchemy + Alembic Persistence Foundation | COMPLETE WITH RESERVATIONS |
| BACKEND-04 — Authentication, Users & Workspace Tenancy | NEXT AUTHORIZED/PLANNED STEP — not started |

Frontend MVP implementation (Login, Dashboard, Campaigns List, New Campaign,
Campaign Workspace, Content Detail & Asset Preview, Metrics & Performance
Workspace, Settings & Workspace Profile) is complete; see `apps/web/AGENTS.md`
for the per-stage table and the FRONTEND-MVP-REVIEW findings. The frontend
**remains mock/static and is not wired to the API** — no frontend code calls
`apps/api` yet, and none of the changes below authorize doing so.

Backend architecture and domain model are documented under `docs/backend/`
(bounded contexts, entity catalog, ID strategy, state machines, API resource
map, MVP scope, and phase proposal). Backend implementation has begun:

- The FastAPI application foundation exists (`apps/api/app/`): application
  factory, typed settings, versioned routing under `/api/v1`, health and
  readiness endpoints, a structured error envelope, request-correlation
  middleware, structured logging, and a CORS foundation (BACKEND-02),
  subsequently hardened for the real unhandled-exception HTTP path
  (BACKEND-02R).
- A PostgreSQL persistence foundation exists (`apps/api/app/persistence/`,
  `apps/api/alembic/`): SQLAlchemy 2.x engine/session lifecycle with an
  explicit transaction-ownership contract, a metadata naming convention,
  and Alembic migrations wired to the project's own metadata (BACKEND-03).
- **Authentication does not exist yet.** No login, password handling,
  sessions, or tokens exist anywhere in `apps/api`.
- **No `User`, `Organization`, `Workspace`, or `Membership` table exists
  yet.** The only table migrated so far is an explicitly non-domain
  persistence-foundation probe (see `apps/api/app/persistence/probe.py`).
- **The Campaign domain is not implemented.** No `Campaign` table, service,
  or route exists.
- **AI agents and the orchestration runtime are not implemented.** No
  `AGENT-00`…`AGENT-10`, `AOL-00`, Handoff/Return/Gate code, or AI provider
  integration exists anywhere in this repository.

When BACKEND-04 is explicitly authorized, it is limited to authentication,
Users, and Workspace tenancy (Organization/Workspace/Membership persistence
and a real login flow). It must **not** implement the Campaign domain or any
AI agent/orchestration work — those are separately authorized, later steps
per the phase proposal in `docs/backend/BACKEND-01-ARCHITECTURE.md`. Planned
status does not authorize starting that step.

## Project layout and stack

- Current application: `apps/web`, using Next.js 16, React 19, TypeScript,
  Tailwind CSS 4, and the App Router. Frontend MVP is complete (see
  Project status above) and remains mock/static, not wired to the API.
- Backend: `apps/api`, Python 3, FastAPI, Uvicorn, PostgreSQL, SQLAlchemy,
  and Alembic. The FastAPI application foundation (BACKEND-02/02R) and the
  PostgreSQL persistence foundation (BACKEND-03) exist; see Project status
  above for exactly what does and does not exist yet. Do not implement
  authentication, Campaign/domain tables, or agent/orchestration code
  without separate, explicit authorization for that specific step.

## Development boundaries

- Never recreate the Next.js app.
- Never move `apps/web` without explicit authorization.
- Do not add dependencies unless required for the requested task and reported.
- Do not implement backend or real authentication before requested.
- Never expose secrets in frontend code, browser bundles, or public configuration.
- Do not use localStorage or sessionStorage for authentication.
- Do not implement external campaign side effects, such as publishing content,
  sending messages, launching ads, or spending money.
- Keep internal AI agent complexity hidden from normal user-facing UI.
- Do not automatically continue to a new numbered step. Planned work is not
  authorization to implement it.
- Design the frontend to consume backend contracts later. Keep future backend
  and business logic out of visual components; isolate data access and contracts
  from presentation as needed by the requested work.

## Git and handoff protocol

At the beginning of every task:

1. Inspect `git status` and preserve existing work.
2. Read repository instructions and relevant files; do not assume previous chat
   context. Use repository documentation as the persistent source of truth.
3. Check `git status` again before material changes if work has intervened.

Do not commit automatically. Do not reset, rebase, checkout, clean, or discard
user changes without explicit instruction.

After code changes, run from `apps/web`:

```bash
npm run lint
npm run build
```

Report failures explicitly, including checks that could not run and why. For
documentation-only changes, report lint/build as not run (documentation only);
do not imply they passed.

At completion, return the implementation summary, files changed, lint result,
build result, remaining issues, and ready/not ready for the next requested step.
Readiness does not authorize starting that step.
