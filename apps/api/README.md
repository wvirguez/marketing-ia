# Impulso API

FastAPI + PostgreSQL + authentication/tenancy/campaign/orchestration/
research foundation for the Impulso backend — **BACKEND-02** (application
skeleton), **BACKEND-03** (persistence foundation), **BACKEND-04**
(identity, authentication, workspace tenancy), **BACKEND-05** (campaign
domain & persistence), **BACKEND-06** (orchestration foundation), and
**BACKEND-07** (Research + Audience persistence/contracts).

> **AI/AGENT EXECUTION NOT IMPLEMENTED. FRONTEND NOT WIRED.**
> BACKEND-07 adds persisted storage for exactly the four entities
> BACKEND-01 canonically assigns to the `research` bounded context —
> Research Report, Research Source, Audience Profile, VOC Evidence —
> with a GET-only read API. It is entirely inert: there is no public
> write endpoint, no AI provider call, no agent execution anywhere in
> this module. No `Content`, `Agent Run`, `Handoff`, `Return`, `Gate
> Decision`, `Strategy`, or billing table exists. `apps/web` has not
> been touched and does not call this API yet — see
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
  `GET /api/v1/auth/csrf` — unchanged since BACKEND-04
- `GET /api/v1/users/me`, `GET /api/v1/workspaces/current` — unchanged since BACKEND-04
- `POST /api/v1/campaigns`, `GET /api/v1/campaigns`,
  `GET /api/v1/campaigns/{id}`, `PATCH /api/v1/campaigns/{id}`,
  `POST /api/v1/campaigns/{id}/archive`,
  `GET /api/v1/campaigns/{id}/runs` — unchanged since BACKEND-05
- `POST /api/v1/campaigns/{id}/runs/{run_id}/initialize`,
  `POST .../start`, `GET .../` (run detail), `GET .../progress`,
  `GET .../stages`, `GET .../events`, `GET .../decisions`,
  `POST .../decisions/{decision_id}/respond` — unchanged since BACKEND-06
- `GET /api/v1/campaigns/{id}/research`, `GET /api/v1/campaigns/{id}/audience`
  — **new**, GET-only
- `GET /docs`, `GET /redoc`, `GET /openapi.json` — OpenAPI (enabled)

If `DATABASE_URL` has migrations applied (`alembic upgrade head`), the
full register → session → csrf → logout flow, the full campaign
create → list → get → patch → archive → list-runs flow, the full
orchestration initialize → start → progress → events → (decision
respond) flow, and the research/audience read endpoints all work with
real cookies — verified live with `curl` (see delivery notes) and in
`tests/test_auth_session.py`/`test_auth_csrf.py`/`test_registration.py`/
`test_campaigns_crud.py`/`test_campaigns_tenancy.py`/
`test_orchestration_lifecycle.py`/`test_orchestration_tenancy.py`/
`test_orchestration_hitl.py`/`test_orchestration_events.py`/
`test_research_domain.py`/`test_research_audit.py`/`test_research_api.py`.

## Campaign domain (BACKEND-05)

Three tables: **Campaign** (the durable business entity, directly
Workspace-owned), **Campaign Brief** (an immutable, versioned capture of
the original prompt + optional structured context — product, price,
audience, budget, channel — belonging to a Campaign; tenant-scoped only
transitively, through its Campaign, per the BACKEND-01 domain model),
and **Campaign Run** (one orchestration attempt against a Campaign,
directly Workspace-owned like `Membership`).

**Campaign ≠ Campaign Run.** Creating a Campaign Run in this stage is
*inert*: it persists exactly one row with `status=CREATED` and does
nothing else — no agent is invoked, no AI provider is called, no
research/strategy/content is produced, no external side effect of any
kind occurs. `PERSISTED RUN != EXECUTED ORCHESTRATION`. The orchestration
runtime that would ever move a run past `CREATED` does not exist yet
(a later, separately authorized stage — see
`docs/backend/BACKEND-01-ARCHITECTURE.md` §13).

**Atomic creation:** `POST /api/v1/campaigns` creates Campaign +
Campaign Brief v1 + Campaign Run #1 in one transaction, committed once
at the end (`app/campaigns/service.py::CampaignService.create_campaign`)
— identical transaction-ownership pattern to
`AuthService.register` (BACKEND-04). If anything fails before that
single `commit()`, nothing is persisted; proven directly (no HTTP) in
`tests/test_campaigns_crud.py::test_campaign_creation_failure_mid_transaction_leaves_no_orphan_rows`.

**Public IDs:** `CMP-`, `CBR-`, `RUN-`, same 12-character Crockford-base32
suffix convention as every other entity. Internal UUIDs are never
returned.

**Tenancy:** every campaign route depends on `get_current_workspace`
(never a client-supplied `workspace_id`) and, for a specific campaign,
`CampaignAccessService.get_authorized_campaign`
(`app/campaigns/service.py`) — the Campaign-domain counterpart of
BACKEND-04's `WorkspaceAccessService`, returning the same `FORBIDDEN`
error whether a campaign id does not exist at all or belongs to a
different workspace.

**Status:** `Campaign.status` mirrors state machine A
(`docs/backend/BACKEND-01-ARCHITECTURE.md` §2A) in full, but BACKEND-05
only ever persists `SUBMITTED` (brief captured, orchestration not
started) — no route in this stage transitions a Campaign to any other
status. `PATCH` only accepts `name`; status transitions are an
orchestration-runtime concern. Archiving is a separate, non-destructive,
idempotent action (`POST /{id}/archive`) that sets a nullable
`archived_at` timestamp — the same "soft state" convention already used
by `AuthSession.revoked_at` — rather than moving `status` to `ARCHIVED`
(which the state machine only reaches via `COMPLETED`, unreachable here).
Archived campaigns are excluded from `GET /api/v1/campaigns` by default;
pass `?include_archived=true` to see them. Nothing is ever deleted.

**Listing:** both `GET /api/v1/campaigns` and
`GET /api/v1/campaigns/{id}/runs` use bounded `limit`/`offset` pagination
(default 20, max 100), newest-first for campaigns and run-number-ascending
for runs.

## Orchestration foundation (BACKEND-06)

Turns `CampaignRun` from BACKEND-05's inert persistence scaffold into a
persisted, auditable **lifecycle** — still entirely inert (no AI, no
agents, no external side effects). ORCHESTRATION FOUNDATION != AI
EXECUTION; RUN STARTED != AI EXECUTED; STAGE COMPLETED != CONTENT
APPROVED — every one of these BACKEND-06's own boundaries is enforced
by what the code structurally cannot do (there is no code path that
calls an AI provider), not only by naming discipline.

**Tenancy invariant hardening:** `CampaignRun.workspace_id` must equal
its parent `Campaign.workspace_id`. Enforced at three layers: (1)
`CampaignRunRepository.create` takes the `Campaign` object, not a raw
`workspace_id` — there is no supported code path that could pass a
mismatched value; (2) `CampaignAccessService.get_authorized_run`
resolves a run only through its already-authorized campaign; (3) the
database itself — `campaigns` carries a `UNIQUE (id, workspace_id)`
candidate key, and `campaign_runs` references it with a **composite**
foreign key `(campaign_id, workspace_id)`, so PostgreSQL rejects a
divergent row even if constructed directly, bypassing every
application-level guard. Proven in
`tests/test_campaign_run_tenancy_invariant.py`.

**Business stages, not agent identifiers:** `RunStageExecution` persists
one row per business stage (`RESEARCH`, `AUDIENCE`, `STRATEGY`, `PLAN`,
`CONTENT`, `CREATIVE`, `DISTRIBUTION`, `PAID_MEDIA`, `TRACKING`,
`MEASUREMENT`, `LEARNING` — matching the module list in
`docs/backend/BACKEND-01-ARCHITECTURE.md` §9 and the `ProgressProjector`
mapping in §3) with a deterministic, persisted `ordinal` — never derived
from insertion order. No `AGENT-0N` identifier appears anywhere in the
default API surface.

This is deliberately new ground relative to BACKEND-01's domain-model
catalog, not a duplicate of anything already defined there: BACKEND-01's
"Campaign Phase" is explicitly a *derived, display-only* value ("which
one phase is active"), not stored, to avoid a second source of truth
once it can be computed from Agent Run data (BACKEND-07+). No Agent Run
exists yet — there is nothing to derive from. `RunStageExecution` is the
persisted *workflow structure* itself; a future "current phase"
projection reading the run's currently non-terminal stage from this
table *is* that derivation, not a competing fact.

**Run lifecycle:** `CampaignRunStatus` is unchanged from BACKEND-05 —
BACKEND-06 adds no new value and no new edge, since BACKEND-01 state
machine B already fully specifies it (`CREATED → RUNNING ⇄
AWAITING_HUMAN_DECISION`, `RUNNING → COMPLETED`, any active state →
`FAILED`/`CANCELLED`). **Stage lifecycle** (`StageExecutionStatus`) is
new: `PENDING → READY → RUNNING → {WAITING_FOR_INPUT, BLOCKED,
COMPLETED, FAILED, CANCELLED}` — terminal states
(`COMPLETED`/`FAILED`/`SKIPPED`/`CANCELLED`) have no outgoing edges.
Both matrices live in exactly one place,
`app/orchestration/transitions.py`; no router or repository ever checks
or applies a transition itself. An illegal transition is a deterministic
`409 INVALID_LIFECYCLE_TRANSITION`, never a silent no-op or a raw 500.

**No client-controlled completion:** there is no generic "set run
status" endpoint and no route accepts a `status` field at all — a
`PATCH` to a run returns `405` (the path simply has no such method).
Every transition happens through a specific, named domain operation.

**Initialize vs. start (BACKEND-06 §14/§15), two explicit steps:**
- `POST .../initialize` — materializes all 11 `RunStageExecution` rows
  (all `PENDING`). **Idempotent**: a second call returns the
  already-materialized stages unchanged rather than erroring or
  duplicating. Requires the run to be `CREATED`.
- `POST .../start` — requires stages to already be materialized
  (`409 ORCHESTRATION_NOT_INITIALIZED` otherwise); transitions the run
  `CREATED → RUNNING` and promotes only stage #1 (`RESEARCH`)
  `PENDING → READY` — never further. **Not idempotent** — a second call
  is a deterministic `409`, since "starting" is a meaningful one-time
  transition, unlike re-materializing the same stage set.

**Human-in-the-loop foundation:** `HumanDecisionRequest`/
`HumanDecisionResponse`, named exactly per
`docs/backend/BACKEND-01-ARCHITECTURE.md` §4 (`OPEN → RESOLVED |
EXPIRED | CANCELLED`; BACKEND-06 never itself sets `EXPIRED` — that
policy is explicitly deferred by BACKEND-01 itself). **No public HTTP
endpoint creates a decision request** — nothing in this stage's
supported flows legitimately needs to raise one yet (no agent exists to
escalate a question); `OrchestrationService.create_decision_request` is
the controlled, service-only mechanism tests use to construct one, and
the entry point a future runtime phase will call for real. Opening a
request forces the run to `AWAITING_HUMAN_DECISION`; responding
(`POST .../decisions/{id}/respond`, CSRF-required) resolves it and
resumes the run to `RUNNING` — the entirety of "resume" this stage
implements, since no Handoff exists yet to advance. One response per
request, enforced by a unique DB index, not only a status check; a
second response attempt is a deterministic `409
DECISION_ALREADY_RESOLVED`. The request row is row-locked
(`SELECT ... FOR UPDATE`) while responding, so two concurrent responses
to the same request cannot both observe `OPEN` and both proceed —
proven with two real threads/connections in
`tests/test_orchestration_concurrency.py`.

**Append-only traceability:** `AuditEvent` (`app/audit/`, its own
top-level module per the BACKEND-01 §9 module-boundary proposal,
implementing the entity BACKEND-01 already named and prefixed — `AUDT`).
No `PATCH`/`DELETE` route exists for it, and the model itself carries no
`updated_at` column at all, so there is no `onupdate` trigger a future
change could ever accidentally rely on. Every lifecycle transition
records exactly one event, in the *same* transaction as the state
change — if the event's own `flush()` fails, nothing commits, proven in
`tests/test_orchestration_events.py::test_failed_event_persistence_rolls_back_the_state_transition`.
`actor_type` is always `USER` in this stage; `SYSTEM`/`AGENT` exist only
for forward compatibility and are never emitted here.

**Progress API:** `GET .../progress` is deliberately minimal —
campaign/run public ids, run status, the current non-terminal stage,
the ordered stage list, `waiting_for_input`, `open_decision_count`. No
percentage (BACKEND-01 defines no formula, so none is fabricated), no
internal ids, no chain-of-thought field exists anywhere in this schema
(BACKEND-06 §27) — checked directly in
`tests/test_orchestration_security.py`.

**Public IDs:** `STG-` (Stage Execution — not BACKEND-01-reserved, chosen
here), `HDR-` (Human Decision Request — the exact BACKEND-01-reserved
prefix), `HDS-` (Human Decision Response — not BACKEND-01-reserved,
chosen to parallel `HDR`), `AUDT-` (Audit Event — the exact
BACKEND-01-reserved prefix). Same 12-character Crockford-base32 suffix
convention as every other entity; no internal UUID ever leaves this
module's response schemas.

**Existing-run migration safety:** the BACKEND-06 migration adds three
new tables and hardens two existing constraints — it does not touch any
`campaign_runs` row's *data*. A `CampaignRun` created back in BACKEND-05
remains `CREATED` with zero `RunStageExecution` rows after this
migration; nothing is retroactively "initialized" or backfilled into
any executed-looking state. Historical truth is preserved exactly:
those runs were persisted, never executed, and still are.

## Research / Audience persistence (BACKEND-07)

Persists exactly the four entities BACKEND-01 canonically assigns to the
`research` bounded context (`docs/backend/BACKEND-01-ARCHITECTURE.md`
§1): **Research Report**, **Research Source**, **Audience Profile**,
**VOC Evidence** — no more, no less. There is no separate "Finding" or
"Audience Insight" table: BACKEND-01's own entity catalog treats a
Report's `summary` as the finding itself, and VOC Evidence belongs
directly to Audience Profile (the ER diagram draws no edge to Research
Source at all).

**Ownership:** both Report and Profile are Campaign-owned (composite FK
`(campaign_id, workspace_id) → campaigns(id, workspace_id)`, reusing the
candidate key BACKEND-06 already added to `Campaign`) — matching
BACKEND-01's ER diagram exactly, where both hang directly off Campaign,
not off CampaignRun. `campaign_run_id` (composite FK against a new
candidate key added to `CampaignRun` in this stage) and
`stage_execution_id` are carried as **required provenance** — which run
and which stage-execution instance produced this version — never as the
ownership relationship. A new `CampaignRun` therefore never overwrites
an earlier run's evidence: two runs on the same Campaign each get their
own Report/Profile rows.

**Provenance is proven, not assumed:** a foreign key alone cannot prove
a `CampaignRun` actually belongs to the stated `Campaign`, or that a
`RunStageExecution` is the right one. `app/research/service.py`
explicitly checks all four: `campaign_run.campaign_id == campaign.id`,
`campaign_run.workspace_id == campaign.workspace_id`,
`stage_execution.campaign_run_id == campaign_run.id`, and
`stage_execution.stage == RESEARCH`/`AUDIENCE` as appropriate — any
mismatch raises the same `PROVENANCE_MISMATCH` error, never revealing
which specific check failed.

**Immutable, versioned, no status field:** neither `ResearchReport` nor
`AudienceProfile` ever uses `TimestampMixin` — only `created_at` exists,
no `updated_at`, so there is no `onupdate` trigger to even accidentally
rely on. A "new version" is a brand-new row with an incremented
`version` integer (mirroring `CampaignBrief`'s own precedent); the
`(campaign_id, version)` unique constraint is the sole, authoritative
concurrency guard — the service computes the next version optimistically
and lets the database reject a race, mapping the resulting
`IntegrityError` to a deterministic `VERSION_CONFLICT`. **No
`status`/`ACTIVE`/`SUPERSEDED`/`supersedes_id` field exists anywhere** —
"current" is simply `MAX(version)` for the Campaign; old versions are
never edited or deleted.

**No public write endpoint.** BACKEND-01's own API map defines
`GET /campaigns/{id}/research` and `GET /campaigns/{id}/audience` as
GET-only — there is no `POST`/`PATCH`/`DELETE` anywhere in this module.
Writes happen only through `ResearchService.record_report`/
`record_audience_profile`, called directly by tests today and by a
future orchestration/agent runtime once one exists — the same
service-layer-only shape BACKEND-06's `create_decision_request` already
established. Persisting a Report/Profile never mutates
`CampaignRun.status`, `RunStageExecution.status`, or any
`HumanDecisionRequest`/`Response` — `OUTPUT PERSISTED != STAGE
COMPLETED`.

**VOC integrity:** `verbatim_quote` and `paraphrase` are separate
columns — a paraphrase never overwrites the original wording. A
database check constraint (`verbatim_quote IS NOT NULL OR paraphrase IS
NOT NULL`) rejects a row recording neither; the repository normalizes
blank/whitespace-only strings to `NULL` before insert, so the constraint
cannot be bypassed with semantically-empty content. VOC Evidence carries
no foreign key to Research Source (BACKEND-01's ER diagram draws no such
edge) — only a plain `source_locator` string.

**Audit attribution:** `AuditEvent` gained two new nullable FKs,
`research_report_id`/`audience_profile_id`, so a
`research.report.recorded`/`audience.profile.recorded` event can be
traced to the *exact* Report/Profile it is about — the same fix
BACKEND-06R applied for decisions, applied here from the start. One
event per aggregate write (Report + all its Sources; Profile + all its
VOC items), in the same transaction as the aggregate — if the event
write fails, or any child row fails, nothing commits.

**Known, explicitly-flagged design choices, not BACKEND-01-canonical:**
the `SRCE`/`VOC` public-ID prefixes (BACKEND-01 only reserves `RPT`/`AUD`);
the `SourceType` enum vocabulary (`ARTICLE`/`REPORT`/`FORUM`/`SOCIAL`/
`SURVEY`/`OTHER`) is descriptive-only, never a reliability/truth signal.

## Planning persistence (BACKEND-09)

Persists exactly the two entities BACKEND-01 canonically assigns to the
`planning` bounded context (`docs/backend/BACKEND-01-ARCHITECTURE.md` §1):
**Content Plan**, **Plan Item** — no more, no less. Content Brief, Content
Piece, Content Version, Content Approval, Creative Brief, and Asset all
belong to other bounded contexts (`content`/`assets`) and are not modeled
here. PLAN ITEM != CONTENT BRIEF. PLAN ITEM != CONTENT PIECE. PLANNING !=
CONTENT PRODUCTION.

**Ownership:** `ContentPlan` is Campaign-owned (composite FK
`(campaign_id, workspace_id) → campaigns(id, workspace_id)`), matching
`Strategy`'s own precedent exactly — the ER diagram hangs Content Plan
directly off Campaign, a sibling of Strategy, not a child of it.
`campaign_run_id`/`stage_execution_id` are carried as **required
provenance** only. `PlanItem` belongs to `ContentPlan` via a plain FK and
carries **no `workspace_id` of its own** — BACKEND-01 annotates its tenant
column "Workspace (**via Plan**)", the same qualified, via-parent pattern
already used by `Positioning`/`ResearchSource`; tenancy is always resolved
by traversal through the parent Content Plan, never trusted from client
input.

**Provenance is proven, not assumed** — the same four explicit checks
`app/strategy/service.py` established: `campaign_run.campaign_id ==
campaign.id`, `campaign_run.workspace_id == campaign.workspace_id`,
`stage_execution.campaign_run_id == campaign_run.id`, and
`stage_execution.stage == PLAN` (the `BusinessStage.PLAN` enum value
already existed since BACKEND-06 — no new enum value was needed). Any
mismatch raises the same non-leaky `ProvenanceMismatchError`.

**Immutable, versioned Content Plan; write-once Plan Items:** `ContentPlan`
follows the Strategy/ResearchReport precedent exactly — no `status`,
`approved`, or `ready_for_production` field; "current" is `MAX(version)`
for the Campaign, and the `(campaign_id, version)` unique constraint is the
sole concurrency guard, mapped to a deterministic `VersionConflictError` on
conflict. `PlanItem` is **not** independently versioned. BACKEND-01
describes it as "Mutable? Yes (until briefed)" but names no mutation
fields, no lifecycle vocabulary, and the triggering state ("briefed") is
reached only by a Content Brief existing — out of scope for this bounded
context. Inventing a status/lifecycle column to represent an undefined
state would fabricate semantics BACKEND-01 never specified (the same
discipline already applied to `Experiment.status` in BACKEND-08).
**Plan Item canonical mutability is acknowledged but not implemented**:
this stage persists Plan Items with their initial values only — no
`PATCH`/`PUT`, no `update_plan_item`/`transition_plan_item`/
`mark_briefed`/`lock_plan_item` method exists anywhere in this module.

**No structural Strategy reference.** BACKEND-01 defines no FK from
Content Plan to Strategy/Positioning/Hypothesis/Experiment — the ER
diagram draws Content Plan as Strategy's direct sibling under Campaign,
not its child. This means a Content Plan cannot be mechanically traced to
the exact Strategy version that informed it. This is a documented,
carried-forward architectural gap, not repaired here by inventing a
relationship BACKEND-01 never specified. STRATEGY PERSISTED != READY FOR
PLANNING.

**No public write endpoint.** BACKEND-01's own API map defines
`GET /campaigns/{id}/plan` as GET-only. Writes happen only through
`PlanningService.record_plan`, called directly by tests today and by a
future orchestration/agent runtime once one exists. `record_plan` is a
single atomic transaction — Content Plan + all initial Plan Items + one
`AuditEvent` per created entity — and never mutates `CampaignRun.status`,
`RunStageExecution.status`, or any `HumanDecisionRequest`/`Response`.
`OUTPUT PERSISTED != STAGE COMPLETED`. `PLAN PERSISTED != PLAN APPROVED`.
`PLAN ITEM PERSISTED != PRODUCTION AUTHORIZED`.

**Audit attribution:** `AuditEvent` gained two new nullable FKs,
`content_plan_id`/`plan_item_id`, so a `planning.plan.recorded`/
`planning.plan_item.recorded` event can be traced to the *exact* Content
Plan/Plan Item it is about, the same fix BACKEND-07/08 already applied for
their own child entities — a Plan Item's identity is never inferred from
`sequence`, event ordering, or event metadata text.

**Scheduling semantics:** `PlanItem.sequence` (an integer ordinal) and
`PlanItem.scheduled_date` (a nullable `DATE`, no time-of-day, no
timezone, no recurrence) are planning-internal target values only — they
never imply external scheduling, publication, or distribution
authorization. Deliberately not named `publish_at`.

**Known, explicitly-flagged design choices, not BACKEND-01-canonical:**
`PlanItem.format`/`PlanItem.objective` are plain bounded strings, not
native enums — BACKEND-01 names no vocabulary for either, the same
discipline already applied to `Experiment.status`.

## Content persistence (BACKEND-10)

Persists exactly the four entities the BACKEND-10 Governance Freeze
authorizes: **Content Brief**, **Content Piece**, **Content Version**,
**Content Approval**. Content Revision Request, Creative Brief, Asset,
Asset Version, Distribution, Paid Media, and any Experiment/Strategy
linkage are explicitly deferred — nothing in `app/content/` implements,
references, or invents any of them.

**Content Brief provenance is derived via Planning ancestry, not its own
run/stage columns.** BACKEND-01's own text — "the creative brief AGENT-04/03
hands to AGENT-05" — makes AGENT-04/03 the producer and AGENT-05 the
consumer, so `BusinessStage.CONTENT` consumes the brief rather than
producing it; `ContentBrief` carries no `campaign_id`/`campaign_run_id`/
`stage_execution_id` of its own. Its tenant-safety composite FK is
anchored at `ContentPlan` (`(content_plan_id, workspace_id) ->
content_plans(id, workspace_id)`) — not at `PlanItem`, which has no
`workspace_id` and must not gain one. `PlanItem` remains the sole semantic
parent; `content_plan_id` exists on `ContentBrief` only as a tenant-safety
anchor, service-verified against `plan_item.content_plan_id` at creation.
This required one small, additive, explicitly-authorized change to
`ContentPlan` itself: a new candidate key, `UniqueConstraint(id,
workspace_id)`, purely so `ContentBrief` could declare its composite FK —
the same pattern BACKEND-08 already used for `Strategy`/`Hypothesis`.
`PlanItem`'s own fields, tenancy, and "mutable until briefed, not
implemented" reservation are all completely unchanged.

**Plan Item -> Content Brief is `1 : 0..1`, `UNIQUE(plan_item_id)`
enforced — a BACKEND-10 governance schema decision, not a BACKEND-01
mandate.** BACKEND-01 itself never states this cardinality in prose; only
an ER-diagram inference exists. This is documented here plainly as an
engineering default chosen for safety and reversibility, not represented
as canonical. No re-brief mechanism (`supersedes_id`/`brief_version`/
`replacement_brief_id`) exists.

**Content Piece** carries the complete, canonical, *named* state-machine-D
vocabulary from `docs/backend/BACKEND-01-ARCHITECTURE.md` §2D verbatim
(`DRAFT` through `ARCHIVED`) as a native enum — unlike `Experiment.status`,
BACKEND-01 explicitly names every value here. Enum membership is not
transition authority: `app/content/transitions.py` knows the *complete*
legal graph (including edges BACKEND-10 exposes no method for), while
`app/content/service.py` exposes methods only for the bookkeeping chain
(`DRAFT/REVISION_REQUESTED -> IN_PRODUCTION -> PRODUCED ->
READY_FOR_REVIEW`), the narrow `READY_FOR_REVIEW | APPROVED -> ARCHIVED`
edge, and the governance-gated `READY_FOR_REVIEW -> APPROVED` edge — which
exists **only** through the Approval-decision coupling below, never as a
freestanding method. No method anywhere enters `READY_FOR_DISTRIBUTION`/
`DISTRIBUTED` — those enum values exist (completing the canonical
vocabulary) but their runtime triggers are deferred to a future
`distribution` bounded context.

**Content Version has no ordinal/version column at all** — BACKEND-01's
own explicit field list for this entity ("id, content_piece_id,
created_at, created_by (agent or user)") omits one entirely. "Current"/
"latest" is derived deterministically by `ORDER BY created_at DESC, id
DESC`. Because Postgres's `now()` returns *transaction* start time (which
would make two Versions created in the same transaction indistinguishable
by timestamp), `ContentVersion.created_at`'s server default uses
`clock_timestamp()` instead — the real per-statement wall-clock time —
so this ordering is genuinely deterministic even for rapid-succession
inserts, without reintroducing an ordinal column.

**Content Approval targets an exact Content Version, never a Piece
directly**, using the complete, canonical, named state-machine-E
vocabulary (`REQUESTED`/`UNDER_REVIEW`/`APPROVED`/`CHANGES_REQUESTED`/
`REJECTED`/`EXPIRED`). PERSISTING AN APPROVAL DECISION != HAVING AUTHORITY
TO MAKE THAT DECISION: `record_authorized_approval_decision` never itself
determines whether the caller was allowed to approve — it persists a
decision already authorized elsewhere (a human reviewer, per BACKEND-01's
own explicit human-in-the-loop MVP path), the same trust boundary
`HumanDecisionResponse`/`StrategyService.transition_hypothesis` already
establish. WORKSPACE ROLE != CONTENT APPROVAL AUTHORITY — nothing in this
module calls `require_role` or checks any role/permission as a substitute
for content-governance authority.

Only the `APPROVED` decision is coupled, atomically, with a Content Piece
transition (`READY_FOR_REVIEW -> APPROVED`) — the architecture doc's own
explicit transaction-boundary rule ("`APPROVED` must never be set without
a corresponding governance record"). `CHANGES_REQUESTED` and `REJECTED`
persist the Approval decision only and never touch `ContentPiece.status`:
the `CHANGES_REQUESTED -> REVISION_REQUESTED` coupling some readers might
expect is a real, carried-forward reservation (inferred from the state
machine's shape, never directly proven the way `APPROVED`'s coupling is),
and `REJECTED` has no canonical Content Piece mapping at all. No automatic
`EXPIRED` mechanism exists (no scheduler/async infrastructure of any
kind).

**No public write endpoint exists anywhere in this module.** BACKEND-01's
own API map marks `GET /campaigns/{id}/content` and `GET
/campaigns/{id}/content/{id}` GET-only; the canonical `POST
.../approvals` route is **deferred**, not implemented — BACKEND-01 itself
defers "who may approve," and this stage does not silently choose "any
workspace member" or "OWNER/ADMIN" as a substitute answer. Every write in
this module is service-layer-only, called directly by tests today and by
a future, separately-authorized runtime later.

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

New in BACKEND-05: `tests/test_campaigns_crud.py` (creation + atomicity,
public ids, listing/pagination, retrieval, patch, archive, run listing,
uniqueness constraints), `tests/test_campaigns_tenancy.py`
(cross-workspace denial), plus a dedicated migration round-trip test in
`tests/test_migrations.py` that downgrades to exactly the pre-BACKEND-05
revision (not `base`) and back — all `postgres`-marked.

New in BACKEND-06: `tests/test_orchestration_lifecycle.py` (initialize/
start, idempotency, transition matrix, terminal-state protection, no
generic status endpoint), `tests/test_orchestration_tenancy.py`
(cross-workspace denial across every orchestration route),
`tests/test_orchestration_hitl.py` (decision open/respond, double-
response rejection, cross-tenant denial, response-history preservation),
`tests/test_orchestration_events.py` (event emission, ordering, tenant
scoping, no mutation endpoint, atomicity-on-failure),
`tests/test_orchestration_concurrency.py` (a real two-thread proof of
the decision-response row lock), `tests/test_campaign_run_tenancy_invariant.py`
(structural + database-level proof of the `CampaignRun.workspace_id ==
Campaign.workspace_id` invariant), `tests/test_orchestration_security.py`
(no raw UUIDs, no agent identifiers, no chain-of-thought field, no
client-supplied tenancy field has authority), plus a dedicated migration
round-trip test in `tests/test_migrations.py` that downgrades to exactly
the pre-BACKEND-06 revision and back — all `postgres`-marked.

New in BACKEND-07: `tests/test_research_domain.py` (persistence,
provenance — valid and invalid — versioning, historical isolation across
runs, VOC verbatim/paraphrase integrity, evidence-integrity/no-forbidden-
field assertions), `tests/test_research_audit.py` (exact Report/Profile
attribution, distinguishable events across versions, aggregate rollback
on event or child failure), `tests/test_research_api.py` (GET-only route
surface, combined response shape, empty-output behavior, tenancy,
security), plus a dedicated migration round-trip test in
`tests/test_migrations.py` that downgrades to exactly the pre-BACKEND-07
revision and back — all `postgres`-marked.

New in BACKEND-09: `tests/test_planning_domain.py` (persistence,
provenance — valid and invalid — versioning, via-parent Plan Item
tenancy, no-forbidden-field/no-forbidden-table governance assertions, no
Plan Item mutation method), `tests/test_planning_audit.py` (exact Content
Plan/Plan Item attribution — never inferred from sequence or event
ordering — distinguishable events across versions, aggregate rollback on
event or child failure, no partial audit set survives a mid-loop
failure), `tests/test_planning_api.py` (GET-only route surface, combined
response shape, empty-output behavior, tenancy, security), plus a
dedicated migration round-trip test in `tests/test_migrations.py` that
downgrades to exactly the pre-BACKEND-09 revision and back — all
`postgres`-marked.

New in BACKEND-10: `tests/test_content_domain.py` (Brief/Piece/Version/
Approval persistence, Brief ancestry provenance — valid and invalid —
duplicate-Brief rejection, tenancy at every level including the two
ancestor-anchored composite FKs, the complete Content Piece/Approval
transition graphs independent of service exposure, bookkeeping-transition
legality, the `APPROVED` coupling and its `READY_FOR_REVIEW`
precondition, `CHANGES_REQUESTED`/`REJECTED` non-coupling, no-forbidden-
field/no-forbidden-method/no-forbidden-table governance assertions),
`tests/test_content_audit.py` (exact Brief/Piece/Version/Approval
attribution — two Pieces created close together never confused — the
`APPROVED` transaction's dual attribution, aggregate rollback on event or
child failure, the coupled Approval+Piece transaction's all-or-nothing
rollback), `tests/test_content_api.py` (GET-only route surface for both
routes, no Content Brief route, no Approval route, combined Piece+latest-
Version response shape, empty-output behavior, tenancy, security), plus a
dedicated migration round-trip test in `tests/test_migrations.py` that
downgrades to exactly the pre-BACKEND-10 revision and back — all
`postgres`-marked.

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
  reported gap from BACKEND-03, still present in BACKEND-05's new tables.
- No Row-Level Security, no production pool tuning, no secret-manager
  integration — see `docs/backend/BACKEND-01-ARCHITECTURE.md` §6.
- Campaign Brief's optional structured context fields (`product_type`,
  `price`, `audience`, `budget`, `channel`) are free-text strings, not
  validated/normalized enums — matching the frontend's current form
  inputs; tightening them into real enums is a later concern.
- Human Decision Request has no separate "Human Decision Option"
  (structured multiple-choice) entity yet — `question`/`response_text`
  are both free text. BACKEND-01 §4 describes structured options as a
  future refinement for consequential decisions; deferred here to keep
  BACKEND-06 minimal.
- `Human Decision Response`'s public-ID prefix (`HDS`) is not among
  BACKEND-01's confirmed prefixes (only `HDR`, Human Decision Request,
  is reserved there) — chosen here to parallel `HDR`; likewise `STG`
  (Stage Execution) is new, since that entity itself is new to
  BACKEND-06. `AUDT` (Audit Event) does reuse BACKEND-01's own reserved
  prefix.
- The BACKEND-06 §6 tenancy-invariant composite FK is scoped exactly to
  `CampaignRun`/`Campaign` (the pair the request explicitly named); it
  is not extended to `RunStageExecution`/`HumanDecisionRequest` and
  their parent `CampaignRun` — those instead use the lighter
  "repository derives `workspace_id` from the parent object, never an
  independent parameter" pattern. A future stage could add the same
  composite-FK treatment there if warranted.
- `ResearchSource`/`VOCEvidence`'s public-ID prefixes (`SRCE`/`VOC`) are
  not among BACKEND-01's confirmed prefixes (only `RPT`/`AUD` are
  reserved there) — explicitly authorized as new, non-canonical
  prefixes for this stage.
- No PII detection/scrubbing exists for `VOCEvidence.verbatim_quote` —
  storing a real customer's exact words risks incidentally capturing an
  identifying detail if the source itself wasn't anonymized. BACKEND-01
  gives no retention/anonymization policy for this; reserved, not solved.
- No discrete "Finding" entity exists separate from
  `ResearchReport.summary` — if a future stage needs independently
  citable, independently approvable findings, that is a real schema
  change (a new table + migration), not a JSON-field patch.

## Explicitly deferred (not in this stage)

- `Content`, `Agent Run`, `Handoff`, `Return`, `Gate Decision`, `Stop
  Condition`, `Subscription`, or any other real-execution/billing table
- `AGENT-00`…`AGENT-10` runtime, real AGENT-00 sequencing logic, and any
  AI provider integration (OpenRouter, OpenAI, Anthropic, etc.) — nothing
  in `app/research/` (or `app/orchestration/`) ever produces a generated
  output
- Any external integration (Meta, Google Ads, Stripe, etc.)
- Strategy, Positioning, Hypothesis, Experiment, Content Plan, and every
  other BACKEND-01-catalogued entity downstream of real agent execution
  or Strategy — `AudienceProfile`/`ResearchReport` never imply a
  strategic decision, approved target, or positioning
- Campaign status transitions beyond `SUBMITTED` — BACKEND-06/07 add
  orchestration/stage lifecycle and evidence-persistence machinery but
  still never move a `Campaign` itself past `SUBMITTED`
- `Human Decision Option` (structured multiple-choice decisions) — see
  Known reservations above
- Asynchronous execution of any kind — Celery/RQ/Dramatiq/Redis/Kafka/
  RabbitMQ/background workers/schedulers. Every BACKEND-06/07 operation
  is a synchronous, in-request/in-test database transaction.
- Frontend wiring — `apps/web` remains mock/static; its "Investigación"/
  "Audiencia" tabs render fixed demo arrays with no shape resembling
  `ResearchReport`/`AudienceProfile` yet

## Future module layout

`app/auth/`, `app/users/`, `app/workspaces/` (BACKEND-04),
`app/campaigns/` (BACKEND-05), `app/orchestration/`/`app/audit/`
(BACKEND-06), and `app/research/` (BACKEND-07) join `app/api`, `app/core`,
`app/shared`, `app/persistence` alongside future `app/agents/`,
`app/strategy/`, `app/content/`, `app/measurement/`, etc. — each with
its own `models.py`, `schemas.py`, `repository.py`, `service.py`, and
`router.py`, included into `app/api/v1/router.py` one line at a time.
