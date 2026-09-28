# Conclave

A small multi-agent runtime that investigates one question using repository,
GitHub, and web evidence, and keeps its findings in structured state instead of
a chat transcript.

A coordinator model decides what to investigate each round, specialist agents
gather evidence with sandboxed tools, and a separate step folds the results back
into shared state (facts, evidence, hypotheses, open questions, investigations).

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- One model backend:
  - `opencode` on `PATH` with an existing login (default backend), or
  - a local [Ollama](https://ollama.com/) server
- Optional, per tool: `gh` (GitHub tools), network access (`web_search`,
  `web_fetch`)

## Install

```bash
uv sync
```

## Run

```bash
# ask a specific question about a repository
uv run main.py "why does the retry path double-count failures?"

# or, once installed by `uv sync`
uv run conclave "why does the retry path double-count failures?"

# explicit repository, different backend
uv run main.py --repo ~/code/my-project --provider ollama --model qwen3.6 "..."

# with no question, it investigates the repository for one important
# architectural weakness
uv run main.py
```

`python -m conclave ...` and `python main.py ...` work too.

| Flag | Default | Meaning |
| --- | --- | --- |
| `question` | a built-in repository review prompt | What to investigate |
| `--repo` | `.` | Repository the tools are scoped to |
| `--provider` | `opencode` | `opencode` or `ollama` |
| `--model` | provider default | Model name passed to the backend |
| `--ollama-url` | `http://127.0.0.1:11434` | Ollama server |

Exit codes: `0` success, `1` error, `130` interrupted.

## Layout

| Module | Responsibility |
| --- | --- |
| `conclave/config.py` | Round, delegation, tool-step and output limits |
| `conclave/providers.py` | `ModelProvider` plus the OpenCode and Ollama backends |
| `conclave/local_tools.py` | `LocalTools`: files, Git, GitHub and web access |
| `conclave/registry.py` | `Tool`, `ToolRegistry`, `build_tool_registry` |
| `conclave/agents.py` | Agent specs, JSON action parsing, `ToolAgentRuntime` |
| `conclave/state.py` | Investigation state and the steps that update it |
| `conclave/investigation.py` | The round loop that ties coordinator and agents together |
| `conclave/cli.py` | Argument parsing, provider selection, process entry point |

Dependencies point one way: `config` -> `providers` / `local_tools` ->
`registry` -> `agents` -> `state` -> `investigation` -> `cli`. Only
`providers.py` and `cli.py` know which backend is in use; everything else
depends on the `ModelProvider` interface. `main.py` is a thin entry point that
calls `conclave.cli.run`.

## Agents

| Agent | Tools |
| --- | --- |
| `repo-researcher` | `list_files`, `read_file`, `search_files`, `git_*` |
| `github-researcher` | `github_issue`, `github_pr`, `github_search_*` |
| `web-researcher` | `web_search`, `web_fetch` |
| `architect` | none, reasons over supplied evidence only |
| `critic` | none, challenges claims and missing evidence |

Specialists never call tools directly: they return one JSON action per step
(`{"type": "tool", ...}` or `{"type": "final", ...}`) and the runtime executes
the tool and feeds the observation back. A tool not listed in the agent's spec
is refused.

## Safety and limits

- Every file path is resolved and checked against `--repo`; anything that
  escapes it raises instead of reading.
- GitHub tools are read-only (`gh ... view` / `gh ... search`).
- `web_fetch` allows only `http`/`https`.
- Budgets in `conclave/config.py`: 8 rounds, 15 delegations total, 3 per round,
  8 tool steps per agent, 300 s per model call, tool output truncated at
  20 000 characters.
- Identical (agent, task) pairs are never delegated twice.
- If the budget runs out, a final answer is forced from the state gathered so
  far.
