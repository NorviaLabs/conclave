import asyncio
import copy
import json

import pytest

from conclave import Tool, ToolRegistry, Capability
from conclave.api import ApplicationAPI
from conclave.application.workspace import Workspace
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conftest import ScriptedProvider
from test_api import make_api


def diamond():
    from test_studio_runs import request
    value = request()
    definition = value['definition']
    definition['agents'] = definition['agents'][:1]
    definition['state'] = [{'key': 'findings', 'type': 'records', 'merge': 'append', 'statuses': []}]
    definition['workflow'] = [{'id': name, 'agent': 'writer', 'task': name, 'output': 'findings'} for name in ['root', 'left', 'right', 'join']]
    definition['connections'] = [{'from': 'root', 'to': 'left'}, {'from': 'root', 'to': 'right'}, {'from': 'left', 'to': 'join'}, {'from': 'right', 'to': 'join'}]
    definition['views'] = [{'id': 'findings_view', 'collection': 'findings', 'layout': 'cards'}]
    return value


def record(identifier):
    return {'id': identifier, 'title': identifier, 'body': identifier, 'status': 'recorded', 'source_refs': []}


async def run_source(api, value, provider):
    _, saved = await api.dispatch('POST', '/api/applications', {'definition': value['definition']})
    request_value = {key: item for key, item in value.items() if key != 'definition'}
    request_value.update(application_id=saved['id'], revision=saved['revision'])
    status, started = await api.dispatch('POST', '/api/runs', request_value)
    assert status in {201, 202}, started
    await api.host.tasks[started['id']][0]
    return saved, started['id']


def test_pinned_fixture_survives_restart_and_tests_only_the_selected_branch(tmp_path):
    calls = []
    provider = ScriptedProvider(default={'type': 'final', 'content': 'Done', 'records': [record('changed')]})

    def host_provider(name='writer'):
        return provider

    async def exercise():
        api = make_api(tmp_path, provider)
        api.host.provider = provider
        value = diamond()
        # The source run records one result per branch; only `left` is tested.
        api.host.provider = ScriptedProvider(scripts={'writer': [
            {'type': 'final', 'content': 'Done', 'records': [record('root')]},
            {'type': 'final', 'content': 'Done', 'records': [record('left')]},
            {'type': 'final', 'content': 'Done', 'records': [record('right')]},
            {'type': 'final', 'content': 'Done', 'records': [record('join')]},
        ]})
        saved, run_id = await run_source(api, value, api.host.provider)
        application = saved['id']
        # Preview refuses an incomplete step rather than fabricating inputs.
        status, preview = await api.dispatch('POST', f'/api/applications/{application}/fixtures/preview', {'run_id': run_id, 'step_id': 'left'})
        assert status == 200, preview
        assert preview['recorded'] and preview['source_run'] == run_id and preview['revision'] == saved['revision']
        assert preview['reference_recorded'] is True and not preview.get('authored')
        assert [item['id'] for item in preview['input_state']['findings']] == ['root']
        assert [item['id'] for item in preview['reference']] == ['left']
        status, fixture = await api.dispatch('POST', f'/api/applications/{application}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': 'left fixture', 'data': preview})
        assert status == 201, fixture
        assert (await api.dispatch('GET', f'/api/applications/{application}/fixtures'))[1]['fixtures'][0]['name'] == 'left fixture'
        # A restarted server reads the same fixture back.
        reopened = ApplicationAPI(LocalRunHost(RunRepository(tmp_path / 'runs.sqlite'), provider, ToolRegistry(20000), reviewer='operator'), Workspace(tmp_path / 'applications.sqlite'))
        assert (await reopened.dispatch('GET', f'/api/applications/{application}/fixtures'))[1]['fixtures'][0]['id'] == fixture['id']
        # Test only the selected branch: its one ancestor is seeded from the
        # record, and the unrelated sibling is neither seeded nor rerun.
        api.host.provider = ScriptedProvider(scripts={'writer': [{'type': 'final', 'content': 'Done', 'records': [record('new-left')]}]})
        status, test = await api.dispatch('POST', f'/api/applications/{application}/tests', {'fixture_id': fixture['id'], 'scope': 'step'})
        assert status == 202, test
        await api.test_tasks[test['id']]
        _, done = await api.dispatch('GET', f'/api/applications/{application}/tests/{test["id"]}')
        assert done['status'] == 'completed'
        assert done['tested_steps'] == ['left'] and done['seeded_steps'] == ['root']
        assert [item['id'] for item in done['output']] == ['new-left']
        assert done['comparison']['equal'] is False
        assert done['comparison']['changes'][0]['reference']['id'] == 'left'
        # The production run is untouched: its own recorded result still stands.
        _, source = await api.dispatch('GET', f'/api/runs/{run_id}')
        assert source['status'] == 'completed'
        assert [item['id'] for item in source['events'][-1]['event']['state']['findings']] == ['root', 'left', 'right', 'join']
        assert (await api.dispatch('GET', f'/api/applications/{application}/tests/{test["id"]}/compare'))[1]['equal'] is False

    asyncio.run(exercise())


def test_test_from_here_runs_descendants_only_and_never_production_approvals(tmp_path):
    writes = []

    async def write(**arguments):
        writes.append(arguments)
        return 'written'

    async def exercise():
        api = make_api(tmp_path)
        api.host.tools = ToolRegistry(20000)
        api.host.tools.register(Tool(name='write_file', description='Write', parameters={'type': 'object', 'properties': {}}, func=write, capabilities=frozenset({Capability.WRITE})))
        value = diamond()
        value['definition']['agents'][0]['tools'] = ['write_file']
        api.host.provider = ScriptedProvider(scripts={'writer': [
            {'type': 'final', 'content': 'Done', 'records': [record('root')]},
            {'type': 'final', 'content': 'Done', 'records': [record('left')]},
            {'type': 'final', 'content': 'Done', 'records': [record('right')]},
            {'type': 'final', 'content': 'Done', 'records': [record('join')]},
        ]})
        saved, run_id = await run_source(api, value, api.host.provider)
        application = saved['id']
        _, preview = await api.dispatch('POST', f'/api/applications/{application}/fixtures/preview', {'run_id': run_id, 'step_id': 'left'})
        _, fixture = await api.dispatch('POST', f'/api/applications/{application}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': 'left', 'data': preview})
        # The tested agent asks for a write. A test never inherits a production
        # approval, so the call is denied and the callable never runs.
        api.host.provider = ScriptedProvider(scripts={'writer': [
            {'type': 'tool', 'tool': 'write_file', 'arguments': {}},
            {'type': 'final', 'content': 'Done', 'records': [record('new-left')]},
            {'type': 'final', 'content': 'Done', 'records': [record('new-join')]},
        ]})
        _, test = await api.dispatch('POST', f'/api/applications/{application}/tests', {'fixture_id': fixture['id'], 'scope': 'downstream'})
        await api.test_tasks[test['id']]
        _, done = await api.dispatch('GET', f'/api/applications/{application}/tests/{test["id"]}')
        assert done['tested_steps'] == ['join', 'left']
        assert done['seeded_steps'] == ['right', 'root']
        # The agent asked for a write; a test never inherits approval, so nothing ran.
        assert writes == []
        assert any(item['event']['type'] == 'ToolDenied' for item in done['events'])

    asyncio.run(exercise())


def test_fixture_edits_and_scope_are_checked(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        api.host.provider = ScriptedProvider(default={'type': 'final', 'content': 'Done', 'records': [record('x')]})
        value = diamond()
        saved, run_id = await run_source(api, value, api.host.provider)
        application = saved['id']
        _, preview = await api.dispatch('POST', f'/api/applications/{application}/fixtures/preview', {'run_id': run_id, 'step_id': 'left'})
        # Provenance cannot be rewritten through the editable envelope.
        forged = copy.deepcopy(preview)
        forged['source_run'] = 'other'
        assert (await api.dispatch('POST', f'/api/applications/{application}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': 'forged', 'data': forged}))[0] == 400
        # A prerequisite patch that no longer matches its state is refused.
        mismatched = copy.deepcopy(preview)
        mismatched['patches']['root'] = [record('different')]
        assert (await api.dispatch('POST', f'/api/applications/{application}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': 'mismatch', 'data': mismatched}))[0] == 400
        # A changed schema is refused, not guessed at.
        _, fixture = await api.dispatch('POST', f'/api/applications/{application}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': 'left', 'data': preview})
        changed = copy.deepcopy(value['definition'])
        changed['state'][0]['statuses'] = ['recorded']
        assert (await api.dispatch('POST', f'/api/applications/{application}/tests', {'fixture_id': fixture['id'], 'scope': 'step', 'definition': changed}))[0] == 400
        # Another owner cannot see or run it.
        from conclave.application.principal import Principal
        other = Principal('someone-else', 'local')
        assert (await api.dispatch('GET', f'/api/applications/{application}/fixtures', principal=other))[1]['fixtures'] == []
        assert (await api.dispatch('POST', f'/api/applications/{application}/tests', {'fixture_id': fixture['id'], 'scope': 'step'}, principal=other))[0] == 404

    asyncio.run(exercise())


def test_fixture_and_test_requests_are_validated(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        api.host.provider = ScriptedProvider(default={'type': 'final', 'content': 'Done', 'records': [record('x')]})
        value = diamond()
        saved, run_id = await run_source(api, value, api.host.provider)
        application = saved['id']
        base = f'/api/applications/{application}'
        # A request must name the source run and step.
        assert (await api.dispatch('POST', f'{base}/fixtures/preview', {'step_id': 'left'}))[0] == 400
        assert (await api.dispatch('POST', f'{base}/fixtures/preview', {'run_id': run_id, 'step_id': 'missing'}))[0] == 400
        # A run from another application cannot be pinned here.
        other, other_run = await run_source(api, value, api.host.provider)
        assert (await api.dispatch('POST', f'{base}/fixtures/preview', {'run_id': other_run, 'step_id': 'left'}))[0] == 404
        preview = (await api.dispatch('POST', f'{base}/fixtures/preview', {'run_id': run_id, 'step_id': 'left'}))[1]
        # A fixture needs a name and the preview's own envelope.
        assert (await api.dispatch('POST', f'{base}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': '  ', 'data': preview}))[0] == 400
        assert (await api.dispatch('POST', f'{base}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': 'n', 'data': {'step_id': 'left'}}))[0] == 400
        fixture = (await api.dispatch('POST', f'{base}/fixtures', {'run_id': run_id, 'step_id': 'left', 'name': 'left', 'data': preview}))[1]
        assert (await api.dispatch('GET', f'{base}/fixtures/{fixture["id"]}'))[1]['name'] == 'left'
        # A test needs a fixture and a scope.
        assert (await api.dispatch('POST', f'{base}/tests', {'scope': 'step'}))[0] == 400
        assert (await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'everything'}))[0] == 400
        assert (await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'step', 'model': 5}))[0] == 400
        # A finite cost cap cannot be honoured by this provider interface.
        capped = copy.deepcopy(value['definition'])
        capped['run_budget']['cost_usd'] = 1
        assert 'cannot report cost' in (await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'step', 'definition': capped}))[1]['error']
        # An unregistered mock tool is refused before anything runs.
        assert (await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'step', 'mocks': {'nope': 'x'}}))[0] == 400
        # A missing resource is a 404, and a fixture can be removed.
        assert (await api.dispatch('GET', f'{base}/tests/missing'))[0] == 404
        assert (await api.dispatch('DELETE', f'{base}/fixtures/{fixture["id"]}'))[0] == 204
        assert (await api.dispatch('GET', f'{base}/fixtures/{fixture["id"]}'))[0] == 404

    asyncio.run(exercise())


def test_a_step_can_be_tested_without_any_source_run(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        supplied = {'sources': 'Supplied by hand'}
        value = diamond()
        definition = value['definition']
        status, saved = await api.dispatch('POST', '/api/applications', {'definition': definition})
        assert status == 201
        application = saved['id']
        base = f'/api/applications/{application}'
        # A root step needs no prerequisite values at all.
        api.host.provider = ScriptedProvider(scripts={'writer': [{'type': 'final', 'content': 'Done', 'records': [record('first')]}]})
        status, fixture = await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'root', 'name': 'root by hand', 'definition': definition, 'inputs': supplied, 'upstream': {}})
        assert status == 201, fixture
        assert fixture['authored'] is True and fixture['recorded'] is False and fixture['source_run'] is None
        assert fixture['patches'] == {} and fixture['reference_recorded'] is False
        status, test = await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'step'})
        assert status == 202, test
        await api.test_tasks[test['id']]
        _, done = await api.dispatch('GET', f'{base}/tests/{test["id"]}')
        assert done['status'] == 'completed' and done['tested_steps'] == ['root'] and done['seeded_steps'] == []
        assert [item['id'] for item in done['output']] == ['first']
        # No reference was written, so nothing claims the result was compared.
        assert done['reference_recorded'] is False and 'comparison' not in done
        assert (await api.dispatch('GET', f'{base}/tests/{test["id"]}/compare'))[1] == {'kind': 'none', 'equal': None, 'note': 'This fixture recorded no reference output to compare against'}
        # A run of this application never happened.
        assert (await api.dispatch('GET', f'/api/runs?application_id={application}'))[1]['runs'] == []

    asyncio.run(exercise())


def test_a_written_fixture_uses_the_prerequisites_the_person_supplies(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        supplied = {'sources': 'Supplied by hand'}
        definition = diamond()['definition']
        _, saved = await api.dispatch('POST', '/api/applications', {'definition': definition})
        application = saved['id']
        base = f'/api/applications/{application}'
        # The join step depends on left and right through root; every one of them
        # must be supplied, and the state the step receives is built from them.
        upstream = {'root': [record('r')], 'left': [record('l')], 'right': [record('rr')]}
        api.host.provider = ScriptedProvider(scripts={'writer': [{'type': 'final', 'content': 'Done', 'records': [record('joined')]}]})
        status, fixture = await api.dispatch('POST', f'{base}/fixtures/author', {
            'step_id': 'join', 'name': 'join by hand', 'definition': definition, 'inputs': supplied,
            'upstream': upstream, 'reference': [record('expected')], 'reference_recorded': True})
        assert status == 201, fixture
        assert fixture['patches'] == upstream
        assert {item['id'] for item in fixture['input_state']['findings']} == {'r', 'l', 'rr'}
        assert fixture['reference'] == [record('expected')] and fixture['reference_recorded'] is True
        status, test = await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'step'})
        assert status == 202, test
        await api.test_tasks[test['id']]
        _, done = await api.dispatch('GET', f'{base}/tests/{test["id"]}')
        assert done['status'] == 'completed'
        # Only the selected step ran; the supplied prerequisites were not re-run.
        assert done['tested_steps'] == ['join'] and done['seeded_steps'] == ['left', 'right', 'root']
        assert [item['id'] for item in done['output']] == ['joined']
        assert done['comparison']['equal'] is False and done['comparison']['kind'] == 'records'
        # What the step was given is the state the person described, and it is
        # visible in the record as the test's own input.
        started = next(item['event'] for item in done['events'] if item['event'].get('stage') == 'join' and item['event']['type'] == 'StageStarted')
        assert {item['id'] for item in started['effective_input']['state']['findings']} == {'r', 'l', 'rr'}

    asyncio.run(exercise())


def test_a_written_fixture_refuses_to_invent_missing_prerequisites(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        supplied = {'sources': 'Supplied by hand'}
        definition = diamond()['definition']
        _, saved = await api.dispatch('POST', '/api/applications', {'definition': definition})
        base = f'/api/applications/{saved["id"]}'
        # Nothing supplied for a step that depends on others.
        status, error = await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'join', 'name': 'incomplete', 'definition': definition, 'inputs': supplied})
        assert status == 400 and error['error'] == 'Provide the output of every prerequisite step: root, left, right'
        # One of them missing.
        status, error = await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'join', 'name': 'incomplete', 'definition': definition, 'inputs': supplied, 'upstream': {'root': [record('r')], 'left': [record('l')]}})
        assert status == 400 and error['error'] == 'Provide the output of every prerequisite step: right'
        # A value for a step that is not a prerequisite.
        status, error = await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'left', 'name': 'extra', 'definition': definition, 'inputs': supplied, 'upstream': {'root': [record('r')], 'join': [record('j')]}})
        assert status == 400 and error['error'] == 'Prerequisite values must name the steps this one depends on'
        # A prerequisite whose value does not fit its collection is refused.
        status, error = await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'left', 'name': 'wrong shape', 'definition': definition, 'inputs': supplied, 'upstream': {'root': 'not records'}})
        assert status == 400 and 'records array' in error['error']
        # A declared request input that is required is still required here.
        required = copy.deepcopy(definition)
        required['inputs'] = [{'id': 'topic', 'label': 'Topic', 'type': 'text', 'required': True}]
        status, error = await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'root', 'name': 'no input', 'definition': required, 'inputs': {}})
        assert status == 400 and error['error'] == 'Enter Topic'
        # An unknown step, an unnamed fixture, and an unknown field are refused.
        assert (await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'missing', 'name': 'x', 'definition': definition, 'inputs': supplied}))[0] == 400
        assert (await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'root', 'name': '  ', 'definition': definition, 'inputs': supplied}))[0] == 400
        assert (await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'root', 'name': 'x', 'definition': definition, 'inputs': {}, 'upstream': {}, 'surprise': 1}))[0] == 400

    asyncio.run(exercise())


def test_a_written_fixture_is_refused_once_the_plan_moves_on(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        supplied = {'sources': 'Supplied by hand'}
        definition = diamond()['definition']
        _, saved = await api.dispatch('POST', '/api/applications', {'definition': definition})
        base = f'/api/applications/{saved["id"]}'
        _, fixture = await api.dispatch('POST', f'{base}/fixtures/author', {'step_id': 'root', 'name': 'root', 'definition': definition, 'inputs': supplied})
        api.host.provider = ScriptedProvider(default={'type': 'final', 'content': 'Done', 'records': [record('x')]})
        changed = copy.deepcopy(definition)
        changed['connections'] = [{'from': 'root', 'to': 'join'}, {'from': 'left', 'to': 'join'}, {'from': 'right', 'to': 'join'}]
        status, error = await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'step', 'definition': changed})
        assert status == 400 and 'schema or dependencies changed' in error['error']
        # With no definition supplied, the written fixture is tested as written,
        # rather than against a saved version that may have moved on.
        status, test = await api.dispatch('POST', f'{base}/tests', {'fixture_id': fixture['id'], 'scope': 'step'})
        assert status == 202, test
        await api.test_tasks[test['id']]
        assert (await api.dispatch('GET', f'{base}/tests/{test["id"]}'))[1]['status'] == 'completed'

    asyncio.run(exercise())
