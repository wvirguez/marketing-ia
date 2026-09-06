<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Impulso — frontend instructions

Read `../../AGENTS.md` for repository-wide development boundaries, Git rules,
quality gates, and the handoff protocol. These instructions apply to the current
frontend in `apps/web` and supplement those shared rules.

## Stack and implementation status

The frontend uses Next.js 16, React 19, TypeScript, Tailwind CSS 4, and the App
Router (`app/`). Check `package.json` and the lockfile for exact versions.

Approved stage baseline for DEV-TOOL-01:

| Stage | Status |
| --- | --- |
| FRONTEND-01 | CLOSED |
| FRONTEND-02 | APPROVED |
| FRONTEND-03 | APPROVED |
| FRONTEND-04 | APPROVED |
| FRONTEND-05 | APPROVED |

Approved UI: Dashboard, Login UX, New Campaign UX, and Campaign Workspace.
Approval of these interfaces does not mean real authentication or backend
integration exists.

Next planned stage: **FRONTEND-06 — Content Detail & Asset Preview**.
Do not start it until requested.

Pending: Content Detail & Asset Preview, Metrics UI, backend, real
authentication, PostgreSQL, and AI integrations. The future backend stack is
Python 3, FastAPI, Uvicorn, and PostgreSQL; do not implement it before requested.

## UI and architecture

- Brand name: **Impulso**.
- Use electric blue, cyan, deep navy, white/light backgrounds, rounded cards,
  soft shadows, and a premium SaaS feel consistent with the existing UI.
- Keep UX simple, intuitive, and business-oriented.
- Internal names `AGENT-00` through `AGENT-10` must not appear in primary user UI.
- Preserve approved flows and keep internal AI orchestration hidden.
- Keep demo data and future backend contracts separate from visual components.
  Do not embed future backend/business logic deeply in those components.

## Validation

After code changes, run `npm run lint` and `npm run build` from `apps/web`.
Report both results and any failures explicitly, following the completion
protocol in the root `AGENTS.md`.
