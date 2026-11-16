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

    @staticmethod
    def _entry(identifier, item, *, whole):
        request = item['request']
        preview = getattr(request, 'preview', None)
        entry = {'id':identifier,'agent':request.agent,'tool':request.tool,'arguments':request.arguments,
                 'preview':preview,'capabilities':sorted(str(cap) for cap in request.capabilities)}
        if not whole and isinstance(preview, dict) and 'blocks' in preview:
            # Polled every second or so: the summary only. The blocks and the
            # report, which can be half a megabyte, come once from `get`.
            entry['preview'] = {key: value for key, value in preview.items() if key != 'blocks'}
            entry['arguments'] = {key: value for key, value in request.arguments.items() if key != 'markdown'}
            entry['detail'] = True
        return entry

    def list(self, run_id, owner):
        return [self._entry(identifier, item, whole=False)
                for identifier,item in self.pending.items() if item['run_id']==run_id and item['owner']==owner]

    def get(self, run_id, identifier, owner):
        """One pending approval in full, for the person it is waiting on."""
        item = self.pending.get(identifier)
        if not item or item['run_id'] != run_id or item['owner'] != owner or item['future'].done():
            raise ValueError('Approval is missing, expired, already decided or belongs to another user')
        return self._entry(identifier, item, whole=True)

    def decide(self, run_id, identifier, owner, approved):
        item = self.pending.get(identifier)
        if not item or item['run_id'] != run_id or item['owner'] != owner or item['future'].done():
            raise ValueError('Approval is missing, expired, already decided or belongs to another user')
        if type(approved) is not bool:
            raise ValueError('Choose approve or reject explicitly')
        self.repository._append(run_id, {'type':'ToolApprovalResolved','approval_id':identifier,
                                         'approved':approved,'reviewer':owner})
        item['future'].set_result(approved)
