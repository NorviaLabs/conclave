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
| Notion (partial) | bearer; manual credentials | search, read_page, read_content | create_page, append_content | Config/assign/disconnect | Contract + boundary | No |
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

## Following pages in one read

A read returns one page unless the agent asks for more. The reads that can
follow a service's pages take three optional arguments: `paginate` (true to
follow), `max_pages` (1–20, default 5) and `max_items` (1–1000, default 500).
`max_pages` or `max_items` without `paginate: true` is refused rather than
ignored, and the paging arguments are never sent to the provider. Without
`paginate` a call is exactly what it was before: one page, the provider's own
shape and the run's tool output limit.

| Service | Operation | Cursor argument | Next page signalled by |
|---|---|---|---|
| Gmail | search_messages | page_token | `nextPageToken` |
| Google Drive | search_files | page_token | `nextPageToken` |
| Slack | read_conversation | cursor | `response_metadata.next_cursor` |
| Notion | search, read_content | start_cursor | `has_more` and `next_cursor` |
| Jira | search_issues | nextPageToken | `nextPageToken` unless `isLast` |
| Confluence | search_pages | start | `_links.next`; the offset is `start + size` |
| Reddit | read_posts | after | `data.after` |
| Amazon S3 | list_objects | continuation_token | `next_page` |

`conclave/integrations/pagination.py` is the one place that knows these signals
(`PAGERS`). A write, an inbound trigger, a single-object read and a read with no
cursor argument (Slack search, GitHub, HubSpot, Linear, Zendesk, Trello, and
MCP, OpenAPI and SQL adapters) never gain the paging arguments.

The combined result keeps the service's shape, with every page's items in its
list and the provider's own cursor fields removed, and starts with a
`pagination` block:

```json
{"pagination": {"pages": 3, "items": 250, "page_items": [100, 100, 50],
  "max_pages": 5, "max_items": 250, "truncated": true, "stopped_because": "max_items",
  "next_cursor": "…", "cursor_argument": "page_token", "repeats": 50, "seconds": 4.0},
 "messages": […], "resultSizeEstimate": 730}
```

`stopped_because` is one of `exhausted` (the service has no more),
`max_items`, `max_pages`, `output_size`, `time` or `error`. To continue, pass
`next_cursor` as the `cursor_argument`. A service's cursor points past a whole
page, so when a limit cuts a page partway through, `next_cursor` is that page's
own cursor and `repeats` says how many of its items will come back again; a
null `next_cursor` with `repeats` means start again and skip that many.
Confluence pages by offset, so it resumes exactly and repeats nothing.

Limits and safety:

- A paginated call may return five times the run's `max_tool_output_chars`,
  never more than 100,000 characters (the ceiling for any connection call); a
  run with no limit gets 100,000. With the default 20,000 that is 100,000, and
  a limit a person set lower is scaled rather than ignored. Items are kept
  while the result fits with 256 characters to spare, so the block and its
  cursor always fit and the registry never cuts the JSON.
- Sixty seconds is a hard limit for one call: a page still loading is
  abandoned — its connection is shut, so the request stops rather than running
  on in the background — and none finishing later is kept. Amazon S3 requests
  go through boto3, which cannot be interrupted this way; an abandoned S3 page
  still ends within its own 25-second timeout.
- Pages are paced 0.2 s apart like separate calls. The connection's lock is
  held per page, not for the whole call, so another call on the same
  connection (a parallel step, another run) is served between pages rather
  than waiting up to a minute. Before every page the grant, revocation and token are checked again: a
  revoked or narrowed grant fails the whole call and returns nothing, and an
  expiring token is refreshed — but only when at least 25 seconds of the call
  remain, because abandoning a refresh halfway could lose a refresh token the
  provider has already rotated. Otherwise the call stops with `time`.
- Every page is redacted with all the credentials the call has used, so a
  token replaced by a refresh partway is still never returned.
- A provider error on a later page returns what was read, with the error and
  the cursor to retry; on the first page the call fails as before.
- Budgets: one paginated call can make up to 20 provider requests, but it is
  one tool call to the run's `tool_calls` budget. Size that budget, and any
  provider quota, with this in mind.
- The agent's conversation: a result over the run's tool output limit is read
  in full once, with a note that it will be shortened; after the agent's next
  action its copy is cut back to that limit (the `pagination` block, which
  opens the result, always survives). Without this every later model request
  re-sent every long result — three paginated reads made the fourth request
  about 300,000 characters; now about 145,000, growing by the run's limit per
  earlier result. Provider results are redacted in a worker thread, so a large
  page does not stall other runs.

Known follow-up: every page opens a new connection (DNS lookup, TCP and TLS),
typically 50–300 ms a page. Reusing the connection already checked for the
first page, within one call, would save that; it changes the transport and
should be verified against live providers.

Studio shows a paginated call in the step's "Tools used" list (outcome, pages
read, what a limit cut, why it stopped) and in the activity row, and marks the
reads that follow pages in the connection manager. The run record holds an
allow-listed summary only: counts, limits, the reason and whether a cursor was
returned — never the cursor or any item the service returned. Only Conclave's
own paginated reads publish one: an MCP or OpenAPI operation that happens to
take a `paginate` argument cannot present its server's output as a summary.

## Publishing a report to Notion

A workflow publishes its report with two Notion write operations. Both are
external changes: each call waits for one person's approval of that call, no
approver means refusal, and an autonomous application pre-authorises them like
any other declared write.

- `create_page(parent_page_id, title, markdown)` — "Create report page": a new
  page under the parent, titled, with the report as its content.
- `append_content(target_page_id, markdown)` — "Append to page": the report
  added at the end of an existing page.

A page is named by its ID, dashed or not, or by a link to it on notion.so;
anything else is refused before anything is sent.

The report's Markdown becomes Notion blocks:

| Markdown | Notion |
|---|---|
| `#`, `##`, `###` (deeper levels become `###`) | heading 1–3 |
| paragraphs (a hard line break is kept) | paragraph |
| `-` `*` `+` / `1.` / `- [ ]` `- [x]` lists, nested | bulleted / numbered / to-do, two levels deep |
| `>` | quote |
| `---` `***` `___` | divider |
| fenced code with a language | code, the language mapped to Notion's list ("plain text" when unknown) |
| pipe tables | a table with a header row |
| `**bold**` `*italic*` `` `code` `` `~~strike~~` `[link](url)` `<url>` | formatted text |
| `![alt](url)` | a link — never an embedded image, which Notion would fetch from the internet |

Only web and mail links stay links; HTML is published as text, never
interpreted. A first heading that repeats the page's title is left out. Every
change of this kind is listed as a note, shown to the approver and returned to
the agent. So is every domain the report links to, and a warning when a link's
text shows one address (a URL, a domain or an email) but goes to another; the
approval's preview also shows each link's real destination beside it.

The conversion runs on text an agent wrote, so its time grows linearly with the
report whatever it contains (a 199,000-character hostile report converts in
under a second), and it runs in a worker thread, never on the server's event
loop. A report that still takes more than ten seconds is refused.

Limits: 2,000 characters per text run, at most 100 top-level blocks and about
400 KB per request, 1,000 blocks and 200,000 characters per call. A long report
is written in several requests (the page is created with the first, the rest
appended in order). A write is never retried: a failure on the first request
fails the call; a later one returns `status: "partial"` with what was written,
`unwritten_from_line` and a `next` hint, so the agent can append the rest —
after a new approval — without repeating what is already on the page.

**Allowed pages.** A Notion connection can name the pages it may publish to
(`config.pages`, up to 50, each an ID or link and a name). When set, new pages
can only be created under them and content only appended to them; reads are
not limited. The names label the destination in the approval. They are chosen
when connecting; changing them means reconnecting. A page the connection itself
created under an allowed page may also be appended to — so a partly written
report can be finished — and is shown as "‹title› (created by this
connection)"; the server remembers the most recent 200 and never takes that
list from a client.

**Checked before approval.** Every connection write is checked before anyone
is asked to approve it: the grant and its revocation, the arguments,
restrictions, scopes and allowed pages (no token is refreshed here). A write
that cannot succeed is refused with its reason and nothing is sent. For a
Notion publish the check also converts the report; the approval shows the
destination, the title, the block count, the notes and the page as Notion will
show it — drawn from the same blocks that are then written — with the Markdown
and the raw request a click away. Previews from a sandbox are not forwarded.

While a publish waits for approval, Studio polls the pending actions every
second or so; the list carries only the preview's summary (destination, counts,
notes), and the blocks and report are fetched once from
`GET /api/runs/{id}/tool-approvals/{approval_id}`. The preview is drawn once
and left alone by later renders.

A long report's requests are spaced 0.35 s apart — about the three a second
Notion allows on average — so a publish does not end half-written on a rate
limit. A publish holds its connection's lock until its last request; with a very
slow Notion that can be minutes, and other calls on that connection wait. Rich
reports reach the 1,000-block limit at roughly 45–55 KB of Markdown, well before
the character limit: a longer report is published as a page and then appended
to, each with its own approval.

The run record keeps a summary of each publish — status, blocks written and in
total, requests and the page's canonical link (`https://www.notion.so/<id>`,
without the title Notion puts in its own links) — never the report.
A refusal before approval is recorded with its reason.

Not yet verified against live Notion: the nesting depth accepted in one request
(deeper list items are moved up to the second level) and the API's rate limits
for long, many-request reports.

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
