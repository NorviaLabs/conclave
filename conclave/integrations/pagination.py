"""Following a service's pages in one read, up to a limit.

A read operation returns one page unless the agent asks for more with
`paginate`. This module is the one place that knows how each paginated service
signals its next page, and it combines the pages into one result that says
whether it was cut short and where to continue.

It never applies to anything but a read: `paged` adds the paging arguments only
to an operation whose capability is `read` and that has an entry in `PAGERS`.
"""
import asyncio
import json
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from .transport import IntegrationError

DEFAULT_PAGES = 5
DEFAULT_ITEMS = 500
MAX_PAGES = 20
MAX_ITEMS = 1000
# A hard limit: no page finishing after it is kept, and a request still in
# flight at that moment is abandoned.
DEADLINE_SECONDS = 60
# Pages are paced like separate calls on the connection; ConnectionStore
# enforces the gap, so a paginated read is never faster than the same reads made
# one at a time.
PAGE_GAP_SECONDS = 0.2
# A paginated call may return as much as any connection call may, whatever the
# run's per-tool output limit.
OUTPUT_LIMIT = 100_000
# A token refresh is never started between pages unless this much of the call's
# time is left: cancelling one halfway could lose a refresh token the provider
# has already rotated. It covers the refresh request's own timeout.
REFRESH_ALLOWANCE_SECONDS = 25
# A paginated call may return this many times the run's own tool output limit,
# never more than OUTPUT_LIMIT: a limit a person set lower is scaled, not ignored.
RUN_LIMIT_MULTIPLIER = 5
# Kept free for the pagination block, so an error message or a cursor can always
# be added without pushing the result over its limit.
RESERVE = 256

PAGING_ARGUMENTS = ('paginate', 'max_pages', 'max_items')


class OutOfTime(Exception):
    """Raised by `before_page` when the next page cannot be prepared in the
    time left; the call ends with what it read, as at its time limit."""


def _at(value, path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _confluence_next(result):
    # Confluence pages by offset: a `next` link means more, and the next offset
    # is where this page started plus how many it returned.
    if not _at(result, ('_links', 'next')):
        return None
    try:
        return int(result.get('start', 0)) + int(result.get('size', 0))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Pager:
    """How one operation pages: where its cursor goes, where its list is, how
    it signals a next page, and which of its fields are its own cursor."""

    cursor_argument: str
    items: tuple
    signal: Callable[[dict], Any]
    strip: tuple = ()
    offset: bool = False

    def next_cursor(self, result):
        value = self.signal(result) if isinstance(result, dict) else None
        if value is None or value == '' or isinstance(value, bool):
            return None
        return str(value)


PAGERS = {
    ('gmail', 'search_messages'): Pager('page_token', ('messages',), lambda r: r.get('nextPageToken'),
                                        (('nextPageToken',),)),
    ('google_drive', 'search_files'): Pager('page_token', ('files',), lambda r: r.get('nextPageToken'),
                                            (('nextPageToken',),)),
    ('slack', 'read_conversation'): Pager('cursor', ('messages',), lambda r: _at(r, ('response_metadata', 'next_cursor')),
                                          (('has_more',), ('response_metadata', 'next_cursor'))),
    ('notion', 'search'): Pager('start_cursor', ('results',), lambda r: r.get('next_cursor') if r.get('has_more') else None,
                                (('next_cursor',), ('has_more',))),
    ('notion', 'read_content'): Pager('start_cursor', ('results',), lambda r: r.get('next_cursor') if r.get('has_more') else None,
                                      (('next_cursor',), ('has_more',))),
    ('jira', 'search_issues'): Pager('nextPageToken', ('issues',), lambda r: None if r.get('isLast') else r.get('nextPageToken'),
                                     (('nextPageToken',), ('isLast',))),
    ('confluence', 'search_pages'): Pager('start', ('results',), _confluence_next,
                                          (('start',), ('limit',), ('size',), ('_links', 'next')), offset=True),
    ('reddit', 'read_posts'): Pager('after', ('data', 'children'), lambda r: _at(r, ('data', 'after')),
                                    (('data', 'after'), ('data', 'before'))),
    ('s3', 'list_objects'): Pager('continuation_token', ('objects',), lambda r: r.get('next_page'),
                                  (('next_page',),)),
}


def pager_for(service_id, operation):
    """The pager for a read operation, or None when it does not follow pages."""
    pager = PAGERS.get((service_id, operation.name))
    if pager is None or operation.capability != 'read':
        return None
    if pager.cursor_argument not in operation.parameters.get('properties', {}):
        return None
    return pager


def paged(service_id, operation):
    """The operation with its optional paging arguments, when it can follow pages."""
    if pager_for(service_id, operation) is None:
        return operation
    parameters = dict(operation.parameters)
    parameters['properties'] = {
        **parameters.get('properties', {}),
        'paginate': {'type': 'boolean'},
        'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': MAX_PAGES},
        'max_items': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ITEMS},
    }
    return replace(operation, parameters=parameters)


def describe(service_id, operation):
    """The sentence a paginated tool adds to its description for the agent."""
    pager = pager_for(service_id, operation)
    if pager is None or 'paginate' not in operation.parameters.get('properties', {}):
        return ''
    return (f'. Set paginate to true to follow up to max_pages pages (default {DEFAULT_PAGES}, at most {MAX_PAGES}) '
            f'and max_items items (default {DEFAULT_ITEMS}, at most {MAX_ITEMS}) in one call; the result then starts '
            f'with a pagination block that says whether it was cut short and which {pager.cursor_argument} continues it')


def paginated_output_limit(arguments, run_limit):
    """The output limit of a call that follows pages; None for any other call,
    which keeps the run's own limit. Bound tools carry this exact function, and
    that is how the run record tells Conclave's paginated reads from any other
    tool that happens to take a `paginate` argument."""
    if arguments.get('paginate') is not True:
        return None
    if run_limit is None:
        return OUTPUT_LIMIT
    return min(OUTPUT_LIMIT, run_limit * RUN_LIMIT_MULTIPLIER)


@dataclass(frozen=True)
class Limits:
    max_pages: int
    max_items: int


def _bounded(value, ceiling):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= ceiling


def options(arguments):
    """Split the paging arguments from what the provider is sent.

    Returns the provider's arguments and the limits, or None for a single page.
    Limits without `paginate: true` are refused rather than silently ignored.
    """
    plain = {key: value for key, value in arguments.items() if key not in PAGING_ARGUMENTS}
    paginate = arguments.get('paginate', False)
    if not isinstance(paginate, bool):
        raise IntegrationError('Tool argument has the wrong type')
    asked = {key: arguments[key] for key in ('max_pages', 'max_items') if key in arguments}
    if not paginate:
        if asked:
            raise IntegrationError('Set paginate to true to use max_pages or max_items')
        return plain, None
    max_pages = asked.get('max_pages', DEFAULT_PAGES)
    max_items = asked.get('max_items', DEFAULT_ITEMS)
    if not _bounded(max_pages, MAX_PAGES) or not _bounded(max_items, MAX_ITEMS):
        raise IntegrationError('Tool argument is outside its numeric limits')
    return plain, Limits(max_pages, max_items)


def _combine(pager, first, items, block):
    """The first page's shape with every page's items, without the provider's
    own cursor fields, and with the pagination block first."""
    body = dict(first)

    def edit(path, value=None, *, delete=False):
        node = body
        for key in path[:-1]:
            child = node.get(key)
            if not isinstance(child, dict):
                return
            node[key] = node = dict(child)
        if delete:
            node.pop(path[-1], None)
        else:
            node[path[-1]] = value

    edit(pager.items, items)
    for path in pager.strip:
        edit(path, delete=True)
    combined = {'pagination': block}
    combined.update((key, value) for key, value in body.items() if key != 'pagination')
    return combined


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False))


async def follow(pager, arguments, limits, fetch, *, before_page=None, budget=OUTPUT_LIMIT,
                 clock=time.monotonic, deadline=DEADLINE_SECONDS):
    """Read pages until the service has no more or a limit is reached.

    `fetch(arguments)` reads one page and returns the provider's result, with
    credentials already redacted. `before_page()` runs before every page after
    the first; whatever it raises (a revoked grant, say) fails the whole call.
    A provider error on a later page ends the call with what was read.
    """
    started = clock()
    base = {key: value for key, value in arguments.items() if key != pager.cursor_argument}
    cursor = arguments.get(pager.cursor_argument) or None
    fits_within = budget - RESERVE
    first, items, page_items = None, [], []

    def page_arguments(position):
        return dict(base) if position is None else {**base, pager.cursor_argument: position}

    def block(reason, resume, repeats, counts, total, error=None):
        value = {'pages': len(counts), 'items': total, 'page_items': counts,
                 'max_pages': limits.max_pages, 'max_items': limits.max_items,
                 'truncated': reason != 'exhausted', 'stopped_because': reason, 'next_cursor': resume,
                 'cursor_argument': pager.cursor_argument, 'repeats': repeats,
                 'seconds': round(max(0.0, clock() - started), 1)}
        if error:
            value['error'] = error
        return value

    def finish(reason, resume, repeats=0, error=None):
        return _combine(pager, first, items, block(reason, resume, repeats, list(page_items), len(items), error))

    def resumed_at(position, kept):
        """Where to continue after keeping `kept` items of the page read from `position`."""
        if pager.offset:
            try:
                return str(int(position or 0) + kept), 0
            except ValueError:
                pass
        return position, kept

    while True:
        if first is not None:
            if clock() - started >= deadline:
                return finish('time', cursor)
            if before_page is not None:
                try:
                    await before_page()
                except OutOfTime:
                    return finish('time', cursor)
        remaining = deadline - (clock() - started)
        try:
            result = await asyncio.wait_for(fetch(page_arguments(cursor)), max(0.0, remaining))
        except TimeoutError:
            if first is None:
                raise IntegrationError('Provider request timed out') from None
            return finish('time', cursor)
        except IntegrationError as error:
            if first is None:
                raise
            return finish('error', cursor, error=str(error))
        if clock() - started > deadline:
            # Finished after the limit: the page is not kept.
            if first is None:
                raise IntegrationError('Provider request timed out')
            return finish('time', cursor)
        found = _at(result, pager.items) if isinstance(result, dict) else None
        if not isinstance(found, list):
            if first is None:
                raise IntegrationError('Provider returned an unexpected page')
            return finish('error', cursor, error='Provider returned an unexpected page')
        if first is None:
            first = result
        following = pager.next_cursor(result)
        repeated = following is not None and following == cursor
        before, number = len(items), len(page_items) + 1
        take = min(limits.max_items - before, len(found))

        def outcome(kept):
            """Why the call ends after keeping `kept` of this page, or None to go on."""
            if kept < len(found):
                resume, repeats = resumed_at(cursor, kept)
                return ('output_size' if kept < take else 'max_items'), resume, repeats, None
            if repeated:
                return 'error', None, 0, 'Provider returned the same page cursor again'
            if following is None:
                return 'exhausted', None, 0, None
            if before + kept >= limits.max_items:
                return 'max_items', following, 0, None
            if number >= limits.max_pages:
                return 'max_pages', following, 0, None
            return None

        def size_with(kept):
            ending = outcome(kept) or ('max_pages', following, 0, None)
            reason, resume, repeats, error = ending
            counts = page_items + [kept]
            return _size(_combine(pager, first, items + found[:kept],
                                  block(reason, resume, repeats, counts, before + kept, error)))

        kept = take
        if size_with(kept) > fits_within:
            low, high = 0, take - 1
            while low < high:
                middle = (low + high + 1) // 2
                if size_with(middle) <= fits_within:
                    low = middle
                else:
                    high = middle - 1
            kept = low
        items.extend(found[:kept])
        page_items.append(kept)
        ending = outcome(kept)
        if ending is not None:
            reason, resume, repeats, error = ending
            return finish(reason, resume, repeats, error)
        cursor = following
