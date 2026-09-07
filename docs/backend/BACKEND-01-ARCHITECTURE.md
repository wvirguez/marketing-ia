# BACKEND-01 — Architecture (Bounded Contexts, State Machines, Runtime Model)

Status: architecture/documentation only. No FastAPI code, no database, no
migrations, no packages installed, no endpoints, no agent execution. This
document describes the target logical architecture for the future
`apps/api` service and does not authorize building it.

## 1. Bounded Contexts (Modular Monolith)

One deployable service (`apps/api`), one PostgreSQL database, hard module
boundaries enforced at the Python-package level (no cross-module ORM
reach-through; modules talk to each other through explicit service
functions/interfaces, not by importing each other's models directly).

| Context | Owns | Does NOT own |
|---|---|---|
| `auth` | Credentials, sessions/tokens, password resets | User profile fields, roles |
| `users` | User identity, profile, preferences | Membership/role assignment |
| `workspaces` | Organization, Workspace, Membership, Role assignment | Campaign content |
| `campaigns` | Campaign, Campaign Brief, Campaign Version, Campaign status (business view) | Orchestration internals |
| `orchestration` | Orchestration Run, Handoff, Return, Gate Decision, Dependency, Stop Condition, Runtime State | Agent business logic, campaign business fields |
| `agents` | Agent Definition (registry), Agent Run | Actual AI provider calls (deferred) |
| `research` | Research Report, Source Reference, VOC Evidence, Audience Profile | Strategy decisions |
| `strategy` | Strategy, Positioning, Hypothesis, Experiment | Content production |
| `planning` | Content Plan, Plan Item | Content Piece bodies |
| `content` | Content Brief, Content Piece, Content Version, Content Approval, Content Revision Request | Creative asset binaries |
| `assets` | Creative Brief, Asset, Asset Version, Asset Metadata | Content copy/text |
| `distribution` | Distribution Plan, Distribution Readiness | Actual publishing (deferred/manual) |
| `paid_media` | Paid Media Plan, Paid Scope, authorization state | Actual ad-platform execution (deferred/manual) |
| `tracking` | Tracking Plan, Tracking Requirement, Tracking Status | Actual analytics platform config (deferred/manual) |
| `measurement` | Metric entries, Performance Observation/Signal, Analysis Result | Learning validation |
| `learning` | Learning Candidate, Provisional/Validated Learning, Strategic Recommendation Candidate | Strategy execution |
| `integrations` | Integration Definition, Workspace Integration (connection state only) | Actual third-party calls (deferred) |
| `billing` | Subscription Plan, Subscription, Billing Customer Reference | Payment processing (deferred) |
| `audit` | Audit Event, Traceability Link, Governance Decision, Capability State, Authorization Record | Business state itself (records facts about it) |

Rationale: this mirrors the frontend's own tab/section boundaries
(Investigación / Audiencia / Estrategia / Plan / Contenido / Creatividades /
Paid Media / Tracking / Métricas learned during FRONTEND-01…09), so each
backend module has a natural 1:1 UI surface it serves — no context spans
more than the UI concepts it backs.

## 2. State Machines

States are business-meaningful and are **not** collapsed even when a
naive implementation might be tempted to merge them, per the governance
invariants in section 4 of the request. Every listed transition is a
single directed edge; unlisted transitions are forbidden.

### A. Campaign
```
DRAFT → SUBMITTED → ORCHESTRATING ⇄ AWAITING_HUMAN_INPUT
ORCHESTRATING → READY_FOR_EXECUTION → ACTIVE ⇄ PAUSED
ACTIVE → COMPLETED → ARCHIVED
DRAFT | SUBMITTED | ORCHESTRATING | READY_FOR_EXECUTION → CANCELLED
```
- `DRAFT` = the natural-language idea has been typed but not submitted (maps to today's New Campaign composer before "Crear campaña").
- `SUBMITTED` = brief captured, orchestration not yet started.
- `ORCHESTRATING` = AOL-00 actively sequencing specialists.
- `AWAITING_HUMAN_INPUT` = a Human Decision Request is open (see §5); Campaign and its Orchestration Run are both logically paused.
- `READY_FOR_EXECUTION` = research/audience/strategy/plan/content/paid-plan/tracking-plan exist; nothing has been executed externally yet.
- `ACTIVE` = user has confirmed real-world execution is underway (manual today).
- `PAUSED`, `COMPLETED`, `ARCHIVED`, `CANCELLED` are terminal-ish states matching the existing frontend `campaignStatuses` vocabulary (`Borrador`, `En preparación`, `Activa`, `Pausada`, `Completada` — see Frontend Compatibility Review in the API map doc for the naming translation).

### B. Orchestration Run
```
CREATED → RUNNING ⇄ AWAITING_HUMAN_DECISION
RUNNING → COMPLETED
CREATED | RUNNING | AWAITING_HUMAN_DECISION → FAILED
CREATED | RUNNING | AWAITING_HUMAN_DECISION → CANCELLED
```
One Orchestration Run belongs to exactly one Campaign (see §17 in the
domain-model doc for why "Campaign Run" and "Orchestration Run" are
*the same table*, not two competing ones). `RUNNING` is the umbrella
state while individual Agent Runs execute in sequence; the currently
active specialist is derived from the latest non-terminal Agent Run,
not from a separate enum on the Orchestration Run itself (single source
of truth — see the Source-of-Truth Matrix).

### C. Agent Run
```
QUEUED → RUNNING → RETURNED
RETURNED → ACCEPTED           (Gate Decision: proceed)
RETURNED → REVISION_REQUESTED (Gate Decision: redo — spawns a new Agent Run, same Handoff)
RETURNED → ESCALATED          (Gate Decision: needs human — raises a Human Decision Request)
QUEUED | RUNNING → FAILED
QUEUED | RUNNING | RETURNED → CANCELLED
```
`ACCEPTED` / `REVISION_REQUESTED` / `ESCALATED` are Gate Decision outcomes,
never something an Agent Run sets on itself — this is what keeps
`PRODUCED ≠ APPROVED` mechanically true: only AGENT-00's Gate can move a
Return into `ACCEPTED`.

### D. Content Piece
```
DRAFT → IN_PRODUCTION → PRODUCED → READY_FOR_REVIEW
READY_FOR_REVIEW → REVISION_REQUESTED → IN_PRODUCTION   (loop)
READY_FOR_REVIEW → APPROVED → READY_FOR_DISTRIBUTION → DISTRIBUTED
READY_FOR_REVIEW | APPROVED → ARCHIVED
```
`PRODUCED` and `READY_FOR_REVIEW` are deliberately distinct: `PRODUCED`
means AGENT-05 finished its own self-check; `READY_FOR_REVIEW` means it
has been formally handed to the approval gate. `APPROVED` is set **only**
by a Content Approval record resolving `APPROVED` (see E) — Content Piece
never self-transitions into `APPROVED`.

### E. Content Approval (one record per approval attempt, tied to a Content Version)
```
REQUESTED → UNDER_REVIEW → APPROVED
UNDER_REVIEW → CHANGES_REQUESTED
UNDER_REVIEW → REJECTED
REQUESTED | UNDER_REVIEW → EXPIRED
```
Only AGENT-00 (or, in the human-in-the-loop MVP path, a human reviewer
acting through the UI control documented in FRONTEND-06/§11) may resolve
`APPROVED`. `AGENT-06` may read this state but cannot write `APPROVED`.

### F. Paid Media Plan
```
DRAFT → PLANNED → PENDING_AUTHORIZATION → AUTHORIZED
AUTHORIZED → EXECUTION_REPORTED → COMPLETED
DRAFT | PLANNED | PENDING_AUTHORIZATION → CANCELLED
```
`AUTHORIZED` means a human has approved the plan for real-world spend —
it does **not** mean spend happened. `EXECUTION_REPORTED` is a manual,
advisory confirmation (MVP has no ad-platform adapter); this preserves
`PAID PLANNING ≠ PAID EXECUTION` exactly.

### G. Tracking Readiness
```
NOT_DEFINED → REQUIREMENTS_DEFINED → CONFIGURATION_PENDING
CONFIGURATION_PENDING → CONFIGURED → VERIFICATION_PENDING → CERTIFIED
CONFIGURATION_PENDING | VERIFICATION_PENDING → FAILED_VERIFICATION → CONFIGURATION_PENDING
```
`CERTIFIED` describes only that tracking is technically believed correct.
It has no edge into any Measurement state — Measurement Cycles reference
Tracking Status for context, never derive from it.

### H. Measurement Cycle (one per reporting period + channel, mirrors today's Metrics tab)
```
OPEN → METRICS_RECORDED → OBSERVATIONS_COMPUTED → SIGNALS_DETECTED
SIGNALS_DETECTED → ANALYSIS_COMPLETED → CLOSED
```
Re-opening a `CLOSED` cycle is not a transition — it creates a new
Metric Observation batch and a new derived Analysis Result; historical
Analysis Results are immutable (append-only, see ER model).

### I. Learning Lifecycle
```
CANDIDATE_IDENTIFIED → PROVISIONAL → VALIDATION_PENDING
VALIDATION_PENDING → VALIDATED
VALIDATION_PENDING → REJECTED
VALIDATION_PENDING → INSUFFICIENT_EVIDENCE → VALIDATION_PENDING   (more data arrives)
VALIDATED → (may produce) STRATEGIC_RECOMMENDATION_CANDIDATE → ACCEPTED | REJECTED
```
An `ACCEPTED` Strategic Recommendation Candidate is what may seed a new
Campaign Version — it never mutates strategy directly; a human/AGENT-00
decision creates the new version.

## 3. Async Orchestration Model (logical only — no queue/worker chosen)

```
User submits brief
  → Campaign(DRAFT→SUBMITTED)
  → Orchestration Run(CREATED)
  → Orchestration Run(RUNNING)
  → AGENT-00 Agent Run: decide next specialist  → Handoff created
  → Specialist Agent Run: QUEUED→RUNNING→RETURNED
  → Return recorded
  → AGENT-00 Gate Decision on the Return: ACCEPTED | REVISION_REQUESTED | ESCALATED
  → (loop to next Handoff, or ESCALATED → Human Decision Request → Orchestration Run(AWAITING_HUMAN_DECISION))
  → ... repeats until AGENT-00 declares the run COMPLETED or a Stop Condition fires
```

**Interfaces required, not implementations:**
- `OrchestrationRunner` — a logical interface with `start(campaign_id)`,
  `resume(run_id, human_response)`, `cancel(run_id)`. Whether it is
  fulfilled synchronously-in-request, via a task queue, or via a
  future workflow engine is an implementation decision for BACKEND-06+,
  deliberately deferred.
- `AgentExecutor` — a logical interface, one implementation per Agent
  Definition eventually, abstracting away the actual AI provider call
  (OpenRouter/OpenAI or otherwise) so `agents` never imports a
  provider SDK directly. Not implemented in this stage.
- `ProgressProjector` — maps internal Orchestration Run + Agent Run
  state to the **public progress phrases already shown in the frontend**
  (`components/campaigns/campaign-progress.tsx`):

| Internal signal | Public phrase (already in frontend) |
|---|---|
| Orchestration Run `CREATED`/`RUNNING`, no Agent Run yet | Entendiendo tu idea |
| AGENT-01 Agent Run `RUNNING`/`RETURNED` | Analizando mercado |
| AGENT-02 Agent Run `RUNNING`/`RETURNED` | Identificando audiencia |
| AGENT-03 Agent Run `RUNNING`/`RETURNED` | Diseñando estrategia |
| AGENT-04 Agent Run `RUNNING`/`RETURNED` | Planificando contenido |
| AGENT-05 Agent Run `RUNNING`/`RETURNED` (copy/script) | Preparando contenido |
| AGENT-05 Agent Run `RUNNING`/`RETURNED` (creative brief/asset) | Preparando creatividades |
| AGENT-09/AGENT-10 Agent Run active | Preparando medición |
| Orchestration Run `COMPLETED` | (progress bar completes — matches existing "Tu campaña está lista para configurar") |

This mapping is exactly why `agents` internal identifiers must never
reach the frontend: the `ProgressProjector` is the only thing allowed to
translate `AGENT-0N` → a business phrase, and it lives entirely
server-side. The public API a client polls (or subscribes to) returns
only the right-hand column plus a percentage, never the agent id, unless
an explicit "advanced audit view" endpoint is called (§5 of the request;
see the API map doc, `/api/v1/campaigns/{id}/runs/{run_id}/audit`).

## 4. Human-in-the-Loop Model

Entities: **Human Decision Request** (question, options, requested_by =
AGENT-00 or a Gate, campaign_id, run_id, status), **Human Decision
Option** (structured choices, not free text, where the decision is
consequential — e.g. budget tiers, positioning A/B), **Human Decision
Response** (chosen option or free text, responded_by user_id, responded_at).

States: `OPEN → RESOLVED | EXPIRED | CANCELLED`. `RESOLVED` requires
exactly one Human Decision Response. `EXPIRED` is a time-boxed fallback
(policy TBD in a later stage) so a run is never stuck forever without an
operator being alerted.

**Pause/resume behavior:** an `OPEN` Human Decision Request forces
`Orchestration Run → AWAITING_HUMAN_DECISION` and (read-only mirror)
`Campaign → AWAITING_HUMAN_INPUT`. `resume()` is only callable with a
`RESOLVED` request as input, and only advances the *specific* Handoff
that raised the question — resolving one request never authorizes any
other pending or future decision (`USER RESPONSE ≠ AUTOMATIC AUTHORITY
FOR UNRELATED ACTIONS`, no broad consent object exists in the model at
all: there is no "approve everything" entity).

## 5. Multi-Tenancy

```
User → Membership → Organization → Workspace → (Campaign | Settings | Integration | Subscription)
```
- **Every** tenant-owned table carries a mandatory, non-nullable
  `workspace_id` foreign key (Organization is one level above Workspace
  to leave room for a future "agency manages multiple client
  workspaces" model, matching the sidebar's existing "Espacio de
  trabajo" switcher UI affordance, which is currently inert).
- **Authorization pattern:** every read/write handler resolves
  `current_user → memberships → allowed workspace_ids` first, then
  filters/validates the target resource's `workspace_id` against that
  set, in every module, unconditionally — never trust a `workspace_id`
  passed in the request body/query alone.
- **Cross-tenant access prevention:** because every domain FK chains
  back to `workspace_id`, a single `WHERE workspace_id = :current`
  predicate (applied at the repository layer, not ad-hoc per query) is
  sufficient; Postgres Row-Level Security is **recommended for a later
  hardening phase** (not implemented now) as defense-in-depth once
  connection-level tenant context exists.
- **Public ID safety:** prefixed public IDs (see domain-model doc §2)
  are unique per entity type but are *not* treated as a security
  boundary — knowing a `CMP-...` id must never be sufficient to read
  it; the workspace-scoping check above is mandatory regardless of how
  unguessable the ID is.
- **Should `workspace_id` be mandatory everywhere?** Yes, on every
  table under Campaign/Orchestration/Research/Strategy/Planning/
  Content/Assets/Distribution/PaidMedia/Tracking/Measurement/Learning/
  Integrations/Billing. The only tables without it are the two above
  Workspace in the tenancy chain (`User`, `Organization`,
  `Membership`) and the global, tenant-independent **Agent Definition**
  and **Integration Definition** registries (catalog data, not
  tenant data).

## 6. Security Architecture (principles only — no implementation)

| Concern | Direction (not implementation) |
|---|---|
| Authentication boundary | A single `auth` module issues/validates sessions; no other module ever checks a password or token directly. |
| Password handling | Hash with a modern slow hash (Argon2id or bcrypt) with per-user salt; never log or return password material; reset flow is token-based, single-use, short-lived. |
| Session/token strategy | Evaluate short-lived access token + refresh token (JWT or opaque, TBD in BACKEND-04) vs. server-side session; either way, tokens must carry `user_id` only — workspace context is resolved server-side per request, not trusted from the token's claims for authorization decisions beyond "which workspaces can this user act as." |
| RBAC | Role is workspace-scoped (a user can be Admin in one workspace, Member in another); permission checks are centralized in `workspaces`, not duplicated per module. |
| Tenant authorization | See §5 above — applied on every request, every module, no exceptions. |
| CSRF | Required if any cookie-based session is chosen; not required for a pure bearer-token API consumed by a same-origin Next.js server action, but must be decided explicitly in BACKEND-04, not assumed. |
| CORS | Allow-list the deployed frontend origin(s) only; no wildcard in production. |
| Rate limiting | Per-user and per-IP limits on auth endpoints and on campaign-creation/orchestration-start endpoints specifically (these are the expensive/abuse-prone ones). |
| Secret management | AI provider keys, DB credentials, etc. live in environment/secret-manager configuration, never in source, never in a table the API serializes back to a client. |
| Audit logging | Every state-changing command emits an Audit Event (see §18 in this doc) independent of business tables. |
| File upload safety | Validate MIME/type/size server-side before persisting; store the binary in object storage (vendor TBD), keep only a reference + metadata row in Postgres; never trust the client-declared content-type alone. |
| Integration credentials | Stored encrypted at rest, scoped to `workspace_id`, never returned by any read endpoint (write-only field pattern). |
| Encryption | TLS in transit everywhere; encryption-at-rest for the database and for any stored integration credentials specifically. |
| Least privilege | The API's DB role should not have superuser rights; background/reporting access (if any is added later) uses a separate read-only role. |
| Idempotency | See §22 in this doc. |
| Replay protection | Idempotency keys + short-lived tokens together cover most replay concerns; no separate mechanism designed yet. |
| Input validation | Every write DTO is validated at the API boundary (Pydantic models in the eventual FastAPI layer) before it reaches any domain service. |
| API versioning | `/api/v1/...` from day one (see API map doc) so a breaking v2 can be introduced later without an in-place break. |

**SECURITY ARCHITECTURE ≠ SECURITY IMPLEMENTATION**: nothing above has
been coded; these are constraints the eventual implementation phases
must satisfy.

## 7. Source-of-Truth Matrix

| State | Authoritative owner | Everyone else... |
|---|---|---|
| User identity/profile | `users` | reads via `users` service, never duplicates |
| Workspace membership/role | `workspaces` | `auth`/`campaigns` call it, never cache authority locally |
| Campaign business status | `campaigns` | derived/read-only mirror in dashboards |
| Orchestration/run state | `orchestration` (the Orchestration Run row *is* "Campaign Run" — see domain-model doc) | `campaigns` reads it, does not duplicate it |
| Agent result content | `agents` (Agent Run + Return) | `content`/`research`/`strategy` reference it by id, copy structured output into their own domain tables once **accepted** by a Gate (so a rejected/superseded Return never silently becomes campaign truth) |
| Content status | `content` | distribution/paid modules read, never set it |
| Approval status | `content` (Content Approval) | nothing else may set `Content Piece.status = APPROVED` |
| Paid state | `paid_media` | tracking/measurement read for context only |
| Tracking state | `tracking` | measurement reads for context only |
| Metric data | `measurement` | learning reads it, never edits it |
| Learning state | `learning` | strategy reads `VALIDATED`/accepted recommendations only |
| Subscription state | `billing` | workspaces checks entitlements via it, doesn't store its own copy |

## 8. Audit / Traceability

**Audit Event** — append-only, one row per fact ("Agent Run X returned",
"Gate Decision Y made", "Human responded to Z"), referencing the acting
identity (user or agent), the entity, and a timestamp. Never mutated,
never deleted (soft-delete elsewhere, but audit rows are truly
immutable).

**Traceability Link** — generic `(from_type, from_id, to_type, to_id,
relation)` edge table so the full chain `Campaign → Orchestration Run →
Agent Run → Handoff → Return → Gate Decision → Strategic Decision → Plan
Item → Content Brief → Content Piece → Approval →
Distribution/Tracking/Measurement/Learning` can be walked without every
intermediate table needing a hand-authored join path.

**Governance Decision** — a *separate* concept from Audit Event: it is
the record of an authority-bearing decision (a Gate Decision or a Human
Decision Response are both Governance Decisions). Audit Events reference
Governance Decisions; they do not replace them.

**Capability State / Authorization Record** — tracks *what a workspace
or plan is currently allowed to do* (e.g., "Paid Media authorized up to
$X", "Distribution authorized: false"), separate from the historical
Audit trail, so "what can happen now" and "what has happened" are never
the same query.

Invariant preserved: **AUDIT EVENT ≠ GOVERNANCE DECISION** — writing an
audit row never itself grants authority; only an explicit Gate Decision
or Human Decision Response (both first-class, authored, attributable
records) does.

## 9. Module Boundary Proposal (documentation only — not created)

```
apps/api/
  app/
    main.py
    core/            # settings, DB session, shared base classes
    auth/
    users/
    workspaces/
    campaigns/
    orchestration/
    agents/
    research/
    strategy/
    planning/
    content/
    assets/
    distribution/
    paid_media/
    tracking/
    measurement/
    learning/
    integrations/
    billing/
    audit/
```
Each module is expected to eventually expose: `models.py` (ORM),
`schemas.py` (Pydantic DTOs), `service.py` (business logic), `router.py`
(FastAPI routes) — **none of these files are created in this stage**.

## 10. Transaction Boundaries (identified, not implemented)

| Composite operation | Must be atomic because |
|---|---|
| Campaign creation + initial Orchestration Run creation | A Campaign must never exist without exactly one initiating run record |
| Agent Return + Gate Decision + resulting state transition | The Return must never be visible as "accepted" without the Gate Decision existing |
| Content Approval + Content Piece status transition + Audit Event | `APPROVED` must never be set without a corresponding governance record |
| Metric import/entry + provenance record | A metric row must never exist without recording where it came from |
| Human Decision Response + Orchestration Run resume | A run must never resume without a resolved decision backing it |

## 11. Idempotency & Retry Model (identified, not implemented)

| Command | Idempotency key candidate | Notes |
|---|---|---|
| Create campaign | client-generated request id | prevents duplicate campaigns from a double-submit |
| Submit metrics | `(workspace_id, campaign_id, period, channel)` natural key + client request id | matches today's one-record-per-period metrics form |
| Submit human decision | `(decision_request_id)` | a request can only be resolved once |
| Process agent return | `(agent_run_id)` | a Return is recorded once; re-delivery must no-op |
| Create content version | client-generated request id | avoids duplicate versions from retry |
| Record approval | `(content_version_id, reviewer_id)` for a given attempt | re-submitting the same approval call must not double-count |

Distinguish three retry layers that must not be conflated:
- **Command retry** — the same client-issued write is safely repeatable (idempotency key above).
- **Agent retry** — a `REVISION_REQUESTED` Gate Decision intentionally creates a *new* Agent Run; this is a business retry, not a transport-level one, and is always audited.
- **Transport retry** — HTTP-level retry (timeouts, 5xx) must not be assumed safe unless the underlying command is idempotent per the table above.

## 12. Backend MVP Scope

**IN MVP**
- Real login/authentication (single credential type, e.g. email+password)
- One workspace per user (multi-workspace switching UI exists in the
  frontend shell but can be stubbed to "one workspace" server-side)
- Campaign creation from a natural-language prompt (persisted Campaign + Campaign Brief)
- Orchestration Run record + internal agent sequencing (even if the
  *specialist logic itself* is a stub/placeholder response rather than
  a real AI call — the state machine and persistence are what MVP proves)
- Research / Audience / Strategy / Plan / Content outputs, persisted and readable
- Content detail retrieval (matching today's Content Detail UI)
- Manual metrics input (matching today's Metrics tab)
- A basic performance/analysis pipeline (Observation → Signal → Analysis, even if Analysis logic is simple/rule-based initially)
- Settings persistence (Profile, Workspace, AI Preferences, Notifications)

**DEFERRED**
- Real AI provider calls (OpenRouter/OpenAI) — agents run against stubs/fixtures until authorized
- External publishing (Distribution stays manual/advisory)
- Paid Media execution (stays manual/advisory)
- Tracking implementation (stays advisory)
- Subscription billing (stub `Subscription Plan`/`Subscription` rows only; no payment processor)
- Real object storage integration for assets (reference field can exist; nothing uploads yet)
- Row-Level Security, rate limiting infrastructure, background workers/queues

## 13. Proposed Implementation Phase Sequence (proposal — not approved by this document)

| Phase | Scope | Depends on |
|---|---|---|
| BACKEND-02 | Repository / FastAPI foundation (app skeleton, health check, settings, no business tables) | BACKEND-01 |
| BACKEND-03 | PostgreSQL + SQLAlchemy/Alembic foundation (connection, migration tooling, base model mixins) | BACKEND-02 |
| BACKEND-04 | Authentication + Users + Workspace + Membership/Role | BACKEND-03 |
| BACKEND-05 | Campaign persistence (Campaign, Campaign Brief, Campaign Version, status machine) | BACKEND-04 |
| BACKEND-06 | Orchestration runtime core (Orchestration Run, Handoff, Return, Gate Decision, Human Decision Request/Response, Stop Condition) — no real AI calls | BACKEND-05 |
| BACKEND-07 | Agent Registry + AGENT-00 sequencing logic against stub specialist responses | BACKEND-06 |
| BACKEND-08 | Specialist agents (AGENT-01…04) producing structured Research/Audience/Strategy/Plan against stubs, then real provider integration as a separate, explicitly authorized step | BACKEND-07 |
| BACKEND-09 | Content persistence (Content Brief, Content Piece, Content Version, Content Approval) + Content Detail read API | BACKEND-08 |
| BACKEND-10 | Metrics / Measurement / Learning pipeline + Settings persistence | BACKEND-09 |

Each phase should re-run the same governance/quality checklist this
document uses (no premature scope, explicit authorization per phase).
