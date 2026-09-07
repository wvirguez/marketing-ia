# BACKEND-01 — Domain Model, ID Strategy, ER Model

Status: architecture/documentation only. No SQL DDL, no migrations, no
ORM code. See `BACKEND-01-ARCHITECTURE.md` for bounded contexts and
state machines this catalog implements.

## 1. Entity Catalog

Each entity lists: purpose, tenant ownership, key relationships,
mutability, versioning need, soft-delete need. "Tenant-owned" = carries
a mandatory `workspace_id`. "Global" = shared reference data, no
`workspace_id`.

### Identity / Tenancy

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| User | A person who can authenticate | Global | has many Memberships | Yes (profile) | No | Yes (account closure) |
| Organization | Top-level tenant owner (an agency or business) | Global (is the tenant root) | has many Workspaces | Yes | No | Yes |
| Workspace | The operative tenant unit (matches sidebar "Espacio de trabajo") | Belongs to Organization | has many Memberships, Campaigns, Settings | Yes | No | Yes |
| Membership | User↔Workspace with a Role | Tenant (Workspace) | User, Workspace, Role | Yes (role change) | No | Yes (removal) |
| Role | Named permission set, workspace-scoped | Tenant (Workspace) or Global (system defaults: Admin/Member) | referenced by Membership | Rarely | No | No |
| User Preferences | Per-user cross-workspace UI prefs (locale, timezone default) | Belongs to User | User | Yes | No | No |

### Campaign

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Campaign | The business unit the user is running | Workspace | has Brief, Versions, one Orchestration Run per cycle | Yes (status/phase) | No (its child Campaign Version is) | Yes (archive) |
| Campaign Brief | The initial natural-language request + captured context fields (product, price, audience, budget, channel — matches `CampaignContextFields`) | Workspace (via Campaign) | belongs to Campaign | Immutable once submitted | N/A (superseded by a new Brief on re-run, not edited) | No |
| Campaign Status | *(modeled as an enum field on Campaign, not a table — see state machine A)* | — | — | — | — | — |
| Campaign Phase | *(modeled as a derived value: "which pipeline stage is active," computed from the latest Agent Run's Agent Definition; not a stored table — avoids a second source of truth for the same fact)* | — | — | — | — | — |
| Campaign Run | **Same table as Orchestration Run** (see §2 note below) — the Campaign domain reads it via a query interface, does not own a duplicate row | Workspace | — | — | — | — |
| Campaign Version | An immutable snapshot of brief+strategy+plan at a point in time, created whenever a Strategic Recommendation Candidate is accepted and the campaign iterates | Workspace | belongs to Campaign, references the Strategy/Plan it snapshots | Immutable | Is itself the version | No (append-only) |

### Orchestration

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Orchestration Run | One AOL-00 execution cycle for a Campaign (this *is* "Campaign Run") | Workspace | belongs to Campaign; has many Agent Runs, Handoffs, Human Decision Requests | Yes (status) | No | No |
| Agent Definition | Registry entry for AGENT-00…10 / AOL-00 (capabilities, version, prompt/config reference — never business data) | Global | referenced by Agent Run | Rarely (new versions) | Yes (definition version) | No |
| Agent Run | One execution attempt of one Agent Definition within an Orchestration Run | Workspace (via Run) | belongs to Orchestration Run, Agent Definition; produces a Return | Append-only after `RETURNED` | Each attempt is its own row (no in-place retry) | No |
| Handoff | The record of AGENT-00 (or a Gate) delegating to a specialist | Workspace (via Run) | links Orchestration Run → Agent Run | Immutable | No | No |
| Return | The structured output an Agent Run reports back | Workspace (via Run) | belongs to Agent Run | Immutable | Yes (a `REVISION_REQUESTED` cycle produces a new Return, old one kept) | No |
| Gate Decision | AGENT-00's (or policy's) accept/revise/escalate ruling on a Return | Workspace (via Run) | belongs to Return | Immutable | No | No |
| Strategic Decision | A higher-order decision affecting campaign direction (may originate from a Gate Decision or an accepted Learning recommendation) | Workspace | references Campaign, optionally a Learning entity | Immutable | No | No |
| Dependency | Declares that Handoff/Agent Run B requires Return A first | Workspace (via Run) | Agent Run ↔ Agent Run | Immutable | No | No |
| Stop Condition | A rule that halts a run (budget/time/error-rate ceiling) | Workspace or Global (policy) | referenced by Orchestration Run | Rarely | No | No |
| Human Decision Request | A question raised to the user (see architecture doc §4) | Workspace | belongs to Orchestration Run | Yes (status) | No | No |
| Human Decision Response | The user's answer | Workspace | belongs to Human Decision Request | Immutable once submitted | No | No |

### Research / Strategy

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Research Report | AGENT-01's structured market findings | Workspace | belongs to Campaign, produced by an Agent Run/Return | Immutable once accepted | Yes (new Return → new Report on revision) | No |
| Source Reference | A citation/source backing a Research Report claim | Workspace (via Report) | belongs to Research Report | Immutable | No | No |
| VOC Evidence | Voice-of-customer data point (AGENT-02) | Workspace | belongs to Campaign / Audience Profile | Immutable | No | No |
| Audience Profile | AGENT-02's structured audience definition | Workspace | belongs to Campaign | Immutable once accepted | Yes | No |
| Strategy | AGENT-03's structured strategy (objective, message, funnel) | Workspace | belongs to Campaign | Immutable once accepted | Yes | No |
| Positioning | The specific positioning statement within a Strategy | Workspace (via Strategy) | belongs to Strategy | Immutable | Follows Strategy version | No |
| Hypothesis | A testable assumption backing the Strategy | Workspace | belongs to Strategy | Yes (status: open/confirmed/refuted) | No | No |
| Experiment | A structured test of a Hypothesis (may later connect to Paid/Content) | Workspace | belongs to Hypothesis, optionally references Content Piece / Paid Media Plan | Yes (status) | No | No |

### Planning / Content

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Content Plan | AGENT-04's structured calendar/plan for a Campaign | Workspace | belongs to Campaign | Immutable once accepted | Yes | No |
| Plan Item | One planned piece within a Content Plan (format, objective, target date/sequence — **replaces the frontend's demo-only `week: 1\|2`**, see API-map doc §Frontend Compatibility) | Workspace (via Plan) | belongs to Content Plan; may produce a Content Brief | Yes (until briefed) | No | No |
| Content Brief | The creative brief AGENT-04/03 hands to AGENT-05 | Workspace | belongs to Plan Item | Immutable once handed off | No | No |
| Content Piece | The tracked unit of content across its lifecycle (state machine D) | Workspace | belongs to Content Brief; has many Content Versions | Yes (status) | No (its child Version is) | Yes (archive) |
| Content Version | One produced draft of a Content Piece's actual copy/script/structure | Workspace (via Piece) | belongs to Content Piece; may reference Assets | Immutable | Is itself the version | No (append-only) |
| Content Approval | One approval attempt against a Content Version (state machine E) | Workspace (via Version) | belongs to Content Version | Immutable once resolved | No | No |
| Content Revision Request | A specific requested change (from a Content Approval `CHANGES_REQUESTED`, or a manual UI action) | Workspace | belongs to Content Approval | Immutable | No | No |
| Creative Brief | The visual/creative direction for Assets tied to a Content Piece | Workspace | belongs to Content Brief or Content Piece | Immutable once handed off | No | No |
| Asset | A creative artifact (image/video/file) — see Content/Asset model doc | Workspace | belongs to Creative Brief or Content Version | Yes (metadata) | Yes (Asset Version) | Yes |

### Distribution / Paid / Tracking

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Distribution Plan | AGENT-06's structured plan for where/when content goes out | Workspace | belongs to Campaign / Content Piece | Yes (status) | No | No |
| Distribution Readiness | A computed/asserted checklist (approval done, assets done, tracking ready) gating Distribution Plan | Workspace | belongs to Distribution Plan | Yes (recomputed) | No | No |
| Paid Media Plan | AGENT-09's structured plan (state machine F) | Workspace | belongs to Campaign | Yes (status) | Yes (revisions) | No |
| Paid Scope | Budget/target/channel scope within a Paid Media Plan | Workspace (via Plan) | belongs to Paid Media Plan | Yes (until authorized) | No | No |
| Tracking Plan | AGENT-10's structured plan (state machine G) | Workspace | belongs to Campaign | Yes (status) | No | No |
| Tracking Requirement | One specific event/pixel/UTM requirement within a Tracking Plan | Workspace (via Plan) | belongs to Tracking Plan | Yes (status) | No | No |
| Tracking Status | Rollup status of a Tracking Plan's requirements | Workspace (via Plan) | belongs to Tracking Plan | Yes (recomputed) | No | No |

### Measurement / Learning

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Metric Entry (Manual/Imported) | Raw metric values for a period+channel (matches today's Metrics form) | Workspace | belongs to Campaign; records provenance (manual/imported/platform) | Immutable once recorded (corrections = new entry, see below) | No | No |
| Performance Observation | A normalized fact derived from one or more Metric Entries (e.g., CTR for a period) | Workspace | belongs to Campaign; references source Metric Entries | Immutable | No | No |
| Performance Snapshot | A point-in-time rollup of Observations for reporting | Workspace | references Observations | Immutable | No | No |
| Performance Signal | A detected pattern/threshold breach across Observations | Workspace | references Observations | Immutable | No | No |
| Analysis Result | AGENT-07's structured interpretation of Signals | Workspace | references Signals | Immutable | No | No |
| Learning Candidate | AGENT-08's proposed learning from an Analysis Result | Workspace | belongs to Analysis Result | Yes (status, state machine I) | No | No |
| Provisional Learning | A Learning Candidate with initial supporting evidence | Workspace (via Candidate) | is a status of Learning Candidate, not a separate table (see note) | — | — | — |
| Validated Learning | A Learning Candidate confirmed with sufficient evidence | Workspace (via Candidate) | is a status of Learning Candidate | — | — | — |
| Strategic Recommendation Candidate | A proposed strategy change from a Validated Learning | Workspace | belongs to Learning Candidate; may seed a Campaign Version | Yes (accepted/rejected) | No | No |

*Note:* Provisional/Validated Learning are **statuses of the same
Learning Candidate row**, not separate tables — this satisfies "do not
merge these into one insight table" (they remain distinct, addressable
*states* with different authority meaning) while avoiding needless
table-per-state proliferation for what is fundamentally one entity's
lifecycle. Metric Entry, Performance Observation, Performance Signal,
and Analysis Result **are** four separate tables, because each is
produced by a different actor at a different time from different
inputs and must remain independently queryable/auditable — this is the
strict separation the request calls out explicitly.

### Settings / Integrations

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Integration Definition | Catalog of supported providers (Meta, Google Ads, TikTok, etc. — matches Settings → Integraciones cards) | Global | referenced by Workspace Integration | Rarely | No | No |
| Workspace Integration | A workspace's connection state to one Integration Definition (today: always "No conectado") | Workspace | belongs to Workspace, Integration Definition | Yes (status, credentials ref) | No | Yes |
| Notification Preference | Per-user, per-workspace toggle set (matches Settings → Notificaciones) | Workspace + User | belongs to User within Workspace | Yes | No | No |
| AI Preference | Per-workspace tone/depth/creativity/approval settings (matches Settings → Preferencias de IA) | Workspace | belongs to Workspace | Yes | No | No |
| Subscription Plan | Catalog of plans (Starter/Professional/Agency — matches Settings → Plan y facturación) | Global | referenced by Subscription | Rarely | No | No |
| Subscription | A workspace's active plan/status | Workspace | belongs to Workspace, Subscription Plan | Yes | No | No |
| Billing Customer Reference | Opaque reference to an external billing-provider customer id (no processor selected yet) | Workspace | belongs to Workspace | Yes | No | No |

### Audit / Governance

| Entity | Purpose | Tenant | Relationships | Mutable? | Versioned? | Soft-delete? |
|---|---|---|---|---|---|---|
| Audit Event | Append-only fact log | Workspace (nullable for system-level events) | polymorphic reference to any entity | Immutable | No | No (never deleted) |
| Traceability Link | Generic edge for chain reconstruction | Workspace | polymorphic `(from, to, relation)` | Immutable | No | No |
| Governance Decision | Authority-bearing decision record (wraps Gate Decision / Human Decision Response) | Workspace | references the underlying decision | Immutable | No | No |
| Capability State | "What is currently allowed" snapshot (e.g., Paid authorized amount) | Workspace | references Campaign/Plan | Yes (current state) | No | No |
| Authorization Record | The specific act that changed a Capability State | Workspace | belongs to Capability State | Immutable | No | No |

## 2. Note on "Campaign Run" vs. "Orchestration Run"

The request lists both under different sections (§5 CAMPAIGN and §5
ORCHESTRATION). To avoid **multiple competing sources of truth**
(explicitly forbidden in §17 of the request), this architecture treats
them as **one physical table, owned by the `orchestration` module**,
with `campaigns` granted read access through a query interface
(`get_current_run(campaign_id)`), never a duplicate write path. "Campaign
Run" is the *name* the Campaign bounded context uses when talking about
it; "Orchestration Run" is the *name* the Orchestration bounded context
uses for the same row. This is a naming/API-surface decision, not two
schemas.

## 3. ID Strategy

**Database primary key:** UUIDv7 on every table. UUIDv7 is
time-ordered, which keeps B-tree index locality reasonable under high
insert volume (unlike UUIDv4) while remaining globally unique without a
central sequence — appropriate for a system that will eventually need
to shard or replicate without renumbering.

**Public/business ID:** a separate, human-readable, prefixed string
column (indexed, unique), generated at creation time and never
reused. Format: `{PREFIX}-{12-character Crockford-base32 random
suffix}`, e.g. `CMP-8K2N4R7QXT3B`. This is what appears in URLs, API
responses, and support conversations. The raw UUID stays internal
(join key, never shown in the API).

Confirmed prefixes (extending the request's own examples to full
catalog coverage):

| Prefix | Entity |
|---|---|
| `USR` | User |
| `ORG` | Organization |
| `WKS` | Workspace |
| `MBR` | Membership |
| `CMP` | Campaign |
| `CBR` | Campaign Brief |
| `CMV` | Campaign Version |
| `RUN` | Orchestration Run (= "Campaign Run") |
| `AGRUN` | Agent Run |
| `HND` | Handoff |
| `RTN` | Return |
| `DEC` | Gate Decision / Strategic Decision |
| `DEP` | Dependency |
| `HDR` | Human Decision Request |
| `RPT` | Research Report |
| `AUD` | Audience Profile |
| `STR` | Strategy |
| `HYP` | Hypothesis |
| `EXP` | Experiment |
| `PLN` | Content Plan |
| `ITM` | Plan Item |
| `CBRF` | Content Brief (distinct from Campaign Brief `CBR`) |
| `CNT` | Content Piece |
| `CNV` | Content Version |
| `APR` | Content Approval |
| `AST` | Asset |
| `DST` | Distribution Plan |
| `PMP` | Paid Media Plan |
| `TRK` | Tracking Plan |
| `MET` | Metric Entry |
| `SIG` | Performance Signal |
| `ANL` | Analysis Result |
| `LRN` | Learning Candidate |
| `SRC` | Strategic Recommendation Candidate |
| `INT` | Workspace Integration |
| `SUB` | Subscription |
| `AUDT` | Audit Event |

**Governance traceability ID:** no separate ID scheme — Traceability
Link rows reference the public IDs above directly, so the audit chain
is human-readable end-to-end without a third identifier system.

**Collision/uniqueness:** UUIDv7 collision risk is cryptographically
negligible. The prefixed public ID's 12-character base32 suffix
(~60 bits of entropy) makes collision negligible per prefix at any
realistic table size; enforce with a `UNIQUE` constraint per prefix
column and a generate-and-retry loop (not implemented now, just the
expectation for BACKEND-03).

**Tenant-scoping implications:** public IDs are unique *globally*, not
per-workspace — this deliberately prevents a workspace from ever
colliding with or guessing another workspace's ID space, but as noted
in the architecture doc, uniqueness is not a substitute for the
mandatory `workspace_id` authorization check on every read/write.

## 4. Conceptual ER Model (high-level, no SQL)

```
Organization 1───* Workspace 1───* Membership *───1 User
                       │                              │
                       │                              └───1 User Preferences
                       │
                       ├───* Campaign 1───1 Campaign Brief
                       │        │
                       │        ├───* Campaign Version
                       │        │
                       │        └───1 Orchestration Run ("Campaign Run")
                       │                 │
                       │                 ├───* Agent Run ──1 Agent Definition
                       │                 │        └──1 Return ──1 Gate Decision
                       │                 ├───* Handoff
                       │                 ├───* Dependency
                       │                 ├───* Human Decision Request ──1 Human Decision Response
                       │                 └───* Strategic Decision
                       │
                       ├───1 Research Report ───* Source Reference
                       ├───1 Audience Profile ───* VOC Evidence
                       ├───1 Strategy ───1 Positioning
                       │        └───* Hypothesis ───* Experiment
                       │
                       ├───1 Content Plan ───* Plan Item ───1 Content Brief
                       │                                        └──1 Content Piece
                       │                                              ├──* Content Version ──* Asset
                       │                                              └──* Content Approval ──* Content Revision Request
                       │
                       ├───1 Distribution Plan ──1 Distribution Readiness
                       ├───1 Paid Media Plan ──* Paid Scope
                       ├───1 Tracking Plan ──* Tracking Requirement ──1 Tracking Status
                       │
                       ├───* Metric Entry ───* Performance Observation ───* Performance Signal
                       │                                                        └──1 Analysis Result ──* Learning Candidate ──* Strategic Recommendation Candidate
                       │
                       ├───* Workspace Integration ──1 Integration Definition
                       ├───* Notification Preference
                       ├───1 AI Preference
                       ├───1 Subscription ──1 Subscription Plan
                       │
                       └───* Audit Event / Traceability Link / Governance Decision / Capability State
```

## 5. Content & Asset Model

**Structured relational data** (queryable, filterable, drives UI lists/state):
- Content Piece (id, campaign_id, format, status, objective, funnel
  stage, cta, channel — this is exactly the shape already proven by the
  frontend's `ContentDetailData` meta fields)
- Content Version (id, content_piece_id, created_at, created_by (agent
  or user))
- Content Approval (status, reviewer, decided_at)
- Asset (id, content_version_id or creative_brief_id, kind, status)
- Asset Version (id, asset_id, storage_reference, created_at)

**JSON payload** (structured but format-specific, mirrors the frontend's
existing discriminated union in `types/content-detail.ts` almost
exactly):
- The *body* of a Content Version: `{ kind: "reel", hook, scenes: [...],
  caption, cta, hashtags }` / `{ kind: "carousel", slides: [...],
  finalCta }` / `{ kind: "story", slides: [...] }` — stored as a single
  JSONB column (`content_version.payload`) rather than one table per
  format, because the shape is genuinely heterogeneous per format and
  the frontend already treats it as a tagged union. Post/Email/Ad Copy
  would extend the same `kind` union with their own payload shape.
- Creative specification (visual direction, aspect ratio, brand
  constraints) similarly JSONB on Creative Brief.

**Object/file storage reference (vendor not selected)**:
- Generated image / uploaded asset binaries live outside Postgres;
  `Asset Version.storage_reference` is an opaque URI/key column plus
  `asset_version.metadata` (JSONB: dimensions, mime type, checksum,
  size). Today's frontend previews are pure CSS/gradient mockups with
  **no real asset binaries at all** — this part of the model is
  forward-looking with nothing to reconcile against existing frontend
  state (see Frontend Compatibility Review in the API-map doc).

## 6. Metrics / Analysis / Learning Model

Strict pipeline, four distinct tables, no merging:

```
Metric Entry (raw, provenance-tagged: manual | imported | platform | derived)
   → Performance Observation (normalized fact, e.g. "CTR = 2.3% for period P, channel C")
      → Performance Signal (a detected pattern: threshold breach, trend, anomaly)
         → Analysis Result (AGENT-07's structured interpretation of one or more Signals)
            → Learning Candidate (AGENT-08's proposal), status ∈ {CANDIDATE_IDENTIFIED, PROVISIONAL, VALIDATION_PENDING, VALIDATED, REJECTED, INSUFFICIENT_EVIDENCE}
               → Strategic Recommendation Candidate (only from a VALIDATED Learning Candidate)
```

Provenance is a field on Metric Entry (`source: manual | imported |
platform | derived`), not a separate table — it's an attribute of the
metric, not an independent lifecycle. "Derived" metrics (e.g., CTR
computed client-side today in `lib/campaign-metrics.ts`'s
`derivedMetrics()`) become **Performance Observations**, not Metric
Entries, once the backend owns the computation — this is the one place
today's frontend logic (a pure function over form state) should move
server-side so Observations are reproducible from stored Metric Entries
rather than recomputed ad hoc by each client.

## 7. Paid Media / Tracking Boundary

```
Paid Media Plan (DRAFT/PLANNED)  ≠  Paid Media Plan.AUTHORIZED  ≠  "execution happened"
```
Enforced by the state machine (architecture doc §2F): nothing in the
schema ever marks a Paid Media Plan as executed automatically — only a
manual `EXECUTION_REPORTED` transition, initiated by a human, exists.
There is no adapter table for an ad platform in this stage.

```
Tracking Requirement  ≠  Tracking configuration (a value/status on the Requirement)  ≠  Tracking Status.CERTIFIED  ≠  Measurement Cycle's result
```
`Tracking Status` never feeds into `Performance Observation`/`Analysis
Result` computation directly — a certified-tracking flag is contextual
metadata an analyst or AGENT-07 may *consider*, not a value the
measurement pipeline reads as input.
