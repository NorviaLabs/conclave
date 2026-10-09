import asyncio
import json

import pytest

from conclave import Tool, ToolRegistry, Capability
from conclave.application.capture import redact
from conclave.application.step_execution import MockExecutor, execute_test, safe_tools
from conclave.application import step_testing
from conclave.application.step_testing import StepTestStore, compare, fixture_preview
from conftest import ScriptedProvider


def base_definition():
    """A valid two-step definition, taken from the suite's own request helper."""

    from test_studio_runs import request

    definition = request()['definition']
    definition['agents'] = definition['agents'][:1]
    definition['state'] = [{'key': 'brief', 'type': 'text', 'merge': 'replace', 'statuses': []},
                           {'key': 'notes', 'type': 'text', 'merge': 'replace', 'statuses': []}]
    definition['views'] = [{'id': 'brief_view', 'collection': 'brief', 'layout': 'prose'}]
    definition['workflow'] = [
        {'id': 'draft', 'name': 'Draft', 'agent': definition['agents'][0]['id'], 'task': 'Draft', 'output': 'brief'},
        {'id': 'review', 'name': 'Review', 'agent': definition['agents'][0]['id'], 'task': 'Review', 'output': 'notes'},
    ]
    definition['connections'] = [{'from': 'draft', 'to': 'review'}]
    definition['run_budget'] = {**definition['run_budget'], 'wall_clock_seconds': 30}
    # No request inputs are needed to exercise the mechanics under test.
    definition.pop('inputs', None)
    return definition


def expected_state(definition, step_id):
    from conclave.application.definition import _dependency_state

    patches = {step['id']: ('Drafted' if step['output'] == 'brief' else 'Notes') for step in definition['workflow']}
    return _dependency_state(definition, step_id, patches)


def source_record(step_id='draft'):
    definition = base_definition()
    return {
        'id': 'run-1',
        'request': {'application_id': 'app-1', 'revision': 3, 'definition': definition, 'task': 'Do it'},
        'events': [
            {'sequence': 1, 'event': {'type': 'StageStarted', 'stage': step_id, 'provider': 'openai', 'model': 'm',
                                      'effective_input': {'inputs': {}, 'state': expected_state(definition, step_id)}}},
            {'sequence': 2, 'event': {'type': 'ApplicationStateChanged', 'stage': step_id, 'patch': 'Drafted'}},
        ],
    }


def test_preview_refuses_a_step_with_no_recorded_snapshot():
    record = source_record()
    record['events'] = [item for item in record['events'] if item['event']['type'] != 'StageStarted']
    with pytest.raises(ValueError, match='no recorded input snapshot'):
        fixture_preview(record, 'draft')
    with pytest.raises(ValueError, match='Unknown step'):
        fixture_preview(source_record(), 'missing')
    # A patch that cannot reproduce the recorded snapshot is refused, not guessed.
    changed = source_record()
    changed['events'][0]['event']['effective_input']['state'] = {'brief': 'Something else'}
    with pytest.raises(ValueError, match='cannot be reconstructed exactly'):
        fixture_preview(changed, 'draft')


def test_store_is_owner_scoped_and_refuses_a_foreign_id(tmp_path):
    store = StepTestStore(tmp_path / 'tests.sqlite')
    saved = store.save('app-1', 'owner-a', 'fixture', {'name': 'one'})
    assert store.get('app-1', 'owner-a', 'fixture', saved['id'])['name'] == 'one'
    assert store.list('app-1', 'owner-b', 'fixture') == []
    with pytest.raises(KeyError):
        store.get('app-1', 'owner-b', 'fixture', saved['id'])
    # Another owner cannot overwrite a record by reusing its identifier.
    with pytest.raises(KeyError):
        store.save('app-1', 'owner-b', 'fixture', {'id': saved['id'], 'name': 'stolen'})
    store.delete('app-1', 'owner-a', 'fixture', saved['id'])
    with pytest.raises(KeyError):
        store.delete('app-1', 'owner-a', 'fixture', saved['id'])


def test_scope_refuses_a_changed_definition_and_warns_on_a_changed_agent():
    preview = fixture_preview(source_record(), 'draft')
    selected, seeds, warnings = step_testing.test_scope(preview, base_definition(), 'step')
    assert selected == {'draft'} and seeds == {} and warnings == []
    with pytest.raises(ValueError, match='schema or dependencies changed'):
        changed = base_definition()
        changed['state'].append({'key': 'more', 'type': 'text', 'merge': 'replace', 'statuses': []})
        step_testing.test_scope(preview, changed, 'step')
    with pytest.raises(ValueError, match='step/output assignments changed'):
        moved = base_definition()
        moved['workflow'][0]['output'] = 'notes'
        step_testing.test_scope(preview, moved, 'step')
    with pytest.raises(ValueError, match='Choose step or downstream'):
        step_testing.test_scope(preview, base_definition(), 'everything')
    renamed = base_definition()
    renamed['agents'].append({'id': 'editor', 'name': 'Editor', 'kind': 'ai', 'instructions': 'Edit', 'tools': []})
    renamed['workflow'][0]['agent'] = 'editor'
    assert step_testing.test_scope(preview, renamed, 'step')[2] == ['Agent assignment changed']


def test_compare_reports_text_diff_records_and_plain_values():
    assert compare('a\n', 'b\n')['diff'].startswith('--- reference')
    assert compare('same', 'same')['equal'] is True
    changes = compare([{'id': 'a'}], [{'id': 'a'}, {'id': 'b'}])['changes']
    assert [item['id'] for item in changes] == ['b']
    assert compare(1, 2) == {'kind': 'value', 'equal': False, 'reference': 1, 'output': 2}


def test_safe_tools_only_answer_named_mocks():
    calls = []

    async def dangerous(**arguments):
        calls.append(arguments)
        return 'done'

    registry = ToolRegistry(20000)
    registry.register(Tool(name='send_email', description='Send', parameters={'type': 'object', 'properties': {}}, func=dangerous, capabilities=frozenset({Capability.READ})))

    async def exercise():
        tools = safe_tools(registry, {})
        # Every tool is treated as destructive, so an unnamed call cannot run.
        assert tools.tools['send_email'].destructive is True
        with pytest.raises(ValueError, match='Blocked in safe test mode'):
            await MockExecutor({}).execute(type('Call', (), {'name': 'send_email'})())
        assert await MockExecutor({'send_email': 'recorded'}).execute(type('Call', (), {'name': 'send_email'})()) == 'recorded'
        assert calls == []
        # The host's registry is not modified by building a safe one.
        assert registry.tools['send_email'].destructive is False

    asyncio.run(exercise())
    with pytest.raises(ValueError, match='must name registered tools'):
        safe_tools(registry, {'unknown': 'text'})
    with pytest.raises(ValueError, match='must name registered tools'):
        safe_tools(registry, {'send_email': 5})


def test_execute_test_records_a_failure_without_exposing_provider_text(tmp_path):
    store = StepTestStore(tmp_path / 'tests.sqlite')
    fixture = store.save('app-1', 'owner-a', 'fixture', fixture_preview(source_record(), 'draft'))

    async def explode(messages):
        raise RuntimeError('secret credential sk-abcdefghijklmnop')

    class Broken:
        async def complete(self, messages):
            return await explode(messages)

    record = {'scope': 'step', 'events': [], 'request': {'definition': base_definition()}}
    asyncio.run(execute_test(store, 'app-1', 'owner-a', record, fixture, Broken(), ToolRegistry(20000)))
    assert record['status'] == 'failed'
    # The provider's own words never reach the record, and no credential shape does either.
    assert 'sk-abcdefghijklmnop' not in json.dumps(record['events'])
    assert record['events'][-1]['event']['type'] == 'TestFinished'


def test_execute_test_renames_events_and_keeps_the_reference_untouched(tmp_path):
    store = StepTestStore(tmp_path / 'tests.sqlite')
    fixture = store.save('app-1', 'owner-a', 'fixture', fixture_preview(source_record(), 'draft'))
    provider = ScriptedProvider(default={'type': 'final', 'content': 'Done', 'text': 'New draft'})
    record = {'scope': 'step', 'events': [], 'request': {'definition': base_definition()}}
    asyncio.run(execute_test(store, 'app-1', 'owner-a', 'x' and record, fixture, provider, ToolRegistry(20000)))
    assert record['status'] == 'completed'
    assert record['output'] == 'New draft'
    assert record['comparison']['equal'] is False
    kinds = [item['event']['type'] for item in record['events']]
    # A test announces itself as a test, never as a workflow run.
    assert kinds[0] == 'TestStarted' and kinds[-1] == 'TestFinished'
    assert not {'ApplicationStarted', 'ApplicationFinished'} & set(kinds)
    # The fixture still holds the recorded reference.
    assert store.get('app-1', 'owner-a', 'fixture', fixture['id'])['reference'] == 'Drafted'
    assert redact({'nested': {'api_key': 'x'}}) == {'nested': {'api_key': '[REDACTED]'}}
