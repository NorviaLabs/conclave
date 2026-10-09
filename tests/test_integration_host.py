import asyncio

import pytest
from cryptography.fernet import Fernet
from conclave import ToolRegistry
from conclave.integrations import ConnectionStore, IntegrationError
from conclave.integrations.host import HostAdapters


class Transport:
    calls=[]
    def __init__(self,hosts):pass
    async def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        body=kwargs.get('body') or {}
        if body.get('method')=='tools/list':return {'id':body['id'],'result':{'tools':[{'name':'read'},{'name':'unapproved_write'}]}}
        if 'jsonrpc' in body:return {'id':body.get('id'),'result':{'content':[{'text':'Result'}]}}
        return {'items':['result']}


def test_host_protocol_connections_discover_bind_and_revoke(tmp_path):
    adapters=HostAdapters({
        'mcp':{'endpoint':'https://mcp.example.com/api','hosts':['mcp.example.com'],
               'approved':{'read':{'description':'Read approved data','capability':'read','parameters':{'properties':{},'required':[]}}}},
        'http_openapi':{'endpoint':'https://api.example.com','hosts':['api.example.com'],'approved':['list_items','create_item'],
                        'spec':{'paths':{'/items':{'get':{'operationId':'list_items','parameters':[]},'post':{'operationId':'create_item','parameters':[]}}}}}
    },transport_factory=Transport)
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key(),adapters=adapters)
    for identifier,operations in [('mcp',['read']),('http_openapi',['list_items','create_item'])]:
        store.create({'id':identifier,'integration':identifier,'name':identifier,'applications':['app'],'operations':operations},'tenant','alice')
    bound=store.bind(ToolRegistry(1000),{'agents':[{'tools':['connection.mcp.read','connection.http_openapi.create_item']}]},'tenant','alice','app')
    assert not bound.tool('connection.mcp.read').destructive
    assert bound.tool('connection.http_openapi.create_item').destructive
    async def exercise():
        assert 'Result' in await store.invoke('mcp','read',{},'tenant','alice','app')
        assert 'result' in await store.invoke('http_openapi','list_items',{},'tenant','alice','app')
        with pytest.raises(IntegrationError):await store.invoke('mcp','unapproved_write',{},'tenant','alice','app')
    asyncio.run(exercise())
    value={'id':'bad','integration':'mcp','name':'Bad','applications':['app'],'operations':['read'],'config':{'base_url':'https://evil.example.com'}}
    with pytest.raises(IntegrationError):store.create(value,'tenant','alice')


def test_missing_optional_drivers_stay_unavailable(monkeypatch):
    monkeypatch.setattr('conclave.integrations.host.importlib.util.find_spec',lambda name:None)
    adapters=HostAdapters({'postgres':{'credentials':{},'views':['report']},'mysql':{'credentials':{},'views':['report']},'s3':{'bucket':'reports','prefix':'public/'}})
    assert adapters.services=={}


def test_database_storage_host_invocation(monkeypatch):
    monkeypatch.setattr('conclave.integrations.host.importlib.util.find_spec',lambda name:object())
    adapters=HostAdapters({'postgres':{'credentials':{'password':'secret'},'views':['reports']},
                           'mysql':{'credentials':{},'views':['reports']},
                           's3':{'bucket':'reports','prefix':'approved/'}})
    class SQLClient:
        def query(self,**args):return {'rows':[['secret']]}
    class S3Client:
        async def invoke(self,operation,**args):return {'objects':[]}
    adapters.clients['postgres']=SQLClient();adapters.clients['mysql']=SQLClient();adapters.clients['s3']=S3Client()
    async def exercise():
        assert (await adapters.invoke('postgres','query',{'query':'SELECT * FROM reports'}))['rows']==[['[redacted]']]
        assert (await adapters.invoke('mysql','query',{'query':'SELECT * FROM reports'}))['rows']
        assert (await adapters.invoke('s3','list_objects',{}))=={'objects':[]}
        with pytest.raises(IntegrationError):await adapters.invoke('postgres','write',{})
    asyncio.run(exercise())


def test_openapi_mutation_body_and_host_secret_redaction():
    class Echo:
        def __init__(self,hosts):pass
        async def request(self,method,url,**kwargs):
            assert method=='POST' and kwargs['body']=={'title':'Review'}
            return {'echo':kwargs['headers']['Authorization']}
    adapters=HostAdapters({'http_openapi':{'endpoint':'https://api.example.com','hosts':['api.example.com'],
        'headers':{'Authorization':'Bearer private'},'approved':['create'],
        'spec':{'paths':{'/items':{'post':{'operationId':'create','requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','properties':{'title':{'type':'string'}},'required':['title']}}}}}}}}}},transport_factory=Echo)
    async def exercise():
        assert await adapters.invoke('http_openapi','create',{'body':{'title':'Review'}})=={'echo':'[redacted]'}
        with pytest.raises(IntegrationError):await adapters.invoke('http_openapi','create',{'body':{'url':'https://evil'}})
    asyncio.run(exercise())


def test_multiple_instances_keep_operation_authority_separate(tmp_path):
    def instance(name,method):return {'integration':'http_openapi','name':name,'endpoint':'https://api.example.com',
        'hosts':['api.example.com'],'approved':['execute'],'spec':{'paths':{'/items':{method:{'operationId':'execute','parameters':[]}}}}}
    adapters=HostAdapters({'reader':instance('Read account','get'),'writer':instance('Write account','post')},transport_factory=Transport)
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key(),adapters=adapters)
    for identifier in ['reader','writer']:
        store.create({'id':identifier,'integration':'http_openapi','name':identifier,'applications':['app'],
                      'operations':['execute'],'config':{'instance':identifier}},'t','u')
    bound=store.bind(ToolRegistry(1000),{'agents':[{'tools':['connection.reader.execute','connection.writer.execute']}]},'t','u','app')
    assert not bound.tool('connection.reader.execute').destructive
    assert bound.tool('connection.writer.execute').destructive
    with pytest.raises(IntegrationError):store.create({'id':'wrong','integration':'mcp','name':'Wrong','applications':['app'],
        'operations':['execute'],'config':{'instance':'reader'}},'t','u')
