# Conclave

A framework for building multi-agent systems out of bounded agent runs, and one
way of building them: an application is a JSON definition you write, or a starter
from the catalogue you adjust in Studio.

The library (`conclave/`) supplies the loop and the guardrails: an agent run
that cannot spin, a delegation budget that refuses instead of recursing, a
capability gate that stops tools from changing the world without an approver, a
caller-schema'd shared store, and an async stream of typed events instead of
`print()` calls. It ships bundled repository, Git, GitHub and web tools, but no
application-specific agents or domain vocabulary.

Applications are definitions, not code. A definition declares its members
(instructions, tools, which of them is human), the shared state and its output
schemas, the workflow of steps and how they connect, the request form, the
limits, and the views a person reads at the end. The same definition runs under
`conclave run`, in Studio, and over REST — there is no second way to build an
application, and `applications/` re-exports the two functions that build one.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- One model backend, whichever you already have:
  - a current [Codex CLI](https://developers.openai.com/codex/cli/) on `PATH`
    logged in with ChatGPT Plus/Pro, or
  - `opencode` on `PATH` with an existing login (supports ChatGPT Plus/Pro
    subscriptions), or
  - a local [Ollama](https://ollama.com/) server
- Node.js 24+ only for the separate Studio dev server; `conclave serve` serves
  Studio itself.
- Git for Git tools, `gh` for GitHub tools, and network access for web/GitHub tools.
  The web-search dependency is included in a normal install.

Conclave finds the backend already set up on this computer: `codex`, then
`opencode`, then an Ollama server with an installed model. It never guesses a
model name, and it never replaces a backend you asked for by name.

## Install

```bash
uv sync
```

## Run the Conclave REST backend and Studio

Conclave exposes a general application CLI and REST API built on the same core.
Studio uses the REST API exclusively for persistence, proposals and execution.

One command serves the API and the Studio page on one address:

```sh
uv run conclave serve
```

Open the address it prints. Startup also reports which AI backend it is using,
or the one setting that would fix it. Press Ctrl+C to stop.

`--port` moves it, and `--provider`/`--model` override the detected backend.
Setting `CONCLAVE_API_TOKEN` leaves the page unserved, because a browser cannot
present a bearer token; use the Studio dev server below in that case.

For UI development, or to run the two processes separately, use Node.js 24+:

```sh
uv run python dev.py --provider codex
```

`dev.py` starts the backend as this deployment's own adapter (`serve --hosted`)
beside the Studio dev server and stops them together, so the page gets what a
deployment has: the hosted routes, the origin allowlist, and Google sign-in when
`studio/.studio-local/login.json` exists. `--login PATH` names another login file
and `--no-login` leaves sign-in off. `--backend-port`, `--studio-port` and
`--node` override ports and the Node executable, `--provider`/`--model` override
the detected backend, and existing `CONCLAVE_*` settings are inherited by both
services. Connections work here without a setting: the first run generates the
credential key into the ignored `studio/.studio-local/integration.key` and every
run after that reuses it, so stored credentials stay readable. Or start them by
hand:

```sh
# Terminal 1, repository root. --hosted is the deployment's adapter, and the
# origin is what lets the page on its own port call the API:
CONCLAVE_UI_ORIGINS=http://127.0.0.1:8787 \
  uv run conclave serve --hosted --provider codex --port 8080

# Terminal 2:
cd studio
CONCLAVE_API_URL=http://127.0.0.1:8080 npm run dev
```

Add `CONCLAVE_GOOGLE_LOGIN_FILE` and `CONCLAVE_SESSION_KEY` to the first command
to sign in, and allow the redirect URI it names in the Google console. See
[REST endpoints and interface boundaries](REST_API.md) for the endpoint list,
configuration, sign-in, existing-data compatibility and local-host limits. The
Studio dev server is at http://127.0.0.1:8787; Conclave stays running
independently.

## Create and run an application

The `conclave` command runs application definitions through the same runtime used
by Studio. It needs no web server.

```bash
uv run conclave create release-notes.json --name "Release writer" \
  --instructions "Write release notes for the supplied changes."
uv run conclave run release-notes.json --provider codex \
  --task "Version one adds CSV export and fixes duplicate notifications."
```

Edit the JSON to define your agents, tools, inputs, output schemas, workflow and
views, or export an application from Studio and pass that file to `conclave run`.
Use `--inputs inputs.json` for declared request values. Every run pins its
definition and reports its ID, status, events and actual output as JSON.
Provider authentication remains with the backend; model IDs pass through.
`--provider` and `--model` are optional: without them the backend already set up
on this computer is used, and Ollama's own installed model is chosen.

Runs persist in the same local database as Studio by default. Use `--database
/absolute/path/runs.sqlite` to select another database consistently across commands.
The same `CONCLAVE_SOURCE_DIR`, `CONCLAVE_EXTENSIONS` and custom provider
factory settings apply. Bundled tools are registered automatically, including
the repository write tools (`write_file`, `edit_file`, `run_tests`, `git_commit`,
`push_branch`, `open_pull_request`, `merge_pull_request`). Applications
whose agents read or change files, Git or GitHub use a source folder: with nothing
configured, that is the folder the command runs in, and `--source-dir <folder>`
or `--source <resource-id>` choose another for one run. Path containment,
hidden-file denial and the document filters apply inside it, and a write tool
may change only that folder. Agent allowlists and write/unknown capability
approvals retain their existing gates. A terminal run has no way to ask, so
`conclave run` refuses a destructive call instead of waiting for an answer;
`conclave serve` and Studio ask the person and wait.

For a trusted application, choose **Controls → Tool authorization → Autonomous
execution** and save, or set `"autonomous_execution": true` in its definition.
The application host then authorizes its declared tools up front instead of
asking on each write, including terminal runs. Missing or `false` retains the
existing behavior. Authorization is recorded as `ToolAuthorizationGranted`;
unknown capabilities are not pre-authorized. Agent allowlists, source and
connection checks, budgets, and explicit human workflow steps remain active.
This authorizes commands and remote writes, not just file edits: command tools
are not an OS sandbox, so enable it only for trusted applications and resources.

```bash
uv run conclave status RUN_ID
# For a waiting human step, supply JSON matching its output schema:
uv run conclave review RUN_ID REVIEW_ID --output review.json
uv run conclave resume RUN_ID --provider codex
uv run conclave cancel RUN_ID
# A run that stopped while it was sending something:
uv run conclave actions RUN_ID
uv run conclave check RUN_ID ACTION_ID
uv run conclave settle RUN_ID ACTION_ID --done --note "Posted at 10:41"   # or --resend, or --undo
```

Human reviews are durable; resume continues from the saved checkpoint without
replaying completed steps. `cancel` applies to waiting, paused, stopped and
interrupted runs, and records the run as cancelled; stop an active CLI run with
Ctrl+C. The command waits until the run completes or pauses for review.

A reviewer who answers `changes_requested` with a reason sends the reviewed
step back: it runs again with the reason, and the review is asked again as a
new round (`revises` and `max_revisions` on the human step choose the steps and
bound the rounds). A run a model backend stopped — a timeout, an outage, a
usage limit — is tried again by itself after a backoff
(`CONCLAVE_RETRY_ATTEMPTS`, `CONCLAVE_RETRY_BASE_SECONDS`,
`CONCLAVE_RETRY_MAX_SECONDS`), and the command waits through it. Every source a
record cites is checked against what its step read, and marked `found`,
`not_found` or `not_checkable`. A step may also declare a `condition` on an
earlier step's output ("only when triage says bug"): when it does not hold, the
step and every step only it leads to are skipped, and the run goes on. An
application's `review_policy` can name who may answer its reviews and keep the
person who started a run from signing off its own review, and a review step's
`answers_required` can ask for several different people before the run goes on.
An application started by an event gets its own incoming address and signing
secret; a signed event starts one run with the event's data as its input, and
the same event again starts nothing. Every saved change to an application is
kept as a version: Studio lists them,
shows what changed between two, and restores an older one as a new version, and
each run links to the version it ran. See [REST_API.md](REST_API.md) for the
details.

**External writes are never sent twice without someone checking.** Every call
to a tool that needs approval is recorded, with the agent's progress, before it
is sent. If a run stops mid-write — the host stopped, the service did not answer
clearly, the run was cut short — that write is *uncertain*, and the run cannot
continue until it is settled:

- Where the service offers a way to check (a GitHub pull request from that
  branch, a file with that text, a pushed branch at that commit…), `resume`
  checks first. Found: it is marked done and not sent again. Proved absent by a
  reliable read of an approved call: it is sent once more, under the same
  action.
- Otherwise a person decides with `conclave settle`: `--done` (it happened; an
  optional `--note` reaches the agent as your words) or `--resend` (send it once
  more — that choice is the approval). Either can be `--undo`ne until the run
  continues. `conclave resume` exits with status `3` and names what is left.

Writes that completed are never sent again: a step that had already sent
something continues from its last recorded action instead of starting over.
Interrupted runs recorded before this journal existed cannot be continued.
The `applications` Python package also exports `run_application` and
`validate_definition`. Importing `conclave` itself remains a reusable core import.

### Results as files

A step whose output is a document, a dataset or an artifact leaves a real file —
Markdown, CSV or JSON — made by Conclave from what the step recorded, never by
the model. The run page shows each file's name, size and first lines with a
Download button; the download is checked against what was stored before it is
sent (`GET /api/runs/<run>/files/<file>`). In Set up › Results, "Stored file"
chooses the format or turns it off.

### A step's own time limit

A step can have its own time limit (Time limit, in its settings; or
`time_limit_seconds` in the definition, 10 seconds to 24 hours). A step that runs
over is stopped while it works and recorded as timed out; the run stops once the
steps beside it finish, and Continue runs the step again with its full limit.
Time waiting for someone to approve an action does not count.

### Who may use an application

Each application has people with a role — viewer, runner, editor or owner — set
in **People** on the application. Viewers read its runs and results; runners also
run it; editors also change it; owners also choose who has access and delete it.
Whoever creates an application owns it; one saved before roles existed stays open
to everyone in the workspace until someone limits access. Every application and
run route checks the caller's role (REST_API.md, "People and roles").

### Compare two runs

In Studio, Run history has a box on every run: choose two and press **Compare 2
runs** to see, step by step, what each run did — the version it used, steps a
condition skipped, outputs that changed (a line diff for text, record by record
otherwise), how each review went, and what each step used. Over REST:
`GET /api/runs/compare?a=<run>&b=<run>` (see REST_API.md, "Comparing two runs").

### Run an application on a schedule

An application can start itself: every few minutes or hours, or daily or on
chosen weekdays at set times, in a named time zone. In Studio, open **Settings →
Connections → When to run**, choose **On a schedule**, and say what each run
starts with (the task and request fields, saved with the application — never
passwords or tokens). Studio shows the next three times as the host works them
out. In a definition:

```json
"start_mode": "schedule",
"schedule": {"kind": "weekly", "days": ["mon", "wed", "fri"], "times": ["09:00", "17:30"],
             "time_zone": "Europe/London", "catch_up": "skip",
             "request": {"task": "Summarise yesterday's support tickets", "inputs": {"team": "billing"}}}
```

A serving host (`conclave serve`, local or `--hosted`) runs it, using the latest
saved version. Each scheduled time starts at most one run, even across restarts
and with several processes on one store. If the previous scheduled run is still
going, the next time is skipped. Times missed while the host was off are skipped
and reported, not run late in a burst; `"catch_up": "latest"` runs only the most
recent one. Studio's header shows the next run and why the last time did not
run, and run history lists scheduled runs and the times that did not run.

Nobody is there to approve anything, so **scheduled runs refuse writes** unless
the application has autonomous execution on, exactly as `conclave run` does;
Studio warns about this, and about having no limits, before you save. Runs act
as the schedule's owner: whoever last saved a change to what the application
runs (moving cards on the canvas does not count), so editing someone else's
schedule makes it yours rather than borrowing their accounts. Studio shows
whose it is. A hosted schedule with no owner waits until someone turns it on,
and with Google sign-in a schedule whose owner is no longer admitted stops and
waits the same way. Only the owner pauses or resumes it:

```bash
uv run conclave schedule status APP_ID
uv run conclave schedule pause APP_ID
uv run conclave schedule resume APP_ID
```

Pausing never creates a new version, and times that pass while paused are not
run. The command only changes the schedule: a serving host starts the runs.

### PDF and Word documents in a source folder

Source folders list `.pdf` and `.docx` files alongside `.txt`, `.md`, `.csv` and
`.html`, and an agent that reads one gets its text, not "Binary file":

- **PDF:** a `[Page N]` line before each page's text. A page without a text
  layer says `[Page N: no text layer]`, and a scanned PDF with no text at all
  says so in one sentence (there is no OCR).
- **Word (.docx):** headings as `#` (Title), `##` (Heading 1) and so on, list
  items as `- `, table rows as `cell | cell`; deleted revisions, hidden text,
  headers, footers and comments are left out — the agent reads what a person
  reading the document sees. Document contents are untrusted input, and the
  tool says so to the model.
- **Paging and search.** Lines are numbered and paged with `start_line` /
  `end_line` as for a text file, and `search_files` / `knowledge_search` in a
  source folder find text inside documents at the same line numbers.
  Repository (code) search skips documents.
- **No text to give.** A PDF only locked against printing is read. One that
  needs a password, a damaged file, a Word 97–2003 `.doc` (save it as `.docx`
  or PDF) and other office formats each get one clear sentence.
- **Limits.** Reading happens in a separate short-lived process, killed when
  it runs over, so no file can crash or hang the host. Each limit's message
  names its setting:

  | Setting | Default |
  |---|---|
  | `CONCLAVE_DOCUMENT_MAX_BYTES` | 50 MB |
  | `CONCLAVE_DOCUMENT_MAX_PAGES` | 1000 |
  | `CONCLAVE_DOCUMENT_MAX_CHARS` | 2,000,000 |
  | `CONCLAVE_DOCUMENT_TIMEOUT` (seconds) | 30 |
  | `CONCLAVE_DOCUMENT_MAX_MEMORY_MB` | 1024 |
  | `CONCLAVE_DOCUMENT_WORKERS` (reading processes at once) | 2 |

  The memory cap is not enforced on systems that refuse to lower a process's
  address-space limit, such as macOS; the time limit still applies there.

  Text is kept in memory until the file changes, so reading and searching the
  same folder does not extract each document again.

### Tell someone when a run waits for them

When a run stops for a human review or a pause, Conclave can send one message
saying so: to a Slack channel, an email address or a webhook, through one of
your own connections (this needs a host with connections, see
`INTEGRATIONS.md`). In Studio, open **Settings → Connections → When a run waits
for someone**, choose the channel and your connection, and Studio shows the
exact message. In a definition:

```json
"notifications": {"waiting": {"via": "slack", "connection": "team_slack", "channel": "#reviews"}}
```

(`"via": "email"` takes an `"address"`; `"via": "webhook"` sends to the
connection's own URL.) The message names the application and the step that
waits and links to the run in Studio; nothing from the run — output, request
values, credentials — is in it. There is one message per waiting point: a
restart while the run waits sends nothing new, and a run that resumes and waits
again sends a new one. A message that cannot be sent never fails or holds up
the run; the run's activity says what happened. Messages are sent as whoever
last saved a change to the application when the run started, from their own
connection, which must be granted to it. So that nobody can flood that
channel by pausing and resuming runs, a run that waits again within 10 minutes
of its last message is not told again, and an application sends at most 20
messages an hour (`CONCLAVE_NOTIFY_RUN_INTERVAL` in seconds,
`CONCLAVE_NOTIFY_HOURLY_LIMIT`); what is not sent is recorded on the run. The
channel or address is saved with the application, so anyone who can open it
can see it. The link needs to know where Studio is:
`CONCLAVE_STUDIO_URL`, or, locally, the address `conclave serve` prints.
Without the setting an application behaves exactly as before.

### Serving a deployment's own adapter

`conclave serve` is the local application: one operator, one tenant, no sign-in.
`conclave serve --hosted` serves the adapter a deployment runs — authentication,
origin allowlist, the hosted routes — from a loopback address, so Google sign-in
and the hosted behaviour can be tried before anything is deployed. `REST_API.md`
has the whole recipe, including the redirect URI Google's console wants. It is
off unless `CONCLAVE_GOOGLE_LOGIN_FILE` and `CONCLAVE_SESSION_KEY` are set, and
with no login file every command behaves exactly as it did.

## Build an application

An application is one JSON file. `conclave create` writes a minimal one; Studio's
"Configure a catalog application" dialog starts from a catalogue starter and
edits the same fields. Nothing is compiled and nothing is registered: the file is
the application.

| Field | What it declares |
| --- | --- |
| `name`, `description` | What the application is called and what it is for |
| `agents` | Its members: `id`, `name`, `kind` (`ai` or `human`), `role`, `instructions`, `tools` |
| `state` | The shared collections: `key`, `type` (`text` or `records`), `merge`, and the record fields with their types |
| `workflow` | The steps: `id`, `agent`, `task`, `output` |
| `connections` | Which step feeds which; omitted means each step follows the one before it |
| `inputs` | The request form a person fills in |
| `views` | What a person reads at the end: which collection, and how to lay it out |
| `run_config`, `run_budget` | Every limit this application chose |
| `research`, `review_checks`, `connector_requirements`, `integrations` | What the plan needs from this host, when it needs anything |

The catalogue in `conclave/application/data/catalogue.json` supplies starters
(`case-resolution`, `research-brief`, `document-review`, `software-delivery`, and
`starter` for a blank one), the role presets they are built from, and the output
kinds that decide how a collection is stored and laid out. A deployment can add
its own starters, roles and output presets in a workspace catalogue; bundled
identifiers stay reserved.

Reading a run:

```bash
uv run conclave status RUN_ID --format text
```

`--format json` is the default and prints the whole record, which is what a
script wants. `--format text` prints the same run for a person: the steps and
what each one is doing, what a waiting step wants from you and the exact command
to answer it, the views the run produced, and the spend against the limits this
application declared. It is formatting only — the same durable record Studio
draws, so the two cannot disagree.

If you are coming from the `investigation` command: it was one application, and
it has been removed. The nearest catalogue starter is `research-desk`, and
`conclave run`/`status`/`review`/`resume` cover what its command line did.

Limits belong to the application that chose them. A definition's `run_config` and
`run_budget` bound action steps per member, notes and their size, tool-output
size, how many steps work at once, model timeout, delegations, per-member and
depth caps, and the five measured dimensions. There is no framework-wide flag that
overrides them: edit the definition, or pass the same file to `conclave run`. The
one host bound is `CONCLAVE_MAX_PARALLEL_STEPS` (default 8), which caps how many
steps of a run work at once, so a blank limit never means as many as a plan has.

Steps that do not depend on each other run side by side: each starts as soon as
the steps it needs have finished and a slot is free. Their outputs still join the
shared results in plan order, so the run ends where it would have one step at a
time. A pause lets the working steps finish and starts nothing new; a failure in
one step lets the others finish and keeps their work; and a token, time or cost
limit is a real stop for every step at once (see
[REST_API.md](REST_API.md#steps-that-run-side-by-side)).

Exit codes: `0` success, `1` error, `130` interrupted.

The bundled tools that declare `WRITE` are the repository write tools, and they
only act inside the source folder the run was authorized with. Every call they
make is approved by a person before it runs.

### Use a ChatGPT subscription

#### Codex (no OpenCode required)

Install a current Codex CLI, then sign in with your ChatGPT subscription:

```bash
codex login
codex login status
uv run conclave run release-notes.json --provider codex --task "Summarise the architecture"
# Optional: override the Codex default with a model available to your account
uv run conclave run release-notes.json --provider codex --model gpt-5.2-codex --task "Explain the retry path"
```

Select the ChatGPT browser login, not `--with-api-key`. This backend requires
ChatGPT authentication and does not fall back to separately billed API access.
Codex owns credentials and refresh; Conclave never reads them. Models and usage
limits depend on your subscription. Upgrade Codex if it does not support
`exec --ignore-user-config --ignore-rules --ephemeral`.

`CodexProvider(model=None, timeout=300)` is also available from `conclave` for
other applications. Model names are passed through unchanged. Calls use stdin
and a fresh temporary directory, ignore user configuration and project rules,
disable native shell, search, plugin and delegation tools, and use a read-only
sandbox. Conclave remains responsible for agent tools and approval. Codex JSONL
events supply the final assistant text and token usage when available; cached
input tokens are already included in input usage and are not counted twice.
No dollar cost is inferred from subscription usage.

#### OpenCode (existing alternative)

Use the existing OpenCode backend, not an OpenAI API key. In OpenCode, run
`/connect`, select **OpenAI**, then **ChatGPT Plus/Pro**, and complete the
browser login. OpenCode owns authentication and credential refresh; Conclave
does not read or store your subscription credentials.

List the available OpenAI models and pass one of the returned IDs unchanged:

```bash
opencode models openai
uv run conclave run release-notes.json --provider opencode --model 'openai/<model-id>' --task "Explain the retry path"
```

Replace `openai/<model-id>` with an ID from the list that is available to your
subscription. Subscription model availability and usage limits are controlled
by OpenAI; a ChatGPT subscription is not API credit. Choosing the API-key login
method instead uses separately billed API access.

With `opencode` already signed in, both flags can be omitted: Conclave detects
it and the backend uses the model it is configured with.

OpenAI authentication stays inside the backend, not the runtime: `--model` is
passed through to OpenCode, which also supports other providers. Ollama and
custom `ModelProvider` implementations remain unchanged.
OpenCode's own tools remain disabled; Conclave owns tool execution and approval.
This backend reports no token usage to Conclave, so a token limit cannot meter
subscription calls (bound tool calls and wall clock in the definition instead).

## Using the library

```python
from conclave import (
    AgentSpec,
    RunBudget,
    RunConfig,
    ToolAgentRuntime,
)

AGENTS = {
    "writer": AgentSpec(
        name="writer",
        instructions=(
            "Draft the note. Finish with a final action "
            "carrying content and a `title` field."
        ),
        tools={"save_note"},
    ),
    "critic": AgentSpec(
        name="critic",
        instructions="Challenge the draft.",
    ),
}

config = RunConfig(
    max_tool_steps=8,
    max_notes=12,
    max_note_chars=300,
    max_tool_output_chars=20_000,
    fan_out_limit=4,
)

budget = RunBudget(
    max_delegations=15,
    max_per_agent=4,
    max_depth=2,
)

runtime = ToolAgentRuntime(
    provider,          # any ModelProvider
    tools,             # a ToolRegistry you filled
    budget,            # delegation caps and work limits
    AGENTS,            # who may be delegated to, injected not global
    config=config,     # required: the library ships no numbers
)

async for event in runtime.stream(AGENTS["writer"], "draft something"):
    ...                # react: render it, log it, count it

result = await runtime.run(AGENTS["critic"], "review it")
print(result.content, result.data["title"], result.nested)
```

### There are no defaults in the core

Every limit in that snippet is spelled out, and that is deliberate. `RunConfig`,
`RunBudget`, `ToolRegistry(max_output_chars=...)`, the provider `timeout=` and
`fan_out(limit=...)` all require their numbers. A library that guesses answers
"how many agents does this system have?" and "how much text is too much?" on
your behalf, and those answers are only visible in the library's source.

The one thing that stays optional is a *measurement*: `RunBudget`'s `tool_calls`,
`wall_clock_seconds`, `tokens` and `cost_usd` default to `None`, which means
unmetered. That is an absence, not a chosen number.

An application is a caller like any other, so its numbers live in its own
definition: `run_config` and `run_budget` are required fields, catalogue starters
fill them in, and Studio edits them.

What the library gives you:

- **One bounded agent run.** `ToolAgentRuntime.stream(spec, task)` yields typed
  events and executes the JSON actions the model returns (`tool`, `note`,
  `delegate`, `final`). `run()` drains that stream and returns the handoff.
- **Guards.** A `RunBudget` counts delegations, tool calls, wall clock, tokens
  and cost. Refusals come back to the model as text, so an agent finalises on
  the evidence it has instead of crashing.
- **A handoff you define.** `AgentResult` carries `content` (prose) plus `data`,
  which holds every other field the agent put in its final action, untouched.
  The runtime never inspects them: the names are whatever the application's
  definition asked its members for.
- **A capability gate.** A tool that declares no capabilities — or declares
  `WRITE` — is destructive: it needs an `Approver`, and with no approver it is
  refused. Non-destructive calls are auto-approved, and every call still emits
  `ToolCalled` with its declared capabilities.
- **A shared store.** `StoreSchema(keys, merges)` plus `SharedStore`, with
  versioned writes and `snapshot()` copies. Writes are the orchestrator's job in
  v0; agents get a copy.
- **`fan_out(items, worker, limit=...)`** when you want breadth. Nested
  delegation is serial by design, so a run is a plain call tree.
- **`conclave.tools`** — 14 bundled repository, Git, GitHub and web tools,
  importable but outside the stability promise. Nothing in the core imports it.

## Test

```bash
uv run pytest
```

The suite is fully offline: model backends are replaced by scripts of JSON
actions, and `opencode`, `gh`, and HTTP calls are faked at the boundary, so no
test needs a network, a login, or a running model. Coverage is enforced at 95%
(`fail_under` in `pyproject.toml`) and currently reports 95% of statements
across `conclave/`.

| Test file | Covers |
| --- | --- |
| `tests/test_package.py` | public surface, import hygiene, no domain vocabulary in the core |
| `tests/test_framework.py` | a system built only from the public API |
| `tests/test_stream.py` | the event stream and the guards: budget, approval, truncation |
| `tests/test_runtime.py` | JSON protocol, private state, runtime, delegation |
| `tests/test_events.py` | the frozen event vocabulary |
| `tests/test_store.py` | schema merges, versions, snapshot isolation |
| `tests/test_approval.py` | capabilities, approvers, refusal text |
| `tests/test_budget.py` | delegation caps, depth and cycle refusals, dimensions |
| `tests/test_registry.py` | tool registration, permissions, truncation |
| `tests/test_local_tools.py` | path containment, files, Git, GitHub, web |
| `tests/test_providers.py` | Codex, OpenCode and Ollama backends, timeouts, usage |
| `tests/test_presentation.py` | the terminal's account of a run |
| `tests/test_applications_cli.py` | creating, running and reading a definition |
| `tests/test_studio_runs.py` | the run lifecycle: waiting, resume, pause, cancel |
| `tests/test_identity.py` | Google sign-in, its refusals and its session |

## Layout

Two layers in one distribution: the runtime, and the application layer that runs
definitions. Application vocabulary cannot enter the core by accident, and a test
enforces it.

| Module | Responsibility |
| --- | --- |
| `conclave/spec.py` | `AgentSpec`, `Tool`, `Capability` |
| `conclave/config.py` | `RunConfig`: the limits one run needs, all required |
| `conclave/providers.py` | `ModelProvider`, `Completion`, `Usage`, Codex, OpenCode, Ollama |
| `conclave/registry.py` | `ToolRegistry`: describe, execute, truncate |
| `conclave/approval.py` | The capability gate and the `Approver` contract |
| `conclave/budget.py` | `RunBudget`: delegation caps and work dimensions |
| `conclave/protocol.py` | The JSON action protocol, private state, `AgentResult` |
| `conclave/events.py` | The frozen event vocabulary |
| `conclave/store.py` | `StoreSchema` and `SharedStore` |
| `conclave/runtime.py` | `ToolAgentRuntime` and `fan_out` |
| `conclave/tools/` | The 14 bundled read tools, and `changes.py` for the write tools |
| `conclave/application/` | Runs, steps, gates, approvals, the shared store, resources, REST host |
| `conclave/application/presentation.py` | A run as text, for a terminal |
| `conclave/application/data/catalogue.json` | Starters, role presets, output kinds and tool descriptors |
| `conclave/asgi.py` | The ASGI adapter, and optional Google sign-in |
| `conclave/identity.py` | Google sign-in: identity tokens and the session cookie |
| `conclave/integrations/` | Connection providers, transports and the encrypted credential store |
| `conclave/cli.py` | `create`, `run`, `status`, `review`, `resume`, `cancel`, `serve` |
| `applications/` | The definition-driven entry point: `run_application`, `validate_definition` |

Dependencies point one way: the application layer imports the runtime, never the
reverse. `conclave/__init__.py` does not import `conclave.tools`, so `import
conclave` pulls no scraper and runs no subprocess. `main.py` is a thin alias for
the one command.

## Agents

An application's members come from its definition: an `id`, a `role` preset from
the catalogue, instructions, and the tools that member may call. A human member
is a step a person answers instead of a model, which is how a review gate is
declared rather than coded.

Members never call tools directly: they return one JSON action per step —
`tool`, `note`, `delegate`, or `final` — and the runtime executes it and feeds
the observation back. A tool not listed in the member's spec is refused.

### Agent-private state

Each agent run owns a private scratchpad: `note` actions record short notes, and
every tool call is appended to a checked ledger (both bounded by `RunConfig`
`max_notes` and `max_note_chars`). The scratchpad is re-rendered into that
agent's prompt on every step, so the agent remembers intent instead of just raw
output, and it never crosses back into the shared state. Only the handoff does:
the final action's `content`, whatever extra fields the agent was asked for
(whatever fields it was asked for, with their provenance), and the checked
ledger.

Delegated specialists cannot see the parent's notes, the parent's transcript, or
the shared state, so delegated tasks have to be self-contained.

### Nested delegation

While it runs, a member may delegate follow-up work to another one:

```json
{"type": "delegate", "agent": "researcher", "task": "...", "purpose": "..."}
```

Nested delegation is awaited inline, so it adds depth but not parallel fan-out
(rounds fan out instead, through `fan_out`), and every delegation — top-level or
nested — is charged to one run-scoped `RunBudget` that refuses work rather than
looping:

| Guard | Effect |
| --- | --- |
| `max_depth = 2` | a run (0) -> its delegate (1) -> that delegate's delegate (2), no deeper |
| Ancestor check | an agent already on the delegation path cannot be delegated to again |
| Per-agent cap | one agent runs at most `max_per_agent` times per run |
| Total cap | `max_delegations` per run, shared by top-level and nested work |

Refusals are returned to the calling model as text ("Do not repeat this
delegation"), are never charged to the budget, and are reported as `nested:`
lines, so a loop attempt is visible instead of silent.

## Safety and limits

- Every file path is resolved and checked against the source folder the run was
  authorized with (`--source-dir`, or the run's resource); anything that escapes
  it raises instead of reading.
- Binary files are refused rather than decoded: `read_file` answers with a
  "Binary file" notice and `search_files` skips them. A NUL byte is valid
  UTF-8 but cannot survive a subprocess argument, so a decoded binary used to
  take down a whole agent run at the model boundary.
- The GitHub tools registered by default are read-only (`gh ... view` /
  `gh ... search`), and `web_fetch` allows only `http`/`https`.
- Capabilities are declared per tool and never inferred from names. `WRITE`, or
  no declaration at all, means destructive: refused unless an approver allows
  it. The read tools declare `read`/`network`/`shell` and never `write`; the
  repository write tools in `conclave.tools.changes` are the one bundled family
  that declares `WRITE`, and each of their calls needs an approval.
- Every limit belongs to the application that declared it, in the definition's
  `run_config` and `run_budget`. The library has no opinion on any of them: it
  refuses to guess, and a definition that leaves a required number out is
  rejected before a run starts.
- Token and cost dimensions are only enforced when the backend reports them:
  Codex and Ollama report counts, OpenCode reports none, and nothing invents a price
  table.
- Each model call is recorded (`ModelCalled`: member, backend, measured tokens,
  seconds — never the prompt or reply), each finished step carries its usage, and
  a run's record adds them up per step. A limit warns once per run at 80% of it,
  or at the application's own `budget_warnings` threshold (`null` turns it off).
- Identical (agent, task) pairs are never delegated twice.
- If the budget runs out, a final answer is forced from the state gathered so
  far.
- A reply that is not a single JSON object — including tool-call markup such as
  `DSML`/`antml` — is retried once with a correction that repeats the rule and
  shows the rejected reply. If it still fails, the run degrades instead of
  aborting: state initialization continues with an empty state, the coordinator
  stops investigating, and a failed state update keeps the previous state.
  A reply that contains both markup and JSON is recovered without a retry.
- An action type the runtime does not recognise (`read_file` instead of
  `tool`) costs one step and comes back with the list of valid types; it does
  not discard what the agent has already done.
