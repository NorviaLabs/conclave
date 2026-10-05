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

The browser sample runner makes no model or tool calls. Account connection,
live runtime bridging, durable run/review resume, schedules and real artifact
delivery still need implementation. The adapter executes ready DAG stages one
at a time using injected core providers/tools and a human_handler callback.
Draft approval never substitutes for separate destructive-tool authorization.

## Files

| Path | Responsibility |
| --- | --- |
| web/ | Interface, canvas, inspectors and sample runner |
| shared/catalogue.js | Starters, all default teams, roles, tools, outputs, connector metadata and research links |
| shared/definition.js | JavaScript definition validation and DAG operations |
| runtime/application.py | Definition-to-Conclave execution adapter |
| worker/index.js | Same-origin application/member APIs and asset routes |
| db/, drizzle/ | Application/member schema and preserved migrations |
| scripts/build.mjs | Bundles Worker, assets, adapter download and migrations |
| scripts/dev.mjs | Development-only local Worker runner and SQLite bridge |
| scripts/check-local.py | Isolated Worker/API smoke check with restart persistence |
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

No live provider/connector calls or full core test-suite result are claimed.
