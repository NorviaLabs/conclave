# Conclave

A framework for building multi-agent systems out of bounded agent runs, plus one
opinionated application built on it.

The library (`conclave/`) supplies the loop and the guardrails: an agent run
that cannot spin, a delegation budget that refuses instead of recursing, a
capability gate that stops tools from changing the world without an approver, a
caller-schema'd shared store, and an async stream of typed events instead of
`print()` calls. It ships no agents, no tools and no domain vocabulary.

The app (`investigation/`) is consumer number one: a coordinator model decides
what to investigate each round, specialist agents gather repository, GitHub and
web evidence with sandboxed tools, and a separate step folds the results into
structured state (facts, evidence, hypotheses, open questions, investigations)
rather than a chat transcript.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- One model backend:
  - a current [Codex CLI](https://developers.openai.com/codex/cli/) on `PATH`
    logged in with ChatGPT Plus/Pro, or
  - `opencode` on `PATH` with an existing login (default backend; supports
    ChatGPT Plus/Pro subscriptions), or
  - a local [Ollama](https://ollama.com/) server
- Optional, per tool: `ddgs` (`uv sync --extra tools`, for `web_search`), `gh`
  (GitHub tools), network access (`web_fetch`)

## Install

```bash
uv sync
```

## Run the Conclave REST backend and Studio

Conclave exposes a general application CLI and REST API built on the same core.
Studio uses the REST API exclusively for persistence, proposals and execution.

Start both together with Node.js 24+ available:

```sh
uv run python dev.py --provider codex
```

Press Ctrl+C to stop both. Use `--backend-port`, `--studio-port` or `--node` to
override ports or the Node executable. Existing `CONCLAVE_*` settings, including
the API token and data directory, are inherited by both services.

Or start them independently:

```sh
# Terminal 1, repository root:
uv run conclave serve --provider codex --port 8080

# Terminal 2:
cd studio
CONCLAVE_API_URL=http://127.0.0.1:8080 npm run dev
```

See [REST endpoints and interface boundaries](REST_API.md)
for the endpoint list, configuration, existing-data compatibility and local-host
limits. Studio is at http://127.0.0.1:8787; Conclave stays running independently.

## Create and run an application

The `conclave` command runs application definitions through the same runtime used
by Studio. It does not require the investigation application or a web server.

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
`--model` is optional for Codex/OpenCode and required for Ollama.

Runs persist in the same local database as Studio by default. Use `--database
/absolute/path/runs.sqlite` to select another database consistently across commands.
The same `CONCLAVE_SOURCE_DIR`, `CONCLAVE_WEB`, `CONCLAVE_EXTENSIONS` and custom provider
factory settings apply. Tools must be registered on the host; write/unknown
capability approvals retain their existing gates.

```bash
uv run conclave status RUN_ID
# For a waiting human step, supply JSON matching its output schema:
uv run conclave review RUN_ID REVIEW_ID --output review.json
uv run conclave resume RUN_ID --provider codex
uv run conclave cancel RUN_ID
```

Human reviews are durable; resume continues from the saved checkpoint without
replaying completed steps. `cancel` applies to waiting runs; stop an active CLI
run with Ctrl+C. The command waits until the run completes or pauses for review.
The `applications` Python package also exports `run_application` and
`validate_definition`. Importing `conclave` itself remains a reusable core import.

## Run the investigation application

Conclave's core is a library. The `investigation` command
belongs to the application built on top of it; all flags below, including
`--repo`, are that application's interface. For other applications, use the
definition-driven `conclave` command above.

```bash
# ask a specific question about a repository
uv run investigation "why does the retry path double-count failures?"

# or run the application module directly
uv run python -m investigation "why does the retry path double-count failures?"

# explicit repository, different backend
uv run investigation --repo ~/code/my-project --provider ollama --model qwen3.6 "..."

# bound the run: at most 40 tool calls, 5 minutes and 200k tokens
uv run investigation --max-tool-calls 40 --max-seconds 300 --max-tokens 200000 "..."

# with no question, it investigates the repository for one important
# architectural weakness
uv run investigation
```

`python main.py ...` remains a compatibility entry point for the investigation
application. Existing users should run `uv sync` to replace the old `conclave`
console script, then use `investigation` instead.

| Flag | Default | Meaning |
| --- | --- | --- |
| `question` | a built-in repository review prompt | What to investigate |
| `--repo` | `.` | Repository the tools are scoped to |
| `--provider` | `opencode` | `codex`, `opencode` or `ollama` |
| `--model` | provider default | Model name passed to the backend |
| `--ollama-url` | `http://127.0.0.1:11434` | Ollama server |
| `--approve-destructive` | off | Approve tools that declare `WRITE`; without it such calls are refused |
| `--max-tool-calls` | unbounded | Tool calls allowed for the whole run |
| `--max-seconds` | unbounded | Wall-clock seconds allowed for the whole run |
| `--max-tokens` | unbounded | Tokens allowed, where the backend reports them |
| `--max-cost` | unbounded | Cost dimension, respected but never filled by the bundled backends |
| `--max-rounds` | 8 | Investigation rounds |
| `--max-delegations` | 15 | Total delegations, including nested work |
| `--max-per-agent` | 4 | Delegations per specialist across the run |
| `--max-depth` | 2 | Maximum delegation depth (top-level specialists are depth 1) |
| `--max-delegations-per-round` | 3 | Maximum independent delegations accepted each round |
| `--fan-out-limit` | 3 | Concurrent specialist runs within a round |
| `--max-tool-steps` | 12 | Action steps per agent run (including notes and delegations) |
| `--max-notes` | 12 | Private notes and checked-ledger entries per agent |
| `--max-note-chars` | 300 | Characters per private note or checked-ledger entry |
| `--max-tool-output-chars` | 20000 | Characters per tool output or delegated handoff in prompts |
| `--model-timeout` | 300 | Seconds allowed per model call |

For a larger repository, raise the independent limits together, for example:

```bash
uv run investigation --repo ~/code/large-project \
  --max-rounds 16 --max-delegations 60 --max-per-agent 12 \
  --max-tool-steps 30 --max-tool-output-chars 40000 \
  --max-delegations-per-round 5 --fan-out-limit 3 \
  --model-timeout 600 "Investigate the architecture"
```

Overrides apply only to that invocation; they never change the application
defaults. Raising one limit does not raise others or guarantee completion.
Concurrency is separate from the number of tasks accepted per round. Structural
delegation and overall spending limits accept zero to disallow spending; rounds,
steps, scratchpad sizes, output sizes, concurrency and model timeouts must be
positive. Floating-point limits must be finite.

Other multi-agent applications can expose their own CLI overrides and pass
`RunConfig` and `RunBudget` to Conclave in the same way. These flags belong to
Investigation, not to a framework-wide argument parser.

Exit codes: `0` success, `1` error, `130` interrupted.

None of the bundled tools declares `WRITE`, so `--approve-destructive` is only
needed once you add a tool that does.

### Use a ChatGPT subscription

#### Codex (no OpenCode required)

Install a current Codex CLI, then sign in with your ChatGPT subscription:

```bash
codex login
codex login status
uv run investigation --provider codex --repo ~/code/my-project "Investigate the architecture"
# Optional: override the Codex default with a model available to your account
uv run investigation --provider codex --model gpt-5.2-codex --max-delegations 30 "Investigate the retry path"
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
uv run investigation --provider opencode --model 'openai/<model-id>' "Investigate the retry path"
```

Replace `openai/<model-id>` with an ID from the list that is available to your
subscription. Subscription model availability and usage limits are controlled
by OpenAI; a ChatGPT subscription is not API credit. Choosing the API-key login
method instead uses separately billed API access.

OpenAI authentication stays inside the backend, not the runtime: `--model` is
passed through to OpenCode, which also supports other providers. Ollama and
custom `ModelProvider` implementations remain unchanged.
OpenCode's own tools remain disabled; Conclave owns tool execution and approval.
This backend reports no token usage to Conclave, so `--max-tokens` cannot meter
subscription calls (use tool-call and wall-clock limits instead).

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

The reference application is a caller like any other, so its numbers live in
`investigation/config.py` — including why `max_tool_steps` is 12 and not 8.

What the library gives you:

- **One bounded agent run.** `ToolAgentRuntime.stream(spec, task)` yields typed
  events and executes the JSON actions the model returns (`tool`, `note`,
  `delegate`, `final`). `run()` drains that stream and returns the handoff.
- **Guards.** A `RunBudget` counts delegations, tool calls, wall clock, tokens
  and cost. Refusals come back to the model as text, so an agent finalises on
  the evidence it has instead of crashing.
- **A handoff you define.** `AgentResult` carries `content` (prose) plus `data`,
  which holds every other field the agent put in its final action, untouched.
  The runtime never inspects them: `findings` and `open_leads` are names *this
  application* chose, declared in `investigation/agents.py`.
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
(`fail_under` in `pyproject.toml`) and currently reports 100% of statements
across `conclave/` and `investigation/`.

| Test file | Covers |
| --- | --- |
| `tests/test_package.py` | public surface, import hygiene, no domain vocabulary in the core |
| `tests/test_framework.py` | a second, non-investigation system built only from the public API |
| `tests/test_stream.py` | the event stream and the guards: budget, approval, truncation |
| `tests/test_runtime.py` | JSON protocol, private state, runtime, delegation |
| `tests/test_events.py` | the frozen event vocabulary |
| `tests/test_store.py` | schema merges, versions, snapshot isolation |
| `tests/test_approval.py` | capabilities, approvers, refusal text |
| `tests/test_budget.py` | delegation caps, depth and cycle refusals, dimensions |
| `tests/test_registry.py` | tool registration, permissions, truncation |
| `tests/test_local_tools.py` | path containment, files, Git, GitHub, web |
| `tests/test_providers.py` | Codex, OpenCode and Ollama backends, timeouts, usage |
| `tests/test_state.py` | the app's state, prompts and store schema |
| `tests/test_investigation.py` | the round loop, filtering, failure paths |
| `tests/test_render.py` | the progress log, driven by events |
| `tests/test_cli.py` | argument parsing, provider choice, budgets, exit codes |

## Layout

Two importable namespaces in one distribution. Domain content cannot enter the
core by accident, and a test enforces it.

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
| `conclave/tools/` | The 14 bundled tools |
| `investigation/agents.py` | The five specialists, their instructions and the handoff schema |
| `investigation/config.py` | Every number this application chose |
| `investigation/state.py` | Investigation state, its schema, and its model steps |
| `investigation/loop.py` | The round loop |
| `investigation/render.py` | Every byte the CLI prints |
| `investigation/cli.py` | Argument parsing, provider selection, entry point |

Dependencies point one way: the app imports the library, never the reverse.
`conclave/__init__.py` does not import `conclave.tools`, so `import conclave`
pulls no scraper and runs no subprocess. `main.py` is a thin entry point that
calls `investigation.cli.run`.

## Agents

The reference app's cast:

| Agent | Tools |
| --- | --- |
| `repo-researcher` | `list_files`, `read_file`, `search_files`, `git_*` |
| `github-researcher` | `github_issue`, `github_pr`, `github_search_*` |
| `web-researcher` | `web_search`, `web_fetch` |
| `architect` | none, reasons over supplied evidence only |
| `critic` | none, challenges claims and missing evidence |

Specialists never call tools directly: they return one JSON action per step —
`tool`, `note`, `delegate`, or `final` — and the runtime executes it and feeds
the observation back. A tool not listed in the agent's spec is refused.

### Agent-private state

Each agent run owns a private scratchpad: `note` actions record short notes, and
every tool call is appended to a checked ledger (both bounded by `RunConfig`
`max_notes` and `max_note_chars`). The scratchpad is re-rendered into that
agent's prompt on every step, so the agent remembers intent instead of just raw
output, and it never crosses back into the shared state. Only the handoff does:
the final action's `content`, whatever extra fields the agent was asked for
(in this application, `findings` with their provenance and `open_leads`), and
the checked ledger.

Delegated specialists cannot see the parent's notes, the parent's transcript, or
the shared state, so delegated tasks have to be self-contained.

### Nested delegation

While it runs, a specialist may delegate follow-up work to another specialist:

```json
{"type": "delegate", "agent": "web-researcher", "task": "...", "purpose": "..."}
```

Nested delegation is awaited inline, so it adds depth but not parallel fan-out
(rounds fan out instead, through `fan_out`), and every delegation — top-level or
nested — is charged to one run-scoped `RunBudget` that refuses work rather than
looping:

| Guard | Effect |
| --- | --- |
| `max_depth = 2` | coordinator (0) -> specialist (1) -> sub-specialist (2), no deeper |
| Ancestor check | an agent already on the delegation path cannot be delegated to again |
| Per-agent cap | one agent runs at most `max_per_agent` times per run |
| Total cap | `max_delegations` per run, shared by top-level and nested work |

Refusals are returned to the calling model as text ("Do not repeat this
delegation"), are never charged to the budget, and are reported as `nested:`
lines, so a loop attempt is visible instead of silent.

## Safety and limits

- Every file path is resolved and checked against `--repo`; anything that
  escapes it raises instead of reading.
- Binary files are refused rather than decoded: `read_file` answers with a
  "Binary file" notice and `search_files` skips them. A NUL byte is valid
  UTF-8 but cannot survive a subprocess argument, so a decoded binary used to
  take down a whole agent run at the model boundary.
- GitHub tools are read-only (`gh ... view` / `gh ... search`), and `web_fetch`
  allows only `http`/`https`.
- Capabilities are declared per tool and never inferred from names. `WRITE`, or
  no declaration at all, means destructive: refused unless an approver allows
  it. The bundled tools declare `read`/`network`/`shell` and never `write`.
- Every limit has an application-owned, CLI-overridable default
  (`investigation/config.py`): 15 delegations, 4 per agent, depth 2, 12 tool
  steps per agent, 300 s per model call, 12 notes of 300 characters, 8 rounds,
  at most 3 delegations per round. The library has no opinion on any of them.
- Token and cost dimensions are only enforced when the backend reports them:
  Codex and Ollama report counts, OpenCode reports none, and nothing invents a price
  table.
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
