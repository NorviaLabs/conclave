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
runs safely; inspect their saved status before retrying. A paused run is not an
interrupted one: it stopped at a step boundary on request, so resuming it
continues from the next step without repeating completed work or approved
external actions.

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
| GET | `/api/applications/{id}/schedule` | The saved schedule's state: `active`, `paused`, `waiting` (no owner), `owner` and `owner_since`, `next_at`/`next_local`/`time_zone`, `last` and up to 20 `recent` scheduled times with their outcome (`started`, `skipped`, `missed`, `failed`) |
| POST | `/api/applications/{id}/schedule` | `{action: "pause"\|"resume"\|"turn_on"}`; owner only (`turn_on` records the caller as owner of a schedule that has none). Never creates a revision; 409 when the application is not scheduled |
| GET | `/api/applications/{id}/notifications` | Whether "this run is waiting for you" can be sent now: `configured`, `owner` and `owner_since`, `via`, `connection`, `target`, `usable` and, when not, `reason` |
| POST | `/api/notifications/preview` | `{definition, application_id?, reason?: "review"\|"pause"}`; the exact message a waiting point would send (`via`, `target`, `text`/`subject`/`payload`, `link`), built by the host's own function; 400 with the reason when it cannot be built |
| POST | `/api/schedules/preview` | `{schedule, definition?, after?}`; the next three times of a schedule being edited, `{times: [{at, local, time_zone}]}`, worked out by the host's rules; 400 with the reason when the schedule is invalid |
| GET | `/api/runs?application_id={id}` | List recent runs for an application; each row has `trigger` (`{kind: "manual"}` or `{kind: "schedule", scheduled_for}`) |
| POST | `/api/runs` | `{version:1, application_id, revision, task, inputs, resources?}`; execute the backend's saved snapshot |
| GET | `/api/runs/{id}` | Persisted status, actual results, metrics, `usage` (per step and against limits; see "What a run used"), events and reviews |
| GET | `/api/runs/{id}/files/{file}` | A file the run stored from a step's output (Markdown, CSV or JSON), as an attachment; `410` when it is missing or no longer what was stored (see "Stored output files") |
| GET | `/api/runs/compare?a={id}&b={id}[&order=given]` | Two runs of one application compared step by step (see "Comparing two runs"); the earlier run is A unless `order=given` |
| GET | `/api/runs/{id}/events?after={sequence}` | Poll ordered events after a cursor; returns `next_cursor` and status |
| GET | `/api/runs/{id}/reviews` | List durable review requests and submitted contributions |
| POST | `/api/runs/{id}/reviews` | `{review_id, output}`; validate and persist a human contribution |
| POST | `/api/runs/{id}/resume` | `{}`; continue a waiting, paused, stopped or interrupted run. Uncertain actions are checked first; `409 {error, actions}` names any that still need a decision |
| GET | `/api/applications/{id}/event` | Whether events start it: address, owner, the latest events received (never the secret) |
| POST | `/api/applications/{id}/event` | `{action: "create"\|"rotate"}`; the incoming address and its secret, answered once |
| POST | `/api/events/{endpoint}` | A signed event: one run, or the first run for a repeat (see "Runs started by an event") |
| GET | `/api/applications/{id}/access` | Who may do what with it: `open`, your role (`you`), `people` and the latest changes (`log`) (see "People and roles") |
| POST | `/api/applications/{id}/access` | `{person, role}`; add someone or change their role (owner). Limiting an open application starts with yourself as owner |
| DELETE | `/api/applications/{id}/access/{person}` | `{}`; remove someone (owner), or yourself; the last owner stays |
| GET | `/api/applications/{id}/versions` | Kept versions, newest first, with who saved each, a summary and run counts |
| GET | `/api/applications/{id}/versions/{n}` | One kept version and its definition |
| GET | `/api/applications/{id}/versions/compare?from=a&to=b` | What changed between two versions, by area, in words |
| POST | `/api/applications/{id}/versions/{n}/restore` | `{revision}`; save version n's content as a new version. `409` when the application changed |
| POST | `/api/runs/{id}/retry/cancel` | `{}`; stop a pending automatic retry ("Don't retry"). The run stays stopped and resumable; `400` when no retry is pending |
| POST | `/api/runs/{id}/pause` | `{}`; ask a running run to stop **between steps** and keep a checkpoint |
| POST | `/api/runs/{id}/cancel` | `{}`; stop active, waiting, paused, stopped or interrupted work. The run is `cancelled` and cannot be continued |
| GET | `/api/runs/{id}/actions` | The run's external actions: state, check, who decided, and — only while they could be sent again — the arguments |
| POST | `/api/runs/{id}/actions` | `{action_id, decision: "done"\|"resend"\|"undo", note?}`; decide an uncertain action, as the requesting principal |
| POST | `/api/runs/{id}/actions/check` | `{action_id}`; ask the service whether an uncertain action happened |
| POST | `/api/applications/{id}/fixtures/preview` | `{run_id, step_id}`; the recorded input snapshot and reference output of one completed step, redacted and ready to inspect |
| POST | `/api/applications/{id}/fixtures` | `{run_id, step_id, name, data?}`; save a named fixture from that preview, with `data` the inspected/edited envelope |
| POST | `/api/applications/{id}/fixtures/author` | `{step_id, name, definition?, inputs?, upstream?, reference?, reference_recorded?, task?}`; write a fixture for a step that has never run, 201 |
| GET | `/api/applications/{id}/fixtures` | List this owner's saved fixtures for the application |
| GET | `/api/applications/{id}/fixtures/{fixture_id}` | Read one saved fixture |
| DELETE | `/api/applications/{id}/fixtures/{fixture_id}` | `{}`; remove a saved fixture, 204 |
| POST | `/api/applications/{id}/tests` | `{fixture_id, scope:'step'\|'downstream', definition?, model?, mocks?}`; start an isolated test, 202 |
| GET | `/api/applications/{id}/tests` | List this owner's test executions |
| GET | `/api/applications/{id}/tests/{test_id}` | Test status, its own events, reference and new output, and a comparison |
| GET | `/api/applications/{id}/tests/{test_id}/compare?other={test_id}` | Compare this test's output with its fixture, or with another test of the same fixture |
| GET | `/api/me` | Who this request is, and how to sign in or out when the deployment has a login |
| GET | `/auth/google/start` | Begin Google sign-in; answers 302 to Google, or 400 when the redirect is not authorized |
| GET | `/auth/google/callback` | 302 to `/` with the session cookie, or to `/?signin=failed` |
| POST | `/auth/logout` | `{}`; clears the session cookie, 204 |

POST creation returns 201. Read/update/actions return 200. Successful deletion
returns 204. Malformed definitions/requests return 400; missing resources return
404; stale revisions and overlapping proposals return 409; unavailable backend
services return 503. HTTP framing adds 401 for a missing/incorrect configured
token, 403 for disallowed origins/Host headers, 413 for oversized requests and
415 for non-JSON mutations. Backend exceptions are redacted.

## Google sign-in (optional)

A deployment can require people to sign in with Google. It is off unless
`CONCLAVE_GOOGLE_LOGIN_FILE` names a JSON file and `CONCLAVE_SESSION_KEY` holds
at least 32 characters; with no login file, every deployment behaves exactly as
before, and the local loopback server never has one.

```json
{
  "client_id": "...", "client_secret": "...",
  "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
  "token_url": "https://oauth2.googleapis.com/token",
  "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
  "hosts": ["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"],
  "redirect_uris": ["https://studio.example.com/auth/google/callback"],
  "after_login": "https://studio.example.com/",
  "tenant": "acme",
  "domains": ["acme.com"], "emails": ["contractor@partner.test"]
}
```

Those four URLs are the live ones from Google's discovery document
(`https://accounts.google.com/.well-known/openid-configuration`), which also
publishes `id_token_signing_alg_values_supported: ["RS256"]`, the scopes
`openid`, `email`, `profile`, and `code_challenge_methods_supported` including
`S256` — the four things this login relies on.

The flow is authorization code with PKCE(S256), a single-use `state` per attempt,
a `nonce` bound into the ID token, and an exact redirect-URI match. The ID token
must be RS256 against a key in `jwks_url` and must agree on `iss`, `aud`, `exp`,
`iat`, the nonce and `email_verified`; a token that asks for another algorithm is
refused, so a shared-secret confusion attack is not possible. The session is a
signed, expiring cookie (`conclave_session`): `HttpOnly`, `SameSite=Lax`, and
`Secure` whenever the request arrived over HTTPS.

A verified address becomes the principal — `name` is the address, `source` is
`google` — and the tenant always comes from `tenant` in the file, never from the
request. **Who may sign in is deny-by-default**: an address must match `domains`
or `emails`, and a file that names neither is refused when the login is built
rather than opened to everyone. A Host header or `X-Forwarded-Proto` is only ever
used to *propose* a redirect URI, which must appear in `redirect_uris`.

`redirect_uris` are exact, because Google requires an exact match, and they must
be HTTPS — **except on a loopback address**, which is the one case Google
exempts ("Redirect URIs must use the HTTPS scheme, not plain HTTP. Localhost URIs
(including localhost IP address URIs) are exempt from this rule"). So
`http://127.0.0.1:8080/auth/google/callback` is accepted for local use and
`http://studio.example.com/...` never is: an authorization code or a session
cookie over plain HTTP is a leak.

Three more keys decide how the browser moves through the flow:

| Key | Default | Meaning |
| --- | --- | --- |
| `after_login` | `/` | Where the browser lands after signing in, and after a failed attempt (with `?signin=failed`). A deployment whose page is served from another origin must set it, or the person lands on the API's own root. An absolute HTTPS URL, a loopback HTTP URL, or a path on this deployment's own address. |
| `same_site` | `lax` | `lax` when the page and the API share a site; `none` when the page is on another site, because a `Lax` cookie is not sent on a cross-site request at all. `none` always sets `Secure`, which a browser requires. |
| `session_seconds` | 43200 | How long a session lasts, between a minute and 30 days. |

With a login configured, `Authorization: Bearer $CONCLAVE_API_TOKEN` still works
for programmatic callers, and either credential ends in a principal established
at the transport. Unauthenticated API requests answer 401 with
`{"error": "Sign in to continue", "login": "/auth/google/start"}`.

A page on another origin reads the API with `credentials: "include"`, which needs
two things this adapter does: the response carries
`access-control-allow-credentials: true` whenever the origin is allowed, and the
cookie is `SameSite=None` when that origin is on another site. Behind a proxy,
forward `X-Forwarded-Proto` so the cookie is marked `Secure`, and set
`CONCLAVE_UI_ORIGINS` to the page's own origin — the adapter refuses a mutation
from any origin it was not told about, credentials or not.

Two limits are deliberate and worth knowing: sessions cannot be revoked
individually before they expire (rotate `CONCLAVE_SESSION_KEY` to end them all),
and signing out ends this deployment's session, not the person's Google session.

Signing in is not the same as connecting Google Drive, Docs or Sheets to an
application: those are *connections* with their own MCP-scoped access tokens, and
the login never shares a token with them.

### Trying it on your own machine

`conclave serve --hosted` runs this deployment's adapter on a loopback address —
the same code path a hosted deployment serves, sign-in included — so the flow can
be exercised before anything is deployed:

```bash
# 1. In the Google Cloud console, create a Web application OAuth client and add
#    exactly this authorised redirect URI:
#      http://127.0.0.1:8123/auth/google/callback
# 2. Write login.json with that same redirect_uris value, a tenant, and the
#    addresses that may sign in (domains or emails).
export CONCLAVE_GOOGLE_LOGIN_FILE=login.json
export CONCLAVE_SESSION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CONCLAVE_UI_ORIGINS=http://127.0.0.1:8123
uv run conclave serve --hosted --port 8123
```

The command prints where the API is and where sign-in starts. Open the printed
address, and the page asks you to sign in: an unauthenticated page is the
intended first view. The same server serves the Studio bundle, because this is a local profile, so no
second process is needed. `CONCLAVE_PROFILE=hosted` turns that off — the shape a
deployment has, where the page is served elsewhere and `after_login` and
`same_site` are set to match.

Being served from one loopback address is what keeps this local: the Host header
must be `127.0.0.1:PORT` or `localhost:PORT`, and only this machine can reach it.

Google's side of the setup matters too: the consent screen must be configured for
the addresses you list, and an app that is still being tested only admits the
**test users** it names, so a sign-in can be refused by Google before this code
sees anything. That refusal arrives as `/?signin=failed` on the page, and this
adapter reports it without inventing a reason.

Polling is the event transport for this version; no SSE/WebSocket endpoint is
claimed. Scheduled applications run on a serving host (see "Scheduled
applications" below); event triggers remain metadata, and execution on an event
is unsupported by this local host. Provider proposals require explicit user
review and application before running.

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
and `CONCLAVE_SOURCE_EXTENSIONS` (default `.txt,.md,.csv,.html,.pdf,.docx`: PDF and
Word files are read as their text, with `[Page N]` and heading markers, within
the `CONCLAVE_DOCUMENT_*` limits — see the README). A source may
name its own instead, as `"extensions": [".rs", ".toml"]` or `"extensions": "*"`
for any text file that is not hidden; a code checkout an application changes
needs this to be readable as code. Built-in knowledge
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
- Source extension filters, history/member limits, and how many steps of one run
  may work at once (`MAX_PARALLEL_STEPS`, 1 to 64, default 8): still host policy
  settings, not yet editable in Studio.
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
- `applications/`: the definition-driven entry point — `run_application` and
  `validate_definition`, with `create`/`run`/`status`/`review`/`resume`/`cancel`
  served by `conclave/cli.py`.

`applications` retains compatibility imports for the earlier application CLI.
Studio's former Python runtime and JSON-lines bridge have been removed. Historical
D1 migration files remain as schema history; they are not run by Studio or used
by the current REST architecture.

Application deletion retains run snapshots and history. Deleted starters are not reseeded on restart. Fictional fixture configuration is no longer exposed.

## Opt-in integration connections

See [integration setup and status](INTEGRATIONS.md) for implemented operations,
security boundaries and the explicit remaining scope. Integration credentials
are not application fields. Configure `CONCLAVE_INTEGRATION_KEY` server-side to
enable encrypted connection storage. `CONCLAVE_INTEGRATION_ADAPTERS_FILE`
loads deployment-approved MCP/OpenAPI/database/S3 instances; custom endpoint hosts require
`CONCLAVE_INTEGRATION_HOSTS`. With no key, discovery works but connection setup
returns 503. No live verification is implied by a configured connection.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/integrations` | Exactly 23 service/protocol descriptors with implemented operations |
| GET / POST | `/api/connections` | List redacted connections / create explicit application and operation grants |
| PUT | `/api/connections/{id}` | `{operations?, applications?}`; change what the connection grants, keeping its stored credentials |
| DELETE | `/api/connections/{id}` | Disconnect and remove encrypted local credentials |
| POST | `/api/connections/{id}/health` | `{}`; probe a safe granted read when possible |
| POST | `/api/integrations/recommend` | `{goal, application_id}`; suggestions, never grants |

Tool assignments use `connection.<id>.<operation>` in the existing agent `tools`
array. Each execution resolves the encrypted connection server-side and rechecks
ownership, tenant, application, operation, scopes and revocation. Mutations
remain WRITE capabilities. The Studio host asks the person before any
destructive call runs — a connection operation or a host tool such as
`write_file` — through the pending-approval endpoints below. A host with no
approval surface refuses instead: `conclave run`, and any host built with
`configured_host(interactive_approvals=False)`. Trusted Python callers can pass
their own approver callback. In-process run events include `connection_id` and
`operation` without credentials or raw arguments.

An optional definition field, `"autonomous_execution": true`, authorizes declared
tools up front in the application host, including hosts without an interactive
approval surface. Missing or `false` keeps per-call approvals. The host records
`ToolAuthorizationGranted` with the authorizer and per-agent tool allowlists.
Unknown capabilities remain denied; connection ownership, scopes, revocation,
source containment, budgets, and explicit human workflow gates remain enforced.
Enable only for trusted definitions: this grants commands and remote writes,
and command tools are not an OS sandbox. Direct `run_application` callers still
supply their own approver; a definition alone does not change core policy.

A GitHub connection can run the repository half of an issue-to-merge flow:
`read_branches`, `read_checks` (each CI check run's name, status and
conclusion for a commit or branch), `create_branch`, `write_content` (plain `text` in, base64 out,
with the current blob `sha` when replacing a file), `create_pull_request` and
`merge_pull_request` (merge, squash or rebase). Each is a granted operation and
each write needs per-call approval unless autonomous execution was authorized.
`read_content` takes an optional `ref` (branch, tag or commit), returns a text file's decoded `text`, marks a binary file
`binary: true`, and lists a directory's entries.
`read_content` and `write_content` are the only
operations whose declared path field may contain `/`; empty, `.` and `..`
segments are refused, as they are for every other placeholder.

OAuth lifecycle is opt-in via `CONCLAVE_INTEGRATION_OAUTH_FILE`; see
[OAuth setup and limitations](INTEGRATIONS.md#oauth-lifecycle-update).
Authorization begin/callback are authenticated POST endpoints. Tokens are
never returned in API responses. Refresh runs server-side before expiry;
provider revocation is separate from ordinary local disconnect.

## Inbound triggers and tool approvals

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/hooks/{connection_id}` | A signed inbound delivery starts one authorized run |
| GET | `/api/runs/{id}/tool-approvals` | Pending external actions for this run; a preview is summarised (`detail: true`) |
| GET | `/api/runs/{id}/tool-approvals/{approval_id}` | One pending action in full: its arguments and whole preview; owner-bound |
| POST | `/api/runs/{id}/tool-approvals` | `{approval_id,approved}`; one-shot, owner-bound |

`/api/hooks/` is exempt from `CONCLAVE_API_TOKEN` only when `CONCLAVE_HOOKS=1`
configures a hook resolver, because the signature is its own authentication.
Without that resolver the path stays token-protected and refuses every caller.
Approvals live in process memory, expire after five minutes and never replay;
arguments are shown to the authenticated run owner and are not persisted.

## Goal refinement

`POST /api/goals/refine` takes `{goal}` (1–4000 characters) and returns
`{goal, summary}`: the person's own goal reworded, plus a short note of what was
made clearer. It reads nothing else, creates nothing, saves nothing and runs
nothing. The result is a suggestion the person edits and accepts in Studio; it is
never applied for them. The request shares the per-tenant proposal lock, so one
model call runs at a time, and it uses the planning timeout on the hosted proxy.
Provider failures return one generic message; provider text is never forwarded.

## Why a run stopped

`ApplicationFinished` carries the `status` and a `reason`. A run cut short by a
member's own limits is `stopped`, and the reason names the member, the limit and
the stage that was never completed — `market_researcher: used all 12 action steps
without completing Research opportunities` — rather than reporting only that a
stage did not complete. A step that could not do its task adds `"blocker"` to
its final answer; its output is kept, the steps after it do not run, and the run
is `failed` with the reason `<member> is blocked: <blocker>`. A member with
tools that reports a blocker is asked once, in the same conversation, to repeat
it if it holds or to continue; a blocker it repeats stands. The limits
themselves are the application's:
`run_config.max_tool_steps` ("Agent action steps" in Setup) bounds the actions one
member may take in a stage before it must answer, and `run_budget.*` bounds the
whole run.

An agent with one action left is told so, in the observation it is given: a budget
that ends without an answer produces nothing, so the last action is spent on the
answer rather than on work that cannot be finished. It is told where it stands
after every action (`Action 3 of 20.`), and its instructions name the budget before
any of it is spent, because an agent that cannot see its budget cannot plan with
it. A run with no action limit is told nothing about one. When the action budget is
spent anyway, the agent is asked once more for the answer, with nothing left to
offer — no tools, no delegations — and that answer completes the stage. The call
is charged like any other, and `AgentResult.exhausted_by` names the setting that
was spent either way, so a stage written up after its budget ran out is not
mistaken for one that finished early. `run_budget.wall_clock_seconds` is
absolute: it is not exceeded to rescue a stage, which is why a new application
does not start with one.

The runtime's own default for every dimension is no limit, and so are the
bundled application defaults (`conclave/application/data/defaults.json`,
replaceable with `DEFAULTS_FILE`): no cap on action steps, tool calls,
delegations or time. They bound only the size of each tool response and note,
and how many steps run at once.

## What runs each step

A member, or a single step, may name the backend and model that run it; a step
that names neither runs on its member's, and one whose member names neither runs
on the host's own default. `StageStarted` carries the `provider` and `model` the
run actually used for that step, which is empty when the host's default ran it —
nothing is invented to fill that in, and a caller that needs a name asks
`/api/runtime` for the host's own.

## Steps that run side by side

A step starts as soon as every step it depends on has finished and one of the
run's slots is free. `run_config.fan_out_limit` ("Steps at once" in Studio) sets
how many steps work at once; `null` means as many as the host allows, which is
`CONCLAVE_MAX_PARALLEL_STEPS` (a whole number from 1 to 64, 8 by default;
`/api/runtime` reports it as `max_parallel_steps`). A limit above the host's is
held to the host's.

- **The results keep the plan's order.** Outputs join the shared results
  strictly in plan order, so for the same answers the results go through exactly
  the changes they go through one step at a time. A step's own input is only the
  output of the steps it depends on.
- **`StageFinished`** is recorded when a step ends: `{stage, agent, status}` with
  `status` `completed`, `failed` or `stopped`. A completed step is `held: true`
  when it finished before an earlier step and waits for its turn to join the
  results (its dependents already have it); only then does it carry its
  `patch`, since `ApplicationStateChanged` carries it once it joins; and
  `cut_short: "tokens" | "wall_clock_seconds" | "cost_usd"` when a run-wide
  limit ended its work and it wrote up what it had. A failed or stopped step
  carries its `reason`. `ApplicationStateChanged` still marks each output
  joining the results, in plan order.
- **A checkpoint is saved after every finished step**, held ones included, so a
  run that is interrupted, paused or stopped never asks a finished step again.
  Only the newest is kept in the run's record: each holds every output so far,
  and it is the only one read.
  A checkpoint must hold every prerequisite of each step in it; checkpoints
  saved by earlier versions still load.
- **Pause:** once a pause is seen, no new step starts, the steps working finish,
  and the run is `paused` with `next_stage` (the first step not started).
  `PauseRequested {running: [...]}` is recorded when the run sees the pause.
- **A review** that becomes ready starts nothing after it in plan order, and is
  asked once nothing is running.
- **A failure or a stop** in one step does not cancel the steps beside it: they
  finish and keep their work, and then the run ends with the most serious
  outcome (failed, then stopped, then a run-wide limit, then paused), naming the
  first step in plan order that had it. A failed run adds every finished step's
  output to its saved results.
- **Run-wide limits are stops.** Once `run_budget.tokens`,
  `run_budget.wall_clock_seconds` or `run_budget.cost_usd` is reached, no new
  step starts, and a step already working makes no further model call except
  one write-up of what it has. Before a further call, a step also weighs it
  against the token and cost limits, counting every call already under way as
  large as the largest call so far, and writes up instead of a call that would
  likely pass the limit. So spend past the limit is, at most, what the calls
  already under way spend, plus one write-up per working step. If steps were left unstarted, the
  run is `stopped` with `budget: "<dimension>"` and a reason such as "Stopped:
  the run reached its token limit (50,000)". `wall_clock_seconds` adds up the
  time of every step, so two steps working together for 10 seconds count 20.

## Continuing a run

A run's record carries `resumable`: true when it stopped in a way that can be
continued — a waiting review, a pause, or a per-step budget stop that recorded a
checkpoint. A run stopped by a run-wide limit (`budget` on its
`ApplicationFinished`) is not resumable: the limit is part of the saved run, and
`POST /api/runs/<id>/resume` answers that it is fixed for this run. The
checkpoint itself stays on the host; the flag is what a caller needs to offer
the choice.

A backend that did not answer in time, or could not be reached, **stops** a run
rather than failing it: that is a condition of the moment, not a defect in the
application, so the run records a checkpoint and can be continued. The timeout is
the host's own (`CONCLAVE_MODEL_TIMEOUT`, 300 seconds by default) and applies to
one call, which is why a slow backend can also overshoot a wall-clock budget
between checks. A backend that failed, reported an error or returned an unreadable
reply is a failure, and a failure is not continued.

**Automatic retry.** Such a stop carries `transient` (`timeout`, `unreachable`
or `rate_limited`) on its `ApplicationFinished`, and the host continues it by
itself: it records `RetryScheduled {attempt, attempts, delay_seconds,
retry_at, reason}` and resumes the run at `retry_at`, from its checkpoint. The
delay starts at `CONCLAVE_RETRY_BASE_SECONDS` (30) and doubles up to
`CONCLAVE_RETRY_MAX_SECONDS` (900), for at most `CONCLAVE_RETRY_ATTEMPTS` (5;
`0` turns it off) tries in a row; a step that completes starts the count again.
Nothing else is retried: a failure, a run-wide limit, a pause, or any action
whose outcome is uncertain waits for a person. While a retry is pending the
record's `retry` is `{at, attempt, attempts, reason}` (otherwise `null`).
`POST /api/runs/<id>/resume` continues at once; `POST /api/runs/<id>/retry/cancel`
("Don't retry") and a pause record `RetryCancelled {reason, by}` and leave the run
stopped and resumable; `cancel` cancels both. A serving host arms again the
retries a previous host recorded (`conclave serve`); `conclave run` and
`conclave resume` wait through them. A retry acts as the run's owner only while
the deployment still admits them (otherwise `RetryCancelled` with reason
`owner_not_admitted`), and a scheduled run's retry still refuses writes nobody
authorized up front.

## Revision rounds

A human step whose output is a `decision` collection may answer
`changes_requested`. The run then does not stop: the steps the review revises
run again with the reviewer's reason, and the review is asked again as a new
round. The review revises the AI steps named in its optional `revises` (earlier
AI steps it depends on), or else the AI steps that feed it directly. Every
finished step that depends on a revised step runs again too, and the shared
results are rebuilt without them. A revised step is given the reason and its own
previous output, marked as data. The run records `RevisionRequested {stage,
round, revises, reset, comment}` with a checkpoint, so a round survives a restart
or a stop; the answered review becomes `revised` and the next one carries
`revision: {round, max, history}` and its `round` in `GET /api/runs/<id>/reviews`.
`max_revisions` on the human step (0–100, blank for no limit) bounds the rounds (a reason longer than 200,000 characters is kept shortened,
marked as such, in the checkpoint; the review record keeps all of it);
reruns are charged to the run budget like any step. `rejected` stops the run, as
does `changes_requested` with no rounds left or no earlier AI step to revise.

## Runs started by an event

An application whose start method is `event` carries `event: {task,
allow_task_override?}`: the task each event runs, and whether an event's own
`task` field may replace it. `POST /api/applications/{id}/event {action:
"create"}` gives the saved application its own incoming address
(`/api/events/ev_…`) and signing secret (`ces_…`), answered **this once**;
`{action: "rotate"}` replaces the secret at once, keeping the address. Whoever
turns events on is the owner the runs act as; the secret is sealed with the host
key when there is one. `GET /api/applications/{id}/event` answers whether events
are on, the address, the owner and the latest 100 events received, never the
secret. Changing the start method turns the address off (404) until it is set
back.

`POST /api/events/{endpoint}` is signed like a hook: `x-conclave-timestamp` and
`x-conclave-signature` = hex HMAC-SHA256(secret, `timestamp.` + body), within
300 seconds, constant-time. It needs no token or session, and on `conclave
serve` it is the one path a forwarded address may reach. An event is a JSON
object of at most 100 KB; its top-level fields matching the application's
declared request fields become the run's inputs (a missing required one is
refused: "Missing required field “title”"), and it starts one run with `trigger:
{kind: "event", event_id, received_at}`, acting as the owner with writes not
authorized up front refused. The event is identified by `x-conclave-event-id`,
else its `event_id`, else `sha256:` of the body; the same event within 7 days
starts nothing and answers `200 {duplicate: true, run_id}` with the first run
(durable, across restarts and workers). Answers: `201 {run_id, event_id}`, `401`
for a signature that does not hold (not logged), `404`, `413`, `400`/`403` with
the reason an event was refused (it holds no claim, so a corrected resend
starts a run), and `429` when 10 runs started by events are still going.

Whoever turns events on, rotates the secret, or saves a change to what runs (not
card positions) while events are on becomes the owner the runs act as, as with
a schedule; each change of owner is in the log. A signed request is accepted
once: its signature is claimed with the event, so a copy resent with another
`x-conclave-event-id` answers as a duplicate. A body declared over 100 KB is
refused before it is read. Each process keeps an address's lookup for 30
seconds to answer unsigned requests cheaply, and checks a request that passes
against the address as it is now, so a rotated-out secret or a former owner is
never acted on; another process may refuse a new secret, or an address just
switched on, for up to those 30 seconds. `allow_task_override` lets whoever holds
the secret tell the team what to do with the owner's accounts; it is off unless
chosen. Events reach the deployment's own tenant: a deployment serving several
tenants from one address cannot route events to the others.

## Application versions

Every saved change to what an application does is kept as a version, with who
saved it (the signed-in principal) and when; a save that only moves cards updates
the current version's layout and makes none. `GET
/api/applications/{id}/versions` lists them newest first: `version`,
`saved_at`, `saved_by`, `restored_from`, `recovered_from_run`, `current`, how
many `runs` used it, and a one-line `summary` of what changed from the previous
kept version. `GET …/versions/{n}` is one version with its definition, and `GET
…/versions/compare?from=a&to=b` is what changed between two, grouped by area
(Application, Team, Steps, Connections, Results, Views, Request form, Limits and
approvals, Review policy, Schedule, Notifications, Connectors, Requirements, Other
settings): each change is `added`, `removed` or `changed`, in words, with
`subject`, `was` and `now` where it has values; card positions are
`layout_changed`; at most 200 changes are listed (`count`, `truncated`).

`POST …/versions/{n}/restore {revision}` saves version n's content and layout as
a new version (`restored_from: n`), through every check a save has; `409` when
the application changed since `revision`, `400` when it already does what
version n does. Runs keep the version they started with; a run's record names it
(`version: {number, application_id, link}`). A store from before versions were
kept starts from each application's current version, and older versions come
back from the runs that stored them (`recovered_from_run`); nothing is made up
for a version no run kept. Deleting an application deletes its versions.

History is kept with secret-looking values redacted (`Bearer …`, `sk-…`,
`api_key=…`, `password=…`, as step-test captures are), so a credential pasted
into an application and then removed is not kept or restored; the application's
current definition is as saved. A version is recovered only from a run whose
stored definition this host accepts, and says who started that run
(`recovered_started_by`); restoring one needs `{"revision": n, "recovered":
true}`, because Conclave cannot confirm it was ever saved here. Studio names the
changes a restore makes to what keeps a team in check (who may answer reviews,
limits and autonomous execution, conditions, members' tools, connectors) before
it is confirmed. Every saved change is kept, so history grows with each save;
there is no retention limit.

## Who may answer reviews

A definition may carry `review_policy: {reviewers?, allow_starter?}`:
`reviewers` lists 1–50 different people as they sign in, or whole domains
(`@acme.com`), compared without case; leaving it out lets anyone who can open
the run answer. `allow_starter` (default `false` once a policy exists) says
whether the person who started the run — for a scheduled run, the schedule's
owner — may answer its reviews. A human step may set `answers_required` (1–50;
above 1 needs a decision output, and with only named reviewers no more than the
people named). An application without a policy behaves as before.

The host enforces this where every answer is recorded, whoever submits it:
`POST /api/runs/<id>/reviews` answers `400` with the reason — "You started this
run, so someone else must answer its review", "Only ana@acme.com or anyone at
@ops.acme.com may answer this review", "You already answered this review; it
needs a different person". Each answer is kept (`reviews[].answers`: reviewer,
output, time; `reviews[].required`) and recorded as `HumanReviewSubmitted
{answers, required, complete}`. A review completes when `answers_required`
different people have answered, or at once on the first rejection; its output is
every answer's records together. Any rejection stops the run, any request for
changes starts a revision with every comment (each round needs its answers
again), and otherwise the run goes on — for an application with a policy, by
itself, as its owner, unless an action's outcome is still uncertain. A run's
record carries `started_by` and, for the person asking, `can_answer: {allowed,
reason, completes?}`. Where connections make runs their owner's, a person a
waiting review is for may also list and open that run and answer it, and
nothing else; once nothing waits for them, it is the owner's alone again — a
stream they opened ends then too. An automatic continue acts only as an owner
the deployment still admits (otherwise `ContinueWithheld {reason:
"owner_not_admitted"}`), keeps a scheduled run's writes refused, and never turns
a recorded answer into an error if it cannot start.

**Limits.** These rules are as strong as the names the host records. Hosted,
names come from a verified sign-in; on a local host they are whoever the
operating system or `CONCLAVE_PRINCIPAL` says, so anyone at that computer can
answer under another name (Studio says so beside a policy). There are no
per-application edit permissions yet: whoever can save an application can change
its policy for later runs. A run keeps the policy of the version it started
with, and its review shows which one.

## Conditional steps

A step may carry `condition: {step, field, operator, value}`: it runs only when
the output of `step` — one of the steps it depends on — satisfies it. `field` is
`text` for a text output, or `status` or a declared record field; `operator` is
`equals`, `not_equals`, `in`, `not_in` (with a list of 1–20 values) or
`contains`; values are nonblank text of at most 200 characters, and an enum
field's or status's values must be among its options. Comparisons ignore case
and surrounding spaces, numbers and booleans compare by their text, and records
hold when any one of them matches. No output, or no record with the field, does
not hold.

When a step's prerequisites are done, it is **skipped** instead of started if its
condition does not hold, if the step its condition checks was skipped, or if
every step leading to it was skipped: `StageSkipped {stage, agent, reason,
condition?}`, with the reason in words ("Triage the issue: type was “feature”,
not bug or regression"). A skipped step has no output, costs no budget and asks
nobody; it counts as done, so a step joining a skipped branch with one that ran
still runs, and the run completes. A skipped **human** step is the exception:
nothing after a review that did not happen runs, even where another branch joins
it, because a condition is decided by a model's output and must never route work
around a person (Studio warns before a run when a condition decides a review). The checkpoint's `skipped` lists them, so a
resumed run does not decide again; a revision that resets a step asks again.
A step test runs the step it tests whatever its condition says, and reports
`condition: {holds, reason}` for the pinned inputs.

## Source checks

Each record's `source_checks` lists, one per `source_refs` entry, whether the
step that wrote it read what the source names: `found`, `not_found` or
`not_checkable`. Evidence is the step's successful calls, including delegated
members': files read with `read_file` or matched by `search_files`, pages
fetched with `web_fetch`, issues and pull requests opened with `github_issue`
or `github_pr` (by number and by their URL), commits shown with `git_show`, and
the IDs of records the step was given. A link seen only in `web_search`
results, or a file only in `list_files`, is not found. Free text, a person's
citations, and anything unmatched by a step that also read through a tool whose
reads cannot be interpreted (a connection, an extension) are not checkable.
URLs compare without case in scheme and host, default port, fragment or trailing
slash; paths compare without `./`, a leading `/`, `#…` or `:line` anchors.
`source_checks` is reserved: an agent cannot supply it. Tool output is read
while the step runs and is never recorded. Records from earlier runs carry no
`source_checks`.

A host taking over its store marks any run left `running` by a process that is
gone as `interrupted`, with the reason recorded. Every external action it was
still sending becomes **uncertain** (an `ActionUncertain` event names it): it may
or may not have happened, so it is never sent again without a check or a person.

`POST /api/runs/<id>/resume` continues from the last stage that completed, and a
stage that had already sent something continues from its last recorded action
instead of starting over. Completed writes are never sent again. Before claiming
the run, the host checks each uncertain action with its service where it can
(found: done; proved absent by a reliable read of an approved call: sent once
more); anything still uncertain is answered `409 {error, actions}` and waits for
`POST /api/runs/<id>/actions` — `done` (with an optional note, passed to the
agent as the person's words) or `resend` (which is the approval for that one
call). Either can be undone until the run continues. Completed stages and the
budget already spent are restored, so nothing finished is repeated and no spend
is forgotten — which also means a run stopped on a limit needs that limit raised
before resuming is useful. A `failed` or `cancelled` run is not resumable; an
action it left uncertain is listed as an unknown outcome. Runs recorded before
the action journal existed cannot be continued after an interruption.

## What a run used

`GET /api/runs/<id>` carries `usage`, added up from the run's own events:

```json
{"recorded": true,
 "steps": {"research": {"model_calls": 7, "measured_calls": 7, "member_calls": 0, "input_tokens": 21400,
                        "output_tokens": 3100, "tool_calls": 12, "tool_failures": 1, "tool_denied": 0,
                        "seconds": 108.2, "runs": 1, "unmeasured": []}},
 "total": {"model_calls": 14, "measured_calls": 12, "tokens": 41200, "tool_calls": 18, "seconds": 255.0, "cost_usd": null, "...": 0},
 "unmeasured_backends": ["opencode"],
 "limits": {"tokens": {"limit": 50000, "used": 41200, "warn_at": 40000}},
 "warnings": [{"dimension": "tokens", "used": 41200, "threshold": 40000, "limit": 50000, "automatic": true, "stage": "draft"}]}
```

Each model call is recorded as a `ModelCalled` event — the member, backend,
model, the tokens the backend measured (`null` where it measures none) and how
long it took; never the prompt or the reply. An output-repair call carries
`repair: true`. Every `StageFinished` carries the step's `usage`: its model
calls (`member_calls` are those of members it asked), tokens, tool calls
(`tool_failures` failed, `tool_denied` were refused) and working seconds. A step
that ran more than once — a revision, a retry — adds up every time it ran, and
`runs` says how many. Tokens are totalled only where measured: `measured_calls`
below `model_calls` means the total is a lower bound, and `unmeasured` names the
backends that measured nothing. Cost is `null` unless a backend reports it.

A limit warns once per run, resumes included, with a `BudgetWarning` event
(`dimension`, `used`, `threshold`, `limit`, `automatic`). The threshold is the
application's own in `budget_warnings`, or 80% of the `run_budget` limit;
`budget_warnings.<dimension>: null` turns that warning off, and a dimension with
no limit does not warn. Runs recorded before steps carried their usage have
`recorded: false`, no `steps`, and only the run-wide totals they kept. The
runtime reports `features.run_usage`.

Per-step counts come from the run's events. For an agent loop running behind a
boundary (a sandbox), those are what the loop reported: bounded (names up to 200
characters, at most 10¹² tokens and a day per call) but not re-measured. The
limit bars, warnings and the limits themselves use the host's own accounting —
the calls it answered and the tokens its backends measured — so a loop cannot
misreport its way past a limit.

## Comparing two runs

`GET /api/runs/compare?a=<run>&b=<run>` compares two runs of the same
application. The earlier run is A; `order=given` keeps the order asked for
(Studio's Swap). Runs of different applications, or a run with itself, are
`400`; a run that does not exist or cannot be opened is `404`. Only whoever may
open both runs may compare them: the access a waiting review gives its reviewer
is not enough.

```json
{"application_id": "release", "name": "Release notes",
 "runs": {"a": {"id": "…", "created_at": "…", "status": "completed", "version": 5, "started_by": "ana@acme.com",
                "trigger": {"kind": "schedule"}, "task": "…", "in_progress": false}, "b": {"...": "…"}},
 "version": {"a": 5, "b": 7, "same": false, "changes": {"count": 3, "areas": ["…"]},
             "link": "/api/applications/release/versions/compare?from=5&to=7"},
 "request": {"task": {"a": "…", "b": "…"}, "inputs": [{"key": "milestone", "label": "Milestone", "a": "4.1", "b": "4.2"}]},
 "steps": [{"id": "draft", "name": "Draft the notes", "names": {"a": "…", "b": "…"}, "agent": "Writer", "kind": "ai",
            "presence": "both", "status": {"a": {"status": "completed", "reason": ""}, "b": {"status": "skipped", "reason": "…"}},
            "output": {"kind": "text", "lines": [{"kind": "del", "text": "…"}, {"kind": "add", "text": "…"}], "more": 0},
            "review": null, "usage": {"a": {"model_calls": 5, "tokens": 13700, "tool_calls": 6, "seconds": 100, "measured": true},
                                      "b": {"...": 0}, "delta": {"tokens": 3000, "tokens_percent": 22, "...": 0}},
            "tags": [{"kind": "output_changed", "text": "Output changed"}], "differs": true}],
 "summary": {"steps": 7, "differing": 4, "token_change": 18, "text": "4 of 7 steps differ: …"}}
```

Steps are matched by id, in B's plan order, then steps only A's version had
(`presence` `only_a`/`only_b`, status `not_in_version`). A step's `status` is
`completed`, `skipped` (with the routing reason), `failed`, `stopped`,
`running`, `waiting`, `not_reached` or `not_in_version`; a step a reviewer sent
back counts from its last round. `output` compares each step's own final output:
text as a line diff with two lines of context and counted gaps (`gap`), up to
400 lines — when what differs between the shared start and end is too large to
weigh line by line (over 250,000 line pairs for one step, or 1,000,000 for the
whole comparison), it is one changed block and `coarse` is true; a line longer
than 1,000 characters is cut short (`shortened`); records by id — `added`, `removed`, or `changed` with the fields that
changed, with was/now values of at most 80 characters (never a body) — up to 200,
with `more` counting the rest. Which run wrote a record and its source checks are
not compared. A person's step has `review` for each run: decision, who answered,
rounds, whether changes were asked for first (`first_comment`) and the last
comment, shortened to 280 characters. `usage` comes from each run's recorded
usage (`null` for a run recorded before it was kept). `differs` is true when the
step ran in one version only, ended differently, produced a different output or
its review went differently; using more or fewer tokens is tagged (`usage`,
from a 10% change) but is not a difference in outcome. A run not yet finished is
compared as it stands (`in_progress`). The comparison is built from the records
the caller may read and never includes a checkpoint. The runtime reports
`features.run_compare`.

## Stored output files

A step whose output is a **document**, **dataset** or **artifact** leaves a real
file, made by the host from the step's validated output when it joins the
results: a document as Markdown (`.md`, the text as written), a dataset as CSV
(`.csv`: `id, title, body, status, sources`, then each field by its label) and an
artifact as JSON (`.json`: the records, without `contributions`). An output's
`file` setting chooses another format its kind allows — `{"format": "json"}` for
a dataset — or `false` for none; other kinds have no file. The name comes from
the output's label (lowercase letters, digits and hyphens, at most 60
characters); the model names no file, path or link. In CSV, text that a
spreadsheet would run as a formula (`= + - @`, tab, carriage return) is prefixed
with `'`. A file over 10 MB is not stored: the run records `OutputFileSkipped`
with the reason and continues; so does a file that cannot be written (a full
disk, a folder it may not write), recorded as "The file could not be written"
while the details go to the host's log.

Files live beside the run database under `run-files/<run>/<id>` (the folder
`0700`, each file `0600`), written to a temporary name and renamed, and are
recorded in the same transaction as an `OutputFileStored` event (`stage`,
`output`, `file_id`, `name`, `format`, `size`, `round`); a file that could not be
recorded is removed. Each time a step runs — a revision round, a retry — it
stores a new file; `round` counts them. A person's step stores its file too; a
step test stores none.

The run record lists `files`: `id`, `stage`, `output`, `round`, `name`,
`format`, `media_type`, `size`, `created_at`, `preview` (the first 20 lines, at
most 2,000 characters), `lines`, `link` and `available` (the file is there at the
size stored). `GET /api/runs/<run>/files/<file>` is answered for whoever may open
the run, only for that run's own files, and only after the file's size and
SHA-256 match what was stored: `200` with `content-type`, `content-disposition:
attachment; filename="…"`, `x-content-type-options: nosniff` and
`cache-control: no-store`; `404` for a file the run does not have; `410` when it
is missing or was changed. The hash is never published. Runs from before files
were stored have none. The runtime reports `features.output_files`.

Files are kept as long as their run is: there is no quota or clean-up across
runs yet, so a deployment that keeps many runs should watch the size of
`run-files/`. In CSV, header cells (field labels) are guarded against formulas
like every other cell.

## A step's time limit

A plan step may set `time_limit_seconds`: a whole number from 10 to 86,400 (a
day). A person's step may not — it waits for the person. The limit bounds the
step's real working time: its model calls, tool calls, the members it asks and
any output repair; time spent waiting for someone to approve one of its actions
does not count. When the step has worked that long it is stopped where it is:
the model call under way is abandoned, a tool call under way is cancelled, and
a loop running elsewhere is closed. Nothing it had half-written is kept, and
what it spent is recorded. A write that was being sent when it stopped becomes
uncertain, as any interrupted write does, and is checked before the run goes on.

The step's `StageFinished` has `status: "timed_out"`, the `reason` ("“Draft”
ran past its time limit (5 m)"), `time_limit_seconds`, `elapsed_seconds` and its
`usage`. Steps already running beside it finish; then the run ends `stopped`,
with `timed_out: true`, the `stage`, `time_limit_seconds` and a checkpoint, and
`resumable` is true. A timeout is not a backend fault, so no retry is scheduled;
Continue (`POST /api/runs/<id>/resume`) runs the step again with its full limit,
and so does each revision round. It is independent of the run-wide
`wall_clock_seconds` limit. A command a step's tool was running (git, a test
run) runs in a process group of its own, and the group is killed with the step,
so nothing it started keeps working after it stopped. For an agent loop running
behind a boundary, approvals are answered outside the step's own task, so the
clock does not pause for them: a sandboxed step waiting long for a person may
time out. A version comparison says "Step “Draft”: time limit
was 5 m, now 10 m"; a run comparison gives the step status `timed_out`. The
runtime reports `features.step_time_limits`.

## People and roles

Each application names its people, each with one role, and every application and
run route checks it. Roles are ranked; each includes everything below it:

| Role | May |
| --- | --- |
| `viewer` | open the application, its versions, schedule and notification status, its runs, their events, reviews, files and comparisons, and who has access |
| `runner` | start, continue, pause and cancel runs, stop a pending retry, decide uncertain actions, answer tool approvals and reviews |
| `editor` | save the plan, restore a version, change the schedule, see the event setup, preview its notifications, and use saved examples and step tests |
| `owner` | add people and change roles, turn events on and rotate their secret, and delete it |

Someone with no role on an application is answered as if it did not exist
(`404`), for the application and for each of its runs; someone whose role is too
low is told why (`403`, "Your role on this application is viewer; running it
needs runner"). `GET /api/applications` lists only what the caller may open, each
with their `role` and whether it is `open`. A review policy's named reviewers may
answer the review they are named for without a role, as before.

Whoever creates an application is its owner. An application that names nobody —
saved before roles existed — is **open**: everyone in the tenant has owner
access, and `GET …/access` says `open: true`. Limiting it starts with the caller
as owner (`POST …/access` with their own name and `owner`); after that only its
people see it. An application always has an owner: the last one cannot be
removed or made anything less. Anyone may remove themselves. People are matched
whatever the capitals of their sign-in name (`Ana@Acme.com` is `ana@acme.com`),
so nobody is listed twice; an application names at most 200 people. Every change
is kept (`log`: when, by whom, to whom, and what; the latest 500). Deleting an application removes its
people; its runs stay with whoever started them (and runs that name nobody stay
as open as they were).

Work that acts as someone needs them to still be able to run the application: a
schedule whose owner can no longer run it records why and waits for someone to
turn it on; an event whose owner can no longer run it is refused and logged; a
hook connection's owner must be able to run it; and a run is not continued after
a review on behalf of an owner who can no longer run it (`ContinueWithheld`,
`owner_lacks_role`). Tenant-wide routes — the member library, catalogue,
proposals, validation, settings, connections, runtime and `/api/me` — are not
about one application and are unchanged. The runtime reports
`features.application_roles`.

## Telling someone a run waits

A definition may carry `notifications: {waiting: {via, connection, ...}}`:
`via` is `slack` (with `channel`), `email` (with one `address`) or `webhook`
(nothing more: the connection's URL), and `connection` is the id of one of the
owner's connections, granted that service's send operation for this
application. Unknown keys are refused, as is a field of another channel.

- **One notice per waiting point.** When a run stops with `waiting_for_human`
  or `paused`, a notice is recorded in the same transaction as that event. The
  host claims it before sending, so it is sent at most once; a host that
  stopped during a send records `NotificationUncertain` when the store is next
  opened and never sends it again. A notice that was never attempted is sent at
  start-up if the run still waits there. A run that resumes and waits again has
  a new waiting point.
- **Sent after the run has stopped**, through `ConnectionStore.invoke` (grants,
  scopes, pacing and redaction apply), as the notification owner. It never
  fails or holds up the run. The outcome is an event on the run:
  `NotificationSent`, `NotificationFailed` (with a short `reason`) or
  `NotificationUncertain`, each with `waiting_sequence`, `via`, `target`,
  `connection`, `sent_as`, `step` and `waiting` (`review` or `pause`).
- **The message** is the application's name, the step that waits (for a pause,
  the step it stopped before, which the paused event names as `next_stage`)
  and `{CONCLAVE_STUDIO_URL}/?application=…&run=…`. No task, inputs, outputs,
  state or credentials. Slack text is escaped, the email is built so a name
  cannot add headers, and a webhook gets `{type: "run.waiting", reason,
  application, step, run_id, waiting_since, link}` with `Idempotency-Key:
  notice-<run>-<event>`.
- **Limits.** A run that waits again within `CONCLAVE_NOTIFY_RUN_INTERVAL`
  seconds (600) of its last message is not told again, and an application
  sends at most `CONCLAVE_NOTIFY_HOURLY_LIMIT` messages an hour (20). What is
  not sent is a `NotificationFailed` whose reason begins "Not sent:".
- **The owner** is whoever last saved a change to what runs (a save that only
  moves cards does not count). A run is told as the owner **when it was
  created** — whoever last changed the words it quotes — never as whoever owns
  the notifications later. The status endpoint gives the owner the full reason
  a message cannot be sent; anyone else sees host-wide reasons, and otherwise
  "… {owner} can see why", and a webhook connection is named by its id. Adding or changing the setting must name one of
  your own connections of that service (400 otherwise); a save that leaves it
  alone is never refused for it, and the status then says why nothing can be
  sent. A definition run from a file, with no owner recorded, sends as the
  person who started the run.
- **Runtime:** `features.notifications` (the host has connections) and
  `features.studio_url` (a link can be made). `CONCLAVE_STUDIO_URL` must be an
  http(s) address with no query or fragment; locally `conclave serve` uses its
  own address when it is unset.

## Scheduled applications

A definition with `start_mode: "schedule"` carries a `schedule` block — `kind`
(`interval` with `every_minutes` 5–10080, or `daily`/`weekly` with 1–24
`times` as `HH:MM` and, for weekly, `days`), an IANA `time_zone`, an optional
local `starts_at`, `catch_up` (`skip` or `latest`) and the `request` each run
starts with (`task`, `inputs` checked against the input fields, an optional
named `resources.source`). The block is refused for any other start mode, and
`start_mode: "schedule"` is refused when saved without it. The same rules are
enforced by Studio before it sends anything.

`conclave serve` (local and `--hosted`) runs a scheduler for the host, and
`GET /api/runtime` reports `features.schedules: true` only when one is running.
When a time comes, the host starts one run of the **latest saved revision** with
the schedule's request, marked `trigger: {kind: "schedule", scheduled_for}`:

- **Once per time.** The run and the record of its time are written in one
  transaction, so a restart or a second process on the same store never starts
  it twice.
- **No overlap.** If the previous scheduled run is still going (including one
  waiting for a person, or stopped with an uncertain action), the time is
  recorded as `skipped`.
- **Missed times.** Times more than two minutes late — the host was off — are
  recorded as one `missed` entry with their count and range, and not run. With
  `catch_up: "latest"`, the most recent one runs instead.
- **Failed times.** Saved inputs that no longer match the application's fields,
  or a host with no model backend, record the time as `failed` with the reason;
  the schedule carries on.
- **Unattended.** Nobody can approve a write, so writes are refused unless the
  application has autonomous execution on. Runs act as the schedule's
  **owner**, under their connections and grants at run time. The owner is
  whoever last saved a change to what the application runs (a save that only
  moves cards does not count), so an edit never borrows someone else's
  accounts. A hosted schedule with no owner waits for `turn_on`.
- **Owners who have left.** With Google sign-in, a time whose owner the login
  file no longer admits is recorded as `failed`, and the schedule waits for a
  current member to `turn_on` it.
- **The same checks as a person's start.** Each time is checked as a manual
  start is — tools, backends, host connectors, and the source and its version —
  acting as the owner. A refusal is a `failed` time with the reason; no run is
  created.

Changing the timing of a saved schedule reschedules it from the save; changing
only its request does not. Pausing and resuming are host state, not a new
revision, and times passed while paused are not reported as missed.

## A run's events

`GET /api/runs/<id>/events` streams the run's record as server-sent events, one
frame per change, and ends when the run does. Every recorded event carries
`timestamp`, set on the host's clock when it was recorded, so a page can say how
long a step has been working without keeping its own clock — a reload does not
restart it, and the answer describes the run rather than the reader.

A deployment's adapter streams these frames through whatever serves it. This
machine's own server writes each frame to the socket as the adapter produces it:
a server that collected the response first would hold the stream until the run
ended, and a page watching a step work would sit on the snapshot it started with,
with no error to fall back on.

## Questions about a goal

`POST /api/goals/questions` takes `{goal}` (1–4000 characters) and optionally
`{requirements}` — the questions asked so far with the answers given, the same
shape a definition holds — and returns `{questions}`: **at most one** question,
the next one worth asking. Each question follows the answers before it, and the
host returns none as soon as those answers settle what it would ask, so the
person is not asked for more than the host needs. It reads nothing else, creates
nothing, saves nothing and runs nothing, and it shares the same per-tenant lock
and timeout as refinement.

`CONCLAVE_MAX_GOAL_QUESTIONS` sets the host's limit (default 5, 1–20). At that
limit the host answers `{questions: []}` without calling the model at all, and a
model that returns more than one question, repeats a question already asked, or
answers with anything other than a list of questions is refused rather than
trimmed, so the person is never shown a question the host did not intend to ask.
A goal that is already clear is allowed to produce none.

Answering is optional. Studio asks after the goal is submitted, and a host that
cannot answer — no provider, a provider failure, an unusable reply — proposes
from the goal alone rather than standing between the person and their
application. The answers the person gives are saved with the application as
`requirements`, a list of `{question, answer}` in their own words; an unanswered
question is kept with an empty answer so the plan can show it as open, and only
answered requirements are put in front of the agents when the application runs.
A definition without `requirements` is valid, so applications saved before this
existed keep working.

## Testing one step against a pinned fixture

A saved run's steps can be re-run on their own. The backend records each step's
effective input when it starts, so a completed step's real inputs and output can
be pinned as a named **fixture** and replayed later without re-running the DAG.

`POST /api/applications/{id}/fixtures/preview` reads one completed step's recorded
`effective_input` and its output patch from the run record, verifies that the
input still reconstructs exactly from the run's own patches, and returns a
redacted envelope. A step with no recorded snapshot is refused rather than
guessed at. Automatic redaction is conservative — it covers sensitive key names
and obvious credential shapes — so the preview is meant to be inspected and
edited before saving; a fixture is recorded data, never a live source.

`POST /api/applications/{id}/tests` runs the selected step (or, with
`scope: "downstream"`, the selected step and its descendants) on the pinned
inputs. It uses the same runtime, DAG merge rules and output contracts as an
ordinary run:

- Only the tested subgraph runs. Recorded ancestors are seeded from the fixture;
  unrelated branches and ancestors are never re-run, and a missing direct
  prerequisite is refused before any provider call.
- If the definition's state schema or connections changed since the fixture was
  recorded, or a step's output moved, the test is refused. A changed agent
  assignment is allowed with a recorded warning.
- A test never writes to the production run. It is stored separately, scoped to
  the application **and its owner**, and survives a restart.
- Tools run in a safe mode: every registered tool is forced destructive and a
  mock executor answers only the tools named in `mocks`, so an unlisted call is
  blocked instead of executed. Delegated agents use the same restricted
  registry. A test inherits **no** production approval.
- A finite `cost_usd` budget is refused, because the provider interface reports
  tokens and not cost; a finite wall-clock budget becomes a hard deadline on the
  whole test.

The result holds the test's own events (renamed `TestStarted`/`TestFinished` so a
test is never mistaken for a workflow run), its output patch, and a comparison:
a unified text diff for prose, per-record changes for records, and a value
comparison otherwise. The comparison reports exact data differences and does not
claim semantic equivalence.

A reproducible multi-step demonstration with parallel branches lives in
`tests/test_step_testing_api.py` (and the DAG mechanics in
`tests/test_step_testing.py`):

```sh
uv run pytest --no-cov tests/test_step_testing_api.py tests/test_step_testing.py -q
```

### Testing a step that has never run

A fixture does not have to come from a run. `POST
/api/applications/{id}/fixtures/author` writes one from values a person
supplies: the definition to test against (the draft, or the saved version), the
application's declared request inputs, and the output of every step the selected
step depends on. The fixture is stored with `source_run: null`,
`recorded: false` and `authored: true`, so nothing downstream can mistake it for
a run.

- A prerequisite with no value is **refused**, never filled in: the response
  names the steps still needed, and each supplied value is validated against the
  collection it feeds before anything is saved. A step with no prerequisites
  needs none, so a first step can be tested on its own.
- The state the tested step receives is computed by the backend from those
  prerequisite values and the definition's own merge rules, so the input the
  step is given is the same one the DAG would have built — it is not taken from
  the request.
- A reference output is optional. With `reference_recorded: true` the test
  compares against it; without it the test records no comparison at all, and
  `GET /api/applications/{id}/tests/{test_id}/compare` answers
  `{kind: "none", equal: null}` rather than reporting an inequality against
  nothing.
- Because the fixture belongs to the definition it was written against, a test
  started without a `definition` uses the fixture's own definition. A definition
  whose state schema or connections have since changed is still refused.
