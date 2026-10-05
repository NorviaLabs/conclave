# Historical Site README

Preserved from Site commit 9c037d3349eac8d40101aadc5de62e47b94c90c0.
Older catalog counts and sequential-plan descriptions below are stale. Use the
current studio/README.md and implementation for present behavior.

---

# Conclave Application Studio

A private application builder above the provider-agnostic Conclave runtime.
The default Build flow starts with purpose, role cards, plain-language access
and visual outputs, then an editable sequential plan. Technical definitions and integration exports live under
Advanced. Test runs use the current draft; Use runs use the saved application.
Both remain explicitly fictional samples until a live runtime is connected.
Application definitions own agents, sequential workflow stages, shared-state
collections, merge policies and view bindings. Conclave core remains unchanged.

## Boundaries

- Application definitions persist in the Site's D1 database with revision checks.
- Browser previews use deterministic sample output. They do not call models,
  evaluate agent instructions, execute tools or run Conclave.
- `runtime/application.py` executes exported definitions against an injected
  Conclave provider and tool registry. It yields safe runtime events and validated
  state snapshots. No raw replies or private notes are projected into UI events.
- A live runtime host and HTTP/SSE bridge are not connected to this Site.
- The original investigation reference remains at `/investigation`.

## Build

`npm ci`, `npm run db:generate` after schema changes, then `npm run build`.
Generated schema-only migrations accompany the Worker artifact. Preserve applied
migration files. Models and tool implementation credentials belong on the runtime
host, outside application definitions and browser code.

## Adapter usage

Download `application.json` and `conclave_application.py` from Build → Advanced →
Runtime integration. Install the current Conclave project on the runtime host. Supply a provider,
a registered `ToolRegistry`, a task and (only if needed) an explicit approver.
Consume `run_application(definition, provider, tools, task)` as an async event
stream. Sequential stages reserve the shared delegation budget. Invalid output,
exhausted budgets and oversized state prompts stop without committing bad state.

## Catalogue and visual plans

The catalogue contains 12 application templates, every default team member,
23 suggested tool references in seven categories, seven role presets and six
semantic output types. AI and human members are distinct execution kinds.
Automation is configured through host tools, rather than a simulated AI member.
Existing saved v1 definitions remain valid without the optional `kind` and `role`
metadata. New starter templates do not overwrite saved applications.

Build → Plan supports adding AI work and human checkpoints, editing assignments
and instructions, duplication, removal and drag/drop ordering. Keyboard Up/Down
on the handles and Earlier/Later buttons provide equivalent ordering controls.
The visual order is the exported sequential workflow; branching and parallel
execution are not represented. Task outputs can use status boards as well as
cards and tables. Artifact outputs contain references to host-supplied files.

For live human steps, supply `human_handler=collect_human_input`. It receives the
checkpoint request and returns `{"text": "..."}` or `{"records": [...]}` using the
same typed output contract as AI steps. A missing handler returns
`waiting_for_human` with the current snapshot; a host needs its own persistence
and continuation bridge. The adapter does not persist or resume that snapshot.
The callback must collect explicit person input. Rejected decisions and requests
for changes stop subsequent steps. Human waiting does not consume model budgets.

Tool categories cover information, data, computation, creation, business apps,
communication and collaboration. The host supplies every implementation and
scope. Write/send references must be registered as destructive tools; the
separate Conclave `approver` still controls each external action. Human checkpoint
approval never grants tool approval. The Site's sample runs execute no tools.

## Workbench

Saved applications open on compact plan cards. The resource palette adds work
steps, current members, role presets, saved member templates and tool references.
Dropping a tool on a step configures its member; the inspector explicitly states
when that member is shared across steps. Make a copy for this step isolates edits.
Member templates are durable D1 snapshots, reused as independent application
copies rather than silently synchronized references.

The guided Plan assistant supports named add, move and tool-assignment commands.
It creates a validated proposal against the exact current draft. Apply rejects
stale proposals; canvas edits and commands share the same definition. No model
calls or open-ended natural-language interpretation are simulated.

Definition import accepts bounded, validated v1 JSON and creates a new unsaved
draft after review. Export/import retains optional metadata and plan order.
Import never overwrites the selected application's saved definition.

Sample execution uses the same ordered plan and shows step-scoped activity,
immutable input snapshots and output collection snapshots. AI steps remain
fictional; explicit human input is recorded. There is no live event bridge yet.

When browser WebMCP is available, `propose_application_definition` lets a connected
assistant submit a complete validated proposal for review. It never modifies the
draft until Apply. The existing explicit draft-staging tool remains available.

### Request forms and operating controls
Optional version-1 `inputs` define up to 12 text, textarea, number or date fields with stable keys and required settings. Studio validates and captures values in an immutable sample request snapshot. Runtime hosts must independently validate and supply these values to the adapter task; Studio does not execute them. Optional `budget_warnings` declares thresholds below matching `run_budget` hard stops. Warning observation requires a host connection. Owner-only workspace access is separate from requested agent capabilities. Runtime insights display unreported usage explicitly; no evaluation engine or release gates are connected.

### Catalog composition
New application opens the catalog of 12 sample applications, seven prebuilt role presets and 23 tool references. Configure a starter or a custom application, select additional roles and assign tools per AI member. Roles add editable steps; human approvers add explicit decision checkpoints. Catalog tools can also be assigned to existing applications. Definitions are copied before modification and saved through the existing revisioned application API; catalog entries remain unchanged. Tool availability labels describe runtime requirements, not connected integrations.

### Nontechnical builder and canvas
Untouched legacy catalog seed rows stay preserved but are excluded from Your applications; edited starter rows (revision > 1) remain visible. Show three personal applications initially and offer Show all, always retaining the selected personal application. The work canvas uses the real ordered plan with connected cards, zoom, pointer panning and existing accessible drag/reorder actions. List mode provides the same editable plan. Canvas positioning does not imply branching or parallel execution. Developer file actions, field keys, raw results and integration settings are folded behind advanced disclosures.

### Goal-first creation
New application asks for a goal, matches it to catalog examples using bounded keyword rules, and preselects ready-made roles, per-member tools and result types. Suggestions are explicitly catalog-based, not an AI response. The user can revise every selection before creating an independent validated plan on the canvas. Existing template setup also offers Suggest options from this goal. Unknown goals fall back to a general research team with a visible explanation. Changes to the goal regenerate suggestions only on explicit request.

### Editable acyclic flow canvas
Optional version-1 `connections` is an explicit array of `{from,to}` step dependencies; an empty array means independent roots. Omitting it preserves legacy sequential behavior. Optional `canvas.positions` stores each step’s `{x,y}` layout independently of execution. Move cards by their handles or arrow keys; connect using Connect/Connect here or the Runs after checkboxes, which also remove connections. Loops, self-links, duplicates and dangling references are rejected before a draft mutation or save. Tidy layout arranges branches by dependency depth. Removing a node removes its incident connections and position; its former children can become independent roots. Duplicates retain incoming dependencies, without silently connecting to downstream steps.

The demo and downloadable Python adapter execute a deterministic topological order, one ready step at a time. A step sees only outputs from its transitive prerequisites, reconstructed with each output’s merge policy. Join steps wait for every incoming prerequisite, including human review. No concurrent execution, conditional routing, automatic retries or live provider bridge is claimed. Shared final result collections still aggregate stage outputs according to their explicit merge rules.
