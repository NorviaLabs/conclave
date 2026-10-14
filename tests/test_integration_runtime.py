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


def test_blocked_step_fails_the_run_and_keeps_what_it_found():
    definition=request()['definition']
    definition['agents'][1]={'id':'editor','instructions':'Edit the brief','tools':[]}
    definition['workflow'][1]={'id':'edit','agent':'editor','task':'Edit','output':'brief','after':['draft']}
    provider=ScriptedProvider(scripts={
        'writer':[{'type':'final','content':'Stopped','text':'Read the sources; writing was refused.','blocker':'write_file was refused'}],
        'editor':[{'type':'final','content':'Done','text':'Edited'}]})
    async def exercise():
        return [event async for event in run_application(definition,provider,ToolRegistry(1000),'Brief',inputs={'sources':'evidence'})]
    events=asyncio.run(exercise())
    finished=events[-1]
    assert finished['type']=='ApplicationFinished' and finished['status']=='failed'
    assert finished['stage']=='draft' and finished['reason']=='writer is blocked: write_file was refused'
    assert finished['state']['brief']=='Read the sources; writing was refused.'
    assert not any(event.get('stage')=='edit' for event in events)
    from conclave.application.definition import BLOCKER_CONTRACT, _output_contract
    assert all(_output_contract(collection).endswith(BLOCKER_CONTRACT) for collection in
               ({'type':'text'},{'type':'records','statuses':[],'fields':[]}))


def test_step_without_a_blocker_completes():
    definition=request()['definition'];definition['workflow']=definition['workflow'][:1];definition['agents']=definition['agents'][:1]
    provider=ScriptedProvider(scripts={'writer':[{'type':'final','content':'Done','text':'Brief','blocker':' '}]})
    async def exercise():
        return [event async for event in run_application(definition,provider,ToolRegistry(1000),'Brief',inputs={'sources':'evidence'})]
    assert asyncio.run(exercise())[-1]['status']=='completed'


def test_new_applications_do_not_limit_how_much_work_a_run_does():
    from pathlib import Path
    defaults=json.loads((Path(__file__).parents[1]/'conclave/application/data/defaults.json').read_text())
    assert defaults['run_config']['max_tool_steps'] is None
    assert all(value is None for value in defaults['run_budget'].values())


def test_a_text_output_written_as_the_answer_in_prose_is_the_output():
    """The runtime asks for the answer in prose as content; a text step that
    answers only there has still produced its output."""
    definition=request()['definition'];definition['workflow']=definition['workflow'][:1];definition['agents']=definition['agents'][:1]
    provider=ScriptedProvider(scripts={'writer':[{'type':'final','content':'The brief, in prose.'}]})
    async def exercise():
        return [event async for event in run_application(definition,provider,ToolRegistry(1000),'Brief',inputs={'sources':'evidence'})]
    events=asyncio.run(exercise())
    assert events[-1]['status']=='completed' and events[-1]['state']['brief']=='The brief, in prose.'


def test_a_rejected_output_names_the_fields_it_had():
    definition=request()['definition'];definition['workflow']=definition['workflow'][:1];definition['agents']=definition['agents'][:1]
    wrong={'type':'final','content':'','brief':'Written under the collection name'}
    provider=ScriptedProvider(scripts={'writer':[wrong,wrong,wrong]})
    async def exercise():
        return [event async for event in run_application(definition,provider,ToolRegistry(1000),'Brief',inputs={'sources':'evidence'})]
    finished=asyncio.run(exercise())[-1]
    assert finished['status']=='failed'
    assert finished['reason']=='Expected a bounded text output; the final answer had brief'
    assert 'Written under' not in json.dumps(finished['reason'])


def _one_writer_with_a_tool():
    from conclave import Tool, Capability
    async def lookup(**kwargs): return 'found'
    tools=ToolRegistry(1000)
    tools.register(Tool('lookup','Look something up',{},lookup,frozenset({Capability.READ})))
    definition=request()['definition'];definition['workflow']=definition['workflow'][:1];definition['agents']=definition['agents'][:1]
    definition['agents'][0]['tools']=['lookup']
    return definition,tools


def test_reported_blocker_is_checked_once_in_the_same_conversation():
    definition,tools=_one_writer_with_a_tool()
    provider=ScriptedProvider(scripts={'writer':[
        {'type':'tool','name':'lookup','arguments':{}},
        {'type':'final','content':'Stopped','text':'Nothing','blocker':'the runtime stopped'},
        {'type':'tool','name':'lookup','arguments':{}},
        {'type':'final','content':'Done','text':'Brief'}]})
    async def exercise():
        return [event async for event in run_application(definition,provider,tools,'Brief',inputs={'sources':'evidence'})]
    events=asyncio.run(exercise())
    assert events[-1]['status']=='completed' and events[-1]['state']['brief']=='Brief'
    assert sum(event['type']=='StageStarted' for event in events)==1
    checked=[prompt for _,prompt in provider.prompts if 'You reported a blocker: "the runtime stopped"' in prompt]
    assert checked and checked[0].startswith('FINAL CHECK')


def test_blocker_repeated_after_the_check_fails_the_run():
    definition,tools=_one_writer_with_a_tool()
    blocked={'type':'final','content':'Stopped','text':'Nothing','blocker':'the ticket does not exist'}
    provider=ScriptedProvider(scripts={'writer':[blocked,blocked]})
    async def exercise():
        return [event async for event in run_application(definition,provider,tools,'Brief',inputs={'sources':'evidence'})]
    finished=asyncio.run(exercise())[-1]
    assert finished['status']=='failed' and finished['reason']=='writer is blocked: the ticket does not exist'
