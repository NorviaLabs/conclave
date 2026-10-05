# Conclave Studio

The current Studio source imported from the ChatGPT Site into the Conclave
repository. Start with ../STUDIO_HANDOFF.md for product context and the terminal
handoff. ../docs/studio/PRODUCT.md contains accepted UX decisions;
../docs/studio/BUILD_PLAN.md contains the implementation investigation.

## Run locally

Use Node.js 24 or newer:

~~~sh
npm ci
npm run dev
~~~

Open http://127.0.0.1:8787. The development command builds the existing Worker,
applies SQL migrations and saves local applications/member templates in
.studio-local/studio.sqlite. Restart after editing source. To use another port:

~~~sh
STUDIO_PORT=8788 npm run dev
~~~

This preview uses Node's built-in SQLite API and implements only the D1 methods
currently used by worker/index.js. It runs on loopback without cloud credentials.
It can start a local Python run service with an explicitly configured backend.
Production execution and authentication need their own host. Node SQLite API
reference: https://nodejs.org/download/release/latest-v24.x/docs/api/sqlite.html

## Current behavior

- Goal-first creation with editable catalog-based suggestions.
- Eighteen application starters, including six detailed priority workflows.
- Fourteen role presets, 28 tool references, six semantic output types, eight
  output presets and 39 connector choices.
- Editable DAG canvas: multiple roots, branches and joins; independent card
  layout; keyboard/list alternatives; cycle rejection.
- Saved application revisions and reusable independent member snapshots.
- Fictional sample runs with dependency-scoped state and explicit human input.
- The original investigation reference at /investigation.
- Export/import and Python application adapter under advanced options.

The browser sample runner makes no model or tool calls. The local Run view can
now execute a saved application, show persisted results/progress, stop a request,
record human review and resume after restart. Account connections, schedules and
rich artifact delivery still need implementation. Text results can be downloaded
as actual text files. The adapter executes ready DAG stages one
at a time using injected core providers/tools and a human_handler callback.
Draft approval never substitutes for separate destructive-tool authorization.

The Python host now has a versioned run request and local SQLite run records.
Validated request-form values reach every step as source data, including human
review requests. Definitions and inputs are copied when a run is created;
later edits cannot change that run. Events commit before being forwarded.
Human reviews retain explicit output and reviewer identity, and waiting runs
resume from completed-step checkpoints after the host restarts.
The local browser uses this service when enabled; the hosted Site remains in
sample mode because it has no Python host attached.

## Enable local AI runs

Run `uv sync` from the repository root. In `studio/`, select an existing backend
and a folder containing only source documents you intend the team to read:

~~~sh
STUDIO_PROVIDER=opencode STUDIO_SOURCE_DIR=/absolute/path/to/source-documents npm run dev
~~~

`STUDIO_PROVIDER` accepts `codex`, `opencode` or `ollama`. Codex/OpenCode use
their existing CLI authentication. `STUDIO_MODEL` is optional for those CLIs;
OpenCode IDs pass through unchanged, and the backend's unreported default is
not guessed. Ollama requires `STUDIO_MODEL`; `STUDIO_OLLAMA_URL` optionally sets
its server address. `STUDIO_MODEL_TIMEOUT` sets a finite positive timeout in
seconds, default 300. Credentials and backend settings never come from browser
definitions or request bodies. A configured provider is not an authentication
or output-quality check; actual backend errors stop the request safely.

The source folder exposes `read_file` for `.txt`, `.md`, `.csv` and `.html`.
Paths must remain inside the folder; hidden paths, unsupported formats and
escaping symlinks are rejected. Refer to filenames in the request, for example
`evidence.md`. Rich document extraction and cloud-file accounts are not connected.

Set `STUDIO_WEB=1` to explicitly enable the bundled `web_search` and `web_fetch`
tools. Search needs optional `ddgs` (included in the repository's dev group).
The Research brief starter requests file reading and public web research;
enable those tools or remove unused tool selections from the plan. Missing
tools stop a request before creating a run. Connector selections remain metadata;
only this host's registered tools execute, with core capability gates intact.
This local host supplies no blanket approval for write/send tools.

Save the application, open Run, supply real request values and start. Live forms
begin empty and never insert fictional sample inputs. Progress polls committed
events; switching tabs does not stop the run. The selected request's URL restores
it after reload, and Saved requests lists the latest 30 for that application.
Outputs retain the pinned definition version. Review submissions use the local
operator's account name, supplied by the host. Review and resume are separate
API operations: if continuation fails, the saved review can still be resumed.

The Node preview communicates with Python over a child-process pipe. Python
opens no network port. The Worker resolves the saved definition and checks the
requested revision; a browser cannot substitute a definition or reviewer.
Same-origin JSON checks and the loopback Host check apply to the run endpoints.
This is a development workspace for one local operator, not production user
authentication or a team permission system.

`/api/runtime`, `/api/runs`, `/api/runs/:id`, and the `reviews`, `resume`, `cancel`
subroutes expose availability, history, start/status and human contributions.
Host checkpoints stay private; public responses include sanitized events,
actual output patches and available metrics through completed steps. Token
counts and tool attempts appear only when recorded; dollar cost remains absent
when the provider supplies none. Source locators are not verification of claims.
Hard limits use the pinned run budget. Saved warning thresholds are not yet
observed; the settings screen labels this limitation.

Stop cancels the local execution task. CLI model subprocesses are killed and
reaped; an in-flight HTTP request can finish or time out in its worker thread.
Interrupted work is not automatically retried. Cancelling a waiting review
marks the run cancelled and rejects subsequent review submissions.

## Run records for a Python host

From the repository root, import `studio.runtime.runs`. A trusted host resolves
the saved application and revision itself, then builds this request. The stored
revision identifies that snapshot; it does not verify a browser-supplied
definition against the application's saved history.

~~~python
from studio.runtime.runs import RunRepository

repository = RunRepository("studio/.studio-local/runs.sqlite")
run_id = repository.create({
    "version": 1,
    "application_id": saved_application["id"],
    "revision": saved_application["revision"],
    "definition": saved_application["definition"],
    "task": task,
    "inputs": request_values,
})

# Provider authentication and tool permissions belong to this host.
async for event in repository.execute(
    run_id, provider, tools,
    approver=approve_external_action,
):
    await send_to_ui(event)

# Reopening the repository restores the request and ordered event history.
record = repository.get(run_id)

# The host obtains an explicit contribution and authenticates the reviewer.
review = next(item for item in record["reviews"] if item["status"] == "pending")
repository.submit_review(
    run_id, review["id"],
    reviewer=authenticated_reviewer_id,
    output={"text": reviewed_brief},  # Records collections require {"records": [...]}.
)

async for event in repository.resume(
    run_id, provider, tools,
    approver=approve_external_action,
):
    await send_to_ui(event)
~~~

Version 1 requires exactly the six fields shown above. Request values may use
only declared input IDs. Required fields must be filled, text is limited to
2,000 characters, numbers must be finite and dates use valid `YYYY-MM-DD`.
Optional empty fields are omitted. Sample inputs are never inserted by the
host. Source material belongs in request values, separate from member/step
instructions. Values are data, but this labeling alone is not an assurance
against prompt injection.

Without a human handler, the stored run ends as `waiting_for_human`, retaining
the review request and available results. With a handler, the adapter collects
explicit human output. A run can start only once. Closing/cancelling execution
records `interrupted`; a process crash leaves `running`. Both require future
explicit recovery, and completed actions are never automatically replayed.
`submit_review` validates output before committing it. Empty, duplicate, stale
and cross-run submissions are rejected. The host must supply an authenticated
reviewer identity; this local repository does not authenticate people. A
submitted review stays durable until resumed, and its record becomes `consumed`
when its step output commits. Reopening the database applies the additive
`runtime/migrations/0001_reviews.sql` migration to older local run databases.

`resume` claims only a waiting run with a submitted review. It restores each
completed step's patch and the original accounting, then processes the review
and remaining steps. It preserves dependency-scoped inputs, append/upsert
merges and state versions. Waiting for a person consumes no model budget.
Rejected or changes-requested decisions stop downstream execution; revisions
require a new run with a new pinned definition. Multiple human steps pause
independently. Direct `human_handler` callbacks remain supported, but durable
reviewer identity is captured through `submit_review`.

Checkpoints are trusted host records tied to the exact definition, task and
inputs. They contain completed output patches and budget usage, never raw model
replies, tool arguments or private notes. The adapter's restore API does not
establish that an interrupted external action is safe to retry; the repository
refuses resume for running, interrupted, failed, stopped and completed runs.
The browser bridge uses this review protocol. An action ledger and recovery
for uncertain external actions remain future work.

Studio deliberately requires finite positive `run_config` limits even where
Conclave core supports unlimited values. `run_budget` supports `None` for
unlimited dimensions. New runs create fresh accounting; resumed runs restore
their prior usage.

Offline contract and persistence checks:

~~~sh
uv run pytest --no-cov tests/test_studio_runs.py
~~~

## Files

| Path | Responsibility |
| --- | --- |
| web/ | Interface, canvas, inspectors and sample runner |
| shared/catalogue.js | Starters, all default teams, roles, tools, outputs, connector metadata and research links |
| shared/definition.js | JavaScript definition validation and DAG operations |
| runtime/application.py | Definition-to-Conclave execution adapter |
| runtime/runs.py | Versioned run requests, pinned snapshots, SQLite events and durable human review/resume |
| runtime/host.py | Development-only Python command host and explicit source/provider configuration |
| runtime/migrations/ | Additive local run-service migrations, separate from the Site database |
| worker/index.js | Same-origin application/member APIs and asset routes |
| db/, drizzle/ | Application/member schema and preserved migrations |
| scripts/build.mjs | Bundles Worker, assets, adapter download and migrations |
| scripts/dev.mjs | Development-only local Worker runner and SQLite bridge |
| scripts/runtime.mjs | Local Node/Python command pipe |
| scripts/check-local.py | Isolated Worker/API smoke check with restart persistence |
| scripts/check-live.py | Isolated Worker/Python/fake-CLI/source/review/resume smoke check |
| scripts/check-live-ui.mjs | Offline live-result projection and rendering checks |
| scripts/check-browser.mjs | Optional Chromium interaction and screenshot checks |
| .openai/hosting.json | Preserved identity of the existing ChatGPT Site |
| source-snapshot.json | Original source provenance and hashes |

## Build and schema changes

~~~sh
npm run build
~~~

After changing db/schema.ts, generate a new migration with npm run db:generate.
Preserve the existing migration files. Build output is ignored under dist/.
The adapter imports the repository's Python package; run it in the Python 3.14+
uv environment from the repository root with host-provided tools/provider.

## Hosted data

The existing Site's saved D1 rows and unsaved browser drafts are separate from
source. Export a saved application JSON from its advanced menu and import it
locally when needed. The catalog's full starter definitions and samples are
already included. GitHub commits do not publish to the original Site.

## Handoff checks

Ran python scripts/check-local.py using Node.js 24.19.0 and Python 3.12.14.
The check copies source into a temporary directory, starts the actual built
Worker and validates assets, all 18 catalog rows, application create/update,
revision conflicts, same-origin enforcement, cycle rejection and saved
application/member persistence after a process restart. Result: PASS.

Run this check from the repository root in its supported Python environment:

~~~sh
uv run python studio/scripts/check-local.py
~~~

Latest terminal validation (Python 3.14.6, Node 24.21.0): the full
`uv run pytest` suite passed all 327 tests with 99.45% core/application coverage.
The local API, live bridge and UI projection checks below also passed, as did
the Studio build and `git diff --check`. Older finite-default test assumptions
were updated to match current configuration; explicit finite limits still test
refusal and prompt behavior. Unlimited coordinator prompts no longer display
`at most None` or a `2/None` round count.

Additional terminal checks use Python 3.14 and Node 24:

~~~sh
uv run pytest --no-cov tests/test_studio_host.py tests/test_studio_runs.py tests/test_providers.py
uv run python studio/scripts/check-local.py
uv run python studio/scripts/check-live.py
node studio/scripts/check-live-ui.mjs
~~~

The live smoke check copies source into a temporary checkout and supplies a fake
Codex CLI. It validates actual source-file reads, persisted waiting reviews,
restart/resume without repeated completed calls, origin/revision gates, reported
metrics and cancellation. It makes no live model or internet calls and preserves
the user's local databases. UI checks validate projection/rendering logic.

A separate real Codex backend check passed using the Research brief starter,
only `read_file`, a temporary fictional observational study and a temporary
run database. It verified an actual source read, preservation of the study's
40/18/10 counts and causal limitation, a saved brief, human review and completed
resume. This is one workflow quality check, not connector or broad model-quality
validation. The backend selected its default model; no model identity is claimed.

Chromium browser validation also passed using an isolated temporary checkout,
source folder, databases and fake provider. It covered blank-input validation,
live start, polling focus and draft preservation, reload and history, explicit
review/resume, escaped request values, text downloads, cancellation, a 390px
mobile layout, all six featured fictional workflows, keyboard sample stepping,
list reordering without changing dependencies, adding/removing prerequisites,
definition export and saving a new revision. Desktop/mobile screenshots and
the six sample result pages were visually inspected. No page JavaScript errors
were reported.

The terminal-browser command could not identify the Ghostty pane, so this check
uses headless Chromium through an optional external Playwright installation.
Playwright is not a Studio dependency. To repeat it with Node 24+ on PATH:

~~~sh
npm install --prefix /tmp/conclave-browser-check playwright
/tmp/conclave-browser-check/node_modules/.bin/playwright install chromium
STUDIO_PLAYWRIGHT_MODULE=/tmp/conclave-browser-check/node_modules/playwright/index.mjs uv run python studio/scripts/check-live.py --browser
~~~

Screenshots default to `/tmp/conclave-studio-browser`; override that location
with `STUDIO_BROWSER_ARTIFACTS`. The browser check found and fixed lost keyboard
focus during polling, stale saved-request status and hidden run/sample labels.
