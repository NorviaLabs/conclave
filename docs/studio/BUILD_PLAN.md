# Implementation investigation and recommended build plan

Reviewed Conclave main at 0457b8de535f7d1667d3d07043c977d52b1e14cf and Studio
Site source at 9c037d3349eac8d40101aadc5de62e47b94c90c0 on 5 October 2026.
This preserves the investigation from the conversation. Recheck current source
after merging newer core changes. The roadmap below is a recommendation.

Terminal progress: `studio/runtime/runs.py` implements version 1 run requests,
validated form values, pinned application snapshots and SQLite event records.
The adapter binds those values to AI and human steps. Offline checks cover
restart persistence, pending reviews, interruption, failures and single-start
execution. Durable review submissions now retain reviewer identity and typed
output. Waiting runs restore request-bound completed-step patches and budget
usage, preserving branch isolation and skipping completed work. Duplicate/stale
reviews are rejected; rejected or changes-requested decisions stop the run.
The local preview now bridges Worker APIs to a Python child process. It resolves
saved definition snapshots on the server, supports start/status/history/cancel,
records the local operator's reviews and restores selected runs after reload.
Explicit host configuration selects an existing backend, curated text-source
folder and optional bundled public-web tools. Offline Worker/Python/fake-CLI
checks exercise source reads, restart/review/resume and cancellation. They do
not establish live model quality or connected cloud accounts. Production
authentication, bounded source snapshots/claim support, controlled revision runs
and interrupted-action recovery remain pending; the gap table below records
the original investigation.

## Existing pieces to reuse

- conclave/runtime.py: bounded agent loops, nested delegation and typed events.
  fan_out already supports bounded concurrency for caller-owned work.
- conclave/providers.py: Codex CLI, OpenCode CLI and Ollama HTTP providers behind
  ModelProvider. Authentication stays with the external backend. Token usage
  can be unavailable; Completion does not carry dollar-cost measurements.
- conclave/registry.py and approval.py: per-agent tools, READ/WRITE/NETWORK/SHELL
  capabilities and approval callbacks. WRITE and unknown capabilities require
  authorization.
- conclave/budget.py: fresh mutable per-run accounting and limit enforcement.
- conclave/store.py: schema-driven in-memory snapshots, merges and versions.
- studio/runtime/application.py: definition validation, typed outputs, safe
  event projection, topological execution, ancestor-only inputs, all-parent joins
  and explicit human callbacks. Ready stages currently execute one at a time.
- studio/worker/index.js: revisioned application/member persistence.
- studio/shared/definition.js and web/plan.js: validated editable DAG and layout.

## Missing service layer

| Priority | Gap confirmed in source | Required implementation |
| --- | --- | --- |
| P0 | Studio exposes application/member APIs, but its sample runner uses canned sampleOutput values and a browser timer. | Python runtime host plus start/status/cancel APIs and a live event bridge. Run a pinned definition snapshot and preserve sample mode separately. |
| P0 | SharedStore, stage patches and agent messages live in memory. The adapter has no checkpoint restore API. | Persist run identity, definition revision/snapshot, inputs, stage attempts, state patches, sanitized events, artifacts and pending reviews. Resume without replaying completed external actions. |
| P0 | A missing human_handler returns waiting_for_human and ends the adapter. A changes_requested decision stops subsequent steps. | Persistent review requests, explicit reviewer identity/decision, notifications, resume and a controlled revision path. Human review never replaces destructive-tool approval. |
| P0 | Structured request inputs, integration selections, start_mode and budget_warnings have no complete execution binding. | One versioned contract shared by frontend and host validation. Bind values to each run; resolve trusted account/tool bindings and enforce scopes on the server. |
| P0 | Model/connector catalog entries are configuration choices. Bundled core tools mostly cover repositories, Git, GitHub and the web. | Configure real providers; implement account lifecycle, scoped adapters, token refresh/revocation and connection tests for the first selected services. Keep secrets outside definitions. |
| P1 | Bundled file reading is text-oriented; source_refs are bounded strings without a source registry. | Bounded upload/cloud ingestion, document extraction, source snapshots, resolvable locators and support checks for important claims. |
| P1 | Outputs are text or records with id/title/body/status/source_refs; extra custom record fields are not retained by the adapter. | Dedicated comparison/task/channel-draft contracts, real document/artifact creation, storage and download references. Do not fabricate file links. |
| P1 | Provider/host/invalid-output failures terminate the adapter; external writes lack a durable action ledger. | Retry/backoff, timeouts, cancellation and idempotency/action receipts. Retry an uncertain send/publish only after checking its recorded outcome. |
| P1 | Event/schedule start selections are planned; no trigger or delivery service exists. | Stored schedules, validated incoming events, deduplication, reviewer notifications and authorized outgoing delivery. |
| P1 | The UI reports usage as unavailable; warning thresholds are not observed by the adapter. | Actual per-run metrics, budget warning events and cost measurement only where supplied or reliably calculated. Offline evaluations using realistic fixtures plus later live checks. |
| P2 | Goal suggestions and plan commands are bounded keyword/rule-based operations. DAG ready stages run serially. | Stronger structured goal-to-plan proposals, concurrent branch scheduling and explicit conditional-routing contracts where workflows need them. |
| P2 | The existing Site is owner-private; no team workspace/release management layer exists. | Team permissions, shared reviews, isolated account access, versioned catalog/release management and rollback when multiuser scope is requested. |

## Contract mismatch to resolve early

Current core RunConfig permits None for several unlimited limits; the Studio
validators require positive integers for run_config and the adapter compares
state length directly with max_tool_output_chars. Do not copy historical finite
defaults into core. Define Studio's explicit application policy and handle
supported unlimited values consistently, with tests for finite and unlimited
paths. Different allowed contracts must be intentional and documented.

## Recommended first slice: Research brief

1. Define the versioned run request and persist the exact application snapshot
   plus validated request-form values. Keep source packs separate from
   instructions; do not silently discard the captured form.
2. Run the existing application adapter behind a host with an externally
   authenticated existing provider and a small real read-only tool set.
   Choose provider configuration from available backends rather than making
   an API key or a specific vendor mandatory.
3. Persist step completion and sanitized events; connect live progress and
   results to Studio. Reloading the page must recover the same run.
4. Implement a durable human review and checkpoint restore. Demonstrate restart
   and resume with no repeated completed stages. Define where agent-internal
   recovery ends and stage-level retry begins.
5. Retrieve actual sources and save a usable final brief with source links.
   Record uncertainty and conflicting evidence; a string locator alone does
   not prove a claim.
6. Validate failures: missing connection, invalid output, source unavailable,
   budget exceeded, cancel, restart while waiting and explicit rejection/change
   requests. Establish live workflow quality separately from protocol checks.

After this slice, add ticket/policy/issue connections for support workflows;
then add reviewed content destinations with reliable action receipts. Expand
triggers, parallel execution and team sharing as complete workflows need them.

## Verification at handoff

The existing offline adapter-check script was run against the pinned core and
all 18 catalog definitions. It passed dependency ordering, sibling isolation,
joins, human inputs, safe event projection, typed output validation, budget
stops, missing tools and host failures. It used scripted providers and fake
registered tools. This is not a live provider/connector or output-quality check.
The full core pytest suite was not run in its required Python 3.14 environment.
The handoff's local preview verification is recorded in studio/README.md.

Keep core domain-neutral. Put Studio persistence, account bindings, workflow
schemas, service APIs and domain policies above the reusable runtime.
