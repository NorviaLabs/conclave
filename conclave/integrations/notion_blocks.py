"""A report's Markdown as Notion blocks, exactly as they are sent.

Notion's API takes structured blocks, not Markdown. This converts the report
text an agent produced — headings, paragraphs, lists, to-dos, quotes, code,
tables, dividers and inline formatting — and records every change it had to
make, so the person approving the write sees what will not look exactly as
written. It never interprets HTML, never embeds an image (Notion would fetch it
from the internet) and keeps only web and mail links.

The result is split into requests Notion accepts: at most 100 top-level blocks
and a bounded payload each, with one level of nested children.
"""
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .transport import IntegrationError

MAX_BLOCKS = 1000
MAX_CHARS = 200_000
RUN_CHARS = 2000
BLOCKS_PER_REQUEST = 100
# Notion refuses a request over 500 KB; text is sent JSON-escaped, so a
# non-Latin character costs six bytes. Kept well under the limit.
REQUEST_BYTES = 400_000
# Notion accepts at most 100 text runs in one block. A long paragraph, quote or
# code block is continued in another block of the same kind well before that,
# to keep requests small; a block that cannot be split (a heading, a list item,
# a to-do, a table cell) keeps its formatting up to Notion's limit, and beyond
# it keeps all its text with the formatting simplified.
RUNS_PER_BLOCK = 30
NOTION_RUNS = 100
# Notion takes one level of nested children in a request.
NESTING = 1

LANGUAGES = frozenset({
    'abap', 'arduino', 'bash', 'basic', 'c', 'clojure', 'coffeescript', 'c++', 'c#', 'css', 'dart', 'diff',
    'docker', 'elixir', 'elm', 'erlang', 'flow', 'fortran', 'f#', 'gherkin', 'glsl', 'go', 'graphql', 'groovy',
    'haskell', 'html', 'java', 'javascript', 'json', 'julia', 'kotlin', 'latex', 'less', 'lisp', 'livescript',
    'lua', 'makefile', 'markdown', 'markup', 'matlab', 'mermaid', 'nix', 'objective-c', 'ocaml', 'pascal',
    'perl', 'php', 'plain text', 'powershell', 'prolog', 'protobuf', 'python', 'r', 'reason', 'ruby', 'rust',
    'sass', 'scala', 'scheme', 'scss', 'shell', 'sql', 'swift', 'typescript', 'vb.net', 'verilog', 'vhdl',
    'visual basic', 'webassembly', 'xml', 'yaml', 'java/c/c++/c#'})
ALIASES = {'js': 'javascript', 'jsx': 'javascript', 'mjs': 'javascript', 'ts': 'typescript', 'tsx': 'typescript',
           'py': 'python', 'python3': 'python', 'sh': 'shell', 'zsh': 'shell', 'console': 'shell', 'yml': 'yaml',
           'md': 'markdown', 'cpp': 'c++', 'cc': 'c++', 'cs': 'c#', 'csharp': 'c#', 'fs': 'f#', 'golang': 'go',
           'rb': 'ruby', 'rs': 'rust', 'kt': 'kotlin', 'ps1': 'powershell', 'dockerfile': 'docker',
           'objc': 'objective-c', 'htm': 'html', 'text': 'plain text', 'txt': 'plain text',
           'plaintext': 'plain text', '': 'plain text'}

# Every pattern here runs on text an agent wrote, so each is linear: bounded
# lengths, possessive quantifiers and no nested repetition that can backtrack.
FENCE = re.compile(r'^[ \t]{0,3}+(`{3,}+|~{3,}+)[ \t]*+([\w+#./-]{0,40}+)[ \t]*+$')
HEADING = re.compile(r'^[ \t]{0,3}+(#{1,6}+)(?:[ \t]++(.*))?$')
DIVIDER = re.compile(r'^[ \t]{0,3}+([-*_])(?:[ \t]*+\1){2,}+[ \t]*+$')
QUOTE = re.compile(r'^[ \t]{0,3}+>[ \t]?(.*)$')
LIST = re.compile(r'^([ \t]*+)([-*+]|\d{1,9}+[.)])[ \t]++(?:\[([ xX])\][ \t]++)?(.*)$')
TABLE_RULE = re.compile(r'^[ \t]*+\|?[ \t]*+:?-{2,}+:?[ \t]*+(?:\|[ \t]*+:?-{2,}+:?[ \t]*+)*+\|?[ \t]*+$')
HTML = re.compile(r'</?[A-Za-z][^<>\n]{0,200}+>')
PAGE_ID = re.compile(r'[0-9a-fA-F]{32}')
DASHED_ID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
NOTION_HOSTS = re.compile(r'(?:www\.)?notion\.so|[a-z0-9-]+\.notion\.site')

# Inline syntax, found in one pass: escapes, code spans, links and images,
# autolinks, and emphasis markers (paired afterwards, without searching again).
INLINE = re.compile(
    r'\\(?P<escape>[\\`*_{}\[\]()#+\-.!~|>])'
    r'|`(?P<code>[^`]++)`'
    r'|(?P<bang>!?+)\[(?P<label>[^\[\]\n]{0,2000}+)\]\((?P<url>(?:[^()\s]|\([^()\s]{0,2000}+\)){1,2000}+)\)'
    r'|<(?P<auto>(?:https?://|mailto:)[^>\s]{1,2000}+)>'
    r'|(?P<delim>\*\*|__|~~|\*|_)')
EMPHASIS = {'**': 'bold', '__': 'bold', '*': 'italic', '_': 'italic', '~~': 'strike'}
MARKS = ('bold', 'italic', 'strike')
# What a link's text shows when it names an address: an email, a URL or a domain.
SHOWN_EMAIL = re.compile(r'[\w.+-]{1,64}+@((?:[a-z0-9-]{1,63}+\.){1,10}+[a-z]{2,24}+)\b', re.I)
SHOWN_HOST = re.compile(r'\b(?:https?://)?((?:[a-z0-9-]{1,63}+\.){1,10}+[a-z]{2,24}+)\b', re.I)


@dataclass
class Chunk:
    """One request's worth of top-level blocks, and where they start in the Markdown."""
    blocks: list
    start_line: int


@dataclass
class Conversion:
    blocks: list
    total: int
    notes: list
    chunks: list = field(default_factory=list)


def normalize_page_id(value):
    """A Notion page ID — dashed, undashed or in a link to the page — as the
    dashed lower-case form Notion's API uses. Anything else is refused."""
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError('Use a Notion page ID or a link to the page')
    value = value.strip()
    if value.lower().startswith(('https://', 'http://')):
        parts = urlsplit(value)
        if parts.scheme != 'https' or not NOTION_HOSTS.fullmatch(parts.hostname or ''):
            raise IntegrationError('Use a Notion page ID or a link to the page')
        found = PAGE_ID.findall(parts.path)
        if not found:
            raise IntegrationError('Use a Notion page ID or a link to the page')
        raw = found[-1]
    elif DASHED_ID.fullmatch(value):
        raw = value.replace('-', '')
    elif PAGE_ID.fullmatch(value):
        raw = value
    else:
        raise IntegrationError('Use a Notion page ID or a link to the page')
    raw = raw.lower()
    return f'{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}'


class _Notes:
    def __init__(self):
        self.items = []
        # Every link in the report, as (what its text shows, where it goes).
        self.links = []

    def add(self, note):
        if note not in self.items:
            self.items.append(note)


def _safe_url(url, notes):
    url = url.strip()
    if len(url) <= 2000 and not re.search(r'\s', url) and re.match(r'(https?://[^/\s]+|mailto:[^\s]+)', url, re.I):
        return url
    notes.add(f'A link to “{url[:60]}” was kept as plain text: only web and mail links are published.')
    return None


def _host(url):
    """The host a link goes to, as a person reads it: no scheme, no "www."."""
    if url.lower().startswith('mailto:'):
        host = url[7:].split('?')[0].rpartition('@')[2]
    else:
        host = urlsplit(url).hostname or ''
    host = host.lower().rstrip('.')
    return host[4:] if host.startswith('www.') else host


def _shown(text):
    """The host a link's text names, if it names one."""
    match = SHOWN_EMAIL.search(text) or SHOWN_HOST.search(text)
    if not match:
        return None
    host = match.group(1).lower()
    return host[4:] if host.startswith('www.') else host


def _inline(text, notes):
    """Text runs: each a piece of text with its formatting and link.

    One pass finds every piece of syntax; emphasis markers are then paired with
    a stack per kind, and each run's formatting is the count of pairs open over
    it. Nothing is searched twice, so the time is linear in the text."""
    tokens, position = [], 0
    for match in INLINE.finditer(text):
        if match.start() > position:
            tokens.append({'text': text[position:match.start()]})
        if match.group('escape') is not None:
            tokens.append({'text': match.group('escape')})
        elif match.group('code') is not None:
            tokens.append({'text': match.group('code'), 'code': True})
        elif match.group('url') is not None:
            label, image = match.group('label'), match.group('bang') == '!'
            if image:
                notes.add('Images are published as links, never embedded: Notion would fetch them from the internet.')
            url = _safe_url(match.group('url'), notes)
            inner = _inline(label, notes) if label else [{'text': 'image' if image else match.group('url')}]
            if url:
                notes.links.append((''.join(run['text'] for run in inner), url))
                inner = [{**run, 'link': url} for run in inner]
            tokens.append({'runs': inner})
        elif match.group('auto') is not None:
            url = match.group('auto')
            notes.links.append((url, url))
            tokens.append({'text': url, 'link': url})
        else:
            marker = match.group('delim')
            before = text[match.start() - 1] if match.start() else ' '
            after = text[match.end()] if match.end() < len(text) else ' '
            opens, closes = not after.isspace(), not before.isspace()
            if marker[0] == '_':
                # snake_case_words are words, not emphasis.
                opens, closes = opens and not before.isalnum(), closes and not after.isalnum()
            tokens.append({'marker': marker, 'opens': opens, 'closes': closes})
        position = match.end()
    if position < len(text):
        tokens.append({'text': text[position:]})

    open_markers = {marker: [] for marker in EMPHASIS}
    change = {mark: [0] * (len(tokens) + 1) for mark in MARKS}
    for index, token in enumerate(tokens):
        marker = token.get('marker')
        if marker is None:
            continue
        if token['closes'] and open_markers[marker]:
            opener = open_markers[marker].pop()
            tokens[opener]['paired'] = token['paired'] = True
            change[EMPHASIS[marker]][opener + 1] += 1
            change[EMPHASIS[marker]][index] -= 1
        elif token['opens']:
            open_markers[marker].append(index)

    runs, depth = [], dict.fromkeys(MARKS, 0)
    for index, token in enumerate(tokens):
        for mark in MARKS:
            depth[mark] += change[mark][index]
        active = {mark: True for mark in MARKS if depth[mark] > 0}
        if 'marker' in token:
            if not token.get('paired'):
                runs.append({'text': token['marker'], **active})
        elif 'runs' in token:
            runs.extend({**run, **active} for run in token['runs'])
        else:
            if not token.get('code') and HTML.search(token['text']):
                notes.add('HTML in the report is published as plain text, not interpreted.')
            runs.append({**token, **active})
    return runs


def _rich_text(runs):
    """Notion's rich text: neighbouring runs with the same formatting merged,
    then split so no run holds more than 2,000 characters."""
    merged = []
    for run in runs:
        if merged and {k: v for k, v in merged[-1].items() if k != 'text'} == {k: v for k, v in run.items() if k != 'text'}:
            merged[-1] = {**merged[-1], 'text': merged[-1]['text'] + run['text']}
        else:
            merged.append(dict(run))
    out = []
    for run in merged:
        for start in range(0, len(run['text']), RUN_CHARS):
            piece = run['text'][start:start + RUN_CHARS]
            text = {'content': piece}
            if run.get('link'):
                text['link'] = {'url': run['link']}
            out.append({'type': 'text', 'text': text,
                        'annotations': {'bold': bool(run.get('bold')), 'italic': bool(run.get('italic')),
                                        'strikethrough': bool(run.get('strike')), 'underline': False,
                                        'code': bool(run.get('code')), 'color': 'default'}})
    return out


def _runs(rich_text):
    """Notion rich text back into runs, to extend it."""
    out = []
    for item in rich_text:
        marks = item['annotations']
        out.append({'text': item['text']['content'],
                    **({'link': item['text']['link']['url']} if item['text'].get('link') else {}),
                    **{name: True for name, key in (('bold', 'bold'), ('italic', 'italic'), ('strike', 'strikethrough'),
                                                    ('code', 'code')) if marks.get(key)}})
    return out


def _block(kind, rich_text, **extra):
    return {'object': 'block', 'type': kind, kind: {'rich_text': rich_text, **extra}}


def _fit_runs(rich_text, notes):
    """Text for a block that cannot be split: its formatting kept up to
    Notion's limit, and beyond it every character kept as plain text."""
    if len(rich_text) <= NOTION_RUNS:
        return rich_text
    notes.add('Formatting was simplified in a very long line so that Notion accepts it.')
    return _rich_text([{'text': ''.join(run['text']['content'] for run in rich_text)}])


def _text_blocks(kind, rich_text, notes, **extra):
    """One block, or several of the same kind when the text is too long for one."""
    if len(rich_text) <= RUNS_PER_BLOCK:
        return [_block(kind, rich_text, **extra)]
    if kind in ('paragraph', 'quote', 'code'):
        return [_block(kind, rich_text[start:start + RUNS_PER_BLOCK], **extra)
                for start in range(0, len(rich_text), RUNS_PER_BLOCK)]
    return [_block(kind, _fit_runs(rich_text, notes), **extra)]


def _cells(line):
    cells = re.split(r'(?<!\\)\|', line.strip())
    if cells and cells[0].strip() == '':
        cells = cells[1:]
    if cells and cells[-1].strip() == '':
        cells = cells[:-1]
    return [cell.strip().replace('\\|', '|') for cell in cells]


def _starts_block(lines, index):
    line = lines[index]
    return bool(FENCE.match(line) or HEADING.match(line) or DIVIDER.match(line) or QUOTE.match(line)
                or LIST.match(line) or ('|' in line and index + 1 < len(lines) and TABLE_RULE.match(lines[index + 1])))


def _parse(markdown, notes):
    """Top-level blocks, each with the 1-based line it starts on."""
    lines = markdown.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out, index = [], 0
    while index < len(lines):
        line = lines[index]
        start = index + 1
        if not line.strip():
            index += 1
            continue
        fence = FENCE.match(line)
        if fence:
            marker, language = fence.group(1), fence.group(2).lower()
            body, index = [], index + 1
            while index < len(lines) and not lines[index].strip().startswith(marker[0] * len(marker)):
                body.append(lines[index])
                index += 1
            index += 1
            mapped = ALIASES.get(language, language)
            if mapped not in LANGUAGES:
                notes.add(f'Code marked “{language}” is shown as plain text: Notion has no such language.')
                mapped = 'plain text'
            for block in _text_blocks('code', _rich_text([{'text': '\n'.join(body)}]), notes, language=mapped):
                out.append((block, start))
            continue
        heading = HEADING.match(line)
        if heading and heading.group(2) is not None:
            level = len(heading.group(1))
            if level > 3:
                notes.add(f'A level-{level} heading became a level-3 heading: Notion has three levels.')
            words = heading.group(2).strip()
            closing = words.rstrip('#')
            if closing != words and (not closing or closing[-1] in ' \t'):
                words = closing.strip()
            for block in _text_blocks(f'heading_{min(level, 3)}', _rich_text(_inline(words, notes)), notes):
                out.append((block, start))
            index += 1
            continue
        if DIVIDER.match(line):
            out.append(({'object': 'block', 'type': 'divider', 'divider': {}}, start))
            index += 1
            continue
        if QUOTE.match(line):
            quoted = []
            while index < len(lines) and QUOTE.match(lines[index]):
                quoted.append(QUOTE.match(lines[index]).group(1))
                index += 1
            for block in _text_blocks('quote', _rich_text(_inline('\n'.join(quoted), notes)), notes):
                out.append((block, start))
            continue
        if '|' in line and index + 1 < len(lines) and TABLE_RULE.match(lines[index + 1]):
            header = _cells(line)
            rows, index = [header], index + 2
            while index < len(lines) and lines[index].strip() and '|' in lines[index]:
                rows.append(_cells(lines[index]))
                index += 1
            width = len(header)
            if any(len(row) != width for row in rows):
                notes.add('A table row with a different number of cells was padded or cut to the header’s width.')
            children = [{'object': 'block', 'type': 'table_row', 'table_row': {
                'cells': [_fit_runs(_rich_text(_inline(cell, notes)), notes) for cell in (row + [''] * width)[:width]]}}
                for row in rows]
            out.append(({'object': 'block', 'type': 'table', 'table': {
                'table_width': width, 'has_column_header': True, 'has_row_header': False, 'children': children}}, start))
            continue
        if LIST.match(line):
            stack = []
            while index < len(lines):
                current = lines[index]
                if not current.strip():
                    following = lines[index + 1] if index + 1 < len(lines) else ''
                    if LIST.match(following) and not DIVIDER.match(following):
                        index += 1
                        continue
                    break
                # "* * *" and "- - -" are dividers, not list items.
                item = None if DIVIDER.match(current) else LIST.match(current)
                if not item:
                    if stack and re.match(r'^\s{2,}\S', current) and not _starts_block(lines, index):
                        # An indented line continues the item above it.
                        body = stack[-1][1][stack[-1][1]['type']]
                        runs = _runs(body['rich_text']) + [{'text': ' '}] + _inline(current.strip(), notes)
                        body['rich_text'] = _fit_runs(_rich_text(runs), notes)
                        index += 1
                        continue
                    break
                indent = len(item.group(1).replace('\t', '    '))
                kind = ('to_do' if item.group(3) is not None
                        else 'numbered_list_item' if item.group(2)[0].isdigit() else 'bulleted_list_item')
                extra = {'checked': item.group(3) in ('x', 'X')} if kind == 'to_do' else {}
                block = _block(kind, _fit_runs(_rich_text(_inline(item.group(4), notes)), notes), **extra)
                while stack and stack[-1][0] >= indent:
                    stack.pop()
                if len(stack) > NESTING:
                    notes.add('List items nested more than two levels deep were moved up to the second level.')
                    del stack[NESTING:]
                if stack:
                    stack[-1][1][stack[-1][1]['type']].setdefault('children', []).append(block)
                else:
                    out.append((block, index + 1))
                stack.append((indent, block))
                index += 1
            continue
        paragraph = []
        while index < len(lines) and lines[index].strip() and (not paragraph or not _starts_block(lines, index)):
            current = lines[index]
            hard = current.endswith(('  ', '\\'))
            content = current[:-1] if current.endswith('\\') else current
            paragraph.append(content.strip() + ('\n' if hard else ' '))
            index += 1
        text = ''.join(paragraph).rstrip(' \n')
        for block in _text_blocks('paragraph', _rich_text(_inline(text, notes)), notes):
            out.append((block, start))
    return out


def _plain(block):
    body = block.get(block['type'], {})
    return ''.join(run['text']['content'] for run in body.get('rich_text', []))


def _count(block):
    body = block.get(block['type'], {})
    return 1 + sum(_count(child) for child in body.get('children', []))


def count_blocks(blocks):
    """Every block, nested ones and table rows included."""
    return sum(_count(block) for block in blocks)


def _same_title(text, title):
    def norm(value):
        return ' '.join(value.split()).casefold()
    return norm(text) == norm(title)


def convert(markdown, *, title=None):
    """The report as Notion blocks, the changes made, and the requests to send."""
    if not isinstance(markdown, str):
        raise IntegrationError('The report must be Markdown text')
    if len(markdown) > MAX_CHARS:
        raise IntegrationError(f'The report is {len(markdown):,} characters; at most {MAX_CHARS:,} '
                               'can be published in one call')
    notes = _Notes()
    placed = _parse(markdown, notes)
    if title and placed and placed[0][0]['type'].startswith('heading_') and _same_title(_plain(placed[0][0]), title):
        placed = placed[1:]
        notes.add('The first heading repeats the page title, so it was left out.')
    for shown_text, url in notes.links:
        shown, real = _shown(shown_text), _host(url)
        if shown and real and shown != real:
            notes.add(f'A link shows {shown} but goes to {real}.')
    hosts = list(dict.fromkeys(host for host in (_host(url) for _, url in notes.links) if host))
    if hosts:
        notes.add('Links to: ' + ', '.join(hosts))
    blocks = [block for block, _ in placed]
    total = count_blocks(blocks)
    if total > MAX_BLOCKS:
        raise IntegrationError(f'The report makes {total:,} Notion blocks; at most {MAX_BLOCKS:,} '
                               'can be published in one call')
    chunks, current, size = [], None, 0
    for block, line in placed:
        cost = len(json.dumps(block)) + 2
        if cost + 20 > REQUEST_BYTES:
            raise IntegrationError('One part of the report is too large for a single Notion request')
        if current is None or len(current.blocks) >= BLOCKS_PER_REQUEST or size + cost > REQUEST_BYTES - 20:
            current, size = Chunk([], line), 0
            chunks.append(current)
        current.blocks.append(block)
        size += cost
    return Conversion(blocks, total, notes.items, chunks)
