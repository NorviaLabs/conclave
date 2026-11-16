"""Markdown report text → Notion blocks, exactly as they are sent.

One test per construct in the agreed conversion table, plus the limits Notion
imposes: 2,000 characters per text run, 100 top-level blocks per request, a
bounded request size, and one level of nested children.
"""
import importlib
import json

import pytest

from conclave.integrations.transport import IntegrationError


def NB():
    return importlib.import_module('conclave.integrations.notion_blocks')


def convert(markdown, **options):
    return NB().convert(markdown, **options)


def body(block):
    return block[block['type']]


def text(block):
    return ''.join(run['text']['content'] for run in body(block)['rich_text'])


def on(run, mark):
    return run.get('annotations', {}).get(mark, False)


def types(blocks):
    return [block['type'] for block in blocks]


# ---------------------------------------------------------------------------
# Block constructs
# ---------------------------------------------------------------------------

def test_headings_map_to_three_levels():
    result = convert('# One\n## Two\n### Three\n#### Four')
    assert types(result.blocks) == ['heading_1', 'heading_2', 'heading_3', 'heading_3']
    assert [text(block) for block in result.blocks] == ['One', 'Two', 'Three', 'Four']
    assert any('heading' in note for note in result.notes), 'the demoted heading is noted'


def test_every_block_is_shaped_as_notion_expects():
    block = convert('Hello').blocks[0]
    assert block['object'] == 'block' and block['type'] == 'paragraph'
    run = block['paragraph']['rich_text'][0]
    assert run['type'] == 'text' and run['text']['content'] == 'Hello'


def test_paragraphs_join_soft_breaks_and_keep_hard_ones():
    result = convert('Line one\nline two\n\nNext paragraph\n\nHard  \nbreak')
    assert types(result.blocks) == ['paragraph'] * 3
    assert text(result.blocks[0]) == 'Line one line two'
    assert text(result.blocks[2]) == 'Hard\nbreak'


def test_bulleted_numbered_and_to_do_lists():
    result = convert('- a\n* b\n+ c\n\n1. one\n2. two\n\n- [ ] open\n- [x] done')
    assert types(result.blocks) == ['bulleted_list_item'] * 3 + ['numbered_list_item'] * 2 + ['to_do'] * 2
    assert [body(block)['checked'] for block in result.blocks[-2:]] == [False, True]
    assert text(result.blocks[-1]) == 'done'


def test_a_nested_list_becomes_children_two_levels_deep_at_most():
    result = convert('- top\n  - second\n    - third\n- next')
    top = result.blocks[0]
    assert types(result.blocks) == ['bulleted_list_item', 'bulleted_list_item']
    children = body(top)['children']
    assert [text(child) for child in children] == ['second', 'third'], 'the third level moves up to the second'
    assert all('children' not in body(child) or not body(child)['children'] for child in children)
    assert any('nested' in note for note in result.notes)


def test_a_list_item_continues_on_an_indented_line():
    result = convert('- first part\n  continues here\n- second')
    assert text(result.blocks[0]) == 'first part continues here'


def test_quotes_and_dividers():
    result = convert('> first\n> second\n\n---\n\n***\n\n___')
    assert types(result.blocks) == ['quote', 'divider', 'divider', 'divider']
    assert text(result.blocks[0]) == 'first\nsecond'
    assert result.blocks[1]['divider'] == {}


def test_fenced_code_keeps_text_and_maps_the_language():
    result = convert('```py\nprint("hi")\n  indented\n```\n\n~~~\nplain\n~~~\n\n```tsx\nx\n```')
    assert types(result.blocks) == ['code', 'code', 'code']
    assert body(result.blocks[0])['language'] == 'python'
    assert text(result.blocks[0]) == 'print("hi")\n  indented'
    assert body(result.blocks[1])['language'] == 'plain text'
    assert body(result.blocks[2])['language'] == 'typescript'
    assert not result.notes, 'known languages need no note'


def test_an_unknown_code_language_is_plain_text_with_a_note():
    result = convert('```brainfuck\n+++\n```')
    assert body(result.blocks[0])['language'] == 'plain text'
    assert any('brainfuck' in note for note in result.notes)


def test_markdown_inside_code_is_literal():
    result = convert('```\n# not a heading\n**not bold**\n```')
    assert types(result.blocks) == ['code'] and text(result.blocks[0]) == '# not a heading\n**not bold**'


def test_tables_become_notion_tables():
    result = convert('| Queue | Count |\n|---|---:|\n| EU | **118** |\n| US |')
    [table] = result.blocks
    assert table['type'] == 'table'
    assert table['table']['table_width'] == 2 and table['table']['has_column_header'] is True
    rows = table['table']['children']
    assert [row['type'] for row in rows] == ['table_row'] * 3
    cells = [[''.join(run['text']['content'] for run in cell) for cell in row['table_row']['cells']] for row in rows]
    assert cells == [['Queue', 'Count'], ['EU', '118'], ['US', '']]
    assert on(rows[1]['table_row']['cells'][1][0], 'bold')
    assert any('table' in note for note in result.notes), 'the short row was padded'


# ---------------------------------------------------------------------------
# Inline formatting and links
# ---------------------------------------------------------------------------

def test_inline_formatting():
    [block] = convert('Plain **bold** *italic* `code` ~~gone~~ _also italic_').blocks
    runs = body(block)['rich_text']
    marked = {run['text']['content']: run for run in runs}
    assert on(marked['bold'], 'bold') and on(marked['italic'], 'italic') and on(marked['also italic'], 'italic')
    assert on(marked['code'], 'code') and on(marked['gone'], 'strikethrough')
    assert not any(on(marked['Plain '], mark) for mark in ('bold', 'italic', 'code', 'strikethrough'))
    assert text(block) == 'Plain bold italic code gone also italic'


def test_links_including_parentheses_and_autolinks():
    [block] = convert('See [the incident](https://status.example.com/a_(b)) and <https://example.com/x>.').blocks
    links = [(run['text']['content'], run['text'].get('link')) for run in body(block)['rich_text']]
    assert ('the incident', {'url': 'https://status.example.com/a_(b)'}) in links
    assert ('https://example.com/x', {'url': 'https://example.com/x'}) in links


def test_only_web_and_mail_links_are_kept():
    [block] = convert('[run](javascript:alert(1)) [mail](mailto:team@example.com) [file](file:///etc/passwd)').blocks
    runs = body(block)['rich_text']
    assert text(block) == 'run mail file', 'the text of a refused link is kept'
    # Text next to a refused link may merge with it into one unlinked run.
    assert [(run['text']['content'], run['text'].get('link')) for run in runs if run['text'].get('link')] == [
        ('mail', {'url': 'mailto:team@example.com'})]
    assert 'javascript' not in json.dumps(convert('[run](javascript:alert(1))').blocks)
    assert any('link' in note for note in convert('[run](javascript:alert(1))').notes)


def test_images_become_links_never_embedded():
    result = convert('![Weekly chart](https://charts.example.com/w.png)')
    assert 'image' not in json.dumps(types(result.blocks))
    run = body(result.blocks[0])['rich_text'][0]
    assert run['text'] == {'content': 'Weekly chart', 'link': {'url': 'https://charts.example.com/w.png'}}
    assert any('Image' in note or 'image' in note for note in result.notes)


def test_html_is_literal_text():
    result = convert('<b>hi</b> <script>alert(1)</script>')
    assert text(result.blocks[0]) == '<b>hi</b> <script>alert(1)</script>'
    assert any('HTML' in note for note in result.notes)


# ---------------------------------------------------------------------------
# Notion's limits
# ---------------------------------------------------------------------------

def test_text_is_split_into_runs_of_at_most_2000_characters():
    long = 'x' * 4500
    [paragraph] = convert(long).blocks
    runs = body(paragraph)['rich_text']
    assert [len(run['text']['content']) for run in runs] == [2000, 2000, 500]
    [code] = convert(f'```\n{long}\n```').blocks
    assert max(len(run['text']['content']) for run in body(code)['rich_text']) <= 2000
    assert text(code) == long


def test_requests_carry_at_most_100_top_level_blocks():
    markdown = '\n\n'.join(f'Paragraph {n}' for n in range(1, 251))
    result = convert(markdown)
    assert result.total == 250
    assert [len(chunk.blocks) for chunk in result.chunks] == [100, 100, 50]
    assert [chunk.start_line for chunk in result.chunks] == [1, 201, 401]
    assert [block for chunk in result.chunks for block in chunk.blocks] == result.blocks


def test_the_total_counts_nested_blocks_and_table_rows():
    result = convert('- a\n  - b\n\n| h |\n|---|\n| 1 |')
    assert result.total == 2 + 1 + 2


def test_a_request_stays_under_notions_size_limit():
    module = NB()
    # Non-Latin text is sent as \\u escapes: six bytes a character.
    markdown = '\n\n'.join('報告' * 2500 for _ in range(30))
    result = convert(markdown)
    assert len(result.chunks) > 1
    for chunk in result.chunks:
        assert len(json.dumps({'children': chunk.blocks})) <= module.REQUEST_BYTES


def test_reports_over_the_limits_are_refused():
    module = NB()
    assert (module.MAX_BLOCKS, module.MAX_CHARS, module.RUN_CHARS, module.BLOCKS_PER_REQUEST) == (1000, 200_000, 2000, 100)
    with pytest.raises(IntegrationError, match='1,000'):
        convert('- item\n' * 1001)
    with pytest.raises(IntegrationError, match='200,000'):
        convert('x' * 200_001)


def test_an_empty_report_has_no_blocks():
    result = convert('  \n\n ')
    assert result.blocks == [] and result.total == 0 and result.chunks == []


# ---------------------------------------------------------------------------
# The title, and page identifiers
# ---------------------------------------------------------------------------

def test_a_leading_heading_that_repeats_the_title_is_dropped():
    result = convert('# Weekly  Digest\n\nBody', title='weekly digest')
    assert types(result.blocks) == ['paragraph']
    assert any('title' in note for note in result.notes)
    kept = convert('# Weekly digest — November\n\nBody', title='Weekly digest')
    assert types(kept.blocks) == ['heading_1', 'paragraph']


@pytest.mark.parametrize('value', [
    '1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6',
    '1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6',
    '1A2B3C4D5E6F47A8B9C0D1E2F3A4B5C6',
    'https://www.notion.so/acme/Weekly-digests-1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6?pvs=4',
    'https://www.notion.so/1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6',
])
def test_page_ids_are_normalised(value):
    assert NB().normalize_page_id(value) == '1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6'


@pytest.mark.parametrize('value', ['', 'weekly-digests', '1a2b3c', '../1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6',
                                   'https://evil.example/1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6', None, 42])
def test_anything_else_is_not_a_page_id(value):
    with pytest.raises(IntegrationError):
        NB().normalize_page_id(value)


def test_a_divider_right_after_a_list_is_a_divider():
    # Found by a sanity check in step 4: "* * *" also looks like a list item.
    result = convert('- one\n- two\n\n* * *\n\n- three\n---')
    assert types(result.blocks) == ['bulleted_list_item', 'bulleted_list_item', 'divider', 'bulleted_list_item', 'divider']


# ---------------------------------------------------------------------------
# Added in step 5, after coverage showed these branches untested
# ---------------------------------------------------------------------------

def test_backslash_escapes_stay_literal():
    [block] = convert(r'Use \*stars\* and \`ticks\` and \_underscores\_').blocks
    assert text(block) == 'Use *stars* and `ticks` and _underscores_'
    assert not any(on(run, mark) for run in body(block)['rich_text'] for mark in ('bold', 'italic', 'code'))


def test_a_very_long_paragraph_continues_in_more_paragraphs():
    module = NB()
    long = 'word ' * 30_000  # 150,000 characters: more runs than one block should hold
    result = convert(long)
    assert types(result.blocks) == ['paragraph'] * len(result.blocks) and len(result.blocks) > 1
    assert all(len(body(block)['rich_text']) <= module.RUNS_PER_BLOCK for block in result.blocks)
    assert ''.join(text(block) for block in result.blocks) == long.strip()


# ---------------------------------------------------------------------------
# A block that cannot be split never loses text (step 6 finding)
# ---------------------------------------------------------------------------

def formatted(pieces):
    return ' '.join(f'**b{n}** plain' for n in range(pieces))


def plain_of(pieces):
    return ' '.join(f'b{n} plain' for n in range(pieces))


def bold_runs(rich_text):
    return sum(1 for run in rich_text if on(run, 'bold'))


@pytest.mark.parametrize('markdown, kind, prefix', [
    ('- {}', 'bulleted_list_item', ''),
    ('1. {}', 'numbered_list_item', ''),
    ('- [ ] {}', 'to_do', ''),
    ('- start\n  {}', 'bulleted_list_item', 'start '),
    ('## {}', 'heading_2', ''),
])
def test_a_richly_formatted_block_keeps_all_its_text_and_formatting(markdown, kind, prefix):
    result = convert(markdown.format(formatted(40)))
    [block] = result.blocks
    assert block['type'] == kind
    assert text(block) == prefix + plain_of(40), 'not one word is lost'
    assert bold_runs(body(block)['rich_text']) == 40, 'within Notion\'s limit, formatting is kept'
    assert len(body(block)['rich_text']) <= 100
    assert not result.notes


@pytest.mark.parametrize('markdown, kind', [('- {}', 'bulleted_list_item'), ('- [x] {}', 'to_do'), ('### {}', 'heading_3')])
def test_beyond_notions_limit_formatting_is_simplified_but_no_text_is_lost(markdown, kind):
    result = convert(markdown.format(formatted(120)))
    [block] = result.blocks
    assert block['type'] == kind and text(block) == plain_of(120)
    assert len(body(block)['rich_text']) <= 100
    assert any('simplified' in note for note in result.notes), 'the approver is told'


def test_a_richly_formatted_table_cell_keeps_its_text():
    result = convert(f'| A |\n|---|\n| {formatted(40)} |\n| {formatted(120)} |')
    rows = result.blocks[0]['table']['children']
    cell = lambda row: row['table_row']['cells'][0]
    assert ''.join(run['text']['content'] for run in cell(rows[1])) == plain_of(40) and bold_runs(cell(rows[1])) == 40
    assert ''.join(run['text']['content'] for run in cell(rows[2])) == plain_of(120) and len(cell(rows[2])) <= 100
    assert any('simplified' in note for note in result.notes)


# ---------------------------------------------------------------------------
# Security N1: conversion time grows linearly, whatever the report contains
# ---------------------------------------------------------------------------

PATHOLOGICAL = ['*a ', '**a ', '[a ', '`a ', '~~a ', '_a ', '**b** ', '[a](https://x.example) ', '<https://x.example> ',
                '\\* ', '**a *b ~~c `d [e ',
                # Found while fixing N1: a long run of spaces inside a line, in a
                # paragraph and in a heading (the old patterns were quadratic).
                'PARAGRAPH_SPACES', 'HEADING_SPACES']


@pytest.mark.parametrize('unit', PATHOLOGICAL)
def test_a_hostile_report_converts_in_linear_time(unit):
    """Each runs in its own process, killed after 5 seconds: quadratic code
    would otherwise take minutes here and stall the whole test run."""
    import subprocess
    import sys
    text = {'PARAGRAPH_SPACES': "'a' + ' ' * 198_000 + 'b'",
            'HEADING_SPACES': "'# a' + ' ' * 198_000 + 'b'"}.get(unit, f'({unit!r} * 70_000)[:199_000]')
    program = ('import time; from conclave.integrations.notion_blocks import convert\n'
               f'text = {text}\n'
               'start = time.perf_counter()\n'
               'try:\n    convert(text)\nexcept Exception:\n    pass\n'
               'print(time.perf_counter() - start)')
    try:
        done = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail(f'{unit!r} × 199,000 characters took more than 5 seconds to convert')
    assert done.returncode == 0, done.stderr
    assert float(done.stdout) < 2.0, f'{unit!r}: {float(done.stdout):.1f} s'


# ---------------------------------------------------------------------------
# Security N2: where links really go is never hidden from the approver
# ---------------------------------------------------------------------------

def test_every_linked_domain_is_listed():
    result = convert('See [the incident](https://status.example.com/418), <https://docs.example.org/a> '
                     'and [mail us](mailto:team@acme.com).')
    [listed] = [note for note in result.notes if note.startswith('Links to')]
    assert listed == 'Links to: status.example.com, docs.example.org, acme.com'


@pytest.mark.parametrize('markdown, shown, real', [
    ('[https://intranet.acme.com/sso](https://evil.example/login)', 'intranet.acme.com', 'evil.example'),
    ('[intranet.acme.com](https://evil.example)', 'intranet.acme.com', 'evil.example'),
    ('[ceo@acme.com](mailto:attacker@evil.example)', 'acme.com', 'evil.example'),
    ('**[www.acme.com](https://acme.com.evil.example)**', 'acme.com', 'acme.com.evil.example'),
])
def test_a_link_whose_text_shows_another_address_is_flagged(markdown, shown, real):
    notes = convert(markdown).notes
    [warning] = [note for note in notes if 'goes to' in note]
    assert shown in warning and real in warning


@pytest.mark.parametrize('markdown', [
    '[status.example.com](https://status.example.com/x)',
    '[www.acme.com](https://acme.com/)',
    '[https://acme.com](https://www.acme.com/home)',
    '[the incident report](https://status.example.com/418)',
    '[team@acme.com](mailto:team@acme.com)',
])
def test_an_honest_link_is_not_flagged(markdown):
    assert not [note for note in convert(markdown).notes if 'goes to' in note]
