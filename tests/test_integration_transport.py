import asyncio
import json

import pytest
from conclave.integrations.transport import Transport, IntegrationError


class Response:
    status = 200
    data = b'{"ok":true}'
    headers = {}
    def read(self, limit): return self.data[:limit]
    def getheader(self, name): return self.headers.get(name)


class Connection:
    instances = []
    def __init__(self, host, address, timeout):
        self.host, self.address, self.timeout = host, address, timeout
        self.closed = False
        self.instances.append(self)
    def request(self, method, path, body=None, headers=None): self.call = (method, path, body, headers)
    def getresponse(self): return Response()
    def close(self): self.closed = True


def test_pinned_transport_redirect_size_and_rate_limit(monkeypatch):
    from conclave.integrations import transport as module
    monkeypatch.setattr(module, 'public_addresses', lambda host: ['93.184.216.34'])
    monkeypatch.setattr(module, 'PinnedHTTPS', Connection)
    transport = Transport({'example.com'}, max_bytes=100)
    async def exercise():
        assert await transport.request('GET', 'https://example.com/api', params={'q': 'two words'}) == {'ok': True}
        connection = Connection.instances[-1]
        assert connection.address == '93.184.216.34'
        assert connection.call[1] == '/api?q=two+words' and connection.closed
        for status, message in [(302, 'redirect'), (429, 'rate limit'), (401, 'HTTP 401')]:
            monkeypatch.setattr(Response, 'status', status)
            with pytest.raises(IntegrationError, match=message): await transport.request('GET', 'https://example.com')
        monkeypatch.setattr(Response, 'status', 200)
        monkeypatch.setattr(Response, 'data', b'x' * 101)
        with pytest.raises(IntegrationError, match='size limit'): await transport.request('GET', 'https://example.com')
    asyncio.run(exercise())


def test_refusal_names_what_the_token_needs_from_headers_only(monkeypatch):
    from conclave.integrations import transport as module
    monkeypatch.setattr(module, 'public_addresses', lambda host: ['93.184.216.34'])
    monkeypatch.setattr(module, 'PinnedHTTPS', Connection)
    monkeypatch.setattr(Response, 'status', 403)
    monkeypatch.setattr(Response, 'data', b'{"message":"secret body"}')
    transport = Transport({'example.com'})
    async def refused(headers):
        monkeypatch.setattr(Response, 'headers', headers)
        with pytest.raises(IntegrationError) as caught:
            await transport.request('POST', 'https://example.com/pulls')
        return str(caught.value)
    async def exercise():
        assert await refused({'X-Accepted-GitHub-Permissions': 'pull_requests=write'}) == (
            'Provider request failed (HTTP 403); the token needs pull_requests=write')
        assert await refused({'X-GitHub-SSO': 'required; url=https://github.com/orgs/x/sso?token=abc'}) == (
            'Provider request failed (HTTP 403); the token needs SSO authorization for this organization')
        # Anything outside a plain permission list is not repeated.
        assert await refused({'X-Accepted-GitHub-Permissions': 'ghp_leaked <script>'}) == 'Provider request failed (HTTP 403)'
        assert 'secret body' not in await refused({})
        # GitHub names permissions on every response; a missing file is not a refusal.
        monkeypatch.setattr(Response, 'status', 404)
        assert await refused({'X-Accepted-GitHub-Permissions': 'contents=read'}) == 'Provider request failed (HTTP 404)'
    asyncio.run(exercise())


def test_transport_identifies_itself_and_a_service_may_override(monkeypatch):
    from conclave.integrations import transport as module
    monkeypatch.setattr(module, 'public_addresses', lambda host: ['93.184.216.34'])
    monkeypatch.setattr(module, 'PinnedHTTPS', Connection)
    Connection.instances = []
    transport = module.Transport({'example.com'})
    async def exercise():
        # GitHub answers 403 to a request with no User-Agent, so every call
        # carries one and a service that needs its own still wins.
        await transport.request('GET', 'https://example.com/api')
        headers = Connection.instances[-1].call[3]
        assert headers['User-Agent'] == 'Conclave/0.1 (authorized API integration)'
        assert headers['Accept'] == 'application/json'
        await transport.request('GET', 'https://example.com/api', headers={'User-Agent': 'Reddit/1'})
        assert Connection.instances[-1].call[3]['User-Agent'] == 'Reddit/1'
    asyncio.run(exercise())


def test_public_addresses_and_private_targets_are_refused(monkeypatch):
    import socket as module_socket
    from conclave.integrations import transport as module

    class Info:
        def __init__(self, address): self.address = address
        def __getitem__(self, index): return (module_socket.AF_INET, module_socket.SOCK_STREAM, 6, '', (self.address, 443))[index]

    for address, allowed in [('93.184.216.34', True), ('127.0.0.1', False), ('10.0.0.5', False),
                             ('169.254.169.254', False), ('::1', False), ('fd00::1', False), ('::ffff:10.0.0.1', False)]:
        monkeypatch.setattr(module.socket, 'getaddrinfo', lambda *args, _a=address, **kwargs: [Info(_a)])
        if allowed:
            assert module.public_addresses('example.com') == [address]
        else:
            with pytest.raises(IntegrationError):
                module.public_addresses('internal.example.com')
    monkeypatch.setattr(module.socket, 'getaddrinfo', lambda *args, **kwargs: (_ for _ in ()).throw(module_socket.gaierror()))
    with pytest.raises(IntegrationError, match='resolve'):
        module.public_addresses('missing.example.com')


def test_transport_refuses_bad_requests_and_closes_on_failure(monkeypatch):
    from conclave.integrations import transport as module
    monkeypatch.setattr(module, 'public_addresses', lambda host: ['93.184.216.34'])
    for url in ['http://example.com', 'https://example.com:8443', 'https://user:pass@example.com',
                'https://example.com#fragment', 'https://other.example.com', 'not-a-url']:
        with pytest.raises(IntegrationError):
            asyncio.run(module.Transport({'example.com'}).request('GET', url))
    Connection.instances = []
    class Broken(Connection):
        def request(self, *args, **kwargs): raise OSError('connection reset')
    monkeypatch.setattr(module, 'PinnedHTTPS', Broken)
    with pytest.raises(IntegrationError, match='could not be completed'):
        asyncio.run(module.Transport({'example.com'}).request('GET', 'https://example.com'))
    assert Broken.instances[-1].closed is True

    monkeypatch.setattr(module, 'PinnedHTTPS', Connection)
    class Body:
        status = 200
        def read(self, limit): return b''
    Connection.getresponse = lambda self: Body()
    assert asyncio.run(module.Transport({'example.com'}).request('GET', 'https://example.com')) == {}
    Connection.getresponse = lambda self: Response()
