"""Publishing to Notion through a real connection.

The Notion request code runs against a recording fake of Notion's API, so these
tests check the exact requests, the chunked writes, what a partial write tells
the agent, the allowed pages, and the check a write gets before anyone is asked
to approve it.
"""
import asyncio
import importlib
import json

import pytest
from cryptography.fernet import Fernet

from conclave import ToolRegistry
from conclave.integrations import ConnectionStore, IntegrationError

PARENT = '1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6'
OTHER = '77aa88bb-99cc-4dd0-aa11-bb22cc33dd44'
TARGET = '9f8e7d6c-5b4a-4321-9f8e-7d6c5b4a4321'
NEW = '5c6d7e8f-90ab-4cde-b1f2-a3b4c5d6e7f8'
NEW_URL = 'https://www.notion.so/Weekly-digest-5c6d7e8f90ab4cdeb1f2a3b4c5d6e7f8'
WRITES = ['create_page', 'append_content']
REPORT = '# Weekly digest\n\nCustomers raised **214** conversations.\n\n- Export timeouts\n- SSO loop\n'


def NB():
    return importlib.import_module('conclave.integrations.notion_blocks')


def paragraphs(count):
    return '\n\n'.join(f'Paragraph {n}' for n in range(1, count + 1))


class Notion:
    """Answers like Notion's API and records every request."""

    def __init__(self, *, fail_on=None, page_url=NEW_URL):
        self.calls, self.fail_on, self.page_url = [], fail_on, page_url

    def __call__(self, hosts):
        return self

    async def request(self, method, url, *, headers=None, body=None, params=None, raw=False):
        self.calls.append({'method': method, 'url': url, 'headers': dict(headers or {}), 'body': body})
        if self.fail_on == len(self.calls):
            raise IntegrationError('Provider rate limit reached; retry later')
        if method == 'POST' and url.endswith('/v1/pages'):
            return {'object': 'page', 'id': NEW, 'url': self.page_url}
        if method == 'PATCH':
            return {'object': 'list', 'results': [{'object': 'block', 'id': f'b{len(self.calls)}'}]}
        if method == 'GET':
            return {'object': 'page', 'id': url.rsplit('/', 1)[1]}
        return {}


def store_for(tmp_path, notion, *, pages=None, operations=None):
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key(), transport_factory=notion)
    config = {'pages': pages} if pages is not None else {}
    store.create({'id': 'wiki', 'integration': 'notion', 'name': 'Team wiki', 'applications': ['app'],
                  'operations': operations or ['search', 'read_page', 'read_content'] + WRITES, 'scopes': [],
                  'config': config, 'credentials': {'token': 'PRIVATE-TOKEN'}}, 'tenant', 'alice')
    return store


def invoke(store, operation, arguments):
    return json.loads(asyncio.run(store.invoke('wiki', operation, arguments, 'tenant', 'alice', 'app')))


def bound(store, operation):
    registry = store.bind(ToolRegistry(20000), {'agents': [{'tools': [f'connection.wiki.{operation}']}]},
                          'tenant', 'alice', 'app')
    return registry.tool(f'connection.wiki.{operation}')


# ---------------------------------------------------------------------------
# The two operations
# ---------------------------------------------------------------------------

def test_the_notion_catalogue_has_two_write_operations():
    from conclave.integrations.services import integrations
    operations = {op.name: op for op in integrations()['notion'].operations}
    create, append = operations['create_page'], operations['append_content']
    assert (create.capability, append.capability) == ('write', 'write')
    assert (create.description, append.description) == ('Create report page', 'Append to page')
    assert create.parameters['required'] == ['parent_page_id', 'title', 'markdown']
    assert append.parameters['required'] == ['target_page_id', 'markdown']
    assert create.parameters['properties']['markdown']['maxLength'] == NB().MAX_CHARS
    assert 'paginate' not in create.parameters['properties'], 'writes never follow pages'


def test_create_page_sends_one_request_with_the_converted_report(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion)
    result = invoke(store, 'create_page', {'parent_page_id': PARENT.replace('-', ''), 'title': 'Weekly digest',
                                           'markdown': REPORT})
    [call] = notion.calls
    assert (call['method'], call['url']) == ('POST', 'https://api.notion.com/v1/pages')
    assert call['headers']['Authorization'] == 'Bearer PRIVATE-TOKEN' and call['headers']['Notion-Version']
    sent = call['body']
    assert sent['parent'] == {'page_id': PARENT}
    assert sent['properties']['title']['title'][0]['text']['content'] == 'Weekly digest'
    expected = NB().convert(REPORT, title='Weekly digest')
    assert sent['children'] == expected.blocks, 'the title heading is not repeated; the rest is as converted'
    assert result == {'status': 'created', 'page_id': NEW, 'url': NEW_URL, 'blocks_written': expected.total,
                      'total_blocks': expected.total, 'requests': 1, 'notes': expected.notes}


def test_a_long_report_is_created_then_appended_in_order(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion)
    result = invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'Long', 'markdown': paragraphs(250)})
    assert [(call['method'], call['url']) for call in notion.calls] == [
        ('POST', 'https://api.notion.com/v1/pages'),
        ('PATCH', f'https://api.notion.com/v1/blocks/{NEW}/children'),
        ('PATCH', f'https://api.notion.com/v1/blocks/{NEW}/children')]
    chunks = NB().convert(paragraphs(250), title='Long').chunks
    assert notion.calls[0]['body']['children'] == chunks[0].blocks
    assert [call['body'] for call in notion.calls[1:]] == [{'children': chunk.blocks} for chunk in chunks[1:]]
    assert (result['status'], result['blocks_written'], result['total_blocks'], result['requests']) == ('created', 250, 250, 3)


def test_append_adds_to_the_end_of_the_target_page(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion)
    result = invoke(store, 'append_content', {'target_page_id': TARGET, 'markdown': paragraphs(150)})
    assert [(call['method'], call['url']) for call in notion.calls] == [
        ('PATCH', f'https://api.notion.com/v1/blocks/{TARGET}/children')] * 2
    assert all(set(call['body']) == {'children'} for call in notion.calls), 'always at the end: no "after"'
    assert result['status'] == 'appended' and result['page_id'] == TARGET
    assert result['url'] == 'https://www.notion.so/' + TARGET.replace('-', '')
    assert (result['blocks_written'], result['requests']) == (150, 2)


def test_a_partial_write_says_where_to_resume_and_is_never_retried(tmp_path):
    notion = Notion(fail_on=2)
    store = store_for(tmp_path, notion)
    result = invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'Long', 'markdown': paragraphs(250)})
    assert len(notion.calls) == 2, 'the failed request is not retried, and nothing after it is sent'
    assert result['status'] == 'partial'
    assert (result['blocks_written'], result['total_blocks'], result['requests']) == (100, 250, 2)
    assert result['page_id'] == NEW and result['url'] == NEW_URL
    assert result['error'] == 'Provider rate limit reached; retry later'
    assert result['unwritten_from_line'] == 201
    assert 'append_content' in result['next'] and NEW in result['next'] and '201' in result['next']


def test_a_failed_first_request_fails_the_call(tmp_path):
    notion = Notion(fail_on=1)
    store = store_for(tmp_path, notion)
    with pytest.raises(IntegrationError, match='rate limit'):
        invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'Long', 'markdown': paragraphs(250)})
    assert len(notion.calls) == 1


def test_page_links_are_accepted_and_anything_else_is_refused(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion)
    invoke(store, 'create_page', {'parent_page_id': 'https://www.notion.so/acme/Digests-' + PARENT.replace('-', ''),
                                  'title': 'T', 'markdown': 'x'})
    assert notion.calls[0]['body']['parent'] == {'page_id': PARENT}
    with pytest.raises(IntegrationError):
        invoke(store, 'append_content', {'target_page_id': '../secrets', 'markdown': 'x'})
    assert len(notion.calls) == 1


def test_an_empty_append_sends_nothing(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion)
    with pytest.raises(IntegrationError, match='[Nn]othing'):
        invoke(store, 'append_content', {'target_page_id': TARGET, 'markdown': '\n\n'})
    assert notion.calls == []


# ---------------------------------------------------------------------------
# Allowed pages
# ---------------------------------------------------------------------------

PAGES = [{'id': PARENT.replace('-', ''), 'name': 'Weekly digests'}, {'id': TARGET, 'name': 'Runbook'}]


def test_allowed_pages_limit_where_both_writes_go(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion, pages=PAGES)
    for operation, arguments in [('create_page', {'parent_page_id': OTHER, 'title': 'T', 'markdown': 'x'}),
                                 ('append_content', {'target_page_id': OTHER, 'markdown': 'x'})]:
        with pytest.raises(IntegrationError, match='outside the authorized connection scope'):
            invoke(store, operation, arguments)
    assert notion.calls == []
    invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'T', 'markdown': 'x'})
    invoke(store, 'append_content', {'target_page_id': TARGET.replace('-', ''), 'markdown': 'x'})
    assert len(notion.calls) == 2


def test_allowed_pages_never_limit_what_the_reads_may_read(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion, pages=PAGES)
    invoke(store, 'read_page', {'page_id': OTHER})
    assert notion.calls[0]['method'] == 'GET'


@pytest.mark.parametrize('pages', [
    [{'id': 'not-a-page', 'name': 'Bad'}],
    [{'id': PARENT, 'name': 'x' * 101}],
    [{'id': PARENT, 'name': ''}],
    [{'id': PARENT}],
    [{'id': PARENT, 'name': 'A', 'extra': 1}],
    [{'id': PARENT, 'name': f'P{n}'} for n in range(51)],
    'not a list',
])
def test_allowed_pages_are_validated_when_connecting(tmp_path, pages):
    # The message names the allowed pages: today any `pages` setting is
    # refused as an unknown field, which must not pass for validation.
    with pytest.raises(IntegrationError, match='[Aa]llowed pages'):
        store_for(tmp_path, Notion(), pages=pages)


def test_valid_allowed_pages_are_stored_normalised(tmp_path):
    store = store_for(tmp_path, Notion(), pages=PAGES)
    [connection] = store.list('tenant', 'alice')
    assert connection['pages'] == [{'id': PARENT, 'name': 'Weekly digests'}, {'id': TARGET, 'name': 'Runbook'}]


def test_allowed_pages_are_only_for_notion(tmp_path):
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key())
    with pytest.raises(IntegrationError, match='[Aa]llowed pages'):
        store.create({'id': 'team', 'integration': 'slack', 'name': 'Team', 'applications': ['app'],
                      'operations': ['send_message'], 'scopes': ['chat:write'], 'config': {'pages': PAGES},
                      'credentials': {'token': 'PRIVATE'}}, 'tenant', 'alice')


# ---------------------------------------------------------------------------
# The check before approval, and the preview the approver sees
# ---------------------------------------------------------------------------

def test_a_notion_write_previews_exactly_what_will_be_written(tmp_path):
    from conclave.integrations.notion_blocks import convert
    store = store_for(tmp_path, Notion(), pages=PAGES)
    tool = bound(store, 'create_page')
    preview = asyncio.run(tool.precheck({'parent_page_id': PARENT, 'title': 'Weekly digest', 'markdown': REPORT}))
    expected = convert(REPORT, title='Weekly digest')
    assert preview == {'kind': 'notion_publish', 'operation': 'create_page',
                       'destination': {'page_id': PARENT, 'name': 'Weekly digests', 'allowed': True},
                       'title': 'Weekly digest', 'total_blocks': expected.total, 'requests': 1,
                       'notes': expected.notes, 'blocks': expected.blocks}


def test_without_allowed_pages_the_destination_is_unnamed(tmp_path):
    store = store_for(tmp_path, Notion())
    preview = asyncio.run(bound(store, 'append_content').precheck({'target_page_id': TARGET, 'markdown': paragraphs(150)}))
    assert preview['destination'] == {'page_id': TARGET, 'name': None, 'allowed': None}
    assert preview['operation'] == 'append_content' and preview['title'] is None and preview['requests'] == 2


@pytest.mark.parametrize('operation, arguments, message', [
    ('create_page', {'parent_page_id': OTHER, 'title': 'T', 'markdown': 'x'}, 'outside the authorized'),
    ('create_page', {'parent_page_id': 'weekly', 'title': 'T', 'markdown': 'x'}, 'page'),
    ('create_page', {'parent_page_id': PARENT, 'markdown': 'x'}, 'missing'),
    ('create_page', {'parent_page_id': PARENT, 'title': 'T', 'markdown': '- item\n' * 1001}, '1,000'),
    ('append_content', {'target_page_id': TARGET, 'markdown': ''}, 'othing'),
])
def test_a_write_that_cannot_succeed_is_refused_before_approval(tmp_path, operation, arguments, message):
    from conclave import ToolRefused
    notion = Notion()
    store = store_for(tmp_path, notion, pages=PAGES)
    with pytest.raises(ToolRefused, match=message):
        asyncio.run(bound(store, operation).precheck(arguments))
    assert notion.calls == []


def test_a_revoked_connection_is_refused_before_approval(tmp_path):
    from conclave import ToolRefused
    store = store_for(tmp_path, Notion())
    tool = bound(store, 'create_page')
    store.disconnect('wiki', 'tenant', 'alice')
    with pytest.raises(ToolRefused):
        asyncio.run(tool.precheck({'parent_page_id': PARENT, 'title': 'T', 'markdown': 'x'}))


def test_every_connection_write_is_checked_but_reads_are_not(tmp_path):
    from conclave import ToolRefused
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key())
    store.create({'id': 'team', 'integration': 'slack', 'name': 'Team', 'applications': ['app'],
                  'operations': ['send_message', 'read_conversation'], 'scopes': ['chat:write', 'channels:history'],
                  'config': {'restrictions': {'channel': ['C_ALLOWED']}}, 'credentials': {'token': 'PRIVATE'}},
                 'tenant', 'alice')
    registry = store.bind(ToolRegistry(20000), {'agents': [{'tools': ['connection.team.send_message',
                                                                      'connection.team.read_conversation']}]},
                          'tenant', 'alice', 'app')
    send = registry.tool('connection.team.send_message')
    assert asyncio.run(send.precheck({'channel': 'C_ALLOWED', 'text': 'hi'})) is None, 'checked; nothing to preview'
    with pytest.raises(ToolRefused, match='outside the authorized'):
        asyncio.run(send.precheck({'channel': 'C_OTHER', 'text': 'hi'}))
    with pytest.raises(ToolRefused):
        asyncio.run(send.precheck({'channel': 'C_ALLOWED'}))
    assert registry.tool('connection.team.read_conversation').precheck is None


def test_what_is_written_is_what_was_previewed(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion)
    arguments = {'parent_page_id': PARENT, 'title': 'Long', 'markdown': paragraphs(150) + '\n\n' + REPORT}
    preview = asyncio.run(bound(store, 'create_page').precheck(arguments))
    invoke(store, 'create_page', arguments)
    written = notion.calls[0]['body']['children'] + [block for call in notion.calls[1:] for block in call['body']['children']]
    assert written == preview['blocks']


# ---------------------------------------------------------------------------
# Added in step 5, after coverage showed these branches untested
# ---------------------------------------------------------------------------

def test_a_blank_title_is_refused_before_anything_is_sent(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion)
    with pytest.raises(IntegrationError, match='title'):
        invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': '   ', 'markdown': 'x'})
    assert notion.calls == []


def test_an_unexpected_answer_to_create_fails_the_call(tmp_path):
    class Odd(Notion):
        async def request(self, method, url, **kwargs):
            self.calls.append({'method': method, 'url': url})
            return {'object': 'error'}

    notion = Odd()
    store = store_for(tmp_path, notion)
    with pytest.raises(IntegrationError, match='unexpected page'):
        invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'T', 'markdown': paragraphs(150)})
    assert len(notion.calls) == 1, 'nothing is appended to a page Notion did not confirm'


def test_an_append_failing_on_its_first_request_fails_the_call(tmp_path):
    notion = Notion(fail_on=1)
    store = store_for(tmp_path, notion)
    with pytest.raises(IntegrationError, match='rate limit'):
        invoke(store, 'append_content', {'target_page_id': TARGET, 'markdown': paragraphs(150)})
    assert len(notion.calls) == 1


# ---------------------------------------------------------------------------
# Security N1: conversion never runs on the event loop, and is time-limited
# ---------------------------------------------------------------------------

def test_conversion_runs_off_the_event_loop(tmp_path, monkeypatch):
    import threading
    from conclave.integrations import notion_blocks
    threads, original = [], notion_blocks.convert

    def recording(*args, **kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    monkeypatch.setattr(notion_blocks, 'convert', recording)
    notion = Notion()
    store = store_for(tmp_path, notion)
    arguments = {'parent_page_id': PARENT, 'title': 'T', 'markdown': REPORT}
    asyncio.run(bound(store, 'create_page').precheck(arguments))
    invoke(store, 'create_page', arguments)
    assert len(threads) == 2 and not any(threads), 'both the check and the write convert in a worker thread'


def test_a_conversion_that_takes_too_long_is_refused_and_sends_nothing(tmp_path, monkeypatch):
    import time
    from conclave import ToolRefused
    from conclave.integrations import notion_blocks, services
    original = notion_blocks.convert

    def slow(*args, **kwargs):
        time.sleep(0.5)
        return original(*args, **kwargs)

    monkeypatch.setattr(notion_blocks, 'convert', slow)
    monkeypatch.setattr(services, 'CONVERT_SECONDS', 0.1)
    notion = Notion()
    store = store_for(tmp_path, notion)
    arguments = {'parent_page_id': PARENT, 'title': 'T', 'markdown': REPORT}
    with pytest.raises(ToolRefused, match='too long to convert'):
        asyncio.run(bound(store, 'create_page').precheck(arguments))
    with pytest.raises(IntegrationError, match='too long to convert'):
        invoke(store, 'create_page', arguments)
    assert notion.calls == []


# ---------------------------------------------------------------------------
# Security N3: a page this connection created can be finished
# ---------------------------------------------------------------------------

def test_a_partial_publish_can_be_resumed_under_allowed_pages(tmp_path):
    notion = Notion(fail_on=2)
    store = store_for(tmp_path, notion, pages=PAGES)
    partial = invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'Long', 'markdown': paragraphs(250)})
    assert partial['status'] == 'partial'
    notion.fail_on = None
    preview = asyncio.run(bound(store, 'append_content').precheck({'target_page_id': NEW, 'markdown': 'the rest'}))
    assert preview['destination'] == {'page_id': NEW, 'name': 'Long (created by this connection)', 'allowed': True}
    resumed = invoke(store, 'append_content', {'target_page_id': NEW, 'markdown': 'the rest'})
    assert resumed['status'] == 'appended' and notion.calls[-1]['url'].endswith(f'/v1/blocks/{NEW}/children')


def test_a_created_page_does_not_open_any_other_page(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion, pages=PAGES)
    invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'Digest', 'markdown': 'x'})
    with pytest.raises(IntegrationError, match='outside the authorized'):
        invoke(store, 'append_content', {'target_page_id': OTHER, 'markdown': 'x'})
    [connection] = store.list('tenant', 'alice')
    assert connection['pages'] == [{'id': PARENT, 'name': 'Weekly digests'}, {'id': TARGET, 'name': 'Runbook'}], \
        'the pages a person allowed are unchanged'


def test_only_the_most_recent_created_pages_are_remembered(tmp_path, monkeypatch):
    from conclave.integrations import connections

    class Counting(Notion):
        async def request(self, method, url, **kwargs):
            result = await super().request(method, url, **kwargs)
            if method == 'POST':
                number = len(self.calls)
                return {**result, 'id': f'{number:08x}-0000-4000-8000-000000000000'}
            return result

    monkeypatch.setattr(connections, 'MAX_CREATED_PAGES', 2)
    notion = Counting()
    store = store_for(tmp_path, notion, pages=PAGES)
    for _ in range(3):
        invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'Digest', 'markdown': 'x'})
    with pytest.raises(IntegrationError, match='outside the authorized'):
        invoke(store, 'append_content', {'target_page_id': '00000001-0000-4000-8000-000000000000', 'markdown': 'x'})
    invoke(store, 'append_content', {'target_page_id': '00000003-0000-4000-8000-000000000000', 'markdown': 'x'})


def test_nobody_can_claim_a_page_was_created_by_the_connection(tmp_path):
    with pytest.raises(IntegrationError):
        ConnectionStore(tmp_path / 'c.sqlite', Fernet.generate_key()).create(
            {'id': 'wiki', 'integration': 'notion', 'name': 'Wiki', 'applications': ['app'], 'operations': WRITES,
             'scopes': [], 'config': {'pages': PAGES, 'created_pages': [{'id': OTHER, 'title': 'x'}]},
             'credentials': {'token': 'T'}}, 'tenant', 'alice')


# ---------------------------------------------------------------------------
# Performance PN2: a long publish is paced like Notion expects
# ---------------------------------------------------------------------------

def test_a_long_publish_is_paced(tmp_path):
    import time
    from conclave.integrations import services

    class Timed(Notion):
        async def request(self, method, url, **kwargs):
            result = await super().request(method, url, **kwargs)
            self.calls[-1]['at'] = time.monotonic()
            return result

    assert services.NOTION_REQUEST_GAP == pytest.approx(0.35)
    notion = Timed()
    store = store_for(tmp_path, notion)
    started = time.monotonic()
    invoke(store, 'create_page', {'parent_page_id': PARENT, 'title': 'Long', 'markdown': paragraphs(250)})
    gaps = [after['at'] - before['at'] for before, after in zip(notion.calls, notion.calls[1:])]
    assert len(gaps) == 2 and min(gaps) >= services.NOTION_REQUEST_GAP - 0.02
    assert notion.calls[0]['at'] - started < 0.3, 'the first request is not delayed'
