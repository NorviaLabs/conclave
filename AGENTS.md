# Agent instructions

## Project

Conclave is a Python 3.14+ multi-agent runtime with an investigation CLI built on
its public primitives. Use `uv` for dependency management and commands. The core
has no runtime dependencies; `ddgs` is optional for bundled web-search tools.

## Layout and boundaries

- `conclave/`: reusable runtime, JSON protocol, providers, budgets, approvals,
  tool registry, typed events, and schema-driven shared store.
- `conclave/tools/`: bundled repository, Git, GitHub, and web tools. Keep these
  optional: importing `conclave` must not import bundled tools, perform network
  requests, or launch subprocesses.
- `investigation/`: application-specific agents, state schema, prompts, round
  loop, configuration, CLI, and rendering.
- `tests/`: offline tests and scripted providers in `tests/conftest.py`.
- `docs/architecture/` and `docs/design/`: architecture and design references.
- `main.py`: compatibility entry point; the installed command calls
  `investigation.cli:run`.

Keep investigation vocabulary and policy out of the reusable core. The core
accepts caller-defined agents, tools, state schemas, and handoff data. Export
intentional public APIs through `conclave/__init__.py`.

## Development

```bash
uv sync
uv run investigation --help
uv run pytest --no-cov tests/test_registry.py  # narrow validation example
uv run pytest                              # full suite with coverage
```

Pytest automatically enables coverage for `conclave` and `investigation`; the
full-suite threshold is 95%. Use `--no-cov` for focused tests so partial-suite
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
- Keep numeric application policy in `investigation/config.py`, not the core.
  Budgets are mutable accounting: create a fresh budget for each run.
- Many limits currently accept `None` to mean unlimited. Guard comparisons,
  arithmetic, truncation, and concurrency adapters accordingly. Test both
  finite and unlimited paths. Do not assume all APIs accept `None`:
  `fan_out(limit=...)` requires a concrete integer.
- The current application configuration has several unlimited defaults. Some
  README text, comments, and tests describe older finite defaults. Inspect code
  before relying on those numbers; report mismatches rather than silently
  restoring historical policy.
- The runtime emits typed events; CLI presentation belongs in
  `investigation/render.py`. Keep printing out of the core.
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
