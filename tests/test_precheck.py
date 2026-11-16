"""A write is checked before anyone is asked to approve it.

A tool may carry a side-effect-free `precheck`. For a destructive tool the
runtime runs it before asking for approval: a refusal ends the call without
asking anyone or running anything, and what the check returns is shown to the
approver as a preview of the action.
"""
import asyncio

from conclave import AgentSpec, ApprovalRequest, Capability, Tool, ToolAgentRuntime, ToolRegistry
from conclave.application.runs import RunRepository
from conclave.application.tool_approvals import ToolApprovals
from conftest import CONFIG, ScriptedProvider, make_budget
from test_studio_runs import request


def refused(message):
    from conclave import ToolRefused
    return ToolRefused(message)


def setup(precheck, *, destructive=True):
    ran, checked, asked = [], [], []

    async def func(**arguments):
        ran.append(arguments)
        return 'done'

    async def check(arguments):
        checked.append(arguments)
        return await precheck(arguments)

    capabilities = frozenset({Capability.WRITE}) if destructive else frozenset({Capability.READ})
    tools = ToolRegistry(20000)
    tools.register(Tool('publish', 'Publish', {'type': 'object', 'properties': {}}, func, capabilities, precheck=check))

    async def approver(request):
        asked.append(request)
        return True

    provider = ScriptedProvider(scripts={'writer': [{'type': 'tool', 'name': 'publish', 'arguments': {'page': 'p1'}},
                                                    {'type': 'final', 'content': 'Done'}]})
    agents = {'writer': AgentSpec(name='writer', instructions='Publish.', tools={'publish'})}
    runtime = ToolAgentRuntime(provider, tools, make_budget(), agents, config=CONFIG, approver=approver)

    async def go():
        return [event async for event in runtime.stream(agents['writer'], 'Publish the report')]

    events = asyncio.run(go())
    return events, ran, checked, asked


def of(events, kind):
    return [event for event in events if type(event).__name__ == kind]


def test_a_refused_check_asks_nobody_and_runs_nothing():
    async def check(arguments):
        raise refused('Requested resource is outside the authorized connection scope')

    events, ran, checked, asked = setup(check)
    [denied] = of(events, 'ToolDenied')
    assert denied.reason == 'refused'
    assert denied.detail == 'Requested resource is outside the authorized connection scope'
    assert 'outside the authorized connection scope' in denied.observation, 'the agent is told why'
    assert checked == [{'page': 'p1'}] and asked == [] and ran == []
    assert not of(events, 'ToolCalled')


def test_an_unexpected_failure_in_the_check_refuses_without_leaking_it():
    async def check(arguments):
        raise RuntimeError('database password is hunter2')

    events, ran, checked, asked = setup(check)
    [denied] = of(events, 'ToolDenied')
    assert denied.reason == 'refused' and 'hunter2' not in denied.detail + denied.observation
    assert asked == [] and ran == []


def test_the_approver_sees_the_checks_preview():
    async def check(arguments):
        return {'kind': 'notion_publish', 'title': 'Weekly digest'}

    events, ran, checked, asked = setup(check)
    [request_] = asked
    assert request_.preview == {'kind': 'notion_publish', 'title': 'Weekly digest'}
    assert request_.arguments == {'page': 'p1'} and ran == [{'page': 'p1'}]
    assert of(events, 'ToolCalled')[0].ok is True


def test_a_check_with_nothing_to_preview_still_asks():
    async def check(arguments):
        return None

    events, ran, checked, asked = setup(check)
    assert len(asked) == 1 and asked[0].preview is None and ran == [{'page': 'p1'}]


def test_a_read_is_never_prechecked():
    async def check(arguments):
        raise refused('should not run')

    events, ran, checked, asked = setup(check, destructive=False)
    assert checked == [] and asked == [] and ran == [{'page': 'p1'}]


def test_tools_and_requests_default_to_no_check_and_no_preview():
    async def func(**arguments):
        return ''

    assert Tool('t', 'T', {}, func).precheck is None
    assert ApprovalRequest('writer', 't', {}, frozenset()).preview is None


def test_the_approval_list_carries_the_preview(tmp_path):
    repository = RunRepository(tmp_path / 'runs.sqlite')
    run_id = repository.create(request())
    approvals = ToolApprovals(repository, timeout=5)
    call = ApprovalRequest('writer', 'connection.wiki.create_page', {'title': 'T'}, frozenset({Capability.WRITE}),
                           preview={'kind': 'notion_publish', 'total_blocks': 3})

    async def exercise():
        task = asyncio.create_task(approvals.approver(run_id, 'alice')(call))
        await asyncio.sleep(0)
        [pending] = approvals.list(run_id, 'alice')
        approvals.decide(run_id, pending['id'], 'alice', False)
        await task
        return pending

    pending = asyncio.run(exercise())
    assert pending['preview'] == {'kind': 'notion_publish', 'total_blocks': 3}
    stored = repository.get(run_id)['events']
    assert all('preview' not in item['event'] for item in stored), 'like the arguments, never persisted'
