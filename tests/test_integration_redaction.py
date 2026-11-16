"""What a connection hides from the agent: its secrets, and nothing else.

A provider may echo a credential back; every secret value is replaced with
`[redacted]` before the result reaches the agent or the run record. OAuth
lifecycle metadata — which provider issued the token and when it expires — is
not a secret, and redacting it broke ordinary data: with `oauth_provider` set to
"google", every google.com address and link in a Gmail or Drive result was
mangled.
"""
import json
import time

from test_integration_pagination_connections import Provider, store_for, invoke

GOOGLE_TEXT = 'from alerts@google.com, see https://docs.google.com/d/abc'


class OAuth:
    providers = {}


def oauth_store(tmp_path, provider, expires_at):
    return store_for(tmp_path, provider, oauth=OAuth(), extra={'credentials': {
        'access_token': 'SECRET-ACCESS-TOKEN', 'refresh_token': 'SECRET-REFRESH-TOKEN',
        'oauth_provider': 'google', 'expires_at': expires_at}})


def test_oauth_provider_name_is_not_redacted_from_a_single_page(tmp_path):
    expires_at = str(time.time() + 3600)
    provider = Provider(total=1, item=lambda i: {
        'id': 'm1', 'threadId': f'{GOOGLE_TEXT} SECRET-ACCESS-TOKEN SECRET-REFRESH-TOKEN'})
    store = oauth_store(tmp_path, provider, expires_at)
    echoed = json.loads(invoke(store, 'search_messages', {'query': 'x'}))['messages'][0]['threadId']
    assert echoed == f'{GOOGLE_TEXT} [redacted] [redacted]'


def test_oauth_provider_name_is_not_redacted_from_a_paginated_result(tmp_path):
    provider = Provider(total=250, item=lambda i: {
        'id': f'm{i}', 'threadId': f'{GOOGLE_TEXT} SECRET-ACCESS-TOKEN' if i in (0, 150) else 't'})
    store = oauth_store(tmp_path, provider, str(time.time() + 3600))
    result = json.loads(invoke(store, 'search_messages', {'query': 'x', 'paginate': True}))
    assert result['pagination']['pages'] == 3
    for index in (0, 150):
        assert result['messages'][index]['threadId'] == f'{GOOGLE_TEXT} [redacted]'


def test_the_token_expiry_time_is_not_redacted(tmp_path):
    expires_at = str(time.time() + 3600)
    provider = Provider(total=1, item=lambda i: {'id': 'm1', 'threadId': f'cache until {expires_at}'})
    store = oauth_store(tmp_path, provider, expires_at)
    echoed = json.loads(invoke(store, 'search_messages', {'query': 'x'}))['messages'][0]['threadId']
    assert echoed == f'cache until {expires_at}'


def test_every_other_credential_value_is_still_redacted(tmp_path):
    """Guard: only the named OAuth metadata is exempt; any other field — a
    known secret, the account email, or one added later — stays hidden."""
    secrets = {'access_token': 'A-SECRET-VALUE', 'token': 'T-SECRET-VALUE', 'api_key': 'K-SECRET-VALUE',
               'email': 'owner@example.com', 'signing_secret': 'S-SECRET-VALUE-123456',
               'client_secret': 'C-SECRET-VALUE'}
    provider = Provider(total=250, item=lambda i: {'id': f'm{i}', 'threadId': ' '.join(secrets.values())})
    store = store_for(tmp_path, provider, extra={'credentials': secrets})
    single = invoke(store, 'search_messages', {'query': 'x'})
    paged = invoke(store, 'search_messages', {'query': 'x', 'paginate': True})
    for text in (single, paged):
        assert not any(value in text for value in secrets.values())
    assert json.loads(single)['messages'][0]['threadId'] == ' '.join(['[redacted]'] * len(secrets))
