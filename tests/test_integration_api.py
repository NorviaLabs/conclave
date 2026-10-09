import asyncio
import json

from cryptography.fernet import Fernet
from conclave import ToolRegistry
from conclave.api import ApplicationAPI
from conclave.application.principal import Principal
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace


def test_integration_rest_creation_ownership_and_no_secrets(tmp_path):
    from conclave.integrations import ConnectionStore
    principal = Principal('alice', 'team')
    host = LocalRunHost(RunRepository(tmp_path / 'runs.sqlite'), None, ToolRegistry(1000), reviewer='alice', principal=principal)
    host.connections = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key())
    api = ApplicationAPI(host, Workspace(tmp_path / 'apps.sqlite'))
    value = {'id': 'search', 'name': 'Team search', 'integration': 'web_search', 'applications': ['app'],
             'operations': ['search'], 'scopes': [], 'credentials': {'api_key': 'PRIVATE_TOKEN'}}
    async def exercise():
        status, catalogue = await api.dispatch('GET', '/api/integrations')
        assert status == 200 and len(catalogue['integrations']) == 23
        status, result = await api.dispatch('POST', '/api/connections', value)
        assert status == 201 and 'PRIVATE_TOKEN' not in json.dumps(result)
        status, result = await api.dispatch('GET', '/api/connections', principal=Principal('bob', 'team'))
        assert result['connections'] == []
        assert (await api.dispatch('DELETE', '/api/connections/search', principal=Principal('bob', 'team')))[0] == 400
        assert (await api.dispatch('GET', '/api/connections', principal=Principal('alice', 'other')))[0] == 403
        assert (await api.dispatch('DELETE', '/api/connections/search'))[0] == 204
        assert (await api.dispatch('GET', '/api/connections'))[1]['connections'] == []
    asyncio.run(exercise())


def test_oauth_rest_requires_configured_flow_and_recommendations(tmp_path):
    from conclave.integrations import ConnectionStore
    principal=Principal('alice','team')
    host=LocalRunHost(RunRepository(tmp_path/'runs.sqlite'),None,ToolRegistry(1000),reviewer='alice',principal=principal)
    host.connections=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key())
    api=ApplicationAPI(host,Workspace(tmp_path/'apps.sqlite'))
    async def exercise():
        assert (await api.dispatch('POST','/api/connections/oauth/begin',{}))[0]==400
        assert (await api.dispatch('POST','/api/connections/oauth/callback',{}))[0]==400
        result=await api.dispatch('POST','/api/integrations/recommend',{'goal':'support ticket','application_id':'app'})
        assert result[0]==200 and result[1]['recommendations']
        assert (await api.dispatch('POST','/api/connections/missing/revoke',{}))[0]==400
        assert (await api.dispatch('POST','/api/connections/missing/health',{}))[0]==400
    asyncio.run(exercise())


def test_runtime_reports_only_runnable_integrations(tmp_path, monkeypatch):
    """The catalogue's source of truth: what this host can actually run."""
    from conclave.integrations import ConnectionStore
    from conclave.integrations.host import HostAdapters
    principal = Principal('alice', 'team')
    host = LocalRunHost(RunRepository(tmp_path / 'runs.sqlite'), None, ToolRegistry(1000), reviewer='alice', principal=principal)
    api = ApplicationAPI(host, Workspace(tmp_path / 'apps.sqlite'))

    async def without_key():
        _, runtime = await host.handle('runtime', {})
        items = runtime['integrations']
        assert len(items) == 23
        assert all(item['origin'] == 'integration' for item in items)
        by_id = {item['id']: item for item in items}
        # Installed adapters are available; unconfigured host adapters are not.
        assert by_id['slack']['available'] and by_id['rss']['available']
        assert not by_id['mcp']['available'] and by_id['mcp']['status'] == 'unavailable'
        assert by_id['slack']['default_mode'] == 'draft' and by_id['github']['default_mode'] == 'draft'
        assert by_id['rss']['default_mode'] == 'read' and by_id['reddit']['default_mode'] == 'read'
        assert by_id['slack']['category'] == 'Communication' and by_id['s3']['category'] == 'Data and storage'
        assert by_id['webhooks']['operations'][-1]['capability'] == 'inbound'
        assert 'PRIVATE' not in json.dumps(items)
    asyncio.run(without_key())

    # Two database accounts are two catalogue entries, not one service name.
    monkeypatch.setattr('conclave.integrations.host.importlib.util.find_spec', lambda name: object())
    host.connections = ConnectionStore(
        tmp_path / 'connections.sqlite', Fernet.generate_key(),
        adapters=HostAdapters({'reports': {'integration': 'postgres', 'name': 'Reporting read replica',
                                          'credentials': {}, 'views': ['report']}}))
    async def with_instance():
        _, runtime = await host.handle('runtime', {})
        by_id = {item['id']: item for item in runtime['integrations']}
        assert by_id['reports']['name'] == 'Reporting read replica'
        assert by_id['reports']['group'] == 'data-and-storage' and by_id['reports']['available']
        assert 'postgres' not in by_id
        assert len(runtime['integrations']) == 23
    asyncio.run(with_instance())


def test_integration_catalogue_describes_only_runnable_services(tmp_path, monkeypatch):
    """What Studio's catalogue lists: no promise without an adapter, no secret."""
    from conclave.integrations import ConnectionStore
    from conclave.integrations.host import HostAdapters
    monkeypatch.setattr('conclave.integrations.host.importlib.util.find_spec', lambda name: object())
    principal = Principal('alice', 'team')
    host = LocalRunHost(RunRepository(tmp_path / 'runs.sqlite'), None, ToolRegistry(1000), reviewer='alice', principal=principal)
    api = ApplicationAPI(host, Workspace(tmp_path / 'apps.sqlite'))
    host.connections = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key(),
                                       adapters=HostAdapters({'archive': {'integration': 's3', 'name': 'Archive bucket',
                                                                          'bucket': 'reports', 'prefix': 'public/'}}))

    async def exercise():
        status, payload = await api.dispatch('GET', '/api/integrations')
        assert status == 200
        items = payload['integrations']
        assert payload['connections_enabled'] is True
        by_id = {item['id']: item for item in items}
        # The instance replaces the bare service, and both shapes agree.
        assert 's3' not in by_id and by_id['archive']['available'] is True
        assert by_id['archive']['group'] == 'data-and-storage'
        assert by_id['archive']['description'] == 'Connect an account to use Archive bucket.'
        assert by_id['mcp']['available'] is False
        assert by_id['slack']['available'] is True and by_id['slack']['origin'] == 'integration'
        assert all(item['category'] and item['group'] for item in items)
        assert 'PRIVATE' not in json.dumps(payload)
    asyncio.run(exercise())


def test_a_connection_names_the_host_instance_it_uses(tmp_path):
    """Studio must be able to show which account or endpoint a connection uses."""
    from conclave.integrations import ConnectionStore
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key(), hosts={'news.example.com'})
    store.create({'id': 'plain', 'integration': 'slack', 'name': 'Slack', 'applications': ['app'],
                  'operations': ['search_messages'], 'scopes': ['search:read'],
                  'credentials': {'token': 'PRIVATE'}}, 'tenant', 'alice')
    store.create({'id': 'archive', 'integration': 'rss', 'name': 'Feed', 'applications': ['app'],
                  'operations': ['read_feed'], 'config': {'base_url': 'https://news.example.com/feed'},
                  'credentials': {}}, 'tenant', 'alice')
    listed = {item['id']: item for item in store.list('tenant', 'alice')}
    # A native service has no instance; a host instance names itself.
    assert 'instance' not in listed['plain'] and 'config' not in listed['plain']
    assert 'instance' not in listed['archive']
    assert 'PRIVATE' not in json.dumps(listed)
    # A configured host instance does name itself, and still hides configuration.
    from conclave.integrations.host import HostAdapters
    import conclave.integrations.host as module
    module.importlib.util.find_spec = lambda name: object()
    store.adapters = HostAdapters({'reports': {'integration': 'postgres', 'name': 'Reporting',
                                               'credentials': {}, 'views': ['report']}})
    store.create({'id': 'reports_read', 'integration': 'postgres', 'name': 'Reporting read',
                  'applications': ['app'], 'operations': ['query'], 'config': {'instance': 'reports'}},
                 'tenant', 'alice')
    named = {item['id']: item for item in store.list('tenant', 'alice')}
    assert named['reports_read']['instance'] == 'reports'
    assert set(named['reports_read']) == {'id', 'integration', 'name', 'applications', 'operations', 'scopes', 'instance'}


def test_pause_and_resume_over_rest(tmp_path):
    """A paused run is resumable; a run that is not going cannot be paused."""
    from conclave import ToolRegistry
    from conclave.application.service import LocalRunHost
    from conftest import ScriptedProvider
    from test_studio_runs import request as run_request

    provider = ScriptedProvider(default={'type': 'final', 'content': 'Done', 'text': 'Result'})
    host = LocalRunHost(RunRepository(tmp_path / 'runs.sqlite'), provider, ToolRegistry(20000), reviewer='operator')
    api = ApplicationAPI(host, Workspace(tmp_path / 'apps.sqlite'))
    value = run_request()
    value['definition']['workflow'] = value['definition']['workflow'][:1]
    value['definition']['agents'] = value['definition']['agents'][:1]

    async def exercise():
        # The REST route pins the saved revision and supplies the definition.
        _, saved = await api.dispatch('POST', '/api/applications', {'definition': value['definition']})
        request_value = {key: item for key, item in value.items() if key != 'definition'}
        request_value.update(application_id=saved['id'], revision=saved['revision'])
        status, record = await api.dispatch('POST', '/api/runs', request_value)
        assert status == 201, record
        # Pause while the run is still going, then wait for it to stop at a boundary.
        assert (await api.dispatch('POST', f"/api/runs/{record['id']}/pause", {}))[0] == 200
        for _ in range(200):
            paused = await api.dispatch('GET', f"/api/runs/{record['id']}")
            if paused[1]['status'] == 'paused':
                break
            await asyncio.sleep(0.005)
        assert paused[1]['status'] == 'paused'
        assert paused[1]['events'][-1]['event']['status'] == 'paused'
        status, resumed = await api.dispatch('POST', f"/api/runs/{record['id']}/resume", {})
        assert status == 200
        for _ in range(200):
            finished = await api.dispatch('GET', f"/api/runs/{record['id']}")
            if finished[1]['status'] in ('completed', 'waiting_for_human'):
                break
            await asyncio.sleep(0.005)
        assert finished[1]['status'] in ('completed', 'waiting_for_human')
        # Only a run that is still going can pause; an unknown one is not found,
        # and extra fields are refused rather than ignored.
        assert (await api.dispatch('POST', f"/api/runs/{record['id']}/pause", {}))[0] == 400
        assert (await api.dispatch('POST', '/api/runs/missing/pause', {}))[0] == 404
        assert (await api.dispatch('POST', f"/api/runs/{record['id']}/pause", {'extra': 1}))[0] == 400
        await host.close()
    asyncio.run(exercise())
