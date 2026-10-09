"""One-shot, live-process tool approvals; a restart never replays an action."""
import asyncio
import uuid


class ToolApprovals:
    def __init__(self, repository, *, timeout=300):
        self.repository, self.timeout, self.pending = repository, timeout, {}

    def approver(self, run_id, owner):
        async def approve(request):
            identifier = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            # Keep arguments in process memory for authenticated review only; do
            # not persist payloads that may contain personal or secret data.
            self.pending[identifier] = {'run_id': run_id, 'owner': owner, 'request': request, 'future': future}
            self.repository._append(run_id, {'type':'ToolApprovalRequested','approval_id':identifier,
                                             'agent':request.agent,'tool':request.tool})
            try:
                decision = await asyncio.wait_for(future, self.timeout)
                return decision
            except TimeoutError:
                self.repository._append(run_id, {'type':'ToolApprovalResolved','approval_id':identifier,
                                                 'approved':False,'reason':'Approval expired'})
                return False
            finally:
                self.pending.pop(identifier, None)
        return approve

    def list(self, run_id, owner):
        return [{'id':identifier,'agent':item['request'].agent,'tool':item['request'].tool,
                 'arguments':item['request'].arguments,
                 'capabilities':sorted(str(cap) for cap in item['request'].capabilities)}
                for identifier,item in self.pending.items() if item['run_id']==run_id and item['owner']==owner]

    def decide(self, run_id, identifier, owner, approved):
        item = self.pending.get(identifier)
        if not item or item['run_id'] != run_id or item['owner'] != owner or item['future'].done():
            raise ValueError('Approval is missing, expired, already decided or belongs to another user')
        if type(approved) is not bool:
            raise ValueError('Choose approve or reject explicitly')
        self.repository._append(run_id, {'type':'ToolApprovalResolved','approval_id':identifier,
                                         'approved':approved,'reviewer':owner})
        item['future'].set_result(approved)
