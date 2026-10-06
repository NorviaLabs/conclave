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

`uv run python dev.py --provider codex` starts both processes at once and stops
them together; select the Node executable with `--node /path/to/node` or
`STUDIO_NODE`.

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
node scripts/check-config.mjs      # host configuration and catalogues
node scripts/check-api-proxy.mjs   # the same-origin proxy and its token
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

Create a new application, customize its agents, tools, workflow and output schema,
save it, then submit a task in Use. The backend executes the saved revision and
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
