# Conclave Studio: terminal handoff

This is the entry point for continuing the Conclave Studio work in Codex terminal.
It preserves the product decisions and site source from the ChatGPT conversation.
The user wants development to continue in the terminal, with a calm interface for
nontechnical users. This is a source handoff, not a newly connected production runtime.

## What was transferred

- The current Studio source is in studio/, including the catalog, editable DAG
  canvas, request forms, sample runner, Worker API, database schema and migrations.
- The Python application adapter is in studio/runtime/application.py.
- Product decisions and the six priority workflows are in docs/studio/PRODUCT.md.
- Existing research references and their limitations are in docs/studio/RESEARCH.md.
- The code investigation and recommended implementation sequence are in
  docs/studio/BUILD_PLAN.md. Recommended work is clearly distinguished from
  capabilities already shipped.
- studio/source-snapshot.json identifies the imported Site commit and original
  file hashes. The original Site README is retained as a historical snapshot in
  docs/studio/site-readme-snapshot.md; its older counts and sequential-plan
  descriptions should not be used as current behavior.

## Start locally

Use Node.js 24+ for the local Studio preview and Python 3.14+ with uv for Conclave.
Run the following from the repository root:

~~~sh
cd studio
npm ci
npm run dev
~~~

Open http://127.0.0.1:8787. The command builds the actual Worker, applies the
existing SQL migrations to a local SQLite database and serves the existing UI.
Saved applications and member templates persist in studio/.studio-local/;
that directory is ignored by Git. Restart the command after source changes.
It uses the D1 methods the current Worker needs, not a complete D1 emulator.
It makes no model calls or external tool calls.

For core development, return to the repository root and follow AGENTS.md:

~~~sh
uv sync
uv run investigation --help
~~~

## Current boundary

The browser runs deterministic fictional samples. The Python adapter can execute
a definition using a host-provided provider and ToolRegistry, including human
callbacks, but there is no live browser-to-runtime bridge or durable resume
service. Connector catalog selections are metadata, not connected accounts.
The core runtime is provider-agnostic and should stay domain-neutral.

## Saved applications and hosting

The source snapshot comes from Site version 13, commit
9c037d3349eac8d40101aadc5de62e47b94c90c0. The existing live Site is
https://conclave-investigations.mohitranka.chatgpt.site.

The hosted D1 database is separate from Git and has not been copied into this
branch. Its read API returned truncated application-definition cells, so this
handoff does not reconstruct or substitute those saved definitions. There were
no saved member-template rows at export time.

To carry a particular saved application across, open the existing Site, select
it and use Advanced -> export the application JSON. Import that JSON through
the local Studio's definition import. Definitions are copied; a fresh local ID
and revision are created when saved. Unsaved browser drafts and transient
sample-run activity also require export or remain in the original browser.
Catalog starters and their complete sample sources are already in this branch.

The preserved studio/.openai/hosting.json identifies the existing Site. It is
provenance and a future hosting reference, not a standalone deployment setup.
GitHub commits do not publish to that Site. Local terminal development is the
default; hosting migration or publishing is a separate task.

## Suggested first implementation slice

Read docs/studio/BUILD_PLAN.md and inspect the current code before changing it.
The recommended first real workflow is Research brief: bind actual request
values, connect an existing provider, retrieve real sources, save run state,
pause for a person, resume and save the final brief. Keep the existing sample
mode available and labeled accurately.

## Context prompt for Codex terminal

~~~text
Read AGENTS.md, STUDIO_HANDOFF.md, studio/AGENTS.md and docs/studio/PRODUCT.md.
Inspect docs/studio/BUILD_PLAN.md and the current implementation. Continue
building Conclave Studio while preserving the nontechnical UX, editable DAG
plans and six priority workflows. Start by assessing the recommended first
implementation slice; distinguish implemented behavior from sample behavior.
Keep application-specific policy outside Conclave core and preserve approval
gates. Validate changes using the local Studio and relevant offline core tests.
~~~
