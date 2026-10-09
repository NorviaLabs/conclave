import copy

from conclave import ToolRegistry
from conclave.application.definition import run_application
from conftest import ScriptedProvider
from test_studio_runs import request, collect


def test_isolated_parallel_branch_does_not_run_ancestors_or_sibling():
    value = request()
    definition = value['definition']
    definition['agents'] = definition['agents'][:1]
    definition['workflow'] = [
        {'id': name, 'agent': 'writer', 'task': name, 'output': 'brief'}
        for name in ['root', 'left', 'right', 'join']
    ]
    definition['connections'] = [
        {'from': 'root', 'to': 'left'}, {'from': 'root', 'to': 'right'},
        {'from': 'left', 'to': 'join'}, {'from': 'right', 'to': 'join'},
    ]
    before = copy.deepcopy(definition)
    provider = ScriptedProvider(default={'type': 'final', 'content': 'Done', 'text': 'Changed left'})
    events = collect(run_application(definition, provider, ToolRegistry(20000), value['task'],
        inputs=value['inputs'], execution_steps={'left'}, seed_patches={'root': 'Recorded root'}))
    assert [event['stage'] for event in events if event['type'] == 'StageStarted'] == ['left']
    assert len(provider.prompts) == 1
    assert 'Recorded root' in provider.prompts[0][1]
    assert definition == before
    assert events[-1]['state']['brief'] == 'Changed left'


def test_missing_isolated_prerequisite_is_refused_before_provider_call():
    value = request()
    provider = ScriptedProvider()
    import pytest
    with pytest.raises(ValueError, match='Missing recorded prerequisite'):
        collect(run_application(value['definition'], provider, ToolRegistry(20000), value['task'],
            inputs=value['inputs'], execution_steps={'review'}))
    assert not provider.prompts


def test_capture_sanitizes_nested_sensitive_fields():
    from conclave.application.capture import redact
    assert redact({'nested': [{'authorization': 'Bearer abc', 'body': 'Bearer abc'}]}) == {
        'nested': [{'authorization': '[REDACTED]', 'body': '[REDACTED]'}]}
