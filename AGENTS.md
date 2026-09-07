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
| BACKEND-01 — Architecture & Domain Model | APPROVED WITH RESERVATIONS |
| BACKEND-02 — FastAPI Foundation | NEXT AUTHORIZED/PLANNED STEP — not started |

Frontend MVP implementation (Login, Dashboard, Campaigns List, New Campaign,
Campaign Workspace, Content Detail & Asset Preview, Metrics & Performance
Workspace, Settings & Workspace Profile) is complete; see `apps/web/AGENTS.md`
for the per-stage table and the FRONTEND-MVP-REVIEW findings.

Backend architecture and domain model are complete and documented under
`docs/backend/` (bounded contexts, entity catalog, ID strategy, state
machines, API resource map, MVP scope, and phase proposal). Backend
**implementation has not started**: there is no FastAPI code, no PostgreSQL,
no authentication, and no AI orchestration in this repository yet. `apps/api`
is currently an empty directory.

When BACKEND-02 is explicitly authorized, it is limited to the FastAPI
application skeleton (app structure, settings, health check). It must **not**
include PostgreSQL, authentication, real agent execution, or AI providers —
those are separately authorized, later steps per the phase proposal in
`docs/backend/BACKEND-01-ARCHITECTURE.md`. Planned status does not authorize
starting that step.

## Project layout and stack

- Current application: `apps/web`, using Next.js 16, React 19, TypeScript,
  Tailwind CSS 4, and the App Router. Frontend MVP is complete (see
  Project status above).
- Future backend: `apps/api`, planned with Python 3, FastAPI, Uvicorn, and
  PostgreSQL. Architecture and domain model are complete (BACKEND-01, see
  `docs/backend/`); implementation is not started and `apps/api` is empty.
  Do not implement backend code without separate, explicit authorization
  for that specific step.

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
