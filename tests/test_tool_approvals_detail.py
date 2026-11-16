"""A pending approval's preview is fetched once, not on every poll.

Studio polls the pending approvals every 1.2 s. A Notion preview can be half a
megabyte, so the list carries only its summary; the blocks and the report come
from the approval's own address, fetched once per approval.
"""
import asyncio
import json

import pytest

from conclave import ApprovalRequest, Capability, ToolRegistry
from conclave.api import ApplicationAPI
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.tool_approvals import ToolApprovals
from conclave.application.workspace import Workspace
from conclave.integrations.notion_blocks import convert
from test_studio_runs import request

REPORT = '\n\n'.join(f'## Theme {n}\n\nCustomers said **{n}** things. See [it](https://x.example/{n}).\n\n- one\n- two'
                     for n in range(150))


def notion_call():
    conversion = convert(REPORT)
    preview = {'kind': 'notion_publish', 'operation': 'append_content',
               'destination': {'page_id': '1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6', 'name': 'Runbook', 'allowed': True},
               'title': None, 'total_blocks': conversion.total, 'requests': len(conversion.chunks),
               'notes': conversion.notes, 'blocks': conversion.blocks}
    arguments = {'target_page_id': '1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6', 'markdown': REPORT}
    return ApprovalRequest('writer', 'connection.wiki.append_content', arguments, frozenset({Capability.WRITE}),
                           preview=preview), preview, arguments


def pending(approvals, run_id, call, owner='alice'):
    async def exercise(inspect):
        task = asyncio.create_task(approvals.approver(run_id, owner)(call))
        await asyncio.sleep(0)
        try:
            return await inspect()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    return exercise


def test_the_list_carries_a_summary_not_the_blocks(tmp_path):
    repository = RunRepository(tmp_path / 'runs.sqlite')
    run_id = repository.create(request())
    approvals = ToolApprovals(repository, timeout=5)
    call, preview, arguments = notion_call()

    async def inspect():
        return approvals.list(run_id, 'alice')

    [item] = asyncio.run(pending(approvals, run_id, call)(inspect))
    assert item['detail'] is True
    assert item['preview'] == {key: value for key, value in preview.items() if key != 'blocks'}
    assert 'markdown' not in item['arguments'] and item['arguments']['target_page_id'] == arguments['target_page_id']
    assert len(json.dumps(item)) < 8_000, 'a poll is a few kilobytes, not half a megabyte'
    assert len(json.dumps({'preview': preview, 'arguments': arguments})) > 200_000, 'the preview itself is large'


def test_a_write_without_a_preview_is_listed_as_before(tmp_path):
    repository = RunRepository(tmp_path / 'runs.sqlite')
    run_id = repository.create(request())
    approvals = ToolApprovals(repository, timeout=5)
    call = ApprovalRequest('writer', 'connection.slack.send_message', {'text': 'Hello'}, frozenset({Capability.WRITE}))

    async def inspect():
        return approvals.list(run_id, 'alice')

    [item] = asyncio.run(pending(approvals, run_id, call)(inspect))
    assert item['arguments'] == {'text': 'Hello'} and item['preview'] is None and not item.get('detail')


def test_the_detail_is_the_whole_approval_for_its_owner_only(tmp_path):
    repository = RunRepository(tmp_path / 'runs.sqlite')
    run_id = repository.create(request())
    other_run = repository.create(request())
    approvals = ToolApprovals(repository, timeout=5)
    call, preview, arguments = notion_call()

    async def inspect():
        [item] = approvals.list(run_id, 'alice')
        full = approvals.get(run_id, item['id'], 'alice')
        for run, owner, identifier in [(run_id, 'bob', item['id']), (other_run, 'alice', item['id']), (run_id, 'alice', 'missing')]:
            with pytest.raises(ValueError):
                approvals.get(run, identifier, owner)
        return full

    full = asyncio.run(pending(approvals, run_id, call)(inspect))
    assert full['preview'] == preview and full['arguments'] == arguments


def test_the_detail_has_its_own_address(tmp_path):
    repository = RunRepository(tmp_path / 'runs.sqlite')
    run_id = repository.create(request())
    host = LocalRunHost(repository, None, ToolRegistry(1000), reviewer='alice')
    api = ApplicationAPI(host, Workspace(tmp_path / 'apps.sqlite'))
    call, preview, arguments = notion_call()

    async def exercise():
        task = asyncio.create_task(host.tool_approvals.approver(run_id, 'alice')(call))
        await asyncio.sleep(0)
        listed = (await api.dispatch('GET', f'/api/runs/{run_id}/tool-approvals'))[1]['approvals'][0]
        status, detail = await api.dispatch('GET', f'/api/runs/{run_id}/tool-approvals/{listed["id"]}')
        missing = (await api.dispatch('GET', f'/api/runs/{run_id}/tool-approvals/nope'))[0]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return status, detail, missing

    status, detail, missing = asyncio.run(exercise())
    assert status == 200 and detail['preview']['blocks'] == preview['blocks'] and detail['arguments']['markdown'] == REPORT
    assert missing in (400, 404)
