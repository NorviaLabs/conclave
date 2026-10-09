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
| GET | `/api/runs?application_id={id}` | List recent runs for an application |
| POST | `/api/runs` | `{version:1, application_id, revision, task, inputs}`; execute the backend's saved snapshot |
| GET | `/api/runs/{id}` | Persisted status, actual results, metrics, events and reviews |
| GET | `/api/runs/{id}/events?after={sequence}` | Poll ordered events after a cursor; returns `next_cursor` and status |
| GET | `/api/runs/{id}/reviews` | List durable review requests and submitted contributions |
| POST | `/api/runs/{id}/reviews` | `{review_id, output}`; validate and persist a human contribution |
| POST | `/api/runs/{id}/resume` | `{}`; continue a waiting run after its contribution, or a paused run from its checkpoint |
| POST | `/api/runs/{id}/pause` | `{}`; ask a running run to stop **between steps** and keep a checkpoint |
| POST | `/api/runs/{id}/cancel` | `{}`; stop active, waiting or paused work |
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
| DELETE | `/api/connections/{id}` | Disconnect and remove encrypted local credentials |
| POST | `/api/connections/{id}/health` | `{}`; probe a safe granted read when possible |
| POST | `/api/integrations/recommend` | `{goal, application_id}`; suggestions, never grants |

Tool assignments use `connection.<id>.<operation>` in the existing agent `tools`
array. Each execution resolves the encrypted connection server-side and rechecks
ownership, tenant, application, operation, scopes and revocation. Mutations
remain WRITE capabilities, so no configured approver means denial. The current
Studio host does not provide a per-tool approval dialog; trusted Python callers
can use the existing approver callback. In-process run events include
`connection_id` and `operation` without credentials or raw arguments.

OAuth lifecycle is opt-in via `CONCLAVE_INTEGRATION_OAUTH_FILE`; see
[OAuth setup and limitations](INTEGRATIONS.md#oauth-lifecycle-update).
Authorization begin/callback are authenticated POST endpoints. Tokens are
never returned in API responses. Refresh runs server-side before expiry;
provider revocation is separate from ordinary local disconnect.

## Inbound triggers and tool approvals

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/hooks/{connection_id}` | A signed inbound delivery starts one authorized run |
| GET | `/api/runs/{id}/tool-approvals` | Pending external actions for this run |
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
stage did not complete. The limits themselves are the application's:
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

The runtime's own default for every dimension is no limit. The bundled
application defaults (`conclave/application/data/defaults.json`, replaceable with
`DEFAULTS_FILE`) bound the work rather than the clock: 12 action steps per stage,
40 tool calls per run, no time limit.

## What runs each step

A member, or a single step, may name the backend and model that run it; a step
that names neither runs on its member's, and one whose member names neither runs
on the host's own default. `StageStarted` carries the `provider` and `model` the
run actually used for that step, which is empty when the host's default ran it —
nothing is invented to fill that in, and a caller that needs a name asks
`/api/runtime` for the host's own.

## Continuing a run

A run's record carries `resumable`: true when it stopped in a way that can be
continued — a waiting review, a pause, or a budget stop that recorded a
checkpoint. The checkpoint itself stays on the host; the flag is what a caller
needs to offer the choice.

A backend that did not answer in time, or could not be reached, **stops** a run
rather than failing it: that is a condition of the moment, not a defect in the
application, so the run records a checkpoint and can be continued. The timeout is
the host's own (`CONCLAVE_MODEL_TIMEOUT`, 300 seconds by default) and applies to
one call, which is why a slow backend can also overshoot a wall-clock budget
between checks. A backend that failed, reported an error or returned an unreadable
reply is a failure, and a failure is not continued.

A host taking over its store marks any run left `running` by a process that is
gone as `interrupted`, with the reason recorded and deliberately without a
checkpoint: a run cut off mid-flight may have had an external action in it, so it
is reported rather than offered for replay.

`POST /api/runs/<id>/resume` continues from the last stage that completed.
Completed stages and the budget already spent are restored from the checkpoint,
so nothing finished is repeated and no spend is forgotten — which also means a
run stopped on a limit needs that limit raised before resuming is useful. A
half-finished stage is not restored: agent-internal recovery is unsupported, and
a call that was interrupted is never replayed. A `failed` run is not resumable at
all, because a failure is not a budget.

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
