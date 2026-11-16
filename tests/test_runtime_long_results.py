"""A result longer than the run's tool output limit does not ride along forever.

Only a tool allowed more than the run's limit for one call — a read that
followed pages — can return such a result. The agent sees it in full when it
arrives, is told it will be shortened, and after its next action the earlier
copy in its conversation is cut back to the run's limit. Without this, every
later model request re-sent every long result: three paginated reads made the
fourth request about 300,000 characters.
"""
import asyncio

from conclave import AgentSpec, Capability, RunConfig, Tool, ToolAgentRuntime, ToolRegistry
from conftest import ScriptedProvider, make_budget

LIMIT = 20_000
BIG = 'connection.mail.search_messages'
BLOCK = '{"pagination": {"pages": 5, "items": 500, "next_cursor": "tok-500"}, "messages": ['


def long_result(number, size=99_000):
    body = ', '.join(f'"r{number}-{i:05d}-' + 'x' * 84 + '"' for i in range(size // 100))
    return BLOCK + body + ']}'


class Recording(ScriptedProvider):
    """Keeps a copy of every request: the runtime keeps editing its own list."""

    def __init__(self, script):
        super().__init__(scripts={'writer': script})
        self.requests = []

    async def complete(self, messages):
        self.requests.append([dict(message) for message in messages])
        return await super().complete(messages)


def registry(limit=LIMIT):
    tools = ToolRegistry(limit)
    counter = {'big': 0}

    async def big(**arguments):
        counter['big'] += 1
        return long_result(counter['big'])

    async def small(**arguments):
        return 'S' * 5_000

    tools.register(Tool(BIG, 'Search messages', {'type': 'object', 'properties': {}}, big,
                        frozenset({Capability.READ, Capability.NETWORK}),
                        output_limit=lambda arguments, cap: 100_000))
    tools.register(Tool('small', 'Small read', {'type': 'object', 'properties': {}}, small,
                        frozenset({Capability.READ})))
    return tools


def run(script, *, limit=LIMIT):
    provider = Recording(script + [{'type': 'final', 'content': 'Done'}])
    config = RunConfig(max_tool_steps=12, max_notes=12, max_note_chars=300, max_tool_output_chars=limit, fan_out_limit=4)
    agents = {'writer': AgentSpec(name='writer', instructions='Gather.', tools={BIG, 'small'})}
    runtime = ToolAgentRuntime(provider, registry(limit), make_budget(), agents, config=config)

    async def go():
        return [event async for event in runtime.stream(agents['writer'], 'Collect everything')]

    asyncio.run(go())
    return provider.requests


def size(request):
    return sum(len(message['content']) for message in request)


def call(name):
    return {'type': 'tool', 'name': name, 'arguments': {}}


def holding(request, marker):
    return [message['content'] for message in request if marker in message['content']]


def test_a_long_result_arrives_in_full_with_a_warning():
    requests = run([call(BIG)])
    [arrived] = holding(requests[1], 'r1-00000')
    assert long_result(1) in arrived, 'the agent reads the whole result once'
    assert 'record anything you will need in a note' in arrived
    assert f'{LIMIT:,}' in arrived


def test_after_the_next_action_the_earlier_copy_is_shortened():
    requests = run([call(BIG), call('small')])
    [earlier] = holding(requests[2], 'r1-00000')
    assert long_result(1) not in earlier
    assert len(earlier) < LIMIT + 1_000
    assert BLOCK in earlier, 'the pagination block opens the result, so it survives'
    assert '[earlier result shortened from' in earlier


def test_the_newest_long_result_is_always_whole():
    requests = run([call(BIG), call(BIG)])
    assert long_result(2) in holding(requests[2], 'r2-00000')[0]
    assert long_result(1) not in holding(requests[2], 'r1-00000')[0]


def test_three_paginated_reads_no_longer_compound():
    requests = run([call(BIG), call(BIG), call(BIG)])
    # Was about 300,000 characters: every full result re-sent every time.
    assert size(requests[-1]) <= 145_000
    for number in (1, 2):
        assert BLOCK in holding(requests[-1], f'r{number}-00000')[0]


def test_results_within_the_limit_are_never_touched():
    requests = run([call('small'), call('small'), call(BIG), call('small')])
    first = holding(requests[1], 'SSSS')
    assert first and all(message in holding(requests[-1], 'SSSS') for message in first)
    assert all('shortened' not in message for message in holding(requests[-1], 'SSSS'))


def test_nothing_is_shortened_when_the_run_has_no_limit():
    requests = run([call(BIG), call('small')], limit=None)
    [earlier] = holding(requests[2], 'r1-00000')
    assert long_result(1) in earlier
    assert 'shortened' not in earlier and 'record anything you will need' not in earlier
