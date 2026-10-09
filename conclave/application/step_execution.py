"""Safe isolated execution adapter over the application runtime."""
import asyncio
import copy
from contextlib import aclosing
from dataclasses import replace

from conclave import Capability
from .capture import redact
from .definition import run_application
from .step_testing import compare, test_scope


class MockExecutor:
    def __init__(self, mocks):
        self.mocks = mocks

    async def execute(self, invocation):
        if invocation.name not in self.mocks:
            raise ValueError('Blocked in safe test mode: ' + invocation.name)
        return self.mocks[invocation.name]


def safe_tools(tools, mocks):
    if not isinstance(mocks, dict) or any(not isinstance(value, str) for value in mocks.values()) or set(mocks) - set(tools.tools):
        raise ValueError('Mock results must name registered tools and contain text')
    registry = tools.copy()
    registry.executor = MockExecutor(mocks)
    registry.tools = {name: replace(tool, capabilities=frozenset({Capability.READ}) if name in mocks else frozenset({Capability.WRITE})) for name, tool in registry.tools.items()}
    return registry


async def execute_test(store, application, owner, record, fixture, provider, tools):
    definition = record['request']['definition']
    selected, seeds, warnings = test_scope(fixture, definition, record['scope'])
    record['warnings'] = warnings
    record['status'] = 'running'
    store.save(application, owner, 'test', record)
    async def consume():
        stream = run_application(definition, provider, safe_tools(tools, record.get('mocks', {})), fixture['task'],
            inputs=fixture['inputs'], execution_steps=selected, seed_patches=seeds, approver=lambda request: False)
        async with aclosing(stream):
            async for event in stream:
                event = {key: value for key, value in event.items() if key != 'checkpoint'}
                if event['type'] == 'ApplicationFinished':
                    record['status'] = event['status']
                    event['type'] = 'TestFinished'
                elif event['type'] == 'ApplicationStarted':
                    event['type'] = 'TestStarted'
                record['events'].append({'sequence': len(record['events']) + 1, 'event': redact(event)})
                if event['type'] == 'ApplicationStateChanged' and event.get('stage') == fixture['step_id']:
                    record['output'] = event['patch']
                    # With no recorded reference there is nothing to compare
                    # against, and a comparison against nothing would read as a
                    # failure. Say so by leaving it out.
                    if fixture.get('reference_recorded'):
                        record['comparison'] = compare(fixture['reference'], record['output'])
                store.save(application, owner, 'test', record)
    try:
        clock = definition['run_budget'].get('wall_clock_seconds')
        async with asyncio.timeout(clock):
            await consume()
    except asyncio.CancelledError:
        record['status'] = 'interrupted'
        store.save(application, owner, 'test', record)
        raise
    except Exception as error:
        record['status'] = 'failed'
        record['error'] = 'Test deadline exceeded' if isinstance(error, TimeoutError) else 'Test execution failed; inspect validation and recorded activity'
        store.save(application, owner, 'test', record)
