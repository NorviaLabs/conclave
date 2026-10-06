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

From the repository root, start the backend in one terminal:

```sh
uv sync
uv run conclave serve --provider codex --port 8080
```

Start Studio in another terminal (Node 24+):

```sh
cd studio
CONCLAVE_API_URL=http://127.0.0.1:8080 npm run dev
```

The UI is at http://127.0.0.1:8787. On this checkout, the working Node executable
is `/tmp/conclave-studio-node/node_modules/node/bin/node`; it can invoke
`scripts/dev.mjs` directly if the system Node installation is unavailable.
Stopping Studio leaves Conclave running. Stopping Conclave closes its active
runs safely; inspect their saved status before retrying.

## Endpoints

All payloads and responses are JSON. The API contract version is 1. Every run
pins an immutable application definition and saved revision. Application IDs are
opaque strings; run IDs come from the service. Request bodies are limited to
200 KB. Responses expose actual events/results, without private checkpoints.

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

Provider and execution settings belong to Conclave: `CONCLAVE_PROVIDER`,
`CONCLAVE_MODEL`, `CONCLAVE_MODEL_TIMEOUT`, `CONCLAVE_OLLAMA_URL`,
`CONCLAVE_PROVIDER_FACTORY`, `CONCLAVE_SOURCE_DIR`, `CONCLAVE_SOURCE_EXTENSIONS`,
`CONCLAVE_WEB`, `CONCLAVE_EXTENSIONS`, `CONCLAVE_DEFAULTS_FILE`,
`CONCLAVE_CATALOGUE_FILE`, `CONCLAVE_FIXTURES_FILE`, `CONCLAVE_MEMBER_LIMIT`,
`CONCLAVE_HISTORY_LIMIT`, `CONCLAVE_DATA_DIR`, `CONCLAVE_DATABASE` and
`CONCLAVE_RUN_DATABASE`. Existing corresponding `STUDIO_*` host settings remain
compatibility aliases; `CONCLAVE_*` takes precedence.

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
bundled tools, optional web dependencies or subprocesses.

## Source boundaries

- `conclave/runtime.py`, providers, budgets, store and tool registry: reusable core.
- `conclave/application/`: definition adapter, execution/reviews, persistence,
  planning, trusted extensions and caller-configured data.
- `conclave/cli.py`: general application CLI.
- `conclave/api.py`: REST routing and local HTTP transport.
- `studio/web/` and `studio/shared/`: UI, editing, rendering and browser checks.
- `studio/worker/index.js`: same-origin REST proxy and static asset serving.
- `studio/scripts/dev.mjs`: UI HTTP server only.
- `investigation/`: separate reference application using the same core.

`applications` retains compatibility imports for the earlier application CLI.
Studio's former Python runtime and JSON-lines bridge have been removed. Historical
D1 migration files remain as schema history; they are not run by Studio or used
by the current REST architecture.

Application deletion retains run snapshots and history. Deleted starters are not reseeded on restart. Fictional fixture configuration is no longer exposed.
