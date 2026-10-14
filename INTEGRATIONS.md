# Integrations — implementation status

This work extends the existing ToolRegistry rather than introducing another
agent runtime. A connection's permitted operations become ordinary tools named
`connection.<connection-id>.<operation>`. The application stores those references,
not credentials. Runtime READ/NETWORK capabilities remain automatic; all
non-read operations declare WRITE and remain subject to the existing approver.
No approver means denial, not implicit authorization from connection setup.

## Host setup

Set `CONCLAVE_INTEGRATION_KEY` to a Fernet key generated and stored by the
operator. It must survive restarts; losing it makes existing credentials
unreadable. Credentials are encrypted at rest in `connections.sqlite` (0600).
Never commit the key or send it to Studio. For local work, `uv run python dev.py`
generates the key into the ignored `studio/.studio-local/integration.key` on its
first run and reuses it after that, so no setting is needed there.
`CONCLAVE_INTEGRATION_HOSTS` is a
comma-separated deployment allowlist for custom service and retrieval hosts.
Native API hosts are fixed by the adapter; browser configuration cannot change
native API paths. HTTPS only, port 443, no userinfo, no redirects; DNS is checked
for public addresses and the actual socket is pinned to the checked address,
with TLS hostname verification. Responses and calls have finite limits.

The existing Studio proxy is a deployment gateway, not a multi-user login
system. Hosted deployments must protect it with authentication and supply a
verified Conclave principal. A public proxy with one shared backend bearer token
must not be used to manage private account credentials.

Nothing here is switched on by a setting. The bundled write tools are registered
with the read tools and bound to the source folder a run was authorized to use,
so they appear in Studio's tool list and in `/api/runtime` without configuration.
A host built with `configured_host(interactive_approvals=False)` — a terminal, a
headless control plane — refuses a destructive call instead of waiting for a
person who cannot answer.

## Management API

- `GET /api/integrations`: adapter descriptions and actual supported operations.
- `GET /api/connections`: current principal's connections, never secret fields.
- `POST /api/connections`: `{id,integration,name,applications,operations,scopes,
  config,credentials}`. Explicit application IDs and operations are required.
- `DELETE /api/connections/{id}`: remove local authorization and credentials.
- `POST /api/connections/{id}/health` with `{}`: run a granted read probe where
  possible; otherwise report configured, not falsely verified.
- `POST /api/integrations/recommend`: `{goal,application_id}`; suggestions only,
  never grants. Uninstalled adapters remain visibly unavailable.

Multiple connections per service are supported. Tenant and owner are verified
by the server, never accepted from the connection payload. Every call rechecks
application grants, operations, scopes and revocation. Runs pin their acting
owner; other principals cannot inspect or resume their integration runs.
Connection-bound tools are currently refused in non-connection-aware sandboxes.

## Validation levels and limitations

No live provider credentials have been used. Contract tests must not be described
as live verification. Provider scopes supplied with manually configured tokens
are a declared restriction, not proof of provider-side consent: the provider
must enforce token scopes too. The shared OAuth PKCE/state coordinator is a
Python primitive, not yet a complete Studio OAuth redirect/callback experience.
SQL, MCP/OpenAPI and S3 adapters are enabled through deployment-owned
configuration and then use the same Studio grant/assignment UI. Missing drivers
are reported unavailable; browser users cannot choose database hosts or roles.
Inbound workflow webhooks and scheduling are not implemented by this change.
Native provider-side token revocation is not implied by local disconnect.
Automatic mutation retries are deliberately absent to avoid duplicate actions.

A final status matrix will identify implemented operations and remaining work;
this document does not claim all 23 integration requirements are complete.

## Status matrix

All entries are **not live verified**. “Partial” means the definition of done is
not met, even when native HTTP operations are contract-tested.

| Integration | Auth | Read | Write | Studio UI | Automated tests | Live verified |
|---|---|---|---|---|---|---|
| Web Search (Brave) (partial) | api_key; manual credentials | search | None | Config/assign/disconnect | Contract + boundary | No |
| Firecrawl (partial) | api_key; manual credentials | extract | None | Config/assign/disconnect | Contract + boundary | No |
| Google Drive (partial) | google_oauth; manual credentials | search_files, file_metadata, read_file | None | Config/assign/disconnect | Contract + boundary | No |
| Google Docs (partial) | google_oauth; manual credentials | read_document | create_document | Config/assign/disconnect | Contract + boundary | No |
| Google Sheets (partial) | google_oauth; manual credentials | read_spreadsheet, read_range | update_range | Config/assign/disconnect | Contract + boundary | No |
| Slack (partial) | bearer; manual credentials | search_messages, read_conversation | send_message | Config/assign/disconnect | Contract + boundary | No |
| Gmail (partial) | google_oauth; manual credentials | search_messages, read_message | create_draft, send_email | Config/assign/disconnect | Contract + boundary | No |
| Confluence (partial) | atlassian; manual credentials | search_pages, read_page | None | Config/assign/disconnect | Contract + boundary | No |
| Notion (partial) | bearer; manual credentials | search, read_page, read_content | None | Config/assign/disconnect | Contract + boundary | No |
| Zendesk (partial) | zendesk; manual credentials | search_tickets, read_ticket, read_history | prepare_reply | Config/assign/disconnect | Contract + boundary | No |
| HubSpot CRM (partial) | bearer; manual credentials | search_companies, search_contacts, search_deals, read_record | update_record | Config/assign/disconnect | Contract + boundary | No |
| Jira (partial) | atlassian; manual credentials | search_issues, read_issue | create_issue, update_issue | Config/assign/disconnect | Contract + boundary | No |
| GitHub (partial) | bearer; manual credentials | search_repositories, search_issues, read_content, read_issue, read_pull_request, read_branches, read_checks | create_issue, create_branch, write_content, create_pull_request, merge_pull_request | Config/assign/disconnect | Contract + boundary | No |
| Linear (partial) | bearer; manual credentials | search_issues, read_projects | create_issue | Config/assign/disconnect | Contract + boundary | No |
| Trello (partial) | trello; manual credentials | read_boards, read_lists, read_cards | create_card, update_card | Config/assign/disconnect | Contract + boundary | No |
| Reddit (partial) | bearer; manual credentials | read_posts, read_comments, read_community | None | Config/assign/disconnect | Contract + boundary | No |
| RSS / Atom (partial) | none; manual credentials | read_feed | None | Config/assign/disconnect | Contract + boundary | No |
| Webhooks (partial) | bearer; manual credentials | None | send | Config/assign/disconnect | Contract + boundary | No |
| MCP (partial) | host_adapter; manual credentials | Host-configured adapter | None | Configure on host, then grant in Studio | Primitive + connection contracts | No |
| HTTP / OpenAPI (partial) | host_adapter; manual credentials | Host-configured adapter | None | Configure on host, then grant in Studio | Primitive + connection contracts | No |
| PostgreSQL (partial) | host_adapter; manual credentials | Host-configured adapter | None | Configure on host, then grant in Studio | Primitive + connection contracts | No |
| MySQL (partial) | host_adapter; manual credentials | Host-configured adapter | None | Configure on host, then grant in Studio | Primitive + connection contracts | No |
| Amazon S3 (partial) | host_adapter; manual credentials | Host-configured adapter | None | Configure on host, then grant in Studio | Primitive + connection contracts | No |

### Exact remaining scope

- Google/Atlassian OAuth browser callbacks, persistent refresh and provider
  revocation are not wired into connection management. The OAuth flow has
  deterministic state/PKCE/redirect/scope tests only.
- MCP currently offers a restricted HTTP JSON-RPC primitive, not all transports,
  session negotiation or complete Streamable HTTP. Host classification is
  mandatory. It is selectable when the deployment has configured approved operations.
- HTTP/OpenAPI supports host-selected GET/query/path operations and classification
  of mutations; explicit JSON object request bodies are supported. External refs and
  browser schema import are not implemented. No arbitrary URL/header tool is exposed.
- PostgreSQL/MySQL only accept a deliberately restricted SELECT grammar on
  authorized views, bound values, server timeout, read-only transactions and
  100-row fetches. Optional psycopg/pymysql drivers must be installed on the host; deployment
  wiring now makes them selectable in Studio.
- S3 SDK primitive supports bucket/prefix-scoped listing, metadata and bounded
  UTF-8 object reads. boto3 is optional and not installed by default. Temporary role credential refresh remains outstanding; deployment configuration
  now exposes authorized operations in Studio.
- External writes are denied by the current Studio run host, which has no
  interactive per-tool approver. Trusted Python callers can pass an approver;
  the runtime test verifies denial, approval and disconnect. A workflow's human
  review never silently authorizes a later write.
- Notion does not yet write reports (not required by its minimum scope); the
  requested end-to-end monitoring workflow therefore cannot publish to Notion.
- Pagination is bounded and exposes provider cursors where supported; automatic
  page traversal is not universal. Health checks can only probe granted reads
  without required parameters. No live health was performed.
- RSS/Atom reads feeds on deployment-approved public HTTPS endpoints; polling
  subscriptions/scheduling and inbound webhook workflow triggers are absent.
- Native REST contracts were tested using deterministic mocked responses, not
  provider sandboxes. Six full representative workflows A–F remain unverified.

Do not enable this as a public hosted credential-management service without a
verified per-user authentication boundary and a completed security review.

## Deployment-owned protocol, database and storage adapters

Set `CONCLAVE_INTEGRATION_ADAPTERS_FILE` to an operator-controlled JSON file,
read only at startup. Never make this file browser-editable or commit secrets.
Only explicitly selected operations enter discovery; configuring an adapter
never grants it to an agent. Users still create a named connection restricted
to application IDs and operations, then assign those tools to members.

Example non-secret OpenAPI configuration:

```json
{
  "http_openapi": {
    "endpoint": "https://api.example.com",
    "hosts": ["api.example.com"],
    "approved": ["read_items"],
    "spec": {
      "paths": {
        "/items": {
          "get": {"operationId": "read_items", "parameters": []}
        }
      }
    }
  }
}
```

MCP config uses `endpoint`, `hosts`, and an `approved` mapping of remote tool
names to trusted `description`, `capability` (`read` or `write`) and `parameters`
JSON schema. Discovery filters remote tool names against this mapping; remote
descriptions or newly added tools cannot change authority.

PostgreSQL/MySQL config uses `credentials` (driver keyword arguments) and
`views` (authorized table/view names). Install `psycopg` or `pymysql` separately;
use a least-privilege DB account and TLS configured in driver arguments. These
endpoints are deployment-owned, not browser-controlled public network targets.
Both read-only transaction and server-side query timeout are set. Queries
without an explicit limit acquire `LIMIT 100` before execution.

S3 config uses `bucket`, `prefix`, optional `region` and `role_arn`. Install
`boto3` separately. The SDK resolves host credentials; Studio never receives
AWS credentials and cannot change bucket/prefix/role. Authorized buckets are
represented by separate host deployments in this version; multiple bucket
instances in one deployment remain a limitation.

Currently one configured host instance per protocol/database/storage adapter
is supported, with multiple user connection grants over that instance. Native
services continue to support separate credentials per named connection. This
host-instance limitation must be removed before claiming the complete
multiple-account requirement for these five adapters.

## OAuth lifecycle update

`CONCLAVE_INTEGRATION_OAUTH_FILE` now configures shared OAuth providers with
`authorize_url`, `token_url`, optional `revoke_url`, approved `hosts`,
`client_id`, `client_secret`, exact `redirect_uris`, allowed `scopes` and
`integrations` (service IDs permitted to use this provider). The file is
operator-owned secret material, not a browser-editable setting. Google services
can share one provider registration while connections retain their explicit
service-specific scopes.

Studio shows **Authorize account** only for configured provider registrations.
It opens authorization in a separate window. This first implementation requires
pasting the returned callback URL into **Finish account authorization**; it is
not a seamless browser callback receiver. Code/state are cleared from the form
and posted to the authenticated backend. State, owner, tenant, redirect URI,
PKCE and returned scopes are checked. Tokens are persisted encrypted, never
returned to Studio. An expired token refreshes under the connection lock before
an authorized operation; refresh tokens are preserved or rotated when returned.
**Revoke provider access** invokes a configured provider revocation endpoint
before local disconnect. Ordinary Disconnect is local-only.

Endpoints: `POST /api/connections/oauth/begin` accepts
`{connection,provider,redirect_uri}`; callback accepts
`{state,code,redirect_uri}` at `POST /api/connections/oauth/callback`.
`POST /api/connections/{id}/revoke` accepts `{}`.

OAuth states are memory-only and expire after five minutes: restarting the host
requires restarting authorization. The generic authorization-code provider flow
is contract-tested, not live verified. Provider-specific token response shapes,
OAuth app registration/consent and any nonstandard provider flows still need
provider sandbox validation. Interactive per-tool Studio approvals, inbound
workflow triggers, multiple protocol host instances, and full A–F live workflows
remain outstanding. Earlier matrix rows listing manual auth now also support
this opt-in OAuth lifecycle where a compatible provider is configured.

## Tool approvals and multiple host instances update

Studio now displays pending connection-tool actions with agent, tool,
destination/content arguments, and explicit **Approve this action / Reject**
controls. The decision applies to that exact pending invocation once, never to
an agent or workflow generally. Every destructive tool asks the person, not only
a connection operation: a host with an approval surface waits for the answer, and
a host built with `configured_host(interactive_approvals=False)` refuses
immediately instead of holding the run open.
Five-minute approval expiry denies the action. Cancel
or process restart discards pending requests; external actions are never
replayed automatically. Arguments are available only to the authenticated run
owner via the pending-approval endpoint and are not persisted in activity logs.
The audit retains request ID, tool/agent and approval outcome.

`GET /api/runs/{id}/tool-approvals` lists live pending actions;
`POST` accepts `{approval_id,approved}`. Approvals are process-local: deployments
with multiple workers need sticky routing or a shared approval implementation.
The pending action remains within the running stage and does not masquerade
as the existing durable workflow checkpoint. Human workflow approval never
implicitly approves tool calls after resume.

Host adapter config now accepts multiple named instances. For example,
`reports_primary` and `reports_archive` may both specify `integration: postgres`
with independent deployment credentials/views. Each instance has a bounded ID
and optional display `name`. Studio lists instances separately; connection
config selects only `{instance: "reports_primary"}`. Calls and tool capability
classification resolve the selected instance, not another account's operations.
The same mechanism supports multiple MCP/OpenAPI endpoints and S3 buckets/roles.
Host definitions still cannot be edited through browser connection payloads.

These updates supersede earlier limitations describing blanket Studio write
denial and a single host instance per adapter. Remaining gaps include seamless
OAuth redirects, provider-specific live compatibility, inbound triggers,
complete MCP transports/sessions, broader safe SQL/OpenAPI schemas, temporary
S3 role refresh, full A–F workflow validation and browser/live smoke testing.

## Inbound triggers, approvals in Studio, and what the tests prove

Opt-in inbound hooks let an external service start a run that a person already
authorized. Set `CONCLAVE_HOOKS=1` to enable them. A delivery is authenticated
by HMAC-SHA256 over `x-conclave-timestamp` + `.` + the exact body bytes, sent as
`x-conclave-signature`, within a 300-second window, with
`x-conclave-delivery` naming the delivery so one signed request starts one run.
The connection must be a Webhooks connection whose granted operations include
`receive`, and its `signing_secret` credential (at least 16 characters) is the
key. Only the application inputs that connection authorized are read from the
payload, as bounded text; the trigger text comes from `task` or `text`.

An inbound trigger is never an agent tool: `receive` has capability `inbound`,
so binding it to a member is refused and it can never be invoked as an outbound
call. A browser or the Studio proxy cannot reach this path by claiming in a
payload that a delivery was verified; dispatch additionally requires the
transport-verified hook principal. Without the resolver configured, the path
stays behind the ordinary bearer token and refuses everyone.

Deferred on purpose: schedules, cron, queueing, delivery retries, backoff and
provider-specific inbound formats (Slack events, Jira webhooks and similar).
Those need a scheduling/queue runtime this change does not add, and hook
replay protection is process-local, so a multi-worker deployment needs sticky
routing or a shared delivery store.

`tests/test_integration_workflows.py` runs six representative workflows (A–F)
through the real runtime with a scripted provider and a recording HTTP
boundary: a two-service read, a denied-then-approved write, two accounts of one
service staying separate, cross-application and cross-owner refusal, revocation
stopping an already-bound run, and a failed write that is reported rather than
retried. `tests/test_live_integrations.py` holds the opt-in live checks; they
skip unless `CONCLAVE_LIVE_TESTS=1` and that provider's credential are present.
Skipped live checks are not evidence that a provider works. No provider has been
live verified in this work.

## Repository writes: the issue-to-merge flow

Reading an issue, changing the repository, opening a pull request and merging it
is available in two shapes, and a definition may use either. Both are gated the
same way: a destructive call waits for one person's approval of that exact call.

- **Connection operations.** A GitHub connection grants `read_branches` (the
  base branch's current commit, which a new branch is created from),
  `read_checks` (each CI check run of a commit or branch: name, status and
  conclusion),
  `create_branch`, `write_content` (the host base64-encodes plain `text` and
  sends the blob `sha` when replacing an existing file), `create_pull_request`
  and `merge_pull_request` (merge, squash or rebase). `read_content` takes an optional `ref`
  (branch, tag or commit) and returns a
  text file as `text` (decoded from GitHub's base64), a binary file as
  `binary: true` without its bytes, and a directory as its entries. `read_content` and
  `write_content` are the only operations whose path field may contain `/`:
  empty, `.` and `..` segments are refused, and every other placeholder stays a
  single segment. The write reaches GitHub directly, with no checkout on the
  host.
- **Local write tools.** `write_file`, `edit_file` (one exact passage replaced), `run_tests`, `git_commit`,
  `push_branch`, `open_pull_request` and `merge_pull_request` are registered
  with the read tools and bound to the source folder the run was authorized to
  use, so a run changes the folder it may already read and no other — chosen in
  Studio's source settings, not by a deployment setting. Every path is resolved
  and checked back into that folder; hidden paths such as `.git` are refused;
  `push_branch` creates the branch when it does not exist; and `git push`/`gh`
  use the accounts this machine is already signed in to, so the host owns the
  credentials exactly as it does for the read tools. This is the shape to
  choose when the agent should run the tests it just wrote.

Neither shape merges anything a person did not approve: the merge is the last
approved call, not the end of the gate.

## Where integrations appear in the catalogue

Studio's catalogue **Connections** tab lists the services *this host* can run,
grouped by category (Web and research, Documents and knowledge, Communication,
Customer support, Engineering, Data and storage, Automation). Availability comes
from the host, never from the bundled catalogue: an entry is `available` only
when an execution adapter exists, and it says so honestly otherwise:

- **Connected for this application** — you have an authorized connection for the
  application you are editing.
- **Available — not connected yet** — the adapter is installed; connect an
  account to use it.
- **Needs host setup** — MCP, HTTP/OpenAPI, PostgreSQL, MySQL and Amazon S3
  until an operator configures them, or a missing optional driver.

Each entry shows what it can do in plain language ("3 read actions · 2 change
actions needing approval"), and **Connect** opens the connection manager at that
service. Nothing in the catalogue grants access.

A host instance (a named database account, bucket or endpoint) is its own
catalogue entry with its own id, and it names the service it runs. A connection
records which instance it uses, so Studio can show "Archive bucket" rather than
"Amazon S3" and match tools to the right account. Configuration and credentials
still never leave the host.

`/api/runtime` also reports these descriptors, so a template's connector list
shows real availability instead of a bundled guess. Services that templates
mention but this host cannot run stay in a collapsed "Suggested by templates, not
installed on this host" section, labelled as intent rather than capability.
