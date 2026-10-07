# Running Conclave locally and hosted

Status: **decided, partially implemented.** The three target decisions below are
settled. Everything marked *PROPOSED* is a shape I am suggesting and is cheap to
reject. Only §"S0" is built.

The goal: one codebase that runs as the current single-user local host **and** as
a multi-tenant hosted control plane whose agents and tool calls execute in
per-sandbox microkernels.

## Targets

1. **Local host** — one operator, loopback, files on this machine, model backend
   already signed in. This is what exists today and what `conclave serve` gives.
2. **Hosted control plane** — this application service behind a real ASGI server,
   with managed storage instead of local SQLite. `ApplicationAPI.dispatch` stays
   the contract; the socket server is an adapter, not the interface.
3. **Sandboxed execution** — every agent and every tool call runs in its own
   microkernel/VM and talks back over a transport. Nothing executes tools in the
   control-plane process.
4. **Multi-tenant** — many users and organizations share one deployment, so every
   request, run, event and stored object is scoped to a principal and a tenant.

## What already survives a hosted deployment

Do not rebuild these; they are the reason the split is tractable.

- **The API is transport-free.** `ApplicationAPI.dispatch(method, target, value)
  -> (status, payload)` (`conclave/api.py`) — `start_server` is a thin loopback
  adapter around it.
- **The application definition is pure JSON** (agents, state, workflow, views,
  run_config, run_budget) and never stores a filesystem path: a source is an ID
  (`"resources": {"source": "case-a"}`).
- **Handoffs, events and snapshots are plain dicts** — `AgentResult` (prose plus
  `data`), the typed event vocabulary, `SharedStore.snapshot()`. The JSON action
  protocol already crosses a process boundary in principle.
- **A run is a plain call tree** with serial nested delegation and
  orchestrator-owned store writes, so one authority can hold the budget and the
  store while everything else is remote.
- **Providers and tools are injected**, with extension points that a hosted
  deployment can use without touching the core: `PROVIDER_FACTORY`, `EXTENSIONS`,
  `DEFAULTS_FILE`, `CATALOGUE_FILE`.
- **Durable reviews and resume** already pin the definition and revision.
- **`/api/runtime` advertises capabilities** and the UI already branches on them,
  so a hosted UI needs a profile, not a rewrite.

## What is local-only by construction

Each of these is correct locally and wrong hosted. The profile switch (§S0)
exists so they cannot leak into the hosted path by accident.

| Local assumption | Where | Hosted replacement |
| --- | --- | --- |
| Tools are in-process Python callables | `Tool.func` (`conclave/spec.py`), executed by the registry; file/Git/GitHub tools shell out in-process (`conclave/tools/local.py`) | `ToolExecutor` over a transport; the call round-trips to a sandbox |
| Provider is a local CLI or localhost Ollama | `resolve_provider` and `LocalBackends` (`conclave/providers.py`), Codex/OpenCode subprocess backends | HTTP providers with credentials from a secret store, injected by a factory. A factory-backed host serves one backend, so it refuses a member or step that names another one rather than substituting it |
| A member or step chooses its backend, and the picker offers what this machine reports | `/api/runtime` `backends` (`conclave/application/service.py`), Ollama's installed models read from its own server | The deployment's own catalogue and model list; the UI renders from whatever the host reports and offers no invented model IDs |
| Identity is the OS user | `reviewer=getpass.getuser()` (`conclave/application/service.py`) | Principal from authentication, threaded through every route |
| Storage is local SQLite in a data dir | `conclave/application/runs.py`, `conclave/application/workspace.py`, `sources.json` | Managed database plus object storage, keyed by tenant |
| Transport is loopback-only | `start_server` binds `127.0.0.1` and rejects any other Host (`conclave/api.py`) | ASGI adapter; the loopback Host check stays in the local adapter |
| Auth is one shared bearer token | `setting("API_TOKEN")` | Per-user sessions/tokens; authorization on every endpoint |
| Events are polled | `features.events: False` in `/api/runtime`; UI polls `/api/runs/{id}/events` | SSE or WebSocket push, with a cursor for replay |
| The UI bundle is served from the checkout | `studio_assets()` (`conclave/api.py`); `studio/` is not in the wheel (`pyproject.toml`) | Separately deployed static artifact; base URL plus auth in the UI |
| Sources are host folders, paths shown to the operator | `sources.json`, `GET/PUT /api/settings/sources` | Per-tenant resource registry (object storage, Git/GitHub app installs); never a host path |
| UI copy says "this computer" / "This folder" | `studio/web/live.js`, `conclave/application/resources.py` | Copy and options render from the reported profile |

## Invariants that must never move into a sandbox

A sandbox is untrusted by default. If any of these decisions execute inside one,
a compromised or buggy sandbox can escalate. They stay in the control plane, and
the sandbox receives only short-lived, scoped grants.

1. **Capability and approval decisions** — the `WRITE`/unknown gate and every
   approver callback. A tool call from a sandbox is authorized outside it, then
   executed inside with a scoped grant.
2. **Budget accounting** — `RunBudget` is mutable accounting and must have exactly
   one owner. Refusals continue to come back to the model as text.
3. **Shared-store authority** — agents receive copies; the orchestrator writes.
4. **Source and resource authorization** — which tenant may read which folder,
   repository or connector, revalidated on resume.
5. **Tenant scoping and audit** — every run, event, review and stored object
   carries its tenant and principal; nothing trusts a sandbox's claim about them.

## Seams

### S0 — `HostProfile` (landed)

`conclave/application/profile.py` defines what a host may assume about where it
runs: `detect_backend`, `implicit_source`, `serve_ui`. Selected with
`CONCLAVE_PROFILE` (`local`, the default, or `hosted`). Reported by
`GET /api/runtime` as `profile`, so the UI renders from it.

This is the smallest switch that keeps local conveniences local: hosted mode
requires an explicit provider or factory, does not treat the working folder as a
source, and does not serve the UI bundle.

What landed with it: `LocalRunHost.profile` (default `LOCAL` when a host is built
directly), `runtime.profile` and a `runtime.local` that is derived from the
profile so the older flag cannot disagree with it, `startup_lines` naming the
API-only case, and the Run page rendering its banner, pill and provider message
from `runtime.profile` — a host that reports no profile is treated as local.

### S1 — Principal, tenancy and the ASGI adapter (landed)

- `conclave/application/principal.py`: `Principal(name, tenant)`, resolved from
  `CONCLAVE_PRINCIPAL`/`CONCLAVE_TENANT`, defaulting to the OS user in the
  `local` tenant. Tenant names are validated slugs because they become
  directory names.
- `conclave/application/storage.py`: `StoragePaths`, `tenant_paths()` and the
  `RunStore`/`WorkspaceStore` protocols a host actually uses, so a managed
  database or one schema per tenant can replace SQLite without touching the
  application service.
- **Storage is scoped by tenant instance, not by a column.** The `local` tenant
  keeps the files this checkout already has; every other tenant gets
  `<data_dir>/tenants/<tenant>/`, including its own `sources.json`. Nothing had
  to migrate, and a host physically cannot open another tenant's data.
  `CONCLAVE_RUN_DATABASE`/`CONCLAVE_DATABASE` stay single-operator overrides and
  apply to the local tenant only.
- The acting identity is per request: `dispatch(..., principal=...)` and
  `handle(..., principal=...)`, defaulting to the host's own principal, because
  several people may act inside one tenant.
- `conclave/asgi.py`: a dependency-free ASGI app over the same `dispatch`
  contract — bearer-token auth, an origin allowlist with preflight, 413 on an
  oversized body, 415 for a non-JSON write, and no loopback Host check (that
  stays in the local adapter). `TenantAPIs` builds one service per tenant on
  demand; `configured_application()` reads `CONCLAVE_API_TOKEN` and
  `CONCLAVE_UI_ORIGINS`, and any ASGI server can serve it
  (`uvicorn --factory conclave.asgi:configured_application`).
- Tenant headers are never trusted on their own. A deployment supplies
  `principal_for` to map a caller *it* verified to a tenant; without one, the
  host serves its own tenant and a caller naming another changes nothing.

Still open in this area — do not treat it as finished:

- Real user authentication and per-user authorization *inside* a tenant. The
  adapter assumes the deployment authenticates callers, or uses a shared token.
- A managed-database implementation of the two protocols; SQLite per tenant is
  the only implementation today.
- Per-tenant resources and connector credentials: source folders are still
  host-level, so a hosted deployment must not expose file/Git/GitHub tools to
  tenants it does not trust until S4 lands.
- Run claims are still in-process (`RunRepository._claim`), so two replicas must
  not serve the same tenant until the claim moves into the database.

### S2 — Tool execution over a transport (landed)

```python
@dataclass(frozen=True)
class ToolInvocation:          # name, arguments, run_id, agent — nothing else
    ...

class ToolExecutor(Protocol):
    async def execute(self, invocation: ToolInvocation) -> str: ...
```

- The registry keeps the capability and approval decisions and the output cap,
  and delegates only the *call*: `ToolRegistry(max_output_chars, executor=...)`.
  `executor=None` is today's behavior, so a single-process host is unchanged.
  The runtime passes the run and agent, so an executor can scope itself to a run
  rather than to the host.
- `ToolInvocation` carries the call and nothing that could authorize it: no
  approver, no budget, no store. Those decided whether the call may run, and a
  test asserts that absence against the dataclass's own fields.
- `conclave/application/sandbox.py` holds both sides of the boundary: `Grant`
  (the tools a run may use, and the source it may read), `SandboxExecutor` (the
  control plane), `serve_call` (the sandbox) and a `SandboxTransport` protocol
  the deployment implements. `InProcessTransport` and `in_process_sandbox()`
  exercise the boundary without a second process; they are not isolation.
- **Both sides enforce the grant.** The control plane refuses to send a call
  outside it, and the sandbox refuses one that arrives anyway, so neither a bug
  nor a forged request becomes an ungranted tool call. The grant is minted from
  the same binding the control plane resolved, so the sandbox reads what the
  request pinned.
- Truncation stays on the control plane: a sandbox does not decide how much text
  reaches a prompt. A failing sandbox is a tool error the run survives, not a
  crash, and a refused approval or an exhausted budget never reaches it at all.

Still open in this area:

- **Source material inside the sandbox.** `InProcessTransport` runs against the
  host's own bound tools, so the material is shared. A real kernel needs the
  control plane to mount or upload the granted source, and to build its own
  tools from it — the containment and extension filters then apply there too.
- **Transport authentication.** `serve_call` enforces the grant it is handed but
  cannot tell a legitimate control plane from a forger, so the transport must
  authenticate it (mTLS, a signed grant, a private network).
- **Provisioning and pooling** (per call or per run) is still an open question
  below; nothing here depends on the answer.
- A sandbox still sees the arguments of a granted call. If a deployment hands
  credentials to a tool, they travel with that call.

### S3 — Per-agent sandbox (landed)

The loop is now movable: it holds no authority, so it can run where the
provider, the budget and the approver are not.

- `conclave/authority.py`: `RunAuthority` (the model call, the budget, the
  permission, the approval) and `InProcessAuthority` — today's behavior, with
  all four in one process. `ToolAgentRuntime(..., authority=...)` defaults to
  it, so nothing existing changed.
- `ModelCall` carries the run, the agent, the messages and the backend the
  agent declared; a deployment decides where a prompt may be assembled and sent,
  and which backend serves it.
- `conclave/application/sandbox.py`: `ControlPlaneAuthority` holds the provider,
  the budget and the approver and answers over a transport; `SandboxAuthority`
  asks; `sandboxed_loop(...)` builds the loop as a sandbox runs it — no
  provider, no budget, no approver, only a transport.
- **A declared backend is served or refused, never replaced.** A team member or
  a single step may name the backend that runs it (`provider`/`model` on the
  definition). `InProcessAuthority.complete` asks the provider object for that
  backend (`for_call`) and raises when it cannot serve it, so a run never
  quietly moves to another backend. `LocalBackends` is the local answer: every
  backend this machine can reach, with the default one answering calls that
  name nothing. A host that runs exactly one configured backend has no
  `for_call`, so it refuses the choice — and `LocalRunHost._validate_backends`
  refuses it before the run starts, naming the step, the member and what the
  host has instead.
- **The control plane decides from its own declaration.** An approval request
  carries a tool *name*; the control plane looks it up in its own registry, so a
  sandbox that reports a write tool as harmless changes nothing, and a tool the
  control plane has never heard of is refused rather than approved.
- **Depth is declared policy; every counter is remote.** `max_depth` and
  `may_delegate` are answered from the limit that travels with the run, because
  a prompt needs them; `reserve_delegation` — the counter that enforces depth —
  is a question to the control plane. Everything that reads or moves a counter
  is asynchronous for that reason.
- **The event transport.** `event_payload`/`rebuild_event` are the vocabulary's
  own wire form: every declared field crosses, and a payload that does not
  describe an event exactly is refused rather than half-rebuilt. The one thing
  that does not cross is `AgentResult.private` — an agent's scratchpad is its
  own, and only the handoff comes back, which is what the protocol already said.
- `LoopChannel` is the channel both ways: the sandbox asks over `call` and
  reports over `emit`. `run_agent(...)` is what a kernel calls;
  `SandboxedLoop.stream(...)` is the same `stream` a local runtime offers, so
  the application layer does not care where the loop ran, and closing it cancels
  the sandbox rather than leaving it working on something nobody watches.
- The application layer takes the placement as one parameter:
  `run_application(..., loop=...)`, `RunRepository.execute(..., loop=...)` and
  `LocalRunHost(loop=...)` accept a factory with the runtime's own signature. A
  whole application is tested with its loop in a sandbox: the durable record is
  the same one a local run produces, and a sandbox that breaks fails the run
  instead of quietly reporting nothing.
- Equivalence is tested: the same scripted run is driven in-process and through
  the boundary, and the events and the final result match. So is the absence:
  `runtime.provider is None`, `runtime.budget is None`,
  `runtime.approver is None`, and the sandbox's authority has no budget. So is
  the ordering: the decision gates the call (the sandbox asks before it acts),
  and events leave as they happen rather than at the end.

Still open in this area:

- **The kernel's own lifecycle**: a real sandbox needs a session to start, a
  place to wait, and something to decide when it is recycled. `run_agent` is the
  entry point; the process, its pooling and its idle policy are the deployment's,
  and the provisioning question below is still open.
- **Channel authentication**, as for tool calls: `LoopChannel` carries questions
  and events, and nothing in this repository can tell a legitimate control plane
  from a forger.
- The original sketch had the control plane *push* an approval decision
  (`approve(tool_call, decision)`) and a `cancel()`. The loop instead asks
  (`approve`) and the caller cancels by closing the stream, which keeps one
  decision point instead of two; a kernel that needs an explicit cancel message
  can send one and close the channel.
- The application layer's own orchestration still runs in the control plane. If
  a deployment moves *that* into the sandbox as well, the shared store and the
  human review gate become authority questions too.

### S4 — UI (landed)

- **Where it talks to.** `studio/web/api.js` resolves the API base, a token and
  the credentials mode; the default is the host that served the page, which is
  what a local `conclave serve` and the same-origin worker proxy both are. A
  deployment that serves the bundle from elsewhere writes `config.json` beside
  the page — `{"base": "https://api.example.com", "credentials": "include"}` —
  and `credentials: "include"` is what a gateway that authenticates with a
  session cookie needs. A token in that file is visible to anyone who can load
  the page, and the file says so.
- **What this host is.** `profile` decides the copy: a host that reports no
  profile is local, which is the server's default. The tenant is named
  (`HOSTED RUN · acme`, `Conclave workspace · acme`) instead of leaving the
  reader to guess whose workspace this is.
- **Push when the host can push.** `features.events` is now the *transport's*
  answer rather than a constant: the ASGI app streams, so it reports `true`; the
  loopback adapter answers one bounded request per connection, so it reports
  `false`. `GET /api/runs/<id>/stream` is server-sent events over the same
  record, and `RunRepository.watch()` is what feeds it — in-process, with one
  slot per watcher so a slow reader coalesces instead of queueing. The UI follows
  the run by push or by asking, and falls back to asking if a stream fails.
- **Who may name a source.** A hosted tenant cannot `PUT /api/settings/sources`:
  its sources come from the deployment, which is also what keeps one tenant's
  folders away from another. The editor is offered only where it works, and the
  copy says what to do instead.

Still open in this area:

- A tenant resource *registry* in the deployment's terms — uploads, object
  stores, connector accounts — rather than the folders a deployment configures
  today. This is the "per-tenant resources" item from S2 as well.
- The stream is in-process, like the run claim: several replicas serving one
  tenant need a shared signal before push means anything across them.
- The UI still assumes one workspace per host in places (a single member library,
  a single catalogue); neither is scoped by tenant yet.

## Staging

| Stage | Contents | Why now |
| --- | --- | --- |
| S0 | `HostProfile` + profile-aware UI copy | Cheap while the surface is small; stops local defaults from becoming the cloud contract |
| S1 | Principal, tenant-scoped storage, ASGI adapter, storage protocols | Landed; unblocks multi-tenant without touching the runtime |
| S2 | `ToolExecutor` + sandbox tool calls | Landed; needs S1's authorization context, which is why it came second |
| S3 | Per-agent sandbox and push events | Landed; the loop and its events cross a channel, and a deployment picks the placement |
| S4 | Hosted UI (base URL, auth, push, tenant resources) | Landed; the resource registry in the deployment's terms is what is left |

## Open questions

- How is a sandbox provisioned and pooled — cold start per tool call, or a
  long-lived kernel per agent run with calls multiplexed?
- Where does source material live for a sandbox: mounted volume, uploaded object,
  or a fetch-through proxy that keeps the path out of the sandbox entirely?
- Are connector credentials per tenant (each tenant connects its own GitHub
  account) or deployment-wide service accounts?
- Does a tenant's budget map to a billing meter, or is it only a safety stop?
