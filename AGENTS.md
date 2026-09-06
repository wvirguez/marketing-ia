# Impulso — repository instructions

Impulso is a digital marketing SaaS assisted by multiple internal AI agents.
These repository-wide instructions apply to Codex and Claude Code. Frontend
instructions and approved stage status live in `apps/web/AGENTS.md`.

## Project layout and stack

- Current application: `apps/web`, using Next.js 16, React 19, TypeScript,
  Tailwind CSS 4, and the App Router.
- Future backend: `apps/api`, planned with Python 3, FastAPI, Uvicorn, and
  PostgreSQL. This backend is not implemented or authorized by this handoff.

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
