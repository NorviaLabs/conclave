# Conclave Studio

The current Studio source imported from the ChatGPT Site into the Conclave
repository. Start with ../STUDIO_HANDOFF.md for product context and the terminal
handoff. ../docs/studio/PRODUCT.md contains accepted UX decisions;
../docs/studio/BUILD_PLAN.md contains the implementation investigation.

## Start Studio with Conclave

From the repository root, start both services with one command:

```sh
uv run python dev.py --provider codex
```

Press Ctrl+C to stop both. This requires Node 24+; select a different executable
with `--node /path/to/node` or `STUDIO_NODE`. The launcher waits for the backend
before starting Studio and stops the other service if either exits.

Studio is a REST client. Start the independent Conclave backend from the repository
root first:

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

Provider, tool, source and database settings belong to the backend process. Set
`CONCLAVE_PROVIDER`, `CONCLAVE_SOURCE_DIR`, `CONCLAVE_WEB` and other
`CONCLAVE_*` host settings there. Older corresponding `STUDIO_*` settings are
accepted by Conclave as compatibility aliases, but Studio no longer interprets them.
Set the same `CONCLAVE_API_TOKEN` on both processes to enable backend authentication.

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
