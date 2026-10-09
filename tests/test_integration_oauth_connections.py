import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from conclave.integrations import ConnectionStore, IntegrationError
from conclave.integrations.oauth import OAuth


class Transport:
    calls=[]
    def __init__(self,hosts=None):pass
    async def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if url.endswith('/token'):
            return {'access_token':'PRIVATE_ACCESS','refresh_token':'PRIVATE_REFRESH','scope':'search:read','expires_in':3600}
        return {'ok':True}


def test_oauth_connection_exchange_refresh_revocation_and_owner(tmp_path):
    config={'hosts':['auth.example.com'],'authorize_url':'https://auth.example.com/authorize',
            'token_url':'https://auth.example.com/token','revoke_url':'https://auth.example.com/revoke',
            'client_id':'client','client_secret':'SECRET','redirect_uris':['https://studio.example.com/callback'],
            'scopes':['search:read'],'integrations':['slack']}
    oauth=OAuth({'slack':config},Transport())
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key(),oauth=oauth,transport_factory=Transport)
    connection={'id':'workspace','name':'Workspace','integration':'slack','operations':['search_messages'],
                'applications':['app'],'scopes':['search:read']}
    result=store.begin_oauth({'connection':connection,'provider':'slack','redirect_uri':config['redirect_uris'][0]},'tenant','alice')
    state=parse_qs(urlsplit(result['authorization_url']).query)['state'][0]
    callback={'state':state,'code':'code','redirect_uri':config['redirect_uris'][0]}
    async def exercise():
        with pytest.raises(IntegrationError):await store.finish_oauth(callback,'tenant','bob')
        public=await store.finish_oauth(callback,'tenant','alice')
        assert 'credentials' not in public and b'PRIVATE' not in store.path.read_bytes()
        with pytest.raises(IntegrationError):await store.finish_oauth(callback,'tenant','alice')
        record=store._read('workspace','tenant','alice');record['credentials']['expires_at']='0';store._save(record,'tenant','alice')
        await store.invoke('workspace','search_messages',{'query':'research'},'tenant','alice','app')
        assert float(store._read('workspace','tenant','alice')['credentials']['expires_at'])>0
        assert b'grant_type=refresh_token' in Transport.calls[-2][2]['body']
        assert await store.revoke('workspace','tenant','alice')=={'disconnected':True,'provider_revoked':True}
        assert store.list('tenant','alice')==[]
    asyncio.run(exercise())
    with pytest.raises(IntegrationError):store.create({**connection,'credentials':{'oauth_provider':'slack','refresh_token':'forged'}},'tenant','alice')


def test_oauth_request_rejection_and_expired_manual_token(tmp_path):
    store=ConnectionStore(tmp_path/'connections.sqlite',Fernet.generate_key())
    with pytest.raises(IntegrationError):store.begin_oauth({},'t','u')
    async def exercise():
        with pytest.raises(IntegrationError):await store.finish_oauth({},'t','u')
    asyncio.run(exercise())
    config={'hosts':['auth.example.com'],'authorize_url':'https://auth.example.com/authorize','token_url':'https://auth.example.com/token',
            'client_id':'c','client_secret':'s','scopes':['search:read'],'redirect_uris':['https://studio.example.com/callback'],'integrations':['slack']}
    store.oauth=OAuth({'slack':config},Transport())
    for connection in [{'credentials':{}}, {'integration':'gmail'}]:
        with pytest.raises(IntegrationError):store.begin_oauth({'connection':connection,'provider':'slack','redirect_uri':config['redirect_uris'][0]},'t','u')
    async def errors():
        with pytest.raises(IntegrationError):await store.finish_oauth({'state':'missing','code':'x','redirect_uri':config['redirect_uris'][0]},'t','u')
    asyncio.run(errors())
    connection={'id':'manual','name':'Manual','integration':'slack','operations':['search_messages'],'applications':['app'],'scopes':['search:read']}
    store.create({**connection,'credentials':{'access_token':'token','expires_at':'0'}},'t','u',oauth_exchange=True)
    async def expired():
        with pytest.raises(IntegrationError,match='expired'):await store.invoke('manual','search_messages',{'query':'x'},'t','u','app')
        assert (await store.revoke('manual','t','u'))['provider_revoked'] is False
    asyncio.run(expired())
