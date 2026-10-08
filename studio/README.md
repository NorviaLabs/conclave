# Conclave Studio

The current Studio source imported from the ChatGPT Site into the Conclave
repository. Start with ../STUDIO_HANDOFF.md for product context and the terminal
handoff. ../docs/studio/PRODUCT.md contains accepted UX decisions;
../docs/studio/BUILD_PLAN.md contains the implementation investigation.

## Start Studio with Conclave

From the repository root, one command serves the REST API and this Studio page
on one address:

```sh
uv run conclave serve
```

Open the address it prints. No Node.js, second port or proxy URL is involved.
Startup also reports the AI backend it found on this computer, so no provider
setting is needed to run an application.

For UI development, or to run the two processes separately, Node 24+ is needed:

```sh
uv sync
uv run conclave serve --provider codex --port 8080
```

In a second terminal, use Node 24 or newer:

```sh
cd studio
npm ci
CONCLAVE_API_URL=http://127.0.0.1:8080 npm run dev
```

Open http://127.0.0.1:8787. If using the working Node executable from this
checkout's session, replace `npm run dev` with
`/tmp/conclave-studio-node/node_modules/node/bin/node scripts/dev.mjs`.
Studio never launches Python, opens a database or configures a provider.
Its server serves static assets and forwards same-origin API requests over HTTP.
Stopping Studio leaves Conclave running.

The development server watches `web/`, `shared/`, and `worker/` and rebuilds
automatically. CSS updates in place; JavaScript, HTML, and other changes reload
the page (unsaved content drafts are lost on a page reload). Failed builds keep
the last working server loaded and report the error in the terminal. Restart
the dev server after changing its scripts or configuration. This is dev-only
live reload, not state-preserving JavaScript HMR; production builds are unchanged.

`uv run python dev.py --provider codex` starts both processes at once and stops
them together; select the Node executable with `--node /path/to/node` or
`STUDIO_NODE`.

## Model backends

A team member — or a single step — may name the backend and model that run it,
chosen in the Setup step's **Team** panel and overridden per step in the step
settings. The host reports which backends it can reach on `/api/runtime`
(`backends`), including the models Ollama reports; a backend that reports no
model list takes a typed model name, and "Backend default" leaves the choice to
the backend rather than inventing an ID. The canvases show what runs each step,
and the run canvas shows what actually ran it once the record says so.

Nothing is substituted silently: a member or step that names a backend this
machine cannot run is refused when the run starts, with the step, the member and
what the host has instead. A host configured with a single backend (a factory or
a hosted deployment) has no catalogue and refuses any named backend.

## Run canvas

The plan and the run are the same canvas: editing shows the plan, an open run
shows the same graph with each step's status. The header's Run control starts a
run and reads Run again once one is open; either way it opens one modal for the
request, its context and its source, so no separate run screen exists. Run again
is prefilled with the request that ran and creates a new run rather than
resuming the old one.

During a run, canvas cards show blue animated working steps, green completed
steps with a check mark, red failed steps with a cross, and neutral dashed steps
that have not run or remain incomplete. Human review and out-of-date results are
amber. Selection has a separate outline; reduced-motion preferences disable the
working spinner and pulse.

## Served from somewhere else

The page talks to the host that served it by default, which covers a local
`conclave serve` and the same-origin proxy above. A deployment that serves this
bundle from another origin writes `config.json` beside the page:

```json
{"base": "https://api.example.com", "credentials": "include"}
```

The API must then allow the page's origin (`CONCLAVE_UI_ORIGINS` on the hosted
host), and `credentials: "include"` is what a gateway that authenticates with a
session cookie needs. A `token` is also accepted and is visible to anyone who
can load the page, so the proxy — which holds the token server-side — remains
the better shape for a secret. `profile` and `features` from `/api/runtime`
decide the copy and whether the page is told about a run or asks on a timer.

## Offline checks

No browser, server or provider is involved:

```sh
node scripts/check-hosted-ui.mjs   # API configuration, push or ask, host copy
node scripts/check-live-ui.mjs     # run projection and rendered markup
node scripts/check-canvas.mjs      # plan and run canvas markup, connections, run states
node scripts/check-studio-source.mjs  # the page markup and the controls that switch view
node scripts/check-config.mjs      # host configuration and catalogues
node scripts/check-api-proxy.mjs   # the same-origin proxy and its token
```

`scripts/check-canvas-browser.mjs` is an optional real-browser check for the two
canvas gestures. It serves the page, its modules and the API from this repository
through request interception, so it needs no Conclave server and no provider:

```sh
STUDIO_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/check-canvas-browser.mjs
```

See [Conclave REST API](../REST_API.md) for the complete
endpoint list, provider configuration, persistence, security and source boundaries.

## Settings

| Studio setting | Default | Purpose |
| --- | --- | --- |
| `CONCLAVE_API_URL` | `http://127.0.0.1:8080` | Independent Conclave REST service |
| `CONCLAVE_API_TOKEN` | empty | Host-only bearer token for the proxy |
| `CONCLAVE_API_TIMEOUT_MS` | `15000` | API request timeout |
| `CONCLAVE_PROPOSAL_TIMEOUT_MS` | `305000` | Proposal request timeout |
| `STUDIO_PORT` | `8787` | UI port |

Provider, tool, source and database settings belong to the backend process. The
backend detects the AI provider already set up on this computer, so
`CONCLAVE_PROVIDER` is only an override; `CONCLAVE_SOURCE_DIR` and other
`CONCLAVE_*` host settings are set there too. Older corresponding `STUDIO_*`
settings are accepted by Conclave as compatibility aliases, but Studio no longer
interprets them.
Set the same `CONCLAVE_API_TOKEN` on both processes to enable backend authentication.
While a token is set, `conclave serve` leaves this page unserved: a browser
cannot present a bearer token, so the dev server above is how the UI reaches it.

One backend can run multiple applications with different document folders.
Open **Advanced → Source settings** to configure named, host-approved folders and
an optional saved source ID for the current application, then
select **Source documents** separately for each request in Run. Selections are
saved with runs and revalidated on resume; concurrent runs have isolated file-tool
registries. See [Multiple applications on one backend](../REST_API.md#multiple-applications-on-one-backend)
for the resource JSON format and application allowlists. Bundled web tools are
always available; Git and GitHub tools are scoped to the selected source resource.
No per-application backend process is necessary.
Web-only agents require no source. Source settings persist without restarting;
legacy source environment variables are only fallbacks. See the REST documentation
for the audit of remaining host-owned environment settings.

Nothing has to be configured to start: until a source is saved, the folder the
backend was started in is offered as **This folder** and used automatically, so an
application that reads documents runs on the first click. Saving any named source
replaces it.

## Application workflows

**Settings → Limits and approvals** exposes workload limits as well as spending
limits. Blank means unlimited for action steps, notes, tool/report text,
concurrency, delegations and budgets. Finite runtime configuration limits must
be positive integers. Concurrency changes scheduling, not total planned work.
Tool output uses the saved run's limit, not the host default; accumulated shared
state is neither capped by that setting nor silently truncated. The backend's
actual context limits still apply. Saved applications and starter defaults keep
their existing values until explicitly edited and saved.

HTTP request size is host policy: `CONCLAVE_MAX_REQUEST_BYTES` defaults to
200,000 and accepts a positive integer or `unlimited`/`none`. Set it on both
Studio's proxy and the backend. Keep a finite cap on publicly exposed services
unless another trusted layer enforces resource protection. Other schema bounds
(record counts, field sizes, application sizes) and provider/tool timeouts remain
unchanged; unlimited workload budgets do not disable these boundaries.

Opening Run configuration runs offline **Validate application / Pre-run checks**
against the saved application:

- Required fields missing from instructions, or explicitly allowed to be empty.
- Explicit array/string/type conflicts and disallowed requested status values.
- Tasks requesting test/command execution or file modification without a known
  matching declared tool (tool IDs and host descriptions inform this heuristic).
- Explicit references to other outputs with no producing ancestor.
- Acceptance criteria without an explicitly named verification step downstream.
- Insufficient total/per-agent delegation budgets, depth below one, and zero
  token or execution-time budgets. Unlimited budgets remain supported.

Errors block Run. Heuristic warnings require acknowledgement for the saved
version and are checked again on submit; reopening Run resets acknowledgement.
Findings show evidence and proposed changes. **Make optional and save** or
**Adjust budget and save** previews the exact change and requires confirmation,
then validates and saves a new content version. **Edit step instructions** and
**Open schema, tools and limits** open the existing editors; save edits before
reopening Run. No tool or permission is automatically added. Existing runs are
unchanged; unsaved edits must be resolved before applying a fix.

These are targeted English-prose heuristics, not exhaustive semantic validation,
full acceptance-criterion mapping, or a guarantee of valid model output. Normal
runtime schema validation and approval gates remain active. Source authorization
and tool availability remain checked in Run configuration; source-path/document
existence is validated by the host during execution. No AI review is performed.

Plan and run use one shared canvas renderer and card frame with mode-specific
content and actions. Run mode is read-only; planning owns layout editing and
connection controls. Rendering still rebuilds the DOM rather than retaining a
canvas element across page renders.

Both modes hide native canvas scrollbars without clipping navigation. Drag the
background, use a trackpad, or focus the canvas and use arrow keys to pan. Fit
shows the entire graph, including layouts larger than the manual zoom range;
the percentage button restores 100%.

Run mode fits the entire graph automatically on entry and browser resize,
using the full available page width and remaining browser height. Manual zoom
is preserved while run updates arrive; Fit restores automatic fitting.

To debug a failed run, click the failed step (it is selected automatically when
the failure arrives). Its inspector shows the recorded failure or stop reason
and an expanded **Step activity** timeline with event sequence numbers, tool
outcomes and denials, delegation reasons, and budget warnings or limits. The
inspector also shows the recorded backend and any saved inputs/results. Failures
without step attribution are explicitly labelled as run-level failures, not
blamed on a guessed step. Private logs, raw replies and tool arguments remain
excluded; a generic host error may require the operator to inspect host logs.

Canvas view controls and step inspector utilities use icons with hover titles
and accessible labels. Run, Add a step, and Tidy layout retain text beside their
icons; statuses and approval decisions stay explicit.

Planning uses a light canvas-first editor with a floating Run/Add a step/Tidy
layout dock and left/right dependency ports. Drag cards directly; double-click
or activate a step with the keyboard to edit it. Existing saved positions are
kept until you explicitly tidy the layout.

Card positions autosave without advancing the content version. The API tracks
these saves with `layout_revision`; clean status reads “Layout saved”. Content
edits still require Save and remain “Unsaved changes” even after layout autosave.
Layout changes do not mark earlier requests as older versions.

Create a new application, customize its agents, tools, workflow and output schema,
save it, then submit a task in Use. The backend executes the saved content revision and
returns durable events, results and human review requests. Editing a definition
never changes an existing run. Restarting the backend preserves reviews and
completed-step checkpoints. Write and unknown capabilities retain approval gates.

Advanced exposes custom workspace catalogs, import/export and typed record fields.
Bundled catalogs and defaults are backend-owned data loaded through REST.
All application execution uses Conclave through REST; fictional sample runners,
sample packs and the legacy investigation demo have been removed. Save the
application, open Run, and submit your own request and source context.

Use Advanced in the left sidebar to delete the selected application. Deletion
requires confirmation and checks the saved revision. Saved run history remains
available through Conclave, and deleted starter applications stay deleted after
restarting the backend. Unsaved drafts can also be removed.

### Canvas-first Run screen

Runs stay on the execution canvas. Agent cards show their recorded status and
output summary; handoff edges and delegated agents remain visible. The bottom
Activity dock starts collapsed. Expand it for newest-first recorded activity,
filter by step, or select an entry to inspect its step. Repeated tool calls are
grouped; arguments and payloads are available under execution details.

Select a card to open its details drawer (a bottom sheet on small screens);
Close or Escape dismisses it. Follow active step is enabled for a newly opened
run and suspends after manual pan, zoom, or selection. Re-enable it explicitly
to return to the current work. Live updates retain the canvas, selection,
scroll positions, disclosure state, and focused input without rebuilding the
application subtree. View results and Review input open the existing results
and human checkpoint without replacing the graph. Context, version and usage
metrics are under Run information. Timestamps appear only when recorded.

Offline Run UX checks:

```sh
node studio/scripts/check-run-experience.mjs
node studio/scripts/check-canvas.mjs
node studio/scripts/check-live-ui.mjs
node studio/scripts/check-run-actions.mjs
```

The optional `check-canvas-browser.mjs` also exercises live DOM identity,
selection, activity scrolling, following, and tablet/mobile inspectors. Set
`STUDIO_PLAYWRIGHT_MODULE` to an installed Playwright module and
`STUDIO_BROWSER_ARTIFACTS` to an artifact directory; optionally set
`STUDIO_BROWSER_CHANNEL=chrome` to use installed Chrome.

### Canvas-first Plan screen

The Plan canvas shows who does each step and what it produces. Click a step
(or focus it and press Enter) to edit its task, member and output. Advanced
member settings, tool access, provider/model, input dependencies, output
structure and execution limits are expandable. Shared-member changes explicitly
apply to every assigned step; choose **Make a separate copy** to isolate one.
The drawer becomes a bottom sheet on mobile; Escape closes secondary panels.

**Add step**, **Suggest change**, and **Arrange** are primary canvas actions.
Add opens a compact picker for AI work, human review, existing members, presets
and saved members. The editor offers **Add after** and **Add parallel branch**;
select a connection to insert between its endpoints or remove it. Drag a
connection handle, or press Enter on it and choose **Connect here**. Dragging a
card changes only its position. List ordering also preserves dependencies.
Adding an independent step never silently connects neighbouring list entries.

Readiness uses definition validation, existing preflight findings and reported
host capabilities. Errors and warnings are distinct, selectable, and associated
with affected steps. Final pre-run checks remain authoritative. Suggestions
remain proposals until **Apply**; review summaries include connection, member,
step, tool-access and authority changes. Draft text, focus, zoom and canvas
position persist through routine editing and opening panels.

```sh
node studio/scripts/check-plan-experience.mjs
node studio/scripts/check-canvas.mjs
node studio/scripts/check-studio-source.mjs
```
