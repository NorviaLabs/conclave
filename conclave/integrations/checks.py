"""Asking a service whether an uncertain write happened.

Each check makes one read, through the same credentials and pinned transport as
the write, and answers found, absent or inconclusive. `absent` is `exact` only
where the read is strongly consistent and the match is unique — the only answer
that may send a write again without a person. Searches that lag behind (Jira,
Linear) and text a service rewrites (Slack) can find an item, but never prove
one missing.

A check may only read what its connection already grants: each names the read
operation it needs, and a connection without that grant gets no check.
"""

from __future__ import annotations

import base64
import email
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from conclave.actions import ActionContext, CheckResult
from .services import authorization, marker
from .transport import IntegrationError


MISSING = object()


class Reader:
    """Read-only requests to one service, with its credentials."""

    def __init__(self, service, config, secrets, transport):
        self.base = config.get('base_url', service.base_url).rstrip('/')
        self.headers = authorization(service, secrets)
        # Trello authenticates with query parameters.
        self.params = {'key': secrets['api_key'], 'token': secrets['token']} if service.auth == 'trello' else {}
        self.transport = transport

    async def get(self, path, params=None):
        """The resource, or MISSING when the service says it does not exist."""
        try:
            return await self.transport.request('GET', self.base + path, headers=self.headers,
                                                params={**self.params, **(params or {})})
        except IntegrationError as error:
            if error.status == 404:
                return MISSING
            raise

    async def post(self, path, body):
        return await self.transport.request('POST', self.base + path, headers=self.headers, body=body)


@dataclass(frozen=True)
class Verifier:
    requires: str
    describe: Callable[[dict], str]
    run: Callable[[Reader, dict, ActionContext], Awaitable[CheckResult]]


def segment(value):
    return quote(str(value), safe='')


def started(context: ActionContext, margin=60) -> datetime:
    """When the action began, less a margin for clocks that disagree."""
    return datetime.fromisoformat(context.started_at).astimezone(timezone.utc) - timedelta(seconds=margin)


def found(summary, detail=''):
    return CheckResult('found', summary, detail=detail or summary)


def absent(summary):
    return CheckResult('absent', summary, exact=True)


def unclear(summary):
    return CheckResult('inconclusive', summary)


# --- GitHub -------------------------------------------------------------------


def repository(arguments):
    return f"/repos/{segment(arguments['owner'])}/{segment(arguments['repo'])}"


async def github_branch(reader, arguments, context):
    branch = arguments['branch']
    result = await reader.get(f"{repository(arguments)}/git/ref/heads/{quote(branch, safe='/')}")
    if result is MISSING:
        return absent(f"Branch {branch} does not exist")
    sha = str((result.get('object') or {}).get('sha') or '')
    if sha and sha == str(arguments['sha']):
        return found(f"Branch {branch} exists at {sha[:7]}")
    return unclear(f"Branch {branch} exists, but at another commit ({sha[:7]})")


async def github_file(reader, arguments, context):
    path, branch = arguments['path'], arguments['branch']
    result = await reader.get(f"{repository(arguments)}/contents/{quote(path, safe='/')}", {'ref': branch})
    if result is MISSING:
        return absent(f"{path} does not exist on {branch}")
    try:
        text = base64.b64decode(result.get('content') or '').decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        text = None
    if text == arguments['text']:
        return found(f"{path} on {branch} has the text that was written")
    return absent(f"{path} on {branch} does not have the text that was written")


def head_filter(arguments):
    head = arguments['head']
    return head if ':' in head else f"{arguments['owner']}:{head}"


async def github_pull_request(reader, arguments, context):
    pulls = await reader.get(f"{repository(arguments)}/pulls",
                             {'head': head_filter(arguments), 'base': arguments['base'], 'state': 'all', 'per_page': 100})
    tag = marker(context.action_id)
    head = arguments['head'].split(':')[-1]
    for pull in pulls if isinstance(pulls, list) else []:
        if tag in str(pull.get('body') or '') or ((pull.get('head') or {}).get('ref') == head
                                                  and (pull.get('base') or {}).get('ref') == arguments['base']):
            number, title, state = pull.get('number'), pull.get('title'), pull.get('state')
            return found(f"Found pull request #{number} “{title}” ({state})",
                         f"#{number} {title} ({state}) {pull.get('html_url') or ''}".strip())
    return absent(f"No pull request from {head} into {arguments['base']}")


async def github_merge(reader, arguments, context):
    number = arguments['number']
    pull = await reader.get(f"{repository(arguments)}/pulls/{segment(number)}")
    if pull is MISSING:
        return unclear(f"Pull request #{number} was not found")
    if pull.get('merged'):
        return found(f"Pull request #{number} is merged")
    if pull.get('state') == 'open':
        return absent(f"Pull request #{number} is open and not merged")
    return unclear(f"Pull request #{number} is closed without being merged")


async def github_issue(reader, arguments, context):
    since = started(context).strftime('%Y-%m-%dT%H:%M:%SZ')
    issues = await reader.get(f"{repository(arguments)}/issues",
                              {'since': since, 'state': 'all', 'per_page': 100, 'sort': 'created', 'direction': 'desc'})
    tag = marker(context.action_id)
    for issue in issues if isinstance(issues, list) else []:
        if tag in str(issue.get('body') or ''):
            return found(f"Found issue #{issue.get('number')} “{issue.get('title')}”",
                         f"#{issue.get('number')} {issue.get('title')} {issue.get('html_url') or ''}".strip())
    return absent('No issue created by this action')


# --- Other services ------------------------------------------------------------


def cells(values):
    return [[str(cell) for cell in row] for row in values or []]


async def sheets_range(reader, arguments, context):
    result = await reader.get(f"/v4/spreadsheets/{segment(arguments['spreadsheet_id'])}/values/{segment(arguments['range'])}")
    if result is not MISSING and cells(result.get('values')) == cells(arguments['values']):
        return found(f"{arguments['range']} holds the values that were written")
    return unclear(f"{arguments['range']} holds other values now")


async def hubspot_record(reader, arguments, context):
    properties = arguments['properties']
    result = await reader.get(f"/crm/v3/objects/{segment(arguments['object_type'])}/{segment(arguments['record_id'])}",
                              {'properties': ','.join(properties)})
    current = (result.get('properties') or {}) if result is not MISSING else {}
    if all(str(current.get(key)) == str(value) for key, value in properties.items()):
        return found(f"Record {arguments['record_id']} has the values that were written")
    return absent(f"Record {arguments['record_id']} does not have the values that were written")


async def jira_update(reader, arguments, context):
    result = await reader.get(f"/rest/api/3/issue/{segment(arguments['issue_id'])}", {'fields': 'summary'})
    summary = ((result.get('fields') or {}).get('summary')) if result is not MISSING else None
    if summary == arguments['summary']:
        return found(f"{arguments['issue_id']} has the summary that was written")
    return absent(f"{arguments['issue_id']} does not have the summary that was written")


def jql_text(value):
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


async def jira_create(reader, arguments, context):
    since = started(context).strftime('%Y/%m/%d %H:%M')
    jql = (f'project = "{jql_text(arguments["project"])}" AND summary ~ "{jql_text(arguments["summary"])}" '
           f'AND created >= "{since}"')
    result = await reader.get('/rest/api/3/search/jql', {'jql': jql, 'fields': 'summary,created', 'maxResults': 10})
    matches = [issue for issue in (result.get('issues') or []) if (issue.get('fields') or {}).get('summary') == arguments['summary']]
    if len(matches) == 1:
        return found(f"Found {matches[0].get('key')} “{arguments['summary']}”")
    return unclear('Jira search found no single match; its search can lag behind')


LINEAR_SEARCH = ('query($title:String!,$since:DateTimeOrDuration!){issues(filter:{title:{eq:$title},'
                 'createdAt:{gte:$since}},first:5){nodes{identifier title url}}}')


async def linear_create(reader, arguments, context):
    result = await reader.post('/graphql', {'query': LINEAR_SEARCH, 'variables': {
        'title': arguments['title'], 'since': started(context).isoformat()}})
    nodes = (((result or {}).get('data') or {}).get('issues') or {}).get('nodes') or []
    if len(nodes) == 1:
        return found(f"Found {nodes[0].get('identifier')} “{nodes[0].get('title')}”")
    return unclear('Linear found no single match; its search can lag behind')


async def trello_card(reader, arguments, context):
    cards = await reader.get(f"/1/lists/{segment(arguments['list_id'])}/cards")
    since = started(context).timestamp()
    for card in cards if isinstance(cards, list) else []:
        try:
            # A Trello id begins with the time it was created.
            created = int(str(card.get('id'))[:8], 16)
        except ValueError:
            continue
        if card.get('name') == arguments['name'] and created >= since:
            return found(f"Found card “{card.get('name')}”")
    return absent(f"No card “{arguments['name']}” created by this action")


def parsed_time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


async def zendesk_note(reader, arguments, context):
    result = await reader.get(f"/api/v2/tickets/{segment(arguments['ticket_id'])}/comments.json")
    since = started(context)
    for comment in (result.get('comments') or []) if result is not MISSING else []:
        created = parsed_time(comment.get('created_at'))
        if comment.get('body') == arguments['text'] and created is not None and created >= since:
            return found(f"Ticket {arguments['ticket_id']} has the note")
    return absent(f"Ticket {arguments['ticket_id']} does not have the note")


async def slack_message(reader, arguments, context):
    result = await reader.get('/api/conversations.history',
                              {'channel': arguments['channel'], 'oldest': f"{started(context).timestamp():.6f}", 'limit': 100})
    if result is not MISSING and result.get('ok') is not False:
        if any(message.get('text') == arguments['text'] for message in result.get('messages') or []):
            return found('The message is in the channel')
    return unclear('The message was not found; Slack may have changed its text')


def message_id(raw):
    try:
        data = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
        return email.message_from_bytes(data).get('Message-ID')
    except (ValueError, TypeError):
        return None


async def gmail_sent(reader, arguments, context):
    identifier = message_id(arguments['raw'])
    if not identifier:
        return unclear('The message has no Message-ID to search for')
    result = await reader.get('/gmail/v1/users/me/messages', {'q': f'rfc822msgid:{identifier}'})
    if result is not MISSING and result.get('messages'):
        return found('The message is in the mailbox')
    return unclear('The message was not found; the mailbox search can lag behind')


def says(text):
    return lambda arguments: text.format(**{key: value for key, value in arguments.items() if isinstance(value, str)})


def safely(text):
    def describe(arguments):
        try:
            return says(text)(arguments)
        except (KeyError, IndexError):
            return text.split('{')[0].strip()
    return describe


VERIFIERS = {
    ('github', 'create_branch'): Verifier('read_branches', safely('Will look on GitHub for the branch {branch}'), github_branch),
    ('github', 'write_content'): Verifier('read_content', safely('Will read {path} on {branch} on GitHub'), github_file),
    ('github', 'create_pull_request'): Verifier('read_pull_request',
                                                 safely('Will look on GitHub for a pull request from {head} into {base}'),
                                                 github_pull_request),
    ('github', 'merge_pull_request'): Verifier('read_pull_request', safely('Will ask GitHub whether pull request {number} is merged'),
                                                github_merge),
    ('github', 'create_issue'): Verifier('read_issue', safely('Will look on GitHub for the issue this action created'), github_issue),
    ('google_sheets', 'update_range'): Verifier('read_range', safely('Will read back {range}'), sheets_range),
    ('hubspot', 'update_record'): Verifier('read_record', safely('Will read back record {record_id}'), hubspot_record),
    ('jira', 'update_issue'): Verifier('read_issue', safely('Will read back {issue_id}'), jira_update),
    ('jira', 'create_issue'): Verifier('search_issues', safely('Will search Jira for “{summary}”'), jira_create),
    ('linear', 'create_issue'): Verifier('search_issues', safely('Will search Linear for “{title}”'), linear_create),
    ('trello', 'create_card'): Verifier('read_cards', safely('Will look in the list for the card “{name}”'), trello_card),
    ('zendesk', 'prepare_reply'): Verifier('read_history', safely('Will look for the note on ticket {ticket_id}'), zendesk_note),
    ('slack', 'send_message'): Verifier('read_conversation', safely('Will look for the message in {channel}'), slack_message),
    ('gmail', 'send_email'): Verifier('search_messages', safely('Will search the mailbox for the message'), gmail_sent),
}


def requirement(service_id: str, operation: str) -> str | None:
    """The read a check of this write needs, or None when there is no check."""
    verifier = VERIFIERS.get((service_id, operation))
    return verifier.requires if verifier else None


def describe(service_id: str, operation: str, arguments: dict) -> str:
    verifier = VERIFIERS.get((service_id, operation))
    return verifier.describe(arguments) if verifier else ''


async def verify(service, operation: str, config: dict, credentials: dict, arguments: dict, context: ActionContext,
                 transport: Any) -> CheckResult:
    """Ask `service` whether this write happened. Never raises: a check that fails could not tell."""

    from .connections import redact

    verifier = VERIFIERS.get((service.id, operation))
    if verifier is None:
        return CheckResult('unavailable', f'{service.name} cannot confirm this automatically')
    try:
        result = await verifier.run(Reader(service, config, credentials, transport), arguments, context)
    except Exception:
        return unclear(f'{service.name} could not be reached to check')
    # What a service echoes back must not repeat a credential.
    secrets = [value for value in credentials.values() if isinstance(value, str) and value]
    return CheckResult(result.result, redact(result.summary, secrets), exact=result.exact, detail=redact(result.detail, secrets))
