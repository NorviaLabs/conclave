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
them together. It runs the backend as the deployment's own adapter, allows this
origin to call it, and signs in with Google when a login file is present —
`--login PATH` names one, `--no-login` leaves sign-in off. Select the Node
executable with `--node /path/to/node` or `STUDIO_NODE`.

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
shows the same graph with each step's status. Run — the play icon in the canvas
action bar — opens one modal for the request, its context and its source, so no
separate run screen exists. Once a run is open the same control reads Run again
and is prefilled with the request that ran, creating a new run rather than
resuming the old one.

### The canvas action bar

The canvas floats one action bar over the graph (`canvas-dock`). Primary commands — Run workflow, Add step and Test step — have labels.
Navigation icons carry their names in `title` and `aria-label`,
and each button in it is one thing a person can do:

| Run state | Run controls | Plan tools |
| --- | --- | --- |
| No run open | Run (play) | enabled |
| Going | Pause (`‖`), Stop (`■`) | disabled |
| Paused | Run (play) continues, Stop (`■`) | disabled |
| Needs your input | Stop (`■`) | disabled |
| Finished, stopped, failed or cancelled | Run again (play) | enabled |

Plan tools are Add step (`+`), Suggest change (`✦`) and Arrange (grid), and they
sit after a separator in the same bar.

**The bar keeps its shape.** While a run is in flight the three plan tools stay
put, greyed out, with the reason in their hovertext ("Add step · finish or stop
the run to change the plan") rather than disappearing, so nothing moves under
your hands mid-run. They work again as soon as the run stops, finishes or fails.

**The bar stays fixed.** It is anchored to the bottom of the canvas viewport,
outside the transformed board. Panning and zooming never move it. On narrow
screens it scrolls horizontally rather than hiding commands.

**What the run is waiting for stays in words above the canvas.** Review input and
View results live in the status line (`run-overview`), not in the bar, so no
action is offered twice and none is wordless-only. There is no Edit plan button:
using any plan tool already leaves the run, so a separate switch would be a
second name for the same thing.

**Pause stops between steps, never during one.** The step already running
finishes, the run records a checkpoint, and Run continues from the next step, so
completed work and approved external actions are never repeated. Stop is
different and final: it discards what has not run.

Because the canvas owns the run controller, the header carries Run only on the
setup screen, where there is no canvas — one Run control per screen, never two.

The header itself holds only what belongs to the whole page: the application
name, Save, run history, and — on the setup screen — Run and the way back to the
canvas. Everything that acts on *this application* is in the canvas's
**Application** menu beside the plan: Set up, Import application file, Export
application file, Delete application. Set up is not a second header button, so
the header stays a caption plus the page's own actions.

### Right-click menu

Right-clicking a canvas surface opens the actions that belong to whatever was
pressed. The menu is a shortcut, never the only route: every item carries the
same data attribute as the on-screen control it stands for, so it runs through
the same handler, the same validation and the same permission gates.

| Pressed | Items |
| --- | --- |
| Step | Edit step · Advanced limits · Add step after · Add parallel branch · Move earlier / later · Duplicate · New member for this step · Edit the shared member · Make a copy for this step · Edit output structure in setup · Connect to another step · Remove what this step depends on · Centre in view · Remove step |
| Connection | Insert a step between · Add a parallel branch from here · Reverse direction · Show the source step · Show the target step · Centre this connection in view · Change where this connection ends · Remove this connection |
| Plan background | Add a step here · Suggest a change · Arrange · Fit to view · Reset to 100% · Full screen |
| Run card | Step details · Show this step in activity · Copy this step's output (plus a note when the result is out of date) |
| Run background | Follow the active step · Show/hide the activity stream · Run information · Fit to view · Reset to 100% |

An item that cannot act yet is disabled with the reason in its hovertext ("Move
earlier" on the first step, "Remove what this step depends on" when there is
nothing to remove, "Copy this step's output" before it has produced anything),
so the menu never changes shape. Removal items are last and red.

Opening a step's menu **selects that step**, and an open settings panel follows
it, because the panel's own commands ("New member for this step", "Edit the
shared member", "Make a copy for this step") read the selected step.

**Connections have one handle, not two floating buttons.** Selecting an edge
draws a single `⋮` handle at its midpoint; clicking it (or pressing Enter on the
focused edge) opens the same menu the right-click does. The old `×` and
`+ Insert step` buttons are gone — they were 28px targets sitting on the graph,
they only worked with a mouse, and the menu covers both. `Delete` on the edge or
its handle removes the connection, and the step panel's dependency checkboxes
remain the other keyboard route.

Each connection is itself a focusable, labelled button (`tabindex="0"`,
`role="button"`), so the whole flow works without a pointer: Tab to the
connection, Enter to select it, Shift+F10 to open its menu. A focused connection
highlights its line.

**What stays in the panel, and why.** Fields and lists do not belong in a menu:
Step name, What happens?, What does it produce?, Team member, Member name, the
backend, Output label, Tool uses, Seconds, the per-tool permission checkboxes
and the dependency checkboxes all need a surface that shows the current value,
takes typing and validates as you go. A menu item is a command with no state.
The panel also keeps its own Move ±, Duplicate, Add after/parallel and Remove
buttons, deliberately: the menu is an accelerator, and a destructive action must
never become reachable only by a gesture a person may not discover.

**Run, Pause and Stop are deliberately not in it.** They stay visible in the
action bar — a control that stops a run must never be hidden behind a
right-click.

**Keyboard and touch.** Shift+F10 or the Menu key opens the menu for the focused
step, edge or canvas; arrow keys, Home and End move through the items, Escape
closes it and returns focus where it was, and choosing an item closes the menu
before the action runs. On a touch device, press and hold does the same thing
after 500ms, and the press does not also click the step. The browser's own menu
still appears on fields and outside the canvas.



The canvas is the page, so it takes the whole height the page has left: the
shell fills the window (`100dvh`) and each wrapper passes that height down with
`flex:1` until the viewport, which is `100%` of it. There is no fixed canvas
height to go stale and no dead band under the graph; the viewport scrolls once
the plan outgrows the space. Because the canvas now reaches the bottom of the
window, the plan's action bar floats inside it (`bottom:16px`) rather than
hanging below the panel where it would be clipped.

The run canvas keeps `--plan-height` (`clamp(520px, calc(100vh - 132px),
1400px)`) because its page has a run overview, request details and results below
the canvas that need the remaining space.

During a run, canvas cards show blue animated working steps, green completed
steps with a check mark, red failed steps with a cross, and neutral dashed steps
that have not run or remain incomplete. Human review and out-of-date results are
amber. Selection has a separate outline; reduced-motion preferences disable the
working spinner and pulse.

## Signing in

A deployment can require people to sign in with Google (`CONCLAVE_GOOGLE_LOGIN_FILE`
plus `CONCLAVE_SESSION_KEY`; see `REST_API.md`). Studio asks who it is before it
fetches anything:

- an API answer of 401 replaces the page with one screen — an explanation and a
  single **Sign in with Google** link — so no application chrome is shown to
  somebody the host has not admitted;
- a completed sign-in returns to `/`, and a failed one to `/?signin=failed`,
  which the screen reports without inventing a reason;
- once signed in, the rail foot names the account and offers **Sign out**, which
  posts to the host and reloads into the sign-in screen.

A local `conclave serve` has no login at all, so the account line and the screen
never appear there. The page sends `credentials: "include"`, which is what a
deployment authenticating with a session cookie needs.

Which deployment shapes can carry that cookie:

| Where the page is served | What it needs |
| --- | --- |
| From the API's own origin (`conclave serve --hosted`, or a host serving both) | Nothing: the cookie is same-origin. Set `CONCLAVE_UI_ORIGINS` to that origin and the API accepts this page's writes. |
| From another origin, with `config.json` naming the API (`{"base": "...", "credentials": "include"}`) | The API must allow the origin in `CONCLAVE_UI_ORIGINS`; if the two are on different *sites*, the login file needs `"same_site": "none"`. |
| Through this bundle's own worker proxy (default `config.json`) | Not usable for sign-in: the proxy answers with its own headers, so a `Set-Cookie` from the API never reaches the browser. Point the page at the API instead, as above. |

The sign-in link and the sign-out request go straight to the API address, never
through the proxy, so `/auth/*` needs no proxy rule.

Signing in is **not** connecting Google Drive, Docs or Sheets: those are
connections with their own scopes and tokens, made in Connections. The sign-in
screen says so, because the two are easy to confuse.

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
node scripts/check-step-tests.mjs  # pinned fixtures, isolated tests and comparison
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

**Controls → Tool authorization** offers **Autonomous execution** for trusted
applications. Save this opt-in to authorize declared tools up front instead of
approving each write. It includes commands and remote writes; explicit human
workflow steps still pause. Source and connection restrictions remain active,
and unknown capabilities are not pre-authorized. Existing applications retain
per-call approval until enabled. Command tools are not an OS sandbox.

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

A card reads top to bottom — what the step is called, what it produces, who does
it, what runs it — and is a fixed 350×240 box that never scrolls. Every value
therefore clamps rather than pushing a neighbour out: the title clamps to two
lines with an ellipsis, and so do the output and the backend, whose full text
stays on hover. The title is the one row exempt from the card's flexible height,
so a card that runs out of room takes the shortage from the facts below it and
never from the second line of the name.

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

### Testing one step

A completed step's inspector offers four actions in place, rather than another
permanently visible sidebar: **Pin output**, **Test step**, **Test from here**
(the step and its descendants), and **Compare results**. They need the recorded
snapshot of a completed step, so they appear on a finished step of the run you
are viewing — for a step that has never run, use the plan action below.

**Pin output** shows the recorded input snapshot and output of that step, read
from the run record and redacted. It is recorded data, not live sources; inspect
and edit it, then save it under a name. **Test step** runs only that step against
a chosen fixture — the current draft or the saved version — with the provider's
real model and an optional model override. Advanced settings (model, explicit
mock results) stay collapsed. **Test from here** runs the step and its
descendants, seeding the recorded ancestors and leaving unrelated branches alone.

Testing is isolated and non-mutating by default: every tool is treated as
destructive and only the tools you name in the mock results answer, so an
unlisted call is blocked instead of executed. A test never inherits a production
approval, and it never overwrites the run's results. If the state schema or the
connections changed since the fixture was recorded, the test is refused instead
of guessing; a changed agent assignment is reported as a warning.

Results show the test's own status, its output beside the reference, and an exact
comparison — a readable diff for prose, per-record changes for records. Tested
steps are marked on the canvas with a distinct TEST state while the draft, pan,
zoom and selection stay as they were; closing the dialog returns to editing with
nothing lost.

**Testing a step that has never run.** A step can also be tested straight from
the plan, without running the application first: select the step on the plan
canvas and use **Test this step** in the dock, or the same item in the step's
right-click menu. Because there is no run to read, you supply what the step would
have received — the application's request fields, and the output of each step it
depends on, listed from the plan's own connections rather than from the step that
happens to sit above it on the canvas. A step with no prerequisites needs none.
The dialog shows the exact fixture it will save, and a reference output is
optional: with one, the result is compared against it; without one, no comparison
is claimed and the results say so plainly. The fixture is stored as written by
hand, with no source run, and is told apart from a recorded one everywhere it is
read. The test runs in the same safe mode, against the draft as it stands now.

Offline check for this flow:

```sh
node studio/scripts/check-step-tests.mjs
```

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
connection handle, or press Enter on its port and click the step to connect to —
while a connection is in progress every other card is a target, and the card you
click completes the connection. Dragging a
card changes only its position. List ordering also preserves dependencies.
Adding an independent step never silently connects neighbouring list entries.

**Groups arrange the canvas.** A step's settings carry a **Group** field, so a
step can be put in a named group (Research, Review, Delivery) or a new one
created from that step. A group draws a labelled box behind its steps and a header
that folds them away: **collapsed hides the cards and their connections, and says
"still runs"**, because a group is arrangement only — the runtime never reads it,
and folding one cannot change order, dependencies or output. Groups live in
`canvas.groups` in the definition, beside the saved positions, and a step records
which group it belongs to. The format stays compatible: a definition with no
groups is valid, both validators accept it, and export/import carries it
unchanged. Limits are enforced where the format is: up to 20 groups, unique
identifiers, a named group, and a step may only claim a group the plan declares.

**Find a step.** A workflow longer than a screen is searched from the canvas
heading: the field matches a step's name, the member who does it, or what it
produces, and the count beside it says how many steps match. Matches are marked on
the graph and the rest are dimmed rather than hidden, so the shape of the workflow
stays visible while you look. **Enter** steps through the matches and opens the
step; **Shift+Enter** goes back; **Escape** clears the search. Typing marks the
drawn canvas directly instead of redrawing it, so the caret never leaves the field
and a pan in progress is never interrupted.

**A saved test example is visible before you open the step.** A step that has one
carries a **Saved example** badge on its card; when the plan's outputs or
connections have moved since that example was written, the badge reads **Example
out of date** in red, because the next test would be refused rather than guessed.
Hovering names the example, whether it was pinned from a run or written by hand,
and whether a reference output is recorded. The badge appears for every step at
once — the examples are read once per application and refreshed whenever a test
example is saved or a test starts, not on every step you click.

**A connection explains itself.** Selecting one draws the chain that has to
finish first — the step it comes from and everything that step itself waits for —
and shows what the connection means in words: *"Draft waits for Research;
Findings is shared with Draft and every step after it."* The wording names the
order a connection enforces and the collection it shares, and does not claim a
private hand-off between two steps, because a step reads every prerequisite's
contribution to one shared collection. The same sentence is the connection's
accessible name, so it is readable without selecting it. Actions stay in the
connection's menu, opened from the handle on the selected connection or by
right-click.

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

### Integration connections (partial first pass)

Open a Plan step's **Connections and tools → Manage connections** to search
service adapters, configure a named connection, check provider access and
remove local access. Save the application first. Credentials are submitted to
Conclave and are never saved in the workflow definition or browser storage.
Select operations explicitly in connection setup, then check the granted tools
for an agent. Shared-member tool changes affect all steps using that member.

The host must configure `CONCLAVE_INTEGRATION_KEY`; the local launcher
(`uv run python dev.py`) generates and keeps one for you, so only a real
deployment needs to set it. Custom API/feed endpoint
hosts require `CONCLAVE_INTEGRATION_HOSTS`. Manual tokens are currently used;
Studio OAuth sign-in is not yet implemented. Reads can run within their
connection grants. Every write — a connection operation or a host tool such as
`write_file` — waits for the person's approval in the run view; `conclave run`
has no approval surface and refuses instead. Protocol/database/S3 adapters use deployment-owned
`CONCLAVE_INTEGRATION_ADAPTERS_FILE` configuration; missing drivers or unconfigured
adapters are shown as unavailable,
not as connected accounts. See [status matrix](../INTEGRATIONS.md).

```sh
node studio/scripts/check-integrations.mjs
uv run pytest --no-cov tests/test_integration_connections.py tests/test_integration_runtime.py
```

### What the catalog shows

**Templates → Connections** lists the services this host can run, grouped by
category. Availability comes from the host, not the bundled catalog: an entry
reads *Connected for this application*, *Available — not connected yet*, or
*Needs host setup* (MCP, HTTP/OpenAPI, PostgreSQL, MySQL and Amazon S3 until an
operator configures them). Each entry says what it can do in plain language, and
**Connect** opens the connection manager at that service.

A named host instance (a database account, bucket or endpoint) is its own entry,
so you pick the account rather than the service name. Services that templates
mention but this host cannot run stay under *Suggested by templates, not installed
on this host*, where **Add to plan** records intent only — it grants nothing.
Installed integrations are connected rather than added to the plan, so there is
one mechanism to learn for granting access.

```sh
node studio/scripts/check-integrations.mjs
```

### Refining a goal

**New application → What do you want to achieve?** shows a sparkle beside the goal
when the host has a provider. One click rewords the goal in place and reports what
changed; there is no second screen and nothing to accept or discard, because
wording is the only thing it touches.

```sh
node studio/scripts/check-goal-refinement.mjs
node studio/scripts/check-goal-questions.mjs
```

### Questions about the goal

Submitting a goal asks the host **one question at a time**, each one following the
answer before it, and stops as soon as the answers settle what the host would ask
next — before the fifth if that is when it has enough. Answer what you can and
leave the rest blank: nothing waits on these, **Stop asking and suggest a team**
ends the questions whenever you want, and a host that cannot answer proposes from
the goal alone.

Only the answers you give shape the proposed team, and all of them — including
the ones still open — are saved with the application as *Clarified requirements*,
editable beside the goal in the Setup step and listed in the plan's Application
menu. When the application runs, the answered requirements are given to the team
as your words, not as instructions from the host.

### While the team is chosen

Choosing a team is the slowest thing in the flow, and it is two calls: the first
asks the host for the team and the results only, and the second designs the plan
around them — the members, the steps, the connections that order those steps, the
instructions and the shared state. The chosen team is named in the second request
rather than handed over as its base definition, because a base definition is
preserved: the team-only reply passes validation with nothing connected, so using
it as the base produced a plan whose steps had no edges between them. The step is replaced by a moving indicator, the backend the host is
using, how many answers are going with the goal, and the seconds elapsed — and as
soon as the first call answers, the members and results it chose are listed by
name while the plan is still being written. A host that will not answer the first
call is not asked twice: the plan is proposed from the goal alone.

Nothing is claimed about stages a call does not report, which is why the member
names appear when the first call lands rather than while it runs: the provider
layer returns one buffered reply per call and streams nothing.

### Budgets: what stops a run

A run does not get to keep going forever. Four things bound it, and they are
written for the person paying for the run rather than for the machine:

| What it bounds | In plain terms |
| --- | --- |
| **Action steps** | how much one member may *do* in a single stage — look something up, read a file, record a finding |
| **Tool calls** | how many lookups the whole run may make, across every member |
| **Time** | how long the whole run may take |
| **Spend** | tokens or money, if you set either |

Delegations and depth are bounded too: how many sub-agents the team may create,
how deep they may nest. Nothing here needs tuning to get a working application —
a new one has no time limit and generous action limits, because the actions are
what actually bound the work.

**When a limit is reached, the work is not thrown away.** The member that ran out
is asked for the answer it would have given, from what it already gathered, and
that is what the stage produces. The record says so, so a stage that was written
up after its actions ran out is not mistaken for one that finished early. If it
still will not answer, the run stops and says which member ran into which limit,
and which stage was never completed — `market_researcher: used all 12 action
steps without completing Research opportunities`.

The idea is simple: **a limit costs you time, not your work.** Limits exist to
stop a runaway, not to decide what is worth keeping.

Three things hold that up, and between them a spent budget never throws work
away:

1. **The agent is told.** Its instructions name the budget before any of it is
   spent, every action reports where it stands (`Action 7 of 20.`), and its last
   action — or the last minute of the clock — is announced as such, so the answer
   is written rather than the search being left unfinished.
2. **The stage hands back what it has.** When a budget runs out anyway, the member
   is asked once more for the answer it would have given, with no tools left to
   offer, and that answer is what the stage produces. The record says which budget
   was spent, so this is never mistaken for a stage that finished early.
3. **The run can be continued.** Every stage that completes is checkpointed as it
   goes, and a run stopped by a budget keeps that checkpoint: press **Continue**
   on a stopped run and it carries on from the last stage that finished — nothing
   already done is repeated, and the spend already recorded is not forgotten. If
   it stopped on a limit, raise the limit first; the resumed run keeps the old
   spend, which is the honest thing for accounting to do.

A backend that stops answering is treated the same way. If a model call does not
come back in time — five minutes by default — the run **stops** rather than
failing, keeps its checkpoint, and can be continued when the backend is well
again. Raise that wait with `CONCLAVE_MODEL_TIMEOUT` if your backend is simply
slow. A backend that reports an error, or whose command fails, is still a failure:
that is a defect to fix, not a moment to wait out.

What is *not* recovered is a stage that was cut off mid-flight: it starts again
from the beginning of that stage, because the runtime does not replay a half-done
call. With the write-up in place, that stage usually answers instead of being cut
off, which is why the write-up matters more than the resume. A run left behind by
a process that died is marked interrupted when a host next opens the store, so it
never sits there pretending to still be working.

Set them, if you want them, in **Setup → Controls → Limits and approvals**. The
warning thresholds there are what make a run say it is approaching a limit before
it reaches it.

### What runs a step

Every step card names the backend it runs on, under the member that does the work:
`Codex · gpt-5` when the step or its member names one, and the host's own name for
its default when neither does — a backend that was never chosen is named as the
host's rather than guessed at. On the run canvas the card shows what the run
*recorded* for that step once it has started, falling back to the plan's
declaration before then, so a step that ran on another backend says so. The
choice itself is `stageBackend`'s, so the card and the run cannot disagree.

### The dock

The dock is the bar of canvas actions, and it holds what you reach for while
looking at the canvas rather than at one step's settings:

- **Always**: Add step · Suggest a change · Arrange · **Fit** (the way back from
  anywhere, now that the canvas has no edge) · Full screen (the plan canvas).
- **On a run**: the run controls — Run or Run again, Pause, Stop — with the plan
  tools still visible but disabled, so the bar never changes shape under your
  hands.
- **When a step is selected**: that step's own actions, in front of the rest. On
  the plan: Edit · Duplicate · Connect · Centre in view. On a run: Step details ·
  Show this step in activity · Copy its output. Removing a step is deliberately
  not here — it stays behind the right-click, where a stray click cannot delete.

The zoom stepper (−, the level, +) stays in the corner: it is a readout with a
step either side of it rather than an action. Every dock action is icon-only with
its name in the hovertext, and every one is also in the right-click menu, so the
menu remains a complete route to everything.

### The canvas has no edge

The plan and run canvases are infinite: the board is *moved*, not scrolled, so
panning continues past the last step in every direction and there is nothing to
reach the end of.

- **Drag the background** to pan, or use the **arrow keys** (a screenful at a
  time). The wheel and trackpad also pan.
- **⌘/Ctrl with the wheel** zooms where the pointer is, so the step you are
  looking at stays under it. The zoom buttons zoom the middle of the view, and
  **Fit** brings the whole graph back. Zoom runs from 40% to 250%.
- Zoom and pan are per canvas, and kept while you work: drawing the canvas again
  (a run event, a re-render) puts the board back exactly where it was.

Steps themselves still live on a grid that starts at the top-left, so a step
cannot be dragged above or left of the origin — the canvas around them is what is
unbounded.

### Watching a step work

The run canvas says what the step in flight is doing, in the state it already
shows at the top of the card: `Working for 1m 20s · 8 tool calls · last Web
fetch`, with a failed call counted and named, and the same words on hover. One
place, not two — the same detail twice on one card is noise.

The elapsed time is read from the run's own event timestamps, so it survives a
reload, and the view is redrawn once a second while a step is working — a long
model call records nothing until it returns, and a still screen would be
indistinguishable from a stuck one. It is not a live region: text that changes
every second would be announced every second.

The activity stream (`Show the activity stream`) lists the same events as they
arrive, newest first, and can be filtered to one step. Both come from
`/api/runs/<id>/events`, which the page follows by push when the host reports it
can, and by asking on a timer when it cannot.

### Icons and hovertext

Dense, repeated gestures are icon-only: removals (×), move earlier/later,
duplicate, arrange, add step, full screen, zoom, fit, run history, download a
result, run information, the plan/canvas switch and the runtime disclosure.

An icon-only control carries **both** `title` and `aria-label`, because a mouse
user needs hovertext and a screen reader needs a name — they are not
interchangeable. `check-studio-source.mjs` fails if either is missing, and the
canvas view group plus the workbench step actions are the pattern to copy.

Words stay where a mistake is expensive or the action is the main one: Delete
application, Disconnect, Revoke provider access, Approve this action, Reject,
Discard, Cancel, Stop, Run, Save, Resume, Connect, Authorize account, Add to
plan, and every recovery action such as Try again or Reload. Approving an
external change must be unambiguous at a glance, so it never becomes a bare
checkmark.

### Build, Test, Run and Results

The workspace navigation separates editing the current draft, safe step tests,
execution of the saved version, and readable production outputs. Switching modes
retains the draft, selected steps, pan/zoom and the open historical run. A new run
opens Run mode; polling never forces someone out of Build or Test.

The step inspector leads with **Test this step**, then **Inputs**, **Output** and
**Test**. Inputs names each declared request field and each prerequisite step with
the value it actually carried, and says where the values came from: the run open
on the canvas first, otherwise the latest saved test example, otherwise nothing at
all — an empty state is never presented as a value. Output shows what the step
produced, and Test names the latest saved example for that step, whether it was
pinned from a run or written by hand, and whether a comparison exists. Long values
are summarised to one readable line; the payload stays behind **Advanced · … as
JSON**, and every value is escaped. Editing which steps this one waits for stays in
**Depends on**, separate from the record of what arrived. Saved examples are read
when a step's settings open, and the underlying fixture API is unchanged — the
interface calls fixtures “saved test examples”. Save a new workflow once before
storing examples; subsequent unsaved edits can be tested directly.

Results show deliverables using the existing views, with request and activity
collapsed. Interrupted, failed and paused runs explicitly distinguish completed
saved work from incomplete output; continuation is offered only for resumable
checkpoints. Production approvals and test outputs remain separate.

Goal-based creation and templates still produce a reviewable draft, not an
automatic execution. Review and save the workflow, test a useful step, and
configure account access when the relevant tools need it.

```sh
node studio/scripts/check-workspace.mjs
```
