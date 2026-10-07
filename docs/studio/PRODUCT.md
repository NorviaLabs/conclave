# Conclave Studio product decisions

These decisions preserve the user's requests in the Studio conversation through
5 October 2026. They are constraints for continued development, not a transcript
or a promise that all runtime services have been built.

## Purpose and audience

Conclave is a provider-agnostic multi-agent runtime. Studio is an approachable
application builder and work surface above it. A person describes a goal,
adapts a suggested team and plan, supplies context, runs the work, inspects
results and contributes at human review steps.

The first three audiences are customer support leads, research/strategy
analysts and content marketing managers. Research supports the suitability of
some underlying tasks; the exact six workflows remain product hypotheses.
Keep the runtime reusable and domain-neutral.

## Accepted experience

1. Applications are personal, saved configurations. Catalog applications are
   independent starters copied into a new application. The sidebar should
   initially show only two or three user-created/customized applications.
2. Start creation with a goal. Preselect suitable roles, tools and output types;
   the person can inspect and change every suggestion before creating the plan.
3. Supply a catalog of sample applications, prebuilt roles, tools and result
   presets. Include all associated team members when explaining a starter.
   Reusable saved members are copied into applications without silent coupling.
4. Provide no-code visual planning: add, edit, copy, delete and assign steps;
   drag cards; edit connections; inspect a selected step. Support any DAG with
   multiple roots, branches and joins. Reject cycles and dangling references.
   The stored card layout must not determine execution dependencies. Arranging
   the canvas saves itself, without the Save button and without carrying
   unfinished edits along with it.
5. Offer accessible keyboard alternatives to canvas interactions: arrow keys
   move a step, ports and edges are real buttons, and reordering lives in the
   selected step's settings. A step is dragged and connected on the canvas
   itself, so it carries no drag handle and no Connect button; double-clicking
   it, or pressing Enter on it, opens its settings. Fit the whole graph by
   default, with zoom, full screen, Tidy layout and adding a step all on the
   canvas rather than in the page header above it. Avoid hiding essential work
   behind a complicated graph editor.
6. Human team members are distinct from AI members. They contribute explicit
   decisions or typed input and can approve, reject or request changes.
   Automated actions are implemented through host tools.
7. Use plain language. Put technical definitions, model configuration, state
   keys, raw output and developer integration settings behind advanced options.
8. Make the UI calmer through progressive disclosure, short labels, compact
   summaries, a selected-step inspector and one clear next action. Avoid walls
   of explanation, duplicated instructions and equally prominent controls.
9. Keep building, testing a draft and using a saved application on one screen.
   The canvas is the plan while you edit it and the run while it runs; one
   play/stop control starts and stops a run and reads Run again once a run is
   open, and the setup questions are what an application with no steps opens to.
   What a run needs — its request, its context and its source — is configured in
   a modal over that canvas rather than on a screen of its own. Every real run
   retains the definition version it used, and the request list names the version
   each one ran.
10. Show source-backed results, limitations and actual human review. Do not
    invent sources, files, usage, cost, performance gains or connected accounts.
11. Users select intended sources, destinations and allowed actions. Account
    setup, execution permissions and approval enforcement belong on the server.
    Draft review and per-action tool authorization remain separate.
12. Show a run the same way the plan is shown: one canvas with a node per step,
    its status, the tools each agent used and who it delegated to, with every
    exchange labelled by what actually crossed. Keep the work plan list as the
    fallback when a saved plan cannot be laid out, and read both from the saved
    run record rather than from a second account of the run. While a run is on
    screen the request form stays behind one button, and one quiet control
    returns to editing the plan. Clicking Run again opens that form prefilled
    with the request that ran, and starting it creates a new run rather than
    resuming the old one. A step whose recorded result came from an
    earlier version of the plan is marked as out of date rather than shown as
    current.

## Which model runs a member

Provider and model are chosen where the team is chosen: each AI member carries
the backend that runs it, in the Setup step's Team panel, and one step may
override its member's choice in the step settings. The host answers what this
machine can reach, so the picker offers only real backends and only the models a
backend reports; where a backend reports none, the model is a typed name and a
blank means that backend's own default. The plan canvas says what runs each
step and the run canvas says what actually ran it.

A choice is honoured or refused, never quietly replaced. A member or step that
names a backend this machine cannot run stops the run before it starts, naming
the step, the member and what the host has instead — the same rule the runtime
enforces per call.

## Requests

Requests are listed apart from the canvas, in the page header: one row per
request with when it started, its status, the definition version it ran, and the
request text. Opening a row shows that run on the canvas. The list is where the
distinction between versions lives, because every request runs a saved version
and none of them is a throwaway test.

## Six priority starters

| Audience | Starter ID and example | Complete team in the current starter | Result and constraints |
| --- | --- | --- | --- |
| Customer support lead | case-resolution: investigate a reported duplicate renewal charge | Case analyst, Policy checker, Support writer, Fact checker, Decision owner (human) | Verified case summary, policy check, safe reply draft and explicit review. Distinguish a settled charge from a pending authorization; do not promise a refund or send a reply automatically. |
| Customer support lead | escalation-preparation: prepare an engineering handoff for intermittent exports | Case analyst, Escalation coordinator, Policy checker, Decision owner (human) | Timeline, missing evidence, escalation eligibility, engineering handoff and lead review. Preserve unknown root cause and missing timezone/permission details; severity requires a person. |
| Research/strategy analyst | options-comparison: compare three knowledge-base products | Comparison analyst, Source researcher, Critical reviewer, Brief writer, Decision owner (human) | Mandatory requirements before weighted preferences, cited option evidence, comparison and recommendation. Record unknowns and disqualifications instead of inventing scores. |
| Research/strategy analyst | research-brief: assess approaches to reducing repeat support contacts | Comparison analyst, Source researcher, Fact checker, Brief writer, Decision owner (human) | Scoped question, independent source findings, claim checks and a reviewed brief. Distinguish observational evidence from causal evidence and preserve incompatible outcome definitions. |
| Content marketing manager | launch-content-production: draft a reviewed product launch article | Brief writer, Fact checker, Brand editor, Decision owner (human) | Writing brief, draft, independent fact/brand reviews, revision and editor approval. Use approved claims and caveats; reject an unsupported ticket-reduction claim. |
| Content marketing manager | content-repurposing: adapt an approved article for LinkedIn, email and FAQ | Fact checker, Channel writer, Brief writer, Decision owner (human) | Source boundaries, three channel drafts, cross-channel checks, package and approval. Preserve meaning; LinkedIn limit 3,000 characters, email limit 150 words plus subject, FAQ three Q&As. No automatic publishing. |

Each starter has complete fictional request inputs, sources, outputs, instructions,
review checks and branch/join connections in studio/shared/catalogue.js.
Replace fictional inputs with real, bounded source access for live runs.

## Catalog snapshot

At handoff the catalog contains 18 application types (six featured), 14 role
presets, 28 tool descriptors, six semantic output types, eight output presets
and 39 connector choices. These are configuration catalogs, not a statement
that every tool or connector has an implementation.

Role families include researcher, analyst, creator/writer, reviewer, verifier,
coordinator and human approver, plus specialized support and content roles.
Semantic output types are findings, tasks, datasets, documents, artifacts and
decisions. The current execution adapter represents these using generic records
or text; richer typed outputs and actual file artifacts require implementation.

## Connections by workflow

| Workflow | Main connection categories | Current suggested services |
| --- | --- | --- |
| Case resolution | AI, support case, policy; optional payment/account context | Model provider, Zendesk, Confluence; optional Stripe and HubSpot CRM |
| Escalation preparation | AI, support evidence, rules, engineering issue; optional notifications | Model provider, Zendesk, Confluence, Jira; optional Slack |
| Options comparison | AI, source documents, comparison spreadsheet; optional web research | Model provider, Google Drive/Docs, Google Sheets; optional search/page reader |
| Research brief | AI, source documents and final brief; optional web research | Model provider, Google Drive/Docs; optional search/page reader |
| Launch content | AI, approved facts and brand guide, website drafts; optional review notifications | Model provider, Google Drive/Docs, WordPress; optional Slack |
| Content repurposing | AI, approved source, email/social drafts; optional FAQ destination | Model provider, Google Drive/Docs, Mailchimp, Buffer; optional WordPress |

The catalog includes alternatives across CRM, support, knowledge, cloud files,
spreadsheets, issue tracking, notifications, email, web research, publishing,
marketing, payment reads and advanced custom APIs/webhooks/tool servers.
Implement a small set for a complete first workflow before widening coverage.

## Design references supplied by the user

### Postman Flows-inspired editor

The editor uses a light neutral canvas-first shell, compact cards, labeled
left/right dependency ports, a floating in-canvas Run/Add a step/Tidy layout
dock, and contextual step settings. Double-click or keyboard activation edits
a step. No PRIVATE label, Connect button, or card drag handle is restored.
See [research and scope](POSTMAN_FLOWS_RESEARCH.md) for the source evidence.

Connections remain dependencies, not typed data routing or Success/Fail ports.
Existing saved coordinates are preserved; horizontal arrangement is applied
only to new automatic positions or on explicit Tidy layout. Groups, minimap,
undo/redo, and deployment/sharing controls are not part of this pass.

Layout autosaves separately from content. A layout-only save increments
`layout_revision`, not the content `revision`; clean status reads “Layout
saved”. Unsaved content retains “Unsaved changes”; content saves read “Saved ·
version N”. Moving cards does not make recorded requests older.

- https://docs-platform.crewai.com/platform/en/features/crew-studio
- https://astropods.com

The requested inspiration was visual team/plan composition, reusable components,
goal-guided setup and an inspectable workbench. Use the accepted product behavior
and current code as the source of truth; this handoff does not assert that each
implemented pattern is an exact feature of either reference site.
