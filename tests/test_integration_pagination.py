"""Following a service's pages in one read: the pagination module on its own.

These tests pin the contract agreed in the plan: paging is opt-in through
`paginate`, limits default to 5 pages / 500 items and never exceed 20 / 1000,
one table knows how each service signals its next page, and the combined
result says why it stopped and where to continue. The provider is a fake
`fetch`, so every stop reason is deterministic.
"""
import asyncio
import importlib
import json
import time

import pytest

from conclave.integrations.connections import validate_arguments
from conclave.integrations.services import integrations
from conclave.integrations.transport import IntegrationError


def P():
    """The module under test, imported per test so each one fails on its own."""
    return importlib.import_module('conclave.integrations.pagination')


def run(coroutine):
    return asyncio.run(coroutine)


def size(result):
    return len(json.dumps(result, ensure_ascii=False, allow_nan=False))


# ---------------------------------------------------------------------------
# Fake services: each builds pages exactly as that provider shapes them.
# ---------------------------------------------------------------------------

def token(offset):
    return f'tok-{offset}'


def offset_of_token(cursor):
    return 0 if cursor is None else int(cursor.split('-')[1])


SERVICES = {
    ('gmail', 'search_messages'): dict(
        cursor='page_token', size=100, args={'query': 'label:support'},
        item=lambda i: {'id': f'm{i:05d}', 'threadId': f't{i:05d}'},
        page=lambda items, nxt, offset, total: {'messages': items, **({'nextPageToken': nxt} if nxt else {}),
                                                'resultSizeEstimate': total},
        items=lambda result: result['messages'], gone=[('nextPageToken',)], kept=[('resultSizeEstimate',)]),
    ('google_drive', 'search_files'): dict(
        cursor='page_token', size=100, args={},
        item=lambda i: {'id': f'f{i}', 'name': f'Report {i}', 'mimeType': 'application/pdf'},
        page=lambda items, nxt, offset, total: {**({'nextPageToken': nxt} if nxt else {}), 'files': items},
        items=lambda result: result['files'], gone=[('nextPageToken',)], kept=[]),
    ('slack', 'read_conversation'): dict(
        cursor='cursor', size=100, args={'channel': 'C024BE91L'},
        item=lambda i: {'type': 'message', 'user': 'U1', 'text': f'message {i}', 'ts': f'{i}.0001'},
        page=lambda items, nxt, offset, total: {'ok': True, 'messages': items, 'has_more': bool(nxt), 'pin_count': 0,
                                                'response_metadata': {'next_cursor': nxt or '', 'warnings': ['w']}},
        items=lambda result: result['messages'], gone=[('has_more',), ('response_metadata', 'next_cursor')],
        kept=[('ok',), ('pin_count',), ('response_metadata', 'warnings')]),
    ('notion', 'search'): dict(
        cursor='start_cursor', size=100, args={'query': 'support'},
        item=lambda i: {'object': 'page', 'id': f'p{i}', 'url': f'https://www.notion.so/p{i}'},
        page=lambda items, nxt, offset, total: {'object': 'list', 'results': items, 'next_cursor': nxt,
                                                'has_more': bool(nxt), 'type': 'page_or_database'},
        items=lambda result: result['results'], gone=[('next_cursor',), ('has_more',)], kept=[('object',), ('type',)]),
    ('notion', 'read_content'): dict(
        cursor='start_cursor', size=100, args={'block_id': 'b1'},
        item=lambda i: {'object': 'block', 'id': f'b{i}', 'type': 'paragraph'},
        page=lambda items, nxt, offset, total: {'object': 'list', 'results': items, 'next_cursor': nxt,
                                                'has_more': bool(nxt), 'type': 'block', 'block': {}},
        items=lambda result: result['results'], gone=[('next_cursor',), ('has_more',)], kept=[('type',)]),
    ('jira', 'search_issues'): dict(
        cursor='nextPageToken', size=50, args={'jql': 'project = SUP'},
        item=lambda i: {'id': str(20000 + i), 'key': f'SUP-{i}'},
        page=lambda items, nxt, offset, total: {'issues': items, **({'nextPageToken': nxt} if nxt else {}),
                                                'isLast': not nxt},
        items=lambda result: result['issues'], gone=[('nextPageToken',), ('isLast',)], kept=[]),
    ('confluence', 'search_pages'): dict(
        cursor='start', size=25, args={'cql': 'space = SUP'}, offset=True,
        item=lambda i: {'id': str(884000 + i), 'type': 'page', 'title': f'Page {i}'},
        page=lambda items, nxt, offset, total: {'results': items, 'start': offset, 'limit': 25, 'size': len(items),
                                                '_links': {'base': 'https://acme.atlassian.net/wiki',
                                                           **({'next': f'/rest/api/content/search?start={nxt}'} if nxt else {})}},
        items=lambda result: result['results'], gone=[('start',), ('limit',), ('size',), ('_links', 'next')],
        kept=[('_links', 'base')]),
    ('reddit', 'read_posts'): dict(
        cursor='after', size=100, args={'subreddit': 'SaaS'},
        item=lambda i: {'kind': 't3', 'data': {'id': f'r{i}', 'title': f'Post {i}'}},
        page=lambda items, nxt, offset, total: {'kind': 'Listing', 'data': {'after': nxt, 'dist': len(items),
                                                                              'children': items, 'before': None}},
        items=lambda result: result['data']['children'], gone=[('data', 'after'), ('data', 'before')],
        kept=[('kind',), ('data', 'dist')]),
    ('s3', 'list_objects'): dict(
        cursor='continuation_token', size=100, args={},
        item=lambda i: {'key': f'approved/{i:05d}.json', 'size': 10},
        page=lambda items, nxt, offset, total: {'objects': items, 'next_page': nxt},
        items=lambda result: result['objects'], gone=[('next_page',)], kept=[]),
}


def lookup(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None, False
        value = value[key]
    return value, True


class Source:
    """A provider that pages through `total` items; records what it was asked."""

    def __init__(self, key=('gmail', 'search_messages'), total=730, *, fail_on=None,
                 error='Provider rate limit reached; retry later', item=None, repeat_cursor=False,
                 clock=None, latency=0.0, sleep_on=None, sleep_for=0.0):
        self.spec = SERVICES[key]
        self.total, self.fail_on, self.error = total, fail_on, error
        self.item = item or self.spec['item']
        self.repeat_cursor = repeat_cursor
        self.clock, self.latency = clock, latency
        self.sleep_on, self.sleep_for = sleep_on, sleep_for
        self.seen = []

    def cursor_for(self, offset):
        return str(offset) if self.spec.get('offset') else token(offset)

    def offset_for(self, cursor):
        if cursor is None:
            return 0
        return int(cursor) if self.spec.get('offset') else offset_of_token(cursor)

    async def fetch(self, arguments):
        self.seen.append(dict(arguments))
        number = len(self.seen)
        if self.clock is not None:
            self.clock.now += self.latency
        if self.sleep_on == number:
            await asyncio.sleep(self.sleep_for)
        if self.fail_on == number:
            raise IntegrationError(self.error)
        cursor = arguments.get(self.spec['cursor'])
        offset = self.offset_for(cursor)
        end = min(offset + self.spec['size'], self.total)
        items = [self.item(i) for i in range(offset, end)]
        following = self.cursor_for(end) if end < self.total else None
        if self.repeat_cursor and cursor is not None:
            following = cursor
        return self.spec['page'](items, following, offset, self.total)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def limits(**paging):
    _, found = P().options({'paginate': True, **paging})
    return found


def follow(source, paging=None, arguments=None, **kwargs):
    module = P()
    key = next(key for key, spec in SERVICES.items() if spec is source.spec)
    pager = module.PAGERS[key]
    arguments = dict(source.spec['args'] if arguments is None else arguments)
    return run(module.follow(pager, arguments, limits(**(paging or {})), source.fetch, **kwargs))


# ---------------------------------------------------------------------------
# The limits and the one table
# ---------------------------------------------------------------------------

def test_the_agreed_limits():
    module = P()
    assert (module.DEFAULT_PAGES, module.DEFAULT_ITEMS) == (5, 500)
    assert (module.MAX_PAGES, module.MAX_ITEMS) == (20, 1000)
    assert module.DEADLINE_SECONDS == 60
    assert module.PAGE_GAP_SECONDS == pytest.approx(0.2)
    # A paginated call may return as much as any connection call may.
    assert module.OUTPUT_LIMIT == 100_000
    assert module.RESERVE == 256


def test_one_table_names_every_paginated_read_and_its_cursor():
    module = P()
    assert set(module.PAGERS) == set(SERVICES)
    catalogue = integrations()
    for (service, operation), pager in module.PAGERS.items():
        assert pager.cursor_argument == SERVICES[(service, operation)]['cursor']
        if service == 's3':
            continue  # a host adapter; checked through HostAdapters elsewhere
        spec = next(item for item in catalogue[service].operations if item.name == operation)
        assert spec.capability == 'read', (service, operation)
        assert pager.cursor_argument in spec.parameters['properties'], (service, operation)


def test_paging_arguments_exist_exactly_on_paginated_reads():
    module = P()
    for service in integrations().values():
        for operation in service.operations:
            properties = operation.parameters.get('properties', {})
            paginated = (service.id, operation.name) in module.PAGERS
            for name in ('paginate', 'max_pages', 'max_items'):
                assert (name in properties) == paginated, (service.id, operation.name, name)
            if operation.capability != 'read':
                assert 'paginate' not in properties, f'a {operation.capability} operation never follows pages'


def test_paging_argument_schema_and_bounds():
    module = P()
    gmail = next(item for item in integrations()['gmail'].operations if item.name == 'search_messages')
    properties = gmail.parameters['properties']
    assert properties['paginate'] == {'type': 'boolean'}
    assert properties['max_pages']['type'] == 'integer'
    assert (properties['max_pages']['minimum'], properties['max_pages']['maximum']) == (1, module.MAX_PAGES)
    assert (properties['max_items']['minimum'], properties['max_items']['maximum']) == (1, module.MAX_ITEMS)
    assert gmail.parameters['required'] == [], 'paging adds no required argument'
    validate_arguments({'query': 'x', 'paginate': True, 'max_pages': 20, 'max_items': 1000}, gmail.parameters)
    for bad in [{'max_pages': 0}, {'max_pages': 21}, {'max_items': 0}, {'max_items': 1001},
                {'max_pages': True}, {'max_pages': '5'}, {'paginate': 'yes'}, {'max_items': 2.5}]:
        with pytest.raises(IntegrationError):
            validate_arguments({'paginate': True, **bad}, gmail.parameters)


def test_the_tool_description_tells_the_agent_how_to_page():
    module = P()
    gmail = next(item for item in integrations()['gmail'].operations if item.name == 'search_messages')
    read = next(item for item in integrations()['gmail'].operations if item.name == 'read_message')
    sentence = module.describe('gmail', gmail)
    assert 'paginate' in sentence and 'page_token' in sentence and 'max_items' in sentence
    assert module.describe('gmail', read) == ''
    # The operation's own description is what a person reads in Studio's
    # connection manager; the paging sentence is for the agent's tool only.
    assert gmail.description == 'Search messages'


def test_paged_leaves_other_operations_untouched():
    module = P()
    slack = integrations()['slack']
    send = next(item for item in slack.operations if item.name == 'send_message')
    search = next(item for item in slack.operations if item.name == 'search_messages')
    assert module.paged('slack', send) == send
    assert module.paged('slack', search) == search, 'a read with no cursor argument is out of scope'


# ---------------------------------------------------------------------------
# Opting in
# ---------------------------------------------------------------------------

def test_without_paginate_nothing_changes():
    plain, found = P().options({'query': 'x', 'page_token': 'abc'})
    assert plain == {'query': 'x', 'page_token': 'abc'} and found is None


def test_paginate_false_is_one_page_and_never_reaches_the_provider():
    plain, found = P().options({'query': 'x', 'paginate': False})
    assert plain == {'query': 'x'} and found is None


def test_paginate_alone_uses_the_defaults():
    plain, found = P().options({'query': 'x', 'paginate': True})
    assert plain == {'query': 'x'}
    assert (found.max_pages, found.max_items) == (5, 500)


def test_explicit_limits_are_used_and_stripped():
    plain, found = P().options({'query': 'x', 'paginate': True, 'max_pages': 3})
    assert plain == {'query': 'x'} and (found.max_pages, found.max_items) == (3, 500)
    plain, found = P().options({'paginate': True, 'max_items': 40, 'max_pages': 20})
    assert plain == {} and (found.max_pages, found.max_items) == (20, 40)


@pytest.mark.parametrize('arguments', [{'max_pages': 3}, {'max_items': 50}, {'paginate': False, 'max_items': 50}])
def test_limits_without_paginate_are_refused_with_a_clear_message(arguments):
    with pytest.raises(IntegrationError, match='paginate'):
        P().options({'query': 'x', **arguments})


# ---------------------------------------------------------------------------
# Following pages: every way a call can end
# ---------------------------------------------------------------------------

def test_all_pages_read_combines_the_list_and_drops_the_provider_cursor():
    source = Source(total=250)
    result = follow(source)
    assert list(result)[0] == 'pagination', 'the block comes first so a cut can never lose it'
    paging = result['pagination']
    assert paging['pages'] == 3 and paging['items'] == 250 and paging['page_items'] == [100, 100, 50]
    assert (paging['max_pages'], paging['max_items']) == (5, 500)
    assert paging['truncated'] is False and paging['stopped_because'] == 'exhausted'
    assert paging['next_cursor'] is None and paging['repeats'] == 0
    assert paging['cursor_argument'] == 'page_token'
    assert isinstance(paging['seconds'], (int, float)) and paging['seconds'] >= 0
    assert 'error' not in paging
    assert [item['id'] for item in result['messages']] == [f'm{i:05d}' for i in range(250)]
    assert 'nextPageToken' not in result and result['resultSizeEstimate'] == 250
    assert [call.get('page_token') for call in source.seen] == [None, token(100), token(200)]
    assert all(call['query'] == 'label:support' for call in source.seen)
    assert all(not {'paginate', 'max_pages', 'max_items'} & set(call) for call in source.seen)


def test_the_page_limit_returns_the_cursor_of_the_next_page():
    result = follow(Source(total=730), {'max_pages': 2})
    paging = result['pagination']
    assert (paging['pages'], paging['items'], paging['stopped_because']) == (2, 200, 'max_pages')
    assert paging['truncated'] is True and paging['next_cursor'] == token(200) and paging['repeats'] == 0


def test_the_item_limit_partway_through_a_page_repeats_that_page():
    result = follow(Source(total=730), {'max_items': 250})
    paging = result['pagination']
    assert (paging['pages'], paging['items'], paging['page_items']) == (3, 250, [100, 100, 50])
    assert paging['stopped_because'] == 'max_items' and paging['truncated'] is True
    # The provider's cursor points past page 3, so continuing from page 3's own
    # cursor is the only way not to lose its other 50 messages.
    assert paging['next_cursor'] == token(200) and paging['repeats'] == 50
    assert len(result['messages']) == 250 and result['messages'][-1]['id'] == 'm00249'


def test_the_item_limit_at_a_page_boundary_repeats_nothing():
    paging = follow(Source(total=730), {'max_items': 200})['pagination']
    assert (paging['pages'], paging['items'], paging['stopped_because']) == (2, 200, 'max_items')
    assert paging['next_cursor'] == token(200) and paging['repeats'] == 0


def test_the_item_limit_reached_exactly_on_the_last_page_is_complete():
    paging = follow(Source(total=200), {'max_items': 200})['pagination']
    assert paging['stopped_because'] == 'exhausted' and paging['truncated'] is False
    assert paging['next_cursor'] is None


def test_a_cut_first_page_starts_again_from_the_beginning():
    paging = follow(Source(total=730), {'max_items': 30})['pagination']
    assert (paging['pages'], paging['items'], paging['stopped_because']) == (1, 30, 'max_items')
    assert paging['truncated'] is True and paging['next_cursor'] is None and paging['repeats'] == 30


def test_following_starts_from_the_cursor_the_agent_gives():
    source = Source(total=250)
    result = follow(source, arguments={'query': 'label:support', 'page_token': token(100)})
    assert [call.get('page_token') for call in source.seen] == [token(100), token(200)]
    assert result['pagination']['items'] == 150 and result['pagination']['stopped_because'] == 'exhausted'


def test_confluence_continues_from_the_exact_offset():
    source = Source(('confluence', 'search_pages'), total=200)
    result = follow(source, {'max_items': 60})
    paging = result['pagination']
    assert (paging['pages'], paging['items'], paging['page_items']) == (3, 60, [25, 25, 10])
    assert paging['stopped_because'] == 'max_items' and paging['next_cursor'] == '60' and paging['repeats'] == 0
    assert [call.get('start') for call in source.seen] == [None, '25', '50']
    assert result['_links'] == {'base': 'https://acme.atlassian.net/wiki'}
    assert not {'start', 'limit', 'size'} & set(result)


def test_a_later_provider_error_keeps_what_was_read():
    source = Source(total=730, fail_on=3)
    result = follow(source)
    paging = result['pagination']
    assert (paging['pages'], paging['items'], paging['stopped_because']) == (2, 200, 'error')
    assert paging['truncated'] is True and paging['next_cursor'] == token(200) and paging['repeats'] == 0
    assert paging['error'] == 'Provider rate limit reached; retry later'
    assert len(result['messages']) == 200


def test_a_first_page_error_fails_the_call_as_today():
    with pytest.raises(IntegrationError, match='rate limit'):
        follow(Source(total=730, fail_on=1))


def test_an_authorization_failure_between_pages_fails_the_whole_call():
    source, checks = Source(total=730), []

    async def before_page():
        checks.append(len(source.seen))
        if len(checks) == 2:
            raise IntegrationError('Connection operation is not granted to this application')

    with pytest.raises(IntegrationError, match='not granted'):
        follow(source, before_page=before_page)
    assert checks == [1, 2], 'checked before every page after the first'
    assert len(source.seen) == 2


def test_before_page_runs_between_pages_only():
    source, checks = Source(total=250), []

    async def before_page():
        checks.append(len(source.seen))

    follow(source, before_page=before_page)
    assert checks == [1, 2]


def test_a_service_repeating_its_cursor_cannot_loop():
    source = Source(total=730, repeat_cursor=True)
    result = follow(source, {'max_pages': 20})
    assert len(source.seen) == 2
    paging = result['pagination']
    assert paging['stopped_because'] == 'error' and paging['next_cursor'] is None
    assert 'cursor' in paging['error']


# ---------------------------------------------------------------------------
# The output limit and the reserved room for the block
# ---------------------------------------------------------------------------

def big_item(i):
    return {'id': f'm{i:05d}', 'threadId': 'x' * 180}


def test_the_output_limit_cuts_items_and_keeps_room_for_the_block():
    module = P()
    source = Source(total=730, item=big_item)
    result = follow(source, budget=5000)
    paging = result['pagination']
    assert paging['stopped_because'] == 'output_size' and paging['truncated'] is True
    assert size(result) <= 5000 - module.RESERVE
    one_more = len(json.dumps(big_item(0))) + 2
    assert size(result) > 5000 - module.RESERVE - one_more - 20, 'as many items as fit are kept'
    cut = paging['page_items'][-1]
    assert paging['items'] == sum(paging['page_items']) == len(result['messages'])
    assert paging['repeats'] == cut
    assert paging['next_cursor'] == (None if paging['pages'] == 1 else token(100 * (paging['pages'] - 1)))


def test_a_paginated_call_never_exceeds_the_connection_ceiling():
    module = P()
    source = Source(total=5000, item=big_item)
    result = follow(source, {'max_pages': 20, 'max_items': 1000})
    assert size(result) <= module.OUTPUT_LIMIT - module.RESERVE
    assert result['pagination']['stopped_because'] == 'output_size'
    assert result['pagination']['items'] > 400, 'far more than the 20,000-character run limit would allow'


def test_an_error_after_a_nearly_full_budget_still_fits():
    module = P()
    first_page = {'messages': [big_item(i) for i in range(100)], 'resultSizeEstimate': 730}
    budget = size(first_page) + 400 + module.RESERVE  # page 1 fits, with little to spare
    source = Source(total=730, item=big_item, fail_on=2, error='Provider request failed (HTTP 503)')
    result = follow(source, budget=budget)
    assert result['pagination']['stopped_because'] == 'error' and result['pagination']['items'] == 100
    assert size(result) <= budget, 'the reserved room absorbs the error and the cursor'


# ---------------------------------------------------------------------------
# Sixty seconds is a hard limit
# ---------------------------------------------------------------------------

def test_no_page_finishing_after_the_deadline_is_kept():
    clock = Clock()
    source = Source(total=730, clock=clock, latency=25.0)
    result = follow(source, {'max_pages': 20}, clock=clock)
    paging = result['pagination']
    # Pages finish at 25 s and 50 s; the third would finish at 75 s.
    assert (paging['pages'], paging['items'], paging['stopped_because']) == (2, 200, 'time')
    assert paging['truncated'] is True and paging['next_cursor'] == token(200) and paging['repeats'] == 0
    assert len(result['messages']) == 200


def test_a_slow_page_is_abandoned_at_the_deadline():
    source = Source(total=730, sleep_on=2, sleep_for=5.0)
    started = time.monotonic()
    result = follow(source, {'max_pages': 20}, deadline=0.4)
    assert time.monotonic() - started < 2.0, 'the request in flight does not hold the call past its limit'
    paging = result['pagination']
    assert (paging['pages'], paging['stopped_because'], paging['next_cursor']) == (1, 'time', token(100))


def test_a_first_page_that_misses_the_deadline_fails_as_a_timeout():
    source = Source(total=730, sleep_on=1, sleep_for=5.0)
    started = time.monotonic()
    with pytest.raises(IntegrationError, match='timed out'):
        follow(source, deadline=0.3)
    assert time.monotonic() - started < 2.0


# ---------------------------------------------------------------------------
# Each service's own next-page signal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('key', list(SERVICES), ids=lambda key: '.'.join(key))
def test_each_service_is_followed_to_its_last_page(key):
    spec = SERVICES[key]
    total = spec['size'] * 2 + 7
    source = Source(key, total=total)
    result = follow(source, {'max_pages': 20, 'max_items': 1000})
    paging = result['pagination']
    assert paging['stopped_because'] == 'exhausted' and paging['pages'] == 3 and paging['items'] == total
    assert paging['cursor_argument'] == spec['cursor']
    assert len(spec['items'](result)) == total
    for path in spec['gone']:
        assert not lookup(result, path)[1], f'{".".join(path)} is the provider cursor; only pagination says where to continue'
    for path in spec['kept']:
        assert lookup(result, path)[1], f'{".".join(path)} is service data and stays'
    cursors = [call.get(spec['cursor']) for call in source.seen]
    assert cursors[0] is None and None not in cursors[1:]


@pytest.mark.parametrize('key', list(SERVICES), ids=lambda key: '.'.join(key))
def test_each_service_reports_where_to_continue(key):
    spec = SERVICES[key]
    source = Source(key, total=spec['size'] * 5)
    paging = follow(source, {'max_pages': 2})['pagination']
    assert paging['stopped_because'] == 'max_pages'
    assert paging['next_cursor'] == source.cursor_for(spec['size'] * 2)


# ---------------------------------------------------------------------------
# Defensive edges (added in step 5, after coverage showed them untested)
# ---------------------------------------------------------------------------

def test_an_unexpected_first_page_fails_and_a_later_one_ends_the_call():
    module = P()
    pager = module.PAGERS[('gmail', 'search_messages')]

    async def broken(arguments):
        return {'messages': 'not a list'}

    with pytest.raises(IntegrationError, match='unexpected page'):
        run(module.follow(pager, {}, limits(), broken))

    source = Source(total=730)

    async def second_breaks(arguments):
        return {'nextPageToken': 'x'} if source.seen else await source.fetch(arguments)

    result = run(module.follow(pager, {}, limits(), second_breaks))
    paging = result['pagination']
    assert (paging['pages'], paging['items'], paging['stopped_because']) == (1, 100, 'error')
    assert paging['error'] == 'Provider returned an unexpected page' and paging['next_cursor'] == token(100)


def test_a_first_page_finishing_after_the_deadline_is_a_timeout():
    clock = Clock()
    with pytest.raises(IntegrationError, match='timed out'):
        follow(Source(total=730, clock=clock, latency=61.0), clock=clock)


def test_the_deadline_is_checked_before_asking_for_another_page():
    clock, source = Clock(), Source(total=730)

    async def slow_check():
        clock.now += 61.0  # e.g. a token refresh that took the rest of the minute

    result = follow(source, {'max_pages': 20}, clock=clock, before_page=slow_check)
    assert result['pagination']['stopped_because'] == 'time' and len(source.seen) == 1


def test_options_checks_types_and_bounds_itself():
    module = P()
    for bad in [{'paginate': 'yes'}, {'paginate': True, 'max_pages': 0}, {'paginate': True, 'max_items': 1001},
                {'paginate': True, 'max_pages': True}]:
        with pytest.raises(IntegrationError):
            module.options(bad)


def test_a_restriction_still_applies_to_a_paginated_call(tmp_path):
    from test_integration_pagination_connections import Provider, store_for, invoke
    provider = Provider(('slack', 'read_conversation'), total=250)
    store = store_for(tmp_path, provider, 'slack', extra={'config': {'restrictions': {'channel': ['C_ALLOWED']}}})
    with pytest.raises(IntegrationError, match='outside the authorized'):
        invoke(store, 'read_conversation', {'channel': 'C_OTHER', 'paginate': True})
    assert provider.calls == []
    result = json.loads(invoke(store, 'read_conversation', {'channel': 'C_ALLOWED', 'paginate': True}))
    assert result['pagination']['pages'] == 3
    assert {call['params']['channel'] for call in provider.calls} == {'C_ALLOWED'}


def test_s5_before_page_can_end_the_call_for_lack_of_time():
    module = P()
    source = Source(total=730)

    async def no_time_to_refresh():
        raise module.OutOfTime()

    result = follow(source, {'max_pages': 20}, before_page=no_time_to_refresh)
    paging = result['pagination']
    assert (paging['pages'], paging['items'], paging['stopped_because']) == (1, 100, 'time')
    assert paging['next_cursor'] == token(100) and len(source.seen) == 1
