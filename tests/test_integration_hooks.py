import asyncio
import json
import time

import pytest
from cryptography.fernet import Fernet
from conclave.application.hooks import Deliveries, resolver, signature, verify
from conclave.integrations import ConnectionStore


def headers(secret, body, *, delivery='delivery-1', when=None):
    timestamp = str(int(when if when is not None else time.time()))
    return {'x-conclave-timestamp': timestamp, 'x-conclave-signature': signature(secret, timestamp, body),
            'x-conclave-delivery': delivery}


def test_signature_window_replay_and_body_bounds():
    secret = 'a-long-enough-signing-secret'
    body = json.dumps({'task': 'Reconcile the report', 'delivery_id': 'delivery-1'}).encode()
    assert verify(secret, headers(secret, body), body)['task'] == 'Reconcile the report'
    for wrong in [headers('another-secret-entirely', body), headers(secret, body, when=time.time() - 3600),
                  {'x-conclave-timestamp': 'now', 'x-conclave-signature': 'x'},
                  {'x-conclave-timestamp': '1', 'x-conclave-signature': 'x'}]:
        with pytest.raises(ValueError):
            verify(secret, wrong, body)
    with pytest.raises(ValueError):
        verify(secret, headers(secret, b'"a string"'), b'"a string"')
    with pytest.raises(ValueError):
        verify('short', headers(secret, body), body)
    deliveries = Deliveries(limit=2)
    assert deliveries.claim('one') and not deliveries.claim('one')
    assert deliveries.claim('two') and deliveries.claim('three')
    assert deliveries.claim('one')
    assert not deliveries.claim('')


def test_hook_connection_authority_and_agent_exclusion(tmp_path):
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key())
    store.create({'id': 'inbound', 'integration': 'webhooks', 'name': 'Inbound', 'applications': ['app'],
                  'operations': ['receive'], 'credentials': {'signing_secret': 'a-long-enough-signing-secret'}},
                 'tenant', 'alice')
    record = store.hook('inbound')
    assert record['owner'] == 'alice' and record['tenant'] == 'tenant'
    assert store.start_hook_run(record, {'task': 'Do the thing', 'delivery_id': 'd1'})['delivery_id'] == 'd1'
    for payload in [{}, {'task': ''}, {'task': 'x' * 20001}]:
        with pytest.raises(Exception):
            store.start_hook_run(record, payload)
    store.create({'id': 'outbound', 'integration': 'webhooks', 'name': 'Outbound', 'applications': ['app'],
                  'operations': ['send'], 'credentials': {'token': 'x'}}, 'tenant', 'alice')
    with pytest.raises(Exception):
        store.hook('outbound')
    with pytest.raises(Exception):
        store.hook('missing')
    with pytest.raises(Exception, match='not agent tools'):
        store.bind(__import__('conclave').ToolRegistry(1000),
                   {'agents': [{'tools': ['connection.inbound.receive']}]}, 'tenant', 'alice', 'app')


def test_default_resolver_maps_one_delivery_to_one_run(tmp_path):
    secret = 'a-long-enough-signing-secret'
    store = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key())
    store.create({'id': 'inbound', 'integration': 'webhooks', 'name': 'Inbound', 'applications': ['app'],
                  'operations': ['receive'], 'credentials': {'signing_secret': secret}}, 'tenant', 'alice')

    class Host:
        connections = store

    class API:
        host = Host()

    class Services:
        def for_principal(self, principal): return API()

    from conclave.application.principal import Principal
    hooks = resolver(Services(), Principal('ops', 'tenant', source='hook'))
    body = json.dumps({'task': 'Reconcile', 'delivery_id': 'd1'}).encode()

    async def exercise():
        principal, value = await hooks('inbound', headers(secret, body), body)
        assert principal.source == 'hook' and principal.name == 'alice' and principal.tenant == 'tenant'
        assert value == {'payload': {'task': 'Reconcile', 'delivery_id': 'd1'}}
        with pytest.raises(ValueError, match='already received'):
            await hooks('inbound', headers(secret, body), body)
        with pytest.raises(ValueError, match='signature'):
            await hooks('inbound', headers('another-secret-entirely', body), body)
        with pytest.raises(AttributeError):
            await resolver(object(), Principal('ops', 'tenant'))('inbound', headers(secret, body), body)
        with pytest.raises(ValueError, match='Unknown hook connection'):
            await hooks('missing', headers(secret, body), body)
    asyncio.run(exercise())


def test_signed_delivery_starts_a_run_and_a_spoofed_one_cannot(tmp_path):
    """The whole path: signature check at the boundary, then a real run."""
    from conclave.asgi import application
    from conclave.application.principal import Principal
    from conclave.application.hooks import resolver as make_resolver
    from test_api import make_api
    from test_studio_runs import request as run_request

    secret = 'a-long-enough-signing-secret'
    api = make_api(tmp_path)
    saved = api.workspace.save(run_request()['definition'])
    tenant, owner = api.host.principal.tenant, api.host.principal.name
    api.host.connections = ConnectionStore(tmp_path / 'connections.sqlite', Fernet.generate_key())
    api.host.connections.create({'id': 'inbound', 'integration': 'webhooks', 'name': 'Inbound',
                                 'applications': [saved['id']], 'operations': ['receive'],
                                 'credentials': {'signing_secret': secret}}, tenant, owner)

    class Services:
        def for_principal(self, principal): return api
        async def close(self): pass

    services = Services()
    app = application(apis=services, token='deployment-token',
                      hooks=make_resolver(services, Principal(owner, tenant, source='hook')))
    # The same services without a hook resolver: this path must stay closed.
    plain = application(apis=services, token='deployment-token')
    body = json.dumps({'task': 'Reconcile the incident', 'delivery_id': 'delivery-1',
                       'sources': 'Support export, Thursday'}).encode()

    async def deliver(payload, *, app_=None, token=None, sign=True, stamp=None):
        when = str(int(stamp if stamp is not None else time.time())).encode()
        scope = {'type': 'http', 'method': 'POST', 'path': '/api/hooks/inbound',
                 'raw_path': b'/api/hooks/inbound',
                 'headers': [(b'host', b'test'), (b'content-type', b'application/json'),
                             (b'x-conclave-timestamp', when),
                             (b'x-conclave-signature',
                              signature(secret, when.decode(), payload).encode() if sign else b'0' * 64),
                             (b'content-length', str(len(payload)).encode())]}
        if token:
            scope['headers'].append((b'authorization', b'Bearer ' + token.encode()))
        pending = [{'type': 'http.request', 'body': payload, 'more_body': False}]
        sent = []
        async def receive(): return pending.pop(0)
        async def send(message): sent.append(message)
        await (app_ or app)(scope, receive, send)
        answer = b''.join(item.get('body', b'') for item in sent if item['type'] == 'http.response.body')
        return sent[0]['status'], json.loads(answer or b'{}')

    async def exercise():
        assert (await deliver(body, sign=False))[0] == 400
        assert (await deliver(body, stamp=time.time() - 3600))[0] == 400
        assert (await deliver(body, app_=plain))[0] == 401
        assert (await deliver(body, app_=plain, token='deployment-token'))[0] == 403
        status, result = await deliver(body)
        assert status == 201 and result['delivery_id'] == 'delivery-1'
        assert api.host.repository.get(result['run_id'])['request']['task'] == 'Reconcile the incident'
        assert (await deliver(body))[0] == 400  # one signed delivery starts one run
        await asyncio.sleep(0.05)
        await api.host.close()
    asyncio.run(exercise())
