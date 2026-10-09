"""Shared OAuth authorization-code/PKCE flow for host-configured providers."""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode
from .transport import IntegrationError, endpoint


class OAuth:
    def __init__(self, providers, transport, *, clock=time.monotonic):
        # Provider URLs, client secrets and exact redirect URIs are deployment data.
        self.providers, self.transport, self.clock = providers, transport, clock
        self.pending = {}

    def begin(self, provider, tenant, owner, scopes, redirect_uri):
        config = self.providers[provider]
        if redirect_uri not in config['redirect_uris'] or set(scopes) - set(config['scopes']):
            raise IntegrationError('OAuth redirect or scopes are not authorized')
        endpoint(config['authorize_url'], config['hosts'])
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.pending[state] = (provider, tenant, owner, tuple(scopes), redirect_uri, verifier, self.clock() + 300)
        params = {'response_type': 'code', 'client_id': config['client_id'], 'redirect_uri': redirect_uri,
                  'scope': ' '.join(scopes), 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
        return config['authorize_url'] + '?' + urlencode(params)

    async def callback(self, state, code, tenant, owner, redirect_uri):
        item = self.pending.get(state)
        if not item or item[1:3] != (tenant, owner) or item[4] != redirect_uri or item[6] < self.clock():
            raise IntegrationError('OAuth state is invalid or expired')
        del self.pending[state]  # A callback can never redeem the same state twice.
        provider, _, _, scopes, _, verifier, _ = item
        config = self.providers[provider]
        result = await self._token(config, {'grant_type': 'authorization_code', 'code': code,
                                            'redirect_uri': redirect_uri, 'code_verifier': verifier})
        granted = set(result.get('scope', '').split())
        if not granted or set(scopes) - granted:
            raise IntegrationError('Provider did not confirm required OAuth scopes')
        return result

    async def refresh(self, provider, refresh_token):
        return await self._token(self.providers[provider], {'grant_type': 'refresh_token', 'refresh_token': refresh_token})

    async def _token(self, config, params):
        endpoint(config['token_url'], config['hosts'])
        params = {**params, 'client_id': config['client_id'], 'client_secret': config['client_secret']}
        result = await self.transport.request('POST', config['token_url'], headers={'Content-Type': 'application/x-www-form-urlencoded'}, body=urlencode(params).encode())
        if not isinstance(result.get('access_token'), str) or not result['access_token']:
            raise IntegrationError('OAuth token exchange failed')
        return result

    async def revoke(self, provider, token):
        config = self.providers[provider]
        if not config.get('revoke_url'):
            return False
        endpoint(config['revoke_url'], config['hosts'])
        await self.transport.request('POST', config['revoke_url'], headers={'Content-Type': 'application/x-www-form-urlencoded'}, body=urlencode({'token': token}).encode())
        return True
