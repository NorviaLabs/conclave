import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from conclave.integrations.oauth import OAuth
from conclave.integrations.transport import IntegrationError


class Transport:
    def __init__(self): self.calls = []
    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return {'access_token': 'private', 'scope': 'read', 'refresh_token': 'refresh'}


def test_oauth_state_pkce_scope_refresh_revocation():
    transport = Transport()
    config = {'hosts': ['auth.example.com'], 'authorize_url': 'https://auth.example.com/authorize',
              'token_url': 'https://auth.example.com/token', 'revoke_url': 'https://auth.example.com/revoke',
              'client_id': 'app', 'client_secret': 'private-client', 'redirect_uris': ['https://studio.example.com/callback'], 'scopes': ['read']}
    oauth = OAuth({'google': config}, transport)
    with pytest.raises(IntegrationError): oauth.begin('google', 't', 'u', ['write'], config['redirect_uris'][0])
    with pytest.raises(IntegrationError): oauth.begin('google', 't', 'u', ['read'], 'https://evil.example/callback')
    url = oauth.begin('google', 't', 'u', ['read'], config['redirect_uris'][0])
    query = parse_qs(urlsplit(url).query)
    assert query['code_challenge_method'] == ['S256']
    assert 'private-client' not in url
    state = query['state'][0]
    async def exercise():
        with pytest.raises(IntegrationError): await oauth.callback(state, 'code', 'other', 'u', config['redirect_uris'][0])
        result = await oauth.callback(state, 'code', 't', 'u', config['redirect_uris'][0])
        assert result['access_token'] == 'private'
        with pytest.raises(IntegrationError): await oauth.callback(state, 'code', 't', 'u', config['redirect_uris'][0])
        await oauth.refresh('google', 'refresh')
        assert await oauth.revoke('google', 'private')
    asyncio.run(exercise())
    assert b'code_verifier=' in transport.calls[0][2]['body']
