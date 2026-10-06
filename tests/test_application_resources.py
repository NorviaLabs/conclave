import asyncio
import json

import pytest

from conclave import ToolRegistry
from conclave.application.resources import load_resources, register_source_tools, bind_resources, SOURCE_TOOLS
from conclave.application.service import LocalRunHost
from conclave.application.runs import RunRepository, validate_run_request
from conftest import ScriptedProvider
from test_studio_runs import request


def setup_resources(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / 'brief.md').write_text('Alpha evidence')
    (second / 'brief.md').write_text('Beta evidence')
    config = tmp_path / 'resources.json'
    config.write_text(json.dumps({'alpha': {'path': str(first), 'applications': ['research-brief']},
                                  'beta': {'path': str(second)}}))
    tools = ToolRegistry(None)
    register_source_tools(tools)
    return tools, load_resources(config), first, second


def source_request(source='alpha'):
    value = request()
    value['definition']['agents'][0]['tools'] = sorted(SOURCE_TOOLS)
    value['resources'] = {'source': source}
    return value


def test_concurrent_resource_bindings_are_isolated(tmp_path):
    tools, resources, first, second = setup_resources(tmp_path)
    alpha = bind_resources(tools, resources, source_request(), {'.md'})
    beta = bind_resources(tools, resources, source_request('beta'), {'.md'})

    async def exercise():
        results = await asyncio.gather(alpha.execute('read_file', {'path': 'brief.md'}),
                                       beta.execute('read_file', {'path': 'brief.md'}))
        assert results == ['1: Alpha evidence', '1: Beta evidence']
        assert await alpha.execute('list_files', {'max_entries': None}) == 'brief.md'
        assert 'Alpha evidence' in await alpha.execute('search_files', {'query': 'evidence', 'max_results': None})
        assert 'Beta evidence' in await beta.execute('knowledge_search', {'query': 'Beta', 'max_results': None})
        assert await alpha.execute('knowledge_search', {'query': ''}) == ''
        with pytest.raises(ValueError, match='Select a source'):
            await tools.execute('read_file', {'path': 'brief.md'})
    asyncio.run(exercise())


def test_two_application_runs_read_their_own_sources_and_resume(tmp_path):
    tools, resources, _, _ = setup_resources(tmp_path)

    class Reader(ScriptedProvider):
        async def complete(self, messages):
            from conclave import Completion
            if messages[-1]['content'].startswith('TOOL RESULT for read_file'):
                content = messages[-1]['content']
                return Completion(text=json.dumps({'type': 'final', 'content': 'Done', 'text': content}))
            return Completion(text=json.dumps({'type': 'tool', 'name': 'read_file', 'arguments': {'path': 'brief.md'}}))

    async def exercise():
        repository = RunRepository(tmp_path / 'runs.sqlite')
        host = LocalRunHost(repository, Reader(), tools, reviewer='operator', resources=resources, source_extensions={'.md'})
        values = [source_request('alpha'), source_request('beta')]
        values[1]['application_id'] = 'other-app'
        records = await asyncio.gather(*(host.handle('start', value) for value in values))
        async with asyncio.timeout(3):
            while any(repository.get(record['id'])['status'] != 'waiting_for_human' for _, record in records):
                await asyncio.sleep(.01)
        for (_, record), expected in zip(records, ['Alpha evidence', 'Beta evidence']):
            saved = repository.get(record['id'])
            review = saved['reviews'][0]
            assert expected in review['request']['state']['brief']
            await host.handle('review', {'run_id': saved['id'], 'review_id': review['id'], 'output': {'text': 'Reviewed'}})
            await host.handle('resume', {'run_id': saved['id']})
        async with asyncio.timeout(3):
            while any(repository.get(record['id'])['status'] != 'completed' for _, record in records):
                await asyncio.sleep(.01)
        await host.close()
    asyncio.run(exercise())


def test_resource_access_does_not_expose_hidden_or_outside_documents(tmp_path):
    tools, resources, first, second = setup_resources(tmp_path)
    (first / '.secret.md').write_text('Secret evidence')
    (first / 'alias.md').symlink_to(first / '.secret.md')
    (first / 'escape.md').symlink_to(second / 'brief.md')
    (first / 'credentials.env').write_text('Secret evidence')
    (first / 'binary.md').write_bytes(b'\x00evidence')
    bound = bind_resources(tools, resources, source_request(), {'.md'})

    async def exercise():
        for path in ('.secret.md', 'alias.md', 'escape.md', '../second/brief.md', str(second / 'brief.md'), 'credentials.env'):
            with pytest.raises((ValueError, RuntimeError)):
                await bound.execute('read_file', {'path': path})
        assert await bound.execute('list_files', {}) == 'binary.md\nbrief.md'
        result = await bound.execute('search_files', {'query': 'evidence'})
        assert result == 'brief.md:1: Alpha evidence'
    asyncio.run(exercise())


def test_bindings_validate_before_creation_and_resume_from_pinned_resource(tmp_path):
    tools, resources, first, second = setup_resources(tmp_path)
    repository = RunRepository(tmp_path / 'runs.sqlite')
    host = LocalRunHost(repository, ScriptedProvider(default={'type': 'final', 'content': 'Done', 'text': 'Brief'}),
                        tools, reviewer='operator', resources=resources, source_extensions={'.md'})

    async def exercise():
        for binding, application, message in [('unknown', 'research-brief', 'unavailable'),
                                               ('alpha', 'other-app', 'not authorized'),
                                               (None, 'research-brief', 'Select a source')]:
            value = source_request(binding)
            value['application_id'] = application
            if binding is None:
                value.pop('resources')
            with pytest.raises(ValueError, match=message):
                await host.handle('start', value)
        assert repository.list('research-brief') == []
        _, runtime = await host.handle('runtime', {})
        assert set(runtime['tools']) == SOURCE_TOOLS
        assert str(first) not in json.dumps(runtime)
        assert str(second) not in json.dumps(runtime)
        _, record = await host.handle('start', source_request())
        async with asyncio.timeout(3):
            while repository.get(record['id'])['status'] != 'waiting_for_human':
                await asyncio.sleep(.01)
        saved = repository.get(record['id'])
        assert saved['request']['resources'] == {'source': 'alpha', 'source_revision': resources['alpha']['revision']}
        restarted = LocalRunHost(repository, host.provider, tools, reviewer='operator', resources=resources, source_extensions={'.md'})
        assert 'Alpha' in await restarted.run_tools(saved['request']).execute('read_file', {'path': 'brief.md'})
        resources['alpha']['revision'] = 'changed'
        with pytest.raises(ValueError, match='resource changed'):
            await restarted.handle('resume', {'run_id': record['id']})
        assert repository.get(record['id'])['status'] == 'waiting_for_human'
        await restarted.close()
        await host.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('value', [None, [], {'source': '/a' * 100}, {'path': '/a'}, {'source': 1}])
def test_invalid_run_resource_contract(value):
    data = request()
    data['resources'] = value
    with pytest.raises(ValueError, match='Run resources'):
        validate_run_request(data)


def test_extension_capabilities_are_not_replaced_by_resource_binding(tmp_path):
    from conclave import Tool, Capability
    tools, resources, _, _ = setup_resources(tmp_path)

    async def extension():
        return 'Extension-owned knowledge'

    custom = Tool('knowledge_search', 'Custom knowledge service', {}, extension, frozenset({Capability.WRITE}))
    tools.register(custom)
    bound = bind_resources(tools, resources, source_request(), {'.md'})
    assert bound.tool('knowledge_search') is custom
    assert bound.tool('knowledge_search').destructive


@pytest.mark.parametrize('config', [[], {'bad/id': {'path': '.'}}, {'ok': {}},
                                   {'ok': {'path': '.', 'credential': 'secret'}},
                                   {'ok': {'path': '.', 'applications': 'app'}},
                                   {'ok': {'path': '.', 'name': ''}}])
def test_invalid_host_resource_configuration(tmp_path, config):
    filename = tmp_path / 'resources.json'
    filename.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        load_resources(filename)
