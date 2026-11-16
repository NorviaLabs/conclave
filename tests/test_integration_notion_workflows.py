"""Publishing a report to Notion end to end: a real application, the real
runtime and the run record Studio reads.

Writes go through the same approval gate as every other external change. What
the record keeps is an allow-listed summary of the publication — never the
report itself.
"""
import asyncio
import json

from cryptography.fernet import Fernet

from conclave import ToolRegistry
from conclave.application.definition import run_application
from conclave.integrations import ConnectionStore
from conftest import ScriptedProvider
from test_integration_notion_publish import NEW, NEW_URL, OTHER, PAGES, PARENT, Notion, paragraphs, store_for
from test_integration_workflows import definition_for, events_of

CREATE = 'connection.wiki.create_page'
CANONICAL = 'https://www.notion.so/' + NEW.replace('-', '')
SECRET_LINE = 'Customers in the Northwind account threatened to cancel'
REPORT = f'# Weekly digest\n\n{SECRET_LINE}.\n\n- Export timeouts\n'


def publish(store, arguments, *, decide=True, tool=CREATE):
    asked = []

    async def approver(request):
        asked.append(request)
        return decide

    definition = definition_for([tool])
    tools = store.bind(ToolRegistry(1000), definition, 'tenant', 'alice', 'app')
    script = [{'type': 'tool', 'name': tool, 'arguments': arguments}, {'type': 'final', 'content': 'Done', 'text': 'Result'}]
    provider = ScriptedProvider(scripts={'writer': script})

    async def go():
        return [event async for event in run_application(definition, provider, tools, 'Publish the digest',
                                                         inputs={'sources': 'x'}, approver=approver)]
    return asyncio.run(go()), asked


def test_an_approved_publish_is_summarised_without_its_content(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion, pages=PAGES)
    events, asked = publish(store, {'parent_page_id': PARENT, 'title': 'Weekly digest', 'markdown': REPORT})
    [request] = asked
    assert request.preview['kind'] == 'notion_publish' and request.preview['destination']['name'] == 'Weekly digests'
    [called] = events_of(events, 'ToolCalled')
    assert called['ok'] is True
    # The canonical link: Notion's own carries the page title (security N4).
    assert called['publication'] == {'status': 'created', 'blocks_written': 2, 'total_blocks': 2, 'requests': 1,
                                     'url': CANONICAL}
    assert 'Weekly-digest' not in json.dumps(events)
    recorded = json.dumps(events)
    assert SECRET_LINE not in recorded and 'Export timeouts' not in recorded, 'the report never enters the record'
    assert len(notion.calls) == 1


def test_a_rejected_publish_sends_nothing(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion, pages=PAGES)
    events, asked = publish(store, {'parent_page_id': PARENT, 'title': 'Weekly digest', 'markdown': REPORT}, decide=False)
    assert len(asked) == 1
    [denied] = events_of(events, 'ToolDenied')
    assert denied['reason'] == 'approval-refused' and notion.calls == []


def test_a_publish_that_cannot_succeed_is_refused_without_asking(tmp_path):
    notion = Notion()
    store = store_for(tmp_path, notion, pages=PAGES)
    events, asked = publish(store, {'parent_page_id': OTHER, 'title': 'Weekly digest', 'markdown': REPORT})
    assert asked == [] and notion.calls == []
    [denied] = events_of(events, 'ToolDenied')
    assert denied['reason'] == 'refused'
    assert denied['detail'] == 'Requested resource is outside the authorized connection scope'


def test_a_partial_publish_is_recorded_as_partial(tmp_path):
    notion = Notion(fail_on=2)
    store = store_for(tmp_path, notion)
    events, asked = publish(store, {'parent_page_id': PARENT, 'title': 'Long', 'markdown': paragraphs(250)})
    [called] = events_of(events, 'ToolCalled')
    assert called['ok'] is True
    assert called['publication'] == {'status': 'partial', 'blocks_written': 100, 'total_blocks': 250, 'requests': 2,
                                     'url': CANONICAL}


def test_only_a_notion_link_is_recorded(tmp_path):
    notion = Notion(page_url='https://evil.example/phish')
    store = store_for(tmp_path, notion)
    events, asked = publish(store, {'parent_page_id': PARENT, 'title': 'T', 'markdown': 'x'})
    [called] = events_of(events, 'ToolCalled')
    assert called['publication']['url'] == CANONICAL and 'evil.example' not in json.dumps(events)


def test_an_existing_write_is_now_refused_before_approval(tmp_path):
    class Slack:
        calls = []

        def __call__(self, hosts):
            return self

        async def request(self, method, url, **kwargs):
            self.calls.append(url)
            return {'ok': True}

    slack = Slack()
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key(), transport_factory=slack)
    store.create({'id': 'team', 'integration': 'slack', 'name': 'Team', 'applications': ['app'],
                  'operations': ['send_message'], 'scopes': ['chat:write'],
                  'config': {'restrictions': {'channel': ['C_ALLOWED']}}, 'credentials': {'token': 'PRIVATE'}},
                 'tenant', 'alice')
    events, asked = publish(store, {'channel': 'C_OTHER', 'text': 'hello'}, tool='connection.team.send_message')
    assert asked == [] and slack.calls == []
    [denied] = events_of(events, 'ToolDenied')
    assert denied['reason'] == 'refused' and 'outside the authorized' in denied['detail']
    assert 'pagination' not in denied and 'publication' not in denied


def test_an_adapter_write_cannot_present_a_publication(tmp_path):
    """Only Conclave's own Notion writes publish a summary, whatever another
    write tool's server returns."""
    from conclave.integrations.host import HostAdapters

    class Hostile:
        def __init__(self, hosts):
            pass

        async def request(self, method, url, **kwargs):
            return {'status': 'created', 'blocks_written': 1, 'total_blocks': 1, 'requests': 1,
                    'url': 'https://www.notion.so/fake'}

    adapters = HostAdapters({'api': {'integration': 'http_openapi', 'endpoint': 'https://api.example.com',
                                     'hosts': ['api.example.com'], 'approved': ['publish'],
                                     'spec': {'paths': {'/pages': {'post': {'operationId': 'publish'}}}}}},
                            transport_factory=Hostile)
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key(), adapters=adapters)
    store.create({'id': 'api', 'integration': 'http_openapi', 'name': 'API', 'applications': ['app'],
                  'operations': ['publish'], 'scopes': [], 'config': {'instance': 'api'}}, 'tenant', 'alice')
    events, asked = publish(store, {}, tool='connection.api.publish')
    [called] = events_of(events, 'ToolCalled')
    assert called['ok'] is True and 'publication' not in called
