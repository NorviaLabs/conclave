import asyncio

import pytest
from conclave.integrations.connections import validate_arguments
from conclave.integrations.services import integrations, invoke


class Transport:
    def __init__(self): self.calls = []
    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if 'search.brave.com' in url: return {'web': {'results': [{'url':'https://example.com','title':'Title','description':'Snippet'}]}}
        if 'docs.googleapis.com' in url: return {'documentId':'doc-1'}
        if 'slack.com' in url: return {'ok':True,'messages':{'matches':[]}}
        if kwargs.get('raw'): return b'file content'
        return {'data': {}, 'results': []}


@pytest.mark.parametrize('identifier', ['web_search','google_drive','google_docs','google_sheets','slack','gmail','confluence','notion','zendesk','hubspot','jira','github','linear','trello','reddit','webhooks'])
def test_real_api_contracts(identifier):
    service = integrations()[identifier]
    transport = Transport()
    config = {'base_url':service.base_url or 'https://company.example.com'}
    credentials = {'access_token':'secret','api_key':'key','token':'token','email':'operator@example.com'}
    async def exercise():
        for operation in service.operations:
            if operation.capability == 'inbound':
                continue  # received with a signature, never sent out
            arguments = {}
            for key in operation.parameters.get('required', []):
                arguments[key] = {'values':[['cell']], 'properties':{'name':'value'}, 'payload':{'result':'done'}, 'object_type':'contacts'}.get(key, 'value')
            validate_arguments(arguments, operation.parameters)
            await invoke(service,operation,config,credentials,arguments,transport)
        assert transport.calls
        assert all(call[1].startswith(config['base_url']) for call in transport.calls)
    asyncio.run(exercise())


def test_search_normalization_and_document_write():
    async def exercise():
        services=integrations();transport=Transport()
        service=services['web_search']
        result=await invoke(service,service.operations[0],{}, {'api_key':'key'}, {'query':'research'}, transport)
        assert result['results']==[{'url':'https://example.com','title':'Title','snippet':'Snippet'}]
        assert transport.calls[0][2]['headers']=={'X-Subscription-Token':'key'}
        service=services['google_docs'];operation=service.operations[1]
        assert operation.capability=='write'
        await invoke(service,operation,{}, {'access_token':'private'}, {'title':'Report','text':'Body'},transport)
        assert transport.calls[-1][1].endswith('/doc-1:batchUpdate')
        assert transport.calls[-1][2]['body']['requests'][0]['insertText']['text']=='Body'
    asyncio.run(exercise())


def test_exact_catalogue_and_mutation_authority():
    services=integrations()
    assert len(services)==23
    assert services['gmail'].operations[-1].capability=='write'
    assert services['slack'].operations[-1].capability=='write'
    assert services['notion'].operations[0].capability=='read'
    assert services['mcp'].operations==()


def test_rss_date_keyword_filter_and_entity_rejection():
    class Feed:
        async def request(self,*args,**kwargs):
            return b'<rss><channel><item><title>Relevant</title><link>https://example.com</link><description>Keyword</description><pubDate>Thu, 08 Oct 2026 09:00:00 +0000</pubDate></item><item><title>Other</title></item></channel></rss>'
    async def exercise():
        service=integrations()['rss'];op=service.operations[0]
        result=await invoke(service,op,{'base_url':'https://example.com/feed'},{},{'keyword':'keyword','since':'2026-10-01T00:00:00+00:00'},Feed())
        assert len(result['entries'])==1
        assert (await invoke(service,op,{'base_url':'https://example.com/feed'},{},{'since':'2027-01-01T00:00:00+00:00'},Feed()))['entries']==[]
    asyncio.run(exercise())


def test_service_errors_and_firecrawl_targets(monkeypatch):
    from conclave.integrations import services as module
    from conclave.integrations.transport import IntegrationError
    monkeypatch.setattr(module,'public_addresses',lambda host:['93.184.216.34'])
    class ErrorTransport:
        async def request(self,*args,**kwargs):return {'ok':False,'errors':['private upstream']}
    async def exercise():
        services=integrations()
        for identifier,args in [('slack',{'query':'x'}),('linear',{'query':'x'})]:
            service=services[identifier]
            with pytest.raises(IntegrationError):await invoke(service,service.operations[0],{}, {'token':'key'},args,ErrorTransport())
        service=services['github']
        with pytest.raises(IntegrationError):await invoke(service,service.operations[0],{}, {},{'query':'x'},Transport())
        service=services['hubspot']
        operation=next(op for op in service.operations if op.name=='read_record')
        with pytest.raises(IntegrationError):await invoke(service,operation,{}, {'token':'key'},{'object_type':'private','record_id':'1'},Transport())
        with pytest.raises(IntegrationError):await invoke(service,operation,{}, {'token':'key'},{'object_type':'contacts','record_id':'../secret'},Transport())
        service=services['firecrawl'];transport=Transport()
        await invoke(service,service.operations[0],{'target_hosts':['example.com']},{'api_key':'key'},{'url':'https://example.com/page'},transport)
        assert transport.calls[-1][2]['body']['formats']==['markdown']
        with pytest.raises(IntegrationError):await invoke(service,service.operations[0],{'target_hosts':['example.com']},{'api_key':'key'},{'url':'http://127.0.0.1'},transport)
    asyncio.run(exercise())
