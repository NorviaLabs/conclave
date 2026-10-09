# Agent instructions

## Project

Conclave is a Python 3.14+ multi-agent runtime, with a definition-driven
application layer, a REST host and Studio on top of it. Use `uv` for dependency
management and commands. The core includes `ddgs` for bundled web-search tools.

## Layout and boundaries

- `conclave/`: reusable runtime, JSON protocol, providers, budgets, approvals,
  tool registry, typed events, and schema-driven shared store.
- `conclave/tools/`: bundled repository, Git, GitHub, and web tools. Keep imports
  lazy: importing `conclave` must not import bundled tools, perform network
  requests, or launch subprocesses.
- `conclave/application/`: definitions, the runs that execute them, the human
  gates, the shared store, resources, integrations wiring, and `presentation.py`
  for reading a run at a terminal.
- `applications/`: the definition-driven entry point that re-exports
  `run_application` and `validate_definition`.
- `conclave/integrations/`: connection providers, the SSRF-guarded transport and
  the encrypted credential store.
- `conclave/asgi.py` and `conclave/identity.py`: the hosted adapter and optional
  Google sign-in. Identity is established at the transport, before application
  code runs.
- `studio/`: the browser application; it talks to the REST host only.
- `tests/`: offline tests and scripted providers in `tests/conftest.py`.
- `docs/architecture/` and `docs/design/`: architecture and design references.
- `main.py`: compatibility alias for the installed `conclave` command.

Keep application vocabulary and policy out of the reusable core. The core accepts
caller-defined agents, tools, state schemas, and handoff data; applications are
definitions, not packages. Export intentional public APIs through
`conclave/__init__.py`.

## Development

```bash
uv sync
uv run conclave --help
uv run python dev.py --no-login            # adapter + Studio dev server, one command
uv run pytest --no-cov tests/test_registry.py  # narrow validation example
uv run pytest                              # full suite with coverage
```

Pytest automatically enables coverage for `conclave`; the full-suite threshold is
95%. Use `--no-cov` for focused tests so partial-suite
coverage does not obscure their results. No formatter or linter is configured.
Match nearby Python style and avoid unrelated formatting changes.

Tests should not require model credentials, a live model, network access, or
installed provider/GitHub CLIs. Fake provider subprocesses and HTTP calls at the
boundary; reuse existing fixtures and scripted providers. Local Git tests may
use temporary repositories.

## Implementation rules

- Prefer small root-cause fixes in shared code over patches in each caller.
  Preserve existing user changes; do not change defaults or dependencies as an
  incidental cleanup.
- Keep numeric application policy in a definition's `run_config` and
  `run_budget`, not the core.
  Budgets are mutable accounting: create a fresh budget for each run.
- Many limits currently accept `None` to mean unlimited. Guard comparisons,
  arithmetic, truncation, and concurrency adapters accordingly. Test both
  finite and unlimited paths. Do not assume all APIs accept `None`:
  `fan_out(limit=...)` requires a concrete integer.
- The current application configuration has several unlimited defaults. Some
  README text, comments, and tests describe older finite defaults. Inspect code
  before relying on those numbers; report mismatches rather than silently
  restoring historical policy.
- The runtime emits typed events. Formatting a run for a terminal belongs in
  `conclave/application/presentation.py`, which returns text and never prints it;
  the CLI prints. Keep printing out of the core.
- Preserve capability and approval gates. Unknown capabilities or tools that
  declare `WRITE` require approval; do not weaken gates to make tests pass.
- Keep repository tool paths contained within the selected repository. Never
  expose credentials through prompts, logs, or tool output.
- Keep provider authentication owned by the external backend. OpenCode model
  IDs are passed through unchanged; do not invent model IDs or claim to know a
  backend-selected default model when it has not been reported.
- GitHub JSON output already includes requested `comments`; do not combine
  `gh ... view --comments` with `--json`.

## Finishing a change

Add a focused regression test for behavior changes, run the relevant tests,
then broaden validation when useful. Update documentation when public behavior
changes. Report actual commands and results, including unrelated failures or
anything unverified; do not claim the full suite passes from targeted tests.
Do not commit, push, or discard unrelated changes without an explicit request.
