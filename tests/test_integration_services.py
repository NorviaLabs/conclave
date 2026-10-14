import asyncio
import json

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


def test_github_list_results_are_projected_into_the_output_bound():
    class Big(Transport):
        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if '/search/issues' in url:
                return {'total_count': 776, 'items': [{'number': number, 'title': 't' * 200, 'body': 'b' * 5000,
                                                       'url': 'u' * 200, 'user': {'login': 'someone'},
                                                       'pull_request': {'url': 'p'}} for number in range(100)]}
            if '/pulls/' in url:
                return {'number': 1, 'state': 'open', 'mergeable_state': 'clean', 'body': 'b' * 40000,
                        'head': {'ref': 'topic', 'sha': 's' * 40, '_links': {'self': 'u' * 100}},
                        'base': {'ref': 'main', 'sha': 's' * 40}, 'user': {'login': 'someone'}, '_links': {'self': 'u' * 200}}
            return [{'ref': f'refs/heads/branch-{number}', 'node_id': 'n' * 20, 'url': 'u' * 200,
                     'object': {'sha': 's' * 40, 'type': 'commit', 'url': 'u' * 200}} for number in range(300)]
    async def exercise():
        service = integrations()['github']
        transport = Big()
        operation = next(item for item in service.operations if item.name == 'search_issues')
        result = await invoke(service, operation, {}, {'token': 'token'}, {'query': 'repo:owner/name is:pr'}, transport)
        assert result['total_count'] == 776 and len(result['items']) == 30
        assert set(result['items'][0]) == {'number', 'title', 'state', 'html_url', 'updated_at', 'is_pull_request'}
        assert result['items'][0]['is_pull_request'] is True
        operation = next(item for item in service.operations if item.name == 'read_branches')
        result = await invoke(service, operation, {}, {'token': 'token'}, {'owner': 'owner', 'repo': 'name'}, transport)
        assert len(result) == 100 and result[0] == {'ref': 'refs/heads/branch-0', 'sha': 's' * 40}
        assert len(json.dumps(result)) < 100000
        operation = next(item for item in service.operations if item.name == 'read_pull_request')
        result = await invoke(service, operation, {}, {'token': 'token'}, {'owner': 'owner', 'repo': 'name', 'number': '1'}, transport)
        # The state a merge gate reads comes before the body, so truncation
        # cannot hide it.
        assert list(result)[:7] == ['number', 'title', 'state', 'draft', 'merged', 'mergeable', 'mergeable_state']
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


def test_github_issue_to_merge_operations():
    import base64
    from conclave.integrations.transport import IntegrationError
    service=integrations()['github'];transport=Transport();config={'base_url':service.base_url}
    operations={item.name:item for item in service.operations}
    assert operations['read_branches'].capability=='read'
    assert all(operations[name].capability=='write' for name in ('create_branch','write_content','create_pull_request','merge_pull_request'))
    async def exercise():
        await invoke(service,operations['read_branches'],config,{'access_token':'private'},{'owner':'acme','repo':'app'},transport)
        assert transport.calls[-1][1]=='https://api.github.com/repos/acme/app/git/refs/heads'
        await invoke(service,operations['create_branch'],config,{'access_token':'private'},{'owner':'acme','repo':'app','branch':'fix/issue-7','sha':'abc123'},transport)
        assert transport.calls[-1][2]['body']=={'ref':'refs/heads/fix/issue-7','sha':'abc123'}
        await invoke(service,operations['write_content'],config,{'access_token':'private'},{'owner':'acme','repo':'app','path':'src/app.py','message':'Fix the parser','text':'print(1)\n','branch':'fix/issue-7'},transport)
        assert transport.calls[-1][1]=='https://api.github.com/repos/acme/app/contents/src/app.py'
        assert transport.calls[-1][2]['body']=={'message':'Fix the parser','content':base64.b64encode(b'print(1)\n').decode(),'branch':'fix/issue-7'}
        await invoke(service,operations['write_content'],config,{'access_token':'private'},{'owner':'acme','repo':'app','path':'src/app.py','message':'Fix the parser','text':'print(1)\n','branch':'fix/issue-7','sha':'blob1'},transport)
        assert transport.calls[-1][2]['body']['sha']=='blob1'
        await invoke(service,operations['create_pull_request'],config,{'access_token':'private'},{'owner':'acme','repo':'app','title':'Fix 7','head':'fix/issue-7','base':'main','body':'Closes #7'},transport)
        assert transport.calls[-1][2]['body']=={'title':'Fix 7','head':'fix/issue-7','base':'main','body':'Closes #7'}
        await invoke(service,operations['merge_pull_request'],config,{'access_token':'private'},{'owner':'acme','repo':'app','number':'12'},transport)
        assert transport.calls[-1][1]=='https://api.github.com/repos/acme/app/pulls/12/merge'
        assert transport.calls[-1][2]['body']=={'merge_method':'squash'}
        await invoke(service,operations['read_content'],config,{'access_token':'private'},{'owner':'acme','repo':'app','path':'docs/guide.md'},transport)
        assert transport.calls[-1][1].endswith('/contents/docs/guide.md')
        for path in ('../.git/config','src/../../etc/passwd','/etc/passwd','src//app.py'):
            with pytest.raises(IntegrationError):await invoke(service,operations['write_content'],config,{'access_token':'private'},{'owner':'acme','repo':'app','path':path,'message':'Fix','text':'x','branch':'fix/issue-7'},transport)
        with pytest.raises(IntegrationError):await invoke(service,operations['merge_pull_request'],config,{'access_token':'private'},{'owner':'acme','repo':'app','number':'12','merge_method':'fast-forward'},transport)
        with pytest.raises(IntegrationError):await invoke(service,operations['read_issue'],config,{'access_token':'private'},{'owner':'acme','repo':'app','issue_number':'../7'},transport)
    asyncio.run(exercise())


def test_github_read_content_returns_text_not_base64():
    import base64
    source = 'fn main() {\n    println!("hi");\n}\n'
    class Contents(Transport):
        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if url.endswith('/contents/src'):
                return [{'name': 'main.rs', 'path': 'src/main.rs', 'type': 'file', 'size': 40, '_links': {'self': 'u' * 200}}]
            if url.endswith('/logo.png'):
                return {'path': 'logo.png', 'sha': 'b2', 'size': 3, 'encoding': 'base64',
                        'content': base64.b64encode(b'\x89PN\xff').decode()}
            return {'path': 'src/main.rs', 'sha': 'b1', 'size': len(source), 'encoding': 'base64',
                    'content': base64.encodebytes(source.encode()).decode(), '_links': {'self': 'u' * 200}}
    service = integrations()['github']; operation = {item.name: item for item in service.operations}['read_content']
    config, secrets, transport = {'base_url': service.base_url}, {'access_token': 'private'}, Contents()
    async def read(path):
        return await invoke(service, operation, config, secrets, {'owner': 'acme', 'repo': 'app', 'path': path}, transport)
    async def exercise():
        assert await read('src/main.rs') == {'path': 'src/main.rs', 'sha': 'b1', 'size': len(source), 'text': source}
        assert await read('src') == [{'name': 'main.rs', 'path': 'src/main.rs', 'type': 'file', 'size': 40}]
        assert await read('logo.png') == {'path': 'logo.png', 'sha': 'b2', 'size': 3, 'binary': True}
        await invoke(service, operation, config, secrets, {'owner': 'acme', 'repo': 'app', 'path': 'src/main.rs', 'ref': 'fix/issue-7'}, transport)
        assert transport.calls[-1][1].endswith('/contents/src/main.rs') and transport.calls[-1][2]['params'] == {'ref': 'fix/issue-7'}
    asyncio.run(exercise())


def test_github_read_checks_lists_each_run_briefly():
    class Checks(Transport):
        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return {'total_count': 2, 'check_runs': [
                {'name': 'Test and format', 'status': 'completed', 'conclusion': 'failure', 'html_url': 'https://github.com/run/1',
                 'completed_at': '2026-10-14T08:06:31Z', 'output': {'text': 'x' * 50000}, 'app': {'owner': {}}},
                {'name': 'cargo audit', 'status': 'in_progress', 'conclusion': None, 'html_url': 'https://github.com/run/2', 'completed_at': None}]}
    service = integrations()['github']; operation = {item.name: item for item in service.operations}['read_checks']
    assert operation.capability == 'read'
    transport = Checks()
    async def exercise():
        result = await invoke(service, operation, {'base_url': service.base_url}, {'access_token': 'private'},
                              {'owner': 'acme', 'repo': 'app', 'ref': 'fix/issue-7'}, transport)
        assert transport.calls[-1][1] == 'https://api.github.com/repos/acme/app/commits/fix/issue-7/check-runs'
        assert result == {'total_count': 2, 'check_runs': [
            {'name': 'Test and format', 'status': 'completed', 'conclusion': 'failure', 'html_url': 'https://github.com/run/1', 'completed_at': '2026-10-14T08:06:31Z'},
            {'name': 'cargo audit', 'status': 'in_progress', 'conclusion': None, 'html_url': 'https://github.com/run/2', 'completed_at': None}]}
        with pytest.raises(Exception):
            await invoke(service, operation, {'base_url': service.base_url}, {'access_token': 'private'},
                         {'owner': 'acme', 'repo': 'app', 'ref': '../secrets'}, transport)
    asyncio.run(exercise())
