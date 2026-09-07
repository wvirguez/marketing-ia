# BACKEND-01 — Frontend↔Backend Contract Map, API Resource Design, Agent Registry Surface, Frontend Compatibility Review

Status: architecture/documentation only. No routes, no endpoint code.
Resource groups below describe *purpose and shape*, not implementations.

## 1. Frontend → Backend Contract Map

| Frontend surface | Backend domain owner | Read model | Write action | Sync/Async | AuthZ | Tenant scope |
|---|---|---|---|---|---|---|
| `/login` | `auth` | — | `POST` credentials → session/token | Sync | Public (unauthenticated) | None yet (pre-tenant) |
| `/` (Dashboard) | Dashboard aggregation (a thin read-model composed from `campaigns` + `measurement`, not its own bounded context) | Recent campaigns, summary stats | none | Sync | Authenticated, workspace-scoped | Workspace |
| `/campaigns` | `campaigns` (query side) | Filterable/sortable campaign list | none (filters are query params) | Sync | Authenticated | Workspace |
| `/campaigns/new` | `campaigns` (command) → triggers `orchestration` | none (form only) | `POST` create Campaign + Campaign Brief → starts Orchestration Run | **Async** (creation ack is sync; orchestration progress is polled/streamed) | Authenticated | Workspace |
| `/campaigns/{id}` (Campaign Workspace) | `campaigns` aggregation, fanning out to `research`/`strategy`/`planning`/`content`/`paid_media`/`tracking`/`measurement` per tab | Campaign + latest Research/Audience/Strategy/Plan/Content/Paid/Tracking summaries | tab-specific (e.g. "Ver" navigations are reads only) | Sync (reads); orchestration updates arrive async | Authenticated | Workspace |
| `/campaigns/{id}/content/{content_id}` | `content` | Content Piece + current Content Version + Approval status | Copy actions are client-only (no write); Approve/Request changes are writes | Sync | Authenticated | Workspace |
| Metrics tab (within Campaign Workspace) | `measurement` | Metric Entries + latest Observations/Analysis for the campaign | `POST`/`PUT` Metric Entry | Sync (entry); analysis pipeline may run async | Authenticated | Workspace |
| `/settings` (Perfil, Espacio de trabajo, Preferencias de IA, Notificaciones, Plan y facturación) | `users` (Perfil), `workspaces` (Espacio de trabajo), `integrations` (AI Preference lives closer to `orchestration`'s consumer-facing config, but is stored as workspace settings — see note below), `billing` (Plan y facturación) | Current settings values | `PUT`/`PATCH` per section | Sync | Authenticated | Workspace (+ User for Perfil/Notificaciones) |
| `/settings` Integraciones tab | `integrations` | Integration Definitions + Workspace Integration connection state | `POST` connect (deferred — real OAuth not in MVP) | Sync (today's "Conectar" is a no-op message; real integration flow is async by nature: redirect + callback) | Authenticated | Workspace |

**Note on AI Preference ownership:** it is *configuration* consumed by
`orchestration`/`agents` at run time, but it is *owned* (stored, edited)
by `workspaces`/`settings` as workspace configuration data — the same
separation as the frontend already models (`lib/settings-demo-data.ts`
holds it independently from campaign data). `orchestration` reads it,
never writes it.

## 2. API Resource Groups (logical only — no route code)

| Resource group | Purpose | Methods | Read/Write | Async? | AuthZ boundary |
|---|---|---|---|---|---|
| `/api/v1/auth` | Login, logout, refresh, password reset | POST | Write (session) | Sync | Public |
| `/api/v1/users/me` | Current user profile + preferences | GET, PATCH | Read+Write | Sync | Authenticated (self only) |
| `/api/v1/organizations` | Organization CRUD (thin, mostly for future agency-of-agencies) | GET, POST | Read+Write | Sync | Authenticated, org-admin for write |
| `/api/v1/workspaces` | Workspace CRUD, membership management | GET, POST, PATCH | Read+Write | Sync | Authenticated; write requires workspace Admin role |
| `/api/v1/workspaces/{id}/settings` | Workspace profile, AI Preference, Notification Preference | GET, PATCH | Read+Write | Sync | Workspace member (read), Admin (write) |
| `/api/v1/campaigns` | Campaign list/create | GET, POST | Read+Write | POST returns a Campaign **and** kicks off an async Orchestration Run — response includes a run reference to poll | Workspace member |
| `/api/v1/campaigns/{campaign_id}` | Campaign detail/update (status transitions like pause/archive) | GET, PATCH | Read+Write | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/runs` | Orchestration Run list/detail for a campaign | GET | Read | Sync (reflects async state) | Workspace member |
| `/api/v1/campaigns/{campaign_id}/runs/{run_id}/progress` | **Business-language** progress projection (the phrases in the architecture doc's table) | GET (or a streaming/poll variant) | Read | Sync read of async state | Workspace member |
| `/api/v1/campaigns/{campaign_id}/runs/{run_id}/audit` | **Advanced audit view** exposing Agent Run / Handoff / Return / Gate Decision detail, including internal agent identifiers | GET | Read | Sync | Workspace member with an explicit "advanced/audit" permission — not shown by default, matching "internal agent identifiers unless an advanced audit view is explicitly requested" |
| `/api/v1/campaigns/{campaign_id}/decisions` | Pending/resolved Human Decision Requests for a campaign | GET, POST (respond) | Read+Write | Responding resumes an async run | Workspace member |
| `/api/v1/campaigns/{campaign_id}/research` | Research Report + Source References | GET | Read | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/audience` | Audience Profile + VOC Evidence | GET | Read | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/strategy` | Strategy, Positioning, Hypotheses, Experiments | GET | Read (Experiments may get a POST later) | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/plan` | Content Plan + Plan Items | GET | Read | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/content` | Content Piece list | GET | Read | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/content/{content_id}` | Content Piece detail + current Content Version | GET | Read | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/content/{content_id}/approvals` | Submit/read Content Approval decisions | GET, POST | Read+Write | Sync | Workspace member (role-gated: who may approve is a future refinement) |
| `/api/v1/campaigns/{campaign_id}/paid-media` | Paid Media Plan + Scope | GET, PATCH (authorize) | Read+Write | Sync | Workspace member; authorize action likely Admin-gated |
| `/api/v1/campaigns/{campaign_id}/tracking` | Tracking Plan + Requirements + Status | GET, PATCH | Read+Write | Sync | Workspace member |
| `/api/v1/campaigns/{campaign_id}/metrics` | Metric Entry submit/list | GET, POST, PUT | Read+Write | Sync (entry); triggers async analysis | Workspace member |
| `/api/v1/campaigns/{campaign_id}/analysis` | Performance Observations/Signals/Analysis Results | GET | Read | Sync (reflects possibly-async pipeline) | Workspace member |
| `/api/v1/campaigns/{campaign_id}/learning` | Learning Candidates + Strategic Recommendation Candidates | GET, PATCH (accept/reject recommendation) | Read+Write | Sync | Workspace member |
| `/api/v1/settings/integrations` | Integration Definitions (catalog) + Workspace Integration state | GET, POST (connect — deferred) | Read (+Write later) | Connect flow is inherently async (OAuth redirect/callback) once implemented | Workspace member (Admin to connect) |
| `/api/v1/settings/billing` | Subscription Plan catalog + current Subscription | GET, PATCH | Read+Write | Sync (stubbed; real billing provider is async webhook-driven later) | Workspace Admin |
| `/api/v1/dashboard` | Aggregated summary for the Dashboard page | GET | Read | Sync | Workspace member |

## 3. Agent Registry — API Surface Implications

- `Agent Definition` is exposed **only** through the advanced audit
  endpoint above, or not at all in v1's public surface. No endpoint
  under `/api/v1/campaigns/...` ever returns an `agent_id` or agent
  name in its default response shape — every default response uses the
  business-language fields already established by the frontend
  (`objective`, `status`, `funnelStage`, `cta`, etc.).
- `AGENT-00`'s role as final content approver is expressed purely
  through *who is allowed to call* `POST
  /campaigns/{id}/content/{content_id}/approvals` with an `APPROVED`
  outcome in the fully-automated future state — in the human-in-the-loop
  MVP, that authority is delegated to the UI's "Aprobar" action
  performed by a human reviewer, and the API must record *that it was a
  human, not AGENT-00, who approved* (an `approved_by: {type: "user"
  | "agent-00", id}` field), preserving the audit trail even while the
  automation isn't live yet.

## 4. Frontend Compatibility Review

Reviewed `types/`, `lib/*-demo-data.ts` against the domain model above.
No BLOCKER found — nothing requires an immediate frontend change. Three
naming/shape decisions should be made **before** wiring real API calls,
listed here for the record (not applied now, per "do not modify
frontend behavior unless absolutely necessary"):

| Frontend shape | Issue | Recommendation for the eventual API contract |
|---|---|---|
| `ContentItem.week: 1 \| 2` (`types/campaign-workspace.ts`) | A fixed 2-week demo scaffold, not a real scheduling concept | Backend `Plan Item` should carry a real `sequence`/`scheduled_date`, not a hardcoded week enum; the frontend's `week` field should be replaced when the real API is wired, not before |
| `CampaignListItem.status` / `ContentItem.format` / `AiPreferencesDraft.tone\|depth\|creativity` use **Spanish literal strings as the TypeScript union values** (`"En preparación"`, `"Carrusel"`, `"Profesional"`, ...) | If reused verbatim as the wire-format enum, the API contract would hard-bake Spanish text into the schema, blocking future localization and making status comparisons in code fragile | Define backend enums with **stable, English, machine-safe keys** (`IN_PREPARATION`, `CAROUSEL`, `PROFESSIONAL`, ...); the frontend maps key→localized label at render time, exactly the way it already maps `state: "done"\|"current"\|"pending"` to icons today — this is a natural extension of an existing pattern, not a new one |
| `MetricsDraft` (`lib/campaign-metrics.ts`) stores every numeric metric as a `string` (controlled-input form state) | Reusing this shape as the API request DTO would push string-typed numbers into the database | Define a proper typed request schema (numeric fields, ISO date strings) at the API boundary; the frontend's string-typed draft remains a UI-only concern and is serialized/parsed at submit time — no frontend change needed, just don't let the demo type leak into the DTO definition |

Everything else — `ContentDetailData`'s discriminated union
(`kind: "reel"\|"carousel"\|"story"`), the `ProfileDraft`/`WorkspaceDraft`
shapes, the `demo-pill`/status-tone conventions — maps cleanly onto the
domain model above and is a good starting point for the eventual
Pydantic response schemas.
