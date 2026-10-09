import asyncio

import pytest
from conclave import ApprovalRequest, Capability
from conclave.application.tool_approvals import ToolApprovals
from conclave.application.runs import RunRepository
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
