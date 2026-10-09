import asyncio
import json

from cryptography.fernet import Fernet
from conclave import ToolRegistry
from conclave.application.definition import run_application
from conclave.integrations import ConnectionStore
from conftest import ScriptedProvider
from test_studio_runs import request


def test_connection_call_attributed_and_mutation_denied_until_approved(tmp_path):
    class Transport:
        calls=[]
        def __init__(self,hosts):pass
        async def request(self,method,url,**kwargs):
            self.calls.append((method,url,kwargs));return {'ok':True}
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key(),transport_factory=Transport)
    store.create({'id':'product','integration':'slack','name':'Product Slack','applications':['app'],
                  'operations':['send_message'],'scopes':['chat:write'],'credentials':{'token':'PRIVATE'}},'tenant','alice')
    definition=request()['definition'];definition['workflow']=definition['workflow'][:1]
    definition['agents']=definition['agents'][:1]
    definition['agents'][0]['tools']=['connection.product.send_message']
    tools=store.bind(ToolRegistry(1000),definition,'tenant','alice','app')
    def provider():return ScriptedProvider(scripts={'writer':[{'type':'tool','name':'connection.product.send_message','arguments':{'channel':'channel','text':'Hello'}}, {'type':'final','content':'Done','text':'Result'}]})
    async def exercise():
        denied=[event async for event in run_application(definition,provider(),tools,'Notify team',inputs={'sources':'evidence'})]
        assert not Transport.calls
        event=next(event for event in denied if event['type']=='ToolDenied')
        assert event['connection_id']=='product' and event['operation']=='send_message'
        approved=[event async for event in run_application(definition,provider(),tools,'Notify team',inputs={'sources':'evidence'},approver=lambda approval:approval.tool=='connection.product.send_message')]
        event=next(event for event in approved if event['type']=='ToolCalled')
        assert event['ok'] and event['connection_id']=='product'
        assert len(Transport.calls)==1 and 'PRIVATE' not in json.dumps(approved)
        store.disconnect('product','tenant','alice')
        revoked=[event async for event in run_application(definition,provider(),tools,'Notify team',inputs={'sources':'evidence'},approver=lambda approval:True)]
        assert len(Transport.calls)==1
        assert any(event['type']=='ToolCalled' and not event['ok'] for event in revoked)
    asyncio.run(exercise())


def test_host_waits_for_exact_tool_approval(tmp_path):
    from conclave.application.service import LocalRunHost
    from conclave.application.runs import RunRepository
    class Transport:
        calls=[]
        def __init__(self,hosts):pass
        async def request(self,*args,**kwargs):self.calls.append(kwargs);return {'ok':True}
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key(),transport_factory=Transport)
    store.create({'id':'product','integration':'slack','name':'Slack','applications':['research-brief'],
                  'operations':['send_message'],'scopes':['chat:write'],'credentials':{'token':'PRIVATE'}},'local','alice')
    value=request();definition=value['definition'];definition['workflow']=definition['workflow'][:1];definition['agents']=definition['agents'][:1]
    definition['agents'][0]['tools']=['connection.product.send_message']
    provider=ScriptedProvider(scripts={'writer':[{'type':'tool','name':'connection.product.send_message','arguments':{'channel':'channel','text':'Hello'}},{'type':'final','content':'Done','text':'Result'}]})
    host=LocalRunHost(RunRepository(tmp_path/'runs.sqlite'),provider,ToolRegistry(1000),reviewer='alice');host.connections=store
    async def exercise():
        _,record=await host.handle('start',value)
        for _ in range(100):
            pending=host.tool_approvals.list(record['id'],'alice')
            if pending:break
            await asyncio.sleep(.001)
        assert pending and not Transport.calls
        host.tool_approvals.decide(record['id'],pending[0]['id'],'alice',True)
        for _ in range(100):
            if host.repository.get(record['id'])['status']=='completed':break
            await asyncio.sleep(.001)
        assert host.repository.get(record['id'])['status']=='completed'
        assert len(Transport.calls)==1
        await host.close()
    asyncio.run(exercise())
