# Conclave interfaces and REST API

Conclave owns the multi-agent framework, application execution and persistence.
The CLI and HTTP adapter use the same application service and runtime. Studio is
an HTTP client; its Node server serves static assets and proxies REST requests.
It does not open the application database, own providers or launch Python.

```text
CLI ────────────────┐
                    ├── Conclave application service ── multi-agent core
Studio ── REST API ─┘              │
                         application/run SQLite stores
```

## Start

From the repository root, one command serves the REST API and the Studio page
on one address:

```sh
uv sync
uv run conclave serve
```

Open the address it prints. Startup reports the backend it found and, when a
token is set, why the page is not served.

For UI development, or to run the two processes separately, start the backend
and the Studio dev server (Node 24+):

```sh
uv run conclave serve --provider codex --port 8080

cd studio
CONCLAVE_API_URL=http://127.0.0.1:8080 npm run dev
```

The dev UI is at http://127.0.0.1:8787. On this checkout, the working Node executable
is `/tmp/conclave-studio-node/node_modules/node/bin/node`; it can invoke
`scripts/dev.mjs` directly if the system Node installation is unavailable.
Stopping Studio leaves Conclave running. Stopping Conclave closes its active
runs safely; inspect their saved status before retrying.

`CONCLAVE_API_TOKEN` applies to the API only: a browser page cannot present a
bearer token, so `conclave serve` leaves Studio unserved while a token is set
and the dev server above remains the way to reach the UI.

## Endpoints

All payloads and responses are JSON. The API contract version is 1. Every run
pins an immutable application definition and saved revision. Application IDs are
opaque strings; run IDs come from the service. Request bodies are limited to
200,000 bytes by default. Set the host's `CONCLAVE_MAX_REQUEST_BYTES` to a
positive integer, or `unlimited`/`none` to disable that transport cap. Configure
the Studio proxy and backend consistently; upstream servers may impose their
own caps. Responses expose actual events/results, without private checkpoints.

All `run_config` and `run_budget` limits accept JSON `null` for unlimited.
`max_tool_output_chars` caps individual tool responses and delegated reports,
not accumulated shared state. Each run uses the pinned application's output
limit on an isolated registry, including sandbox execution. Concurrency remains
controlled by `fan_out_limit`; no shared state is silently truncated. Model
backend context limits still apply. Existing saved defaults are unchanged.

Step-specific failure and stop events (`ApplicationFinished`) include `stage`
and a public `reason`, including output validation failures and host execution
errors. Run-level failures may omit `stage`; clients must not assume the last
started step caused them, since independent steps can run concurrently. Private
provider logs, raw model replies and tool arguments are not exposed in run events.

| Method | Path | Purpose / request |
| --- | --- | --- |
| GET | `/api/health` | Service status and API version |
| GET | `/api/runtime` | Provider availability, registered tools, connectors, approvals and supported features |
| GET | `/api/sdk/application` | Downloadable Python facade source for the installed application adapter |
| GET | `/api/config/defaults` | Host-configured default definition and limits |
| GET | `/api/config/catalogue` | Bundled/deployment roles, starters, output types, tools, connectors and manual planning rules |
| POST | `/api/definitions/validate` | `{definition}`; validate without saving or running |
| GET | `/api/applications` | List persisted application definitions and revisions |
| POST | `/api/applications` | `{definition}`; create and return ID/revision |
| GET | `/api/applications/{id}` | Fetch one saved definition |
| PUT | `/api/applications/{id}` | `{definition, revision}`; compare-and-swap update |
| DELETE | `/api/applications/{id}` | `{revision}`; delete definition without deleting run history |
| GET | `/api/members` | List reusable saved members |
| POST | `/api/members` | `{member}`; save a reusable member |
| DELETE | `/api/members/{id}` | `{}`; remove member template |
| GET | `/api/catalogue` | Read saved workspace catalog and revision |
| PUT | `/api/catalogue` | `{catalogue, revision}`; update custom roles/starters/outputs |
| POST | `/api/proposals` | `{prompt, definition, catalogue}`; propose a validated change without executing it |
| GET | `/api/runs?application_id={id}` | List recent runs for an application |
| POST | `/api/runs` | `{version:1, application_id, revision, task, inputs}`; execute the backend's saved snapshot |
| GET | `/api/runs/{id}` | Persisted status, actual results, metrics, events and reviews |
| GET | `/api/runs/{id}/events?after={sequence}` | Poll ordered events after a cursor; returns `next_cursor` and status |
| GET | `/api/runs/{id}/reviews` | List durable review requests and submitted contributions |
| POST | `/api/runs/{id}/reviews` | `{review_id, output}`; validate and persist a human contribution |
| POST | `/api/runs/{id}/resume` | `{}`; continue after the required human contribution |
| POST | `/api/runs/{id}/cancel` | `{}`; stop active or waiting work |

POST creation returns 201. Read/update/actions return 200. Successful deletion
returns 204. Malformed definitions/requests return 400; missing resources return
404; stale revisions and overlapping proposals return 409; unavailable backend
services return 503. HTTP framing adds 401 for a missing/incorrect configured
token, 403 for disallowed origins/Host headers, 413 for oversized requests and
415 for non-JSON mutations. Backend exceptions are redacted.

Polling is the event transport for this version; no SSE/WebSocket endpoint is
claimed. Schedules and event triggers remain metadata, and execution is explicitly
unsupported by this local host. Provider proposals require explicit user review
and application before running.

## Configuration and ownership

No provider setting is required. Conclave uses the backend already set up on this
computer — `codex`, then `opencode`, then an Ollama server with an installed
model — and reports which one it found in `GET /api/runtime`. A named backend is
never replaced by a detected one, and a model is never guessed: a command
backend keeps its own default and Ollama is asked what it has installed.

Provider and execution settings belong to Conclave: `CONCLAVE_PROVIDER`,
`CONCLAVE_MODEL`, `CONCLAVE_MODEL_TIMEOUT`, `CONCLAVE_OLLAMA_URL`,
`CONCLAVE_PROVIDER_FACTORY`, `CONCLAVE_SOURCE_DIR`, `CONCLAVE_SOURCE_EXTENSIONS`, `CONCLAVE_RESOURCES_FILE`,
`CONCLAVE_EXTENSIONS`, `CONCLAVE_DEFAULTS_FILE`,
`CONCLAVE_CATALOGUE_FILE`, `CONCLAVE_FIXTURES_FILE`, `CONCLAVE_MEMBER_LIMIT`,
`CONCLAVE_HISTORY_LIMIT`, `CONCLAVE_DATA_DIR`, `CONCLAVE_DATABASE` and
`CONCLAVE_RUN_DATABASE`. Existing corresponding `STUDIO_*` host settings remain
compatibility aliases; `CONCLAVE_*` takes precedence.

`CONCLAVE_PROFILE` selects what the host may assume about where it runs. `local`
(the default) may find the model backend on this machine, treat the folder it was
started in as the source, and serve the Studio page from this process. `hosted`
assumes none of that: it requires a configured provider or factory, requires an
authorized source, and serves the API only. `GET /api/runtime` reports the
profile, and the page renders from it. See
[Running Conclave locally and hosted](docs/architecture/hosting.md).

`CONCLAVE_PRINCIPAL` and `CONCLAVE_TENANT` name who acts and whose data they act
on. Locally that is the OS user in the `local` tenant. Storage is scoped by
tenant: the `local` tenant keeps the files this checkout already has, and every
other tenant gets its own directory under `<data_dir>/tenants/<tenant>/`, so no
query can reach another tenant's runs, applications or saved source settings.
`CONCLAVE_RUN_DATABASE` and `CONCLAVE_DATABASE` remain single-operator overrides
and apply to the local tenant only.

The backend reuses existing `studio/.studio-local/studio.sqlite` and
`runs.sqlite` in this checkout, preserving saved applications, member templates,
workspace catalog revisions, runs and reviews. Installed usage without that
checkout defaults to `.conclave/` in the current directory. Set an explicit
`CONCLAVE_DATA_DIR` when multiple commands/processes must share another workspace.
Only the backend opens those databases. Catalog defaults and bundled starters
ship in `conclave/application/data/`; Studio fetches them through REST.

Studio accepts `CONCLAVE_API_URL`, optional `CONCLAVE_API_TOKEN`,
`CONCLAVE_API_TIMEOUT_MS` (15000), `CONCLAVE_PROPOSAL_TIMEOUT_MS` (305000), and
`STUDIO_PORT` (8787). These proxy settings are host-owned; the browser cannot
choose an arbitrary upstream or receive its token. Set `CONCLAVE_API_TOKEN` on
both processes to require bearer authentication at the backend.

This server binds loopback and supports a trusted local OS operator. It does not
provide remote multi-user authentication, per-user authorization or workspace
isolation. Tools and provider authentication remain host-owned. Unknown/write
capabilities require the existing core approval gate; connector metadata and
HTTP clients cannot grant capabilities. Files remain contained in the configured
source folder. The core import does not load these application/HTTP adapters,
bundled tools, web-search dependencies or subprocesses.

### Multiple applications on one backend

In Studio, open **Advanced → Source settings** to authorize document folders or
repositories and save an optional default source ID for the current application.
This requires no environment variable or restart. Sources persist in
`sources.json` in the host data directory and take precedence over legacy source
environment settings. Only agents whose tool allowlists include file, knowledge,
Git or GitHub tools use a source; web-only applications need none. Documents do
not have to be source code. Application definitions store the source ID as
`"resources": {"source": "documents"}`, never a filesystem path.

Nothing has to be authorized to start. When no source is configured — no saved
catalogue, no `CONCLAVE_RESOURCES_FILE`, no `CONCLAVE_SOURCE_DIR` — the folder
the backend was started in is the source, offered as **This folder** (`default`)
and bound automatically for a request that needs one. Containment, hidden-file
denial and `CONCLAVE_SOURCE_EXTENSIONS` apply inside it exactly as for a folder
authorized by name, and saving any named source replaces it.

`GET /api/settings/sources` and `PUT /api/settings/sources` manage this trusted
local operator configuration. Unlike run metadata, this settings endpoint includes
host paths so the operator can edit them. Existing resource authorization and
resume checks still apply. Do not expose this local API to untrusted users.

File-tool implementations are registered once. Each run selects a host-owned
resource ID, and receives its own scoped registry; concurrent runs never rebind
the shared registry. Configure the shared backend once with
`CONCLAVE_RESOURCES_FILE=/absolute/path/to/resources.json`:

```json
{
  "case-a": {"name": "Case A documents", "path": "/data/case-a", "applications": ["saved-application-id"]},
  "case-b": {"name": "Case B documents", "path": "/data/case-b"}
}
```

`applications` is an optional allowlist of saved application IDs. Omission allows
any application used by the trusted local operator; an empty list allows none.
The browser receives resource IDs and labels, never paths. Select **Source
documents** in Run, or add `"resources": {"source": "case-a"}` to the existing
`POST /api/runs` body. Missing, unknown or unauthorized selections fail before
creating a run. The server pins the resource ID and root revision in saved run
requests; resume rechecks authorization and refuses a changed or removed root.
Changing document contents is permitted; this is not a document snapshot.

`CONCLAVE_SOURCE_DIR` remains a compatibility resource named `default`, used when
file tools are requested without an explicit selection. It is not necessary when
using named resources or the working folder. Source tools are `list_files`, `read_file`, `search_files`
and `knowledge_search`. All enforce resolved path containment, hidden-file denial
and `CONCLAVE_SOURCE_EXTENSIONS` (default `.txt,.md,.csv,.html`). Built-in knowledge
search is keyword search over the selected documents, not semantic retrieval;
trusted extensions can replace it without losing their capability declarations.

All bundled tools are registered automatically, including `web_search` and
`web_fetch`; a normal install includes the web-search dependency. Git and GitHub
tools use the selected authorized source resource as their repository and require
Git or the authenticated `gh` CLI respectively. The file extension filter applies
to document tools, not Git history or GitHub output. `CONCLAVE_WEB` and `STUDIO_WEB`
are no longer used. Applications retain their individual agent tool allowlists
and approval gates. No application-specific backend is required.
Resource isolation applies to these Conclave file tools, not arbitrary provider
subprocesses or trusted extensions; this local host is not an OS sandbox.

### Environment settings audit

- Source folders/resource catalogue: now editable and persistent in Studio;
  `SOURCE_DIR` and `RESOURCES_FILE` remain compatibility fallbacks only, and an
  unconfigured host reads the folder it was started in as `default`.
- Application budgets, tool access, workflow and source selection: saved in the
  application definition, not process environment settings.
- Provider/model, model timeout, Ollama URL: optional host overrides only, since
  the backend already on this computer is detected; provider/model also have CLI
  flags. These are not yet editable in Studio.
- Source extension filters, history/member limits: still host policy settings,
  not yet editable in Studio.
- Extensions/provider factories and default/catalogue files: trusted host code
  or deployment overrides; not safe to accept as arbitrary application settings.
- Data/database locations, API token, ports and Studio proxy URLs/timeouts:
  deployment settings remain host-owned. Tokens are never returned to the UI.
- Host profile: `CONCLAVE_PROFILE` decides which conveniences a host may assume
  (`local` by default). It is the switch a hosted deployment sets first.
- Identity and tenancy: `CONCLAVE_PRINCIPAL`/`CONCLAVE_TENANT` name the local
  actor and its storage scope. A hosted deployment supplies its own
  authenticated mapping instead (see below).
- `WEB` enablement: removed; all bundled tools register automatically.

Setting names above use the `CONCLAVE_` prefix; existing `STUDIO_` compatibility
aliases remain supported where applicable.

### Hosted deployment

The loopback server above is the local adapter. A hosted deployment serves
`conclave.asgi:configured_application` with its own ASGI server, for example
`uvicorn --factory conclave.asgi:configured_application`. It answers the same
`ApplicationAPI.dispatch` contract with three differences:

- No loopback Host check, because the deployment terminates traffic itself.
- `CONCLAVE_API_TOKEN` authenticates every request, and `CONCLAVE_UI_ORIGINS`
  lists the origins allowed to read responses (with preflight for the UI).
- One application service per tenant, built on demand. The tenant comes from the
  deployment's own `principal_for` mapping of a verified caller; request headers
  never name a tenant on their own, because a caller that can name any tenant can
  read any tenant.

Authentication and per-user authorization inside a tenant remain the
deployment's responsibility, and file/Git/GitHub tools still use host-level
sources: do not expose them to tenants you do not trust until per-tenant
resources land.

### Running tool calls behind a boundary

Tool calls run in this process by default. A deployment that runs them in a
sandbox installs a factory on the host:

```python
host = LocalRunHost(..., sandbox=my_sandbox_factory)   # factory(bound_tools, request) -> ToolExecutor
```

The factory is consulted per run and returns an executor scoped to that run's
grant, so runs do not share one. What crosses the boundary is a `ToolInvocation`
— the call, its run and its agent — and never the approver, the budget or the
store; those stay where the decision to run was made. The grant is enforced on
both sides: the control plane will not send a call outside it, and the sandbox
will not run one that arrives anyway. `conclave/application/sandbox.py` provides
`Grant`, `SandboxExecutor` and `serve_call`, plus an in-process transport for
exercising a deployment before it has a kernel.

### Running an agent loop behind a boundary

An agent loop holds no authority of its own. It asks a `RunAuthority` for the
model call, the budget, the permission and the approval, and
`ToolAgentRuntime(..., authority=...)` defaults to the in-process one. A
deployment that runs the loop elsewhere uses the pair in
`conclave/application/sandbox.py`:

```python
control = ControlPlaneAuthority(provider, budget, bound_tools, approver)  # the control plane
loop = sandboxed_loop(InProcessControlPlane(control), tools=granted_tools, agents=specs, config=config, max_depth=...)  # the sandbox
```

The sandbox-side loop has no provider, no budget and no approver, and the
control plane decides a destructive call from its own tool declaration, so a
sandbox cannot understate a tool. Everything that reads or moves a budget
counter is a question to the control plane; depth is declared policy, so the
prompt can name the limit without a round trip while `reserve_delegation`
enforces it.

The loop's events cross the same channel: `event_payload` turns one into data
and `rebuild_event` turns it back, refusing a payload that is not exactly an
event. Every declared field crosses except `AgentResult.private` — an agent's
scratchpad stays in the sandbox. A deployment picks the placement in one place:

```python
host = LocalRunHost(..., loop=my_loop_factory)   # factory(provider, tools, budget, agents, config=..., approver=...) -> something with .stream
```

`run_application` and `RunRepository.execute` take the same parameter. Closing
the stream cancels the sandbox, and a sandbox that breaks fails the run rather
than reporting a run that quietly did nothing. What a deployment still supplies
is the kernel itself: the process, its session, its pooling, and the
authentication on the channel.

### Watching a run

`GET /api/runs/<id>` answers with the run's record, and a caller that would
rather be told asks for `GET /api/runs/<id>/stream`: server-sent events carrying
the same record, again whenever it changes. A caller that disconnects stops the
watch. This is what `features.events` reports on `/api/runtime` — the hosted
transport can push, so it says `true`; the loopback adapter answers one bounded
request per connection, so it says `false` and the UI asks on a timer instead.

The stream is in-process, like the run claim: several replicas serving one tenant
need a shared signal before push means anything across them.

### The Studio page

Studio talks to the host that served it, unless a deployment writes
`config.json` beside the page:

```json
{"base": "https://api.example.com", "credentials": "include"}
```

`base` is another origin for the API (the deployment must allow the page's
origin with `CONCLAVE_UI_ORIGINS`), and `credentials` covers a gateway that
authenticates with a session cookie. A `token` is also accepted, and is visible
to anyone who can load the page: prefer the same-origin worker proxy, which
holds the token server-side, or a gateway.

`profile` and `features` decide the copy and the behaviour: a host that reports
no profile is local, and the tenant is named when there is one. A hosted tenant
cannot `PUT /api/settings/sources` — its sources come from the deployment.

## Source boundaries

- `conclave/runtime.py`, providers, budgets, store and tool registry: reusable core.
- `conclave/application/`: definition adapter, execution/reviews, persistence,
  planning, trusted extensions and caller-configured data.
- `conclave/cli.py`: general application CLI.
- `conclave/api.py`: REST routing and local HTTP transport.
- `conclave/asgi.py`: hosted transport over the same routing and dispatch.
- `conclave/application/principal.py` and `storage.py`: who acts, and where that
  tenant's data lives.
- `conclave/application/sandbox.py`: the tool-call boundary, both sides of it.
- `conclave/authority.py`: what a run may not decide for itself — the model
  call, the budget, the permission and the approval.
- `studio/web/` and `studio/shared/`: UI, editing, rendering and browser checks.
- `studio/worker/index.js`: same-origin REST proxy and static asset serving.
- `studio/scripts/dev.mjs`: UI HTTP server only.
- `investigation/`: separate reference application using the same core.

`applications` retains compatibility imports for the earlier application CLI.
Studio's former Python runtime and JSON-lines bridge have been removed. Historical
D1 migration files remain as schema history; they are not run by Studio or used
by the current REST architecture.

Application deletion retains run snapshots and history. Deleted starters are not reseeded on restart. Fictional fixture configuration is no longer exposed.
