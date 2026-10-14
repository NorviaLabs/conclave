import asyncio

import pytest
from conclave import ApprovalRequest, Capability, Tool, ToolRegistry
from conclave.application.tool_approvals import ToolApprovals
from conclave.application.runs import RunRepository
from conftest import ScriptedProvider
from test_studio_runs import request


def test_one_shot_owner_bound_approvals_and_timeout(tmp_path):
    repository=RunRepository(tmp_path/'runs.sqlite')
    run_id=repository.create(request())
    approvals=ToolApprovals(repository,timeout=.01)
    call=ApprovalRequest('writer','connection.slack.send_message',{'channel':'public','text':'Hello'},frozenset({Capability.WRITE}))
    async def exercise():
        task=asyncio.create_task(approvals.approver(run_id,'alice')(call))
        await asyncio.sleep(0)
        assert approvals.list(run_id,'bob')==[]
        pending=approvals.list(run_id,'alice')[0]
        with pytest.raises(ValueError):approvals.decide(run_id,pending['id'],'bob',True)
        with pytest.raises(ValueError):approvals.decide(run_id,pending['id'],'alice','yes')
        approvals.decide(run_id,pending['id'],'alice',True)
        with pytest.raises(ValueError):approvals.decide(run_id,pending['id'],'alice',True)
        assert await task is True
        assert approvals.list(run_id,'alice')==[]
        assert await approvals.approver(run_id,'alice')(call) is False
        task=asyncio.create_task(approvals.approver(run_id,'alice')(call));await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert approvals.list(run_id,'alice')==[]
    asyncio.run(exercise())
    record=repository.get(run_id)
    assert all('arguments' not in item['event'] for item in record['events'])
    assert any(item['event'].get('reason')=='Approval expired' for item in record['events'])


@pytest.mark.parametrize("interactive", [True, False])
def test_autonomous_application_pre_authorizes_declared_writes(tmp_path, interactive):
    host, value, writes = write_host(tmp_path, interactive_approvals=interactive)
    value["definition"]["autonomous_execution"] = True

    async def exercise():
        _, run = await host.handle("start", value)
        record = await settle(host, run["id"])
        assert record["status"] == "completed" and writes == ["notes.txt"]
        events = [item["event"] for item in record["events"]]
        grant = next(event for event in events if event["type"] == "ToolAuthorizationGranted")
        assert grant["authorizer"] == "alice" and grant["tools"] == {"writer": ["write_file"]}
        assert not any(event["type"] in {"ToolApprovalRequested", "ToolDenied"} for event in events)
        await host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("capabilities", [frozenset(), frozenset({"unknown", Capability.WRITE})])
def test_autonomous_application_does_not_authorize_unknown_capabilities(tmp_path, capabilities):
    host, value, writes = write_host(tmp_path)
    value["definition"]["autonomous_execution"] = True
    host.tools.tool("write_file").capabilities = capabilities

    async def exercise():
        _, run = await host.handle("start", value)
        record = await settle(host, run["id"])
        assert writes == []
        assert any(item["event"]["type"] == "ToolDenied" for item in record["events"])
        assert host.tool_approvals.list(run["id"], "alice") == []
        await host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("value", ["true", 1, None, {}])
def test_autonomous_execution_requires_an_explicit_boolean(value):
    from conclave.application.definition import validate_definition
    definition = request()["definition"]
    definition["autonomous_execution"] = value
    with pytest.raises(ValueError, match="Autonomous execution must be a boolean"):
        validate_definition(definition)


def test_autonomous_execution_preserves_explicit_human_steps(tmp_path):
    from conclave.application.service import LocalRunHost
    value = request()
    value["definition"]["autonomous_execution"] = True
    provider = ScriptedProvider(scripts={"writer": [{"type": "final", "content": "Done", "text": "Result"}]})
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, ToolRegistry(2000), reviewer="alice")

    async def exercise():
        _, run = await host.handle("start", value)
        for _ in range(200):
            record = host.repository.get(run["id"])
            if record["status"] == "waiting_for_human":
                break
            await asyncio.sleep(.001)
        assert record["status"] == "waiting_for_human"
        assert record["reviews"][0]["stage"] == "review"
        await host.close()

    asyncio.run(exercise())


def test_approval_rest_decisions_and_one_time_resolution(tmp_path):
    from conclave import ToolRegistry
    from conclave.api import ApplicationAPI
    from conclave.application.service import LocalRunHost
    from conclave.application.workspace import Workspace
    repository=RunRepository(tmp_path/'runs.sqlite');run_id=repository.create(request())
    host=LocalRunHost(repository,None,ToolRegistry(1000),reviewer='alice')
    api=ApplicationAPI(host,Workspace(tmp_path/'apps.sqlite'))
    call=ApprovalRequest('writer','connection.slack.send_message',{'text':'Hello'},frozenset({Capability.WRITE}))
    async def exercise():
        task=asyncio.create_task(host.tool_approvals.approver(run_id,'alice')(call));await asyncio.sleep(0)
        path=f'/api/runs/{run_id}/tool-approvals'
        status,result=await api.dispatch('GET',path)
        assert status==200 and len(result['approvals'])==1
        decision={'approval_id':result['approvals'][0]['id'],'approved':False}
        assert (await api.dispatch('POST',path,decision))[0]==200
        assert await task is False
        assert (await api.dispatch('POST',path,decision))[0]==400
        assert (await api.dispatch('GET',path))[1]['approvals']==[]
    asyncio.run(exercise())


def write_host(tmp_path, *, interactive_approvals=True):
    """A host whose only tool is a write, and a run that wants to call it."""
    from conclave.application.service import LocalRunHost

    writes = []

    async def write_file(path, content):
        writes.append(path)
        return f"Wrote {path}"

    tools = ToolRegistry(2000)
    tools.register(Tool("write_file", "Write a file", {"path": "string", "content": "string"},
                        write_file, frozenset({Capability.WRITE})))
    value = request()
    value["definition"]["workflow"] = value["definition"]["workflow"][:1]
    value["definition"]["agents"] = value["definition"]["agents"][:1]
    value["definition"]["agents"][0]["tools"] = ["write_file"]
    provider = ScriptedProvider(scripts={"writer": [
        {"type": "tool", "name": "write_file", "arguments": {"path": "notes.txt", "content": "x"}},
        {"type": "final", "content": "Done", "text": "Result"},
    ]})
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, tools, reviewer="alice",
                        interactive_approvals=interactive_approvals)
    return host, value, writes


async def settle(host, run_id):
    for _ in range(200):
        record = host.repository.get(run_id)
        if record["status"] in {"completed", "failed", "interrupted"}:
            return record
        await asyncio.sleep(.001)
    return host.repository.get(run_id)


def test_a_host_asks_the_person_before_a_write_tool_runs(tmp_path):
    host, value, writes = write_host(tmp_path)

    async def exercise():
        _, run = await host.handle("start", value)
        for _ in range(200):
            pending = host.tool_approvals.list(run["id"], "alice")
            if pending:
                break
            await asyncio.sleep(.001)
        assert pending and pending[0]["tool"] == "write_file" and writes == []
        host.tool_approvals.decide(run["id"], pending[0]["id"], "alice", True)
        assert (await settle(host, run["id"]))["status"] == "completed"
        assert writes == ["notes.txt"]
        await host.close()

    asyncio.run(exercise())


def test_a_host_with_no_approval_surface_refuses_without_waiting(tmp_path):
    host, value, writes = write_host(tmp_path, interactive_approvals=False)

    async def exercise():
        _, run = await host.handle("start", value)
        record = await settle(host, run["id"])
        assert record["status"] == "completed" and writes == []
        assert host.tool_approvals.list(run["id"], "alice") == []
        assert any(item["event"].get("type") == "ToolDenied" for item in record["events"])
        await host.close()

    asyncio.run(exercise())
