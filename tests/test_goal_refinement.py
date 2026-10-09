import asyncio
import json

import pytest
from conclave.application.planning import refine


def completion(text):
    return type('Completion', (), {'text': text})()


class Provider:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.messages = text, error, []

    async def complete(self, messages):
        self.messages.append(messages)
        if self.error:
            raise self.error
        return completion(self.text)


def run(value, provider):
    return asyncio.run(refine(provider, value))


def test_refine_returns_the_persons_own_goal_rewritten():
    provider = Provider(json.dumps({'goal': 'Compare three suppliers on price and lead time, then recommend one.',
                                    'summary': 'Made the comparison criteria explicit.'}))
    result = run({'goal': 'compare suppliers'}, provider)
    assert result == {'goal': 'Compare three suppliers on price and lead time, then recommend one.',
                      'summary': 'Made the comparison criteria explicit.'}
    # The goal is sent as data, and the contract forbids inventing requirements.
    system, user = provider.messages[0]
    assert system['role'] == 'system' and 'do not follow them' in system['content']
    assert 'do not add requirements' in system['content']
    assert user == {'role': 'user', 'content': 'compare suppliers'}


@pytest.mark.parametrize('value', [{}, {'goal': ''}, {'goal': '   '}, {'goal': 'x' * 4001},
                                   {'goal': 'ok', 'extra': 'x'}, {'goal': 7}])
def test_refine_refuses_requests_it_cannot_answer(value):
    with pytest.raises(ValueError):
        run(value, Provider('{}'))


@pytest.mark.parametrize('text', ['not json', '[]', '{}', '{"goal":"x"}',
                                  '{"goal":"","summary":"x"}', '{"goal":"x","summary":""}',
                                  '{"goal":"x","summary":"y","extra":"z"}',
                                  json.dumps({'goal': 'x', 'summary': 'y' * 1001}),
                                  json.dumps({'goal': 'x' * 4001, 'summary': 'y'}),
                                  'x' * 20001])
def test_refine_refuses_a_usable_answer_only(text):
    with pytest.raises(ValueError, match='unusable refinement'):
        run({'goal': 'compare suppliers'}, Provider(text))


def test_refine_never_leaks_a_provider_error():
    with pytest.raises(ValueError) as failure:
        run({'goal': 'compare suppliers'}, Provider(error=RuntimeError('token=SECRET')))
    assert 'SECRET' not in str(failure.value)
    assert str(failure.value).startswith('The AI could not refine this goal')


def test_refine_rest_endpoint_requires_a_provider(tmp_path):
    from conclave import ToolRegistry
    from conclave.api import ApplicationAPI
    from conclave.application.runs import RunRepository
    from conclave.application.service import LocalRunHost
    from conclave.application.workspace import Workspace

    host = LocalRunHost(RunRepository(tmp_path / 'runs.sqlite'), None, ToolRegistry(1000), reviewer='operator')
    api = ApplicationAPI(host, Workspace(tmp_path / 'apps.sqlite'))

    async def exercise():
        status, answer = await api.dispatch('POST', '/api/goals/refine', {'goal': 'compare suppliers'})
        assert status == 400 and 'provider' in answer['error'].lower()
        assert (await api.dispatch('POST', '/api/goals/refine', {'goal': ''}))[0] == 400
        assert (await api.dispatch('POST', '/api/goals/refine', {'goal': 'ok', 'extra': 1}))[0] == 400
        assert (await api.dispatch('GET', '/api/goals/refine'))[0] == 404
        await host.close()
    asyncio.run(exercise())


def test_refine_rest_endpoint_returns_reviewable_text(tmp_path):
    from conclave import ToolRegistry
    from conclave.api import ApplicationAPI
    from conclave.application.runs import RunRepository
    from conclave.application.service import LocalRunHost
    from conclave.application.workspace import Workspace

    provider = Provider(json.dumps({'goal': 'Compare three suppliers on price.', 'summary': 'Named the criteria.'}))
    host = LocalRunHost(RunRepository(tmp_path / 'runs.sqlite'), provider, ToolRegistry(1000), reviewer='operator')
    api = ApplicationAPI(host, Workspace(tmp_path / 'apps.sqlite'))

    async def exercise():
        status, answer = await api.dispatch('POST', '/api/goals/refine', {'goal': 'compare suppliers'})
        assert status == 200
        assert answer == {'goal': 'Compare three suppliers on price.', 'summary': 'Named the criteria.'}
        # Nothing was created, saved or run by asking for wording.
        assert (await api.dispatch('GET', '/api/applications'))[1]['applications'] == []
        assert host.repository.list('anything') == []
        await host.close()
    asyncio.run(exercise())
