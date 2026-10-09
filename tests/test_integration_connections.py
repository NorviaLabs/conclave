import json
import socket

import pytest
from cryptography.fernet import Fernet
from conclave import ToolRegistry
from conclave.integrations import ConnectionStore, IntegrationError
from conclave.integrations.transport import endpoint, public_addresses


def connection():
    return {'id': 'research', 'integration': 'web_search', 'name': 'Research search',
            'applications': ['app'], 'operations': ['search'], 'scopes': [],
            'credentials': {'api_key': 'private-token'}}


def test_encrypted_connections_authorize_and_revoke(tmp_path):
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key())
    value = connection()
    public = store.create(value, 'tenant', 'alice')
    assert 'credentials' not in public and 'config' not in public
    assert b'private-token' not in store.path.read_bytes()
    assert store.list('other', 'alice') == []
    assert store.list('tenant', 'bob') == []
    for tenant, owner, app, operation in [('other', 'alice', 'app', 'search'), ('tenant', 'bob', 'app', 'search'),
                                          ('tenant', 'alice', 'other', 'search'), ('tenant', 'alice', 'app', 'send')]:
        with pytest.raises(IntegrationError):
            store.authorize('research', operation, tenant, owner, app)
    registry = ToolRegistry(1000)
    definition = {'agents': [{'tools': ['connection.research.search']}]}
    bound = store.bind(registry, definition, 'tenant', 'alice', 'app')
    assert bound.tool('connection.research.search') and not registry.tools
    assert not bound.tool('connection.research.search').destructive
    store.disconnect('research', 'tenant', 'alice')
    with pytest.raises(IntegrationError):
        store.authorize('research', 'search', 'tenant', 'alice', 'app')


def test_endpoint_and_dns_rejection(monkeypatch):
    for url in ['http://example.com', 'https://example.com:8080', 'https://secret@example.com',
                'https://other.example', 'https://example.com/#fragment']:
        with pytest.raises(IntegrationError):
            endpoint(url, {'example.com'})
    assert endpoint('https://example.com/api', {'example.com'}).path == '/api'
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(IntegrationError):
        public_addresses('example.com')


def test_argument_contract():
    from conclave.integrations.connections import validate_arguments
    schema = {'properties': {'limit': {'type': 'integer'}, 'query': {'type': 'string', 'maxLength': 3}}, 'required': ['query']}
    validate_arguments({'query': 'x', 'limit': 1}, schema)
    for value in [{'query': 'long'}, {'query': 'x', 'limit': True}, {'query': 'x', 'url': 'https://bad'}, {}]:
        with pytest.raises(IntegrationError):
            validate_arguments(value, schema)


def test_bad_connection_fields_and_configuration(tmp_path):
    import copy
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key())
    for key,value in [('id','../x'),('integration','missing'),('operations',[]),('operations',['send']),
                      ('applications',[]),('scopes','read'),('credentials',{'token':1}),
                      ('config',{'headers':{'secret':'x'}}),('config',{'target_hosts':['private.example']}),
                      ('config',{'restrictions':{'channel':[]}}),('config',{'base_url':'https://api.search.brave.com/evil'}),
                      ('config',{'base_url':'https://api.search.brave.com?q=secret'}),('name','')]:
        invalid=connection();invalid[key]=value
        with pytest.raises(IntegrationError):store.create(invalid,'t','u')
    invalid=connection();invalid['extra']='x'
    with pytest.raises(IntegrationError):store.create(invalid,'t','u')
    store.create(connection(),'t','u')
    with pytest.raises(IntegrationError):store.create(connection(),'t','u')
    store.pin_run('run','t','u');assert store.run_owner('run','t')=='u'
    assert store.run_owner('run','other') is None
    with pytest.raises(IntegrationError):store.bind(ToolRegistry(1000),{'agents':[{'tools':['connection.research.search']}]},'t','u','app',sandbox=True)
    with pytest.raises(IntegrationError):store.bind(ToolRegistry(1000),{'agents':[{'tools':['connection.invalid']}]},'t','u','app')


def test_recommendations_and_recursive_secret_redaction():
    from conclave.integrations.connections import redact
    from conclave.integrations.recommendations import recommend
    secret='token"\\\n'
    assert redact({'result':[secret]},[secret])=={'result':['[redacted]']}
    result=recommend('Customer complaint support ticket research report',[], 'app')
    assert any(item['integration']=='zendesk' for item in result)
    assert all(not item['access_granted'] for item in result)


def test_health_scopes_restrictions_and_errors(tmp_path):
    import asyncio
    class Transport:
        def __init__(self,hosts):pass
        async def request(self,*args,**kwargs):return {'ok':True,'token':'PRIVATE'}
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key(),transport_factory=Transport)
    value={'id':'slack','integration':'slack','name':'Slack','applications':['app'],
           'operations':['send_message','read_conversation'],'scopes':['chat:write','channels:history'],
           'credentials':{'token':'PRIVATE'},'config':{'restrictions':{'channel':['public']}}}
    invalid={**value,'scopes':[]}
    with pytest.raises(IntegrationError):store.create(invalid,'t','u')
    store.create(value,'t','u')
    async def exercise():
        assert (await store.health('slack','t','u'))['status']=='configured'
        with pytest.raises(IntegrationError):await store.invoke('slack','read_conversation',{'channel':'private'},'t','u','app')
        result=await store.invoke('slack','send_message',{'channel':'public','text':'Hello'},'t','u','app')
        assert 'PRIVATE' not in result
    asyncio.run(exercise())


def test_argument_nested_bounds():
    from conclave.integrations.connections import validate_arguments
    for value,schema in [({'limit':101},{'properties':{'limit':{'type':'integer','maximum':100}}}),
                         ({'kind':'unknown'},{'properties':{'kind':{'enum':['read']}}}),
                         ({'items':[1,2]},{'properties':{'items':{'type':'array','maxItems':1}}}),
                         ({'payload':{'other':'x'}},{'properties':{'payload':{'type':'object','properties':{'allowed':{'type':'string'}}}}})]:
        with pytest.raises(IntegrationError):validate_arguments(value,schema)
