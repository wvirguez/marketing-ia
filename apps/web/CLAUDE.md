@../../AGENTS.md
@AGENTS.md

# Claude Code frontend entry point

The imports above load shared repository rules and frontend-specific rules,
including the Next.js documentation warning, approved stages, UI principles,
and validation requirements. Keep these rules in the corresponding `AGENTS.md`
files so Claude Code and Codex follow the same instructions.

The current frontend is `apps/web`. Its MVP (FRONTEND-01 through
FRONTEND-09) is complete and approved with reservations (FRONTEND-MVP-REVIEW).
No further FRONTEND-NN stage is currently planned. The FastAPI application
foundation (BACKEND-02/02R) and the PostgreSQL persistence foundation
(BACKEND-03) exist in `apps/api`, but the frontend remains mock/static and is
not wired to the API. The next authorized/planned step is BACKEND-04 —
Authentication, Users & Workspace Tenancy, which has not started; see the
root `AGENTS.md` `## Project status` section and `docs/backend/` for the full
architecture. Begin backend work only when explicitly requested.
