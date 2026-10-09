import asyncio
import io

import pytest
from conclave.integrations.s3 import S3
from conclave.integrations.transport import IntegrationError


class Client:
    def list_objects_v2(self, **kwargs):
        assert kwargs['Bucket'] == 'reports' and kwargs['Prefix'] == 'approved/'
        return {'Contents': [{'Key': 'approved/report.txt', 'Size': 3}], 'NextContinuationToken': 'next'}
    def head_object(self, **kwargs): return {'ContentLength': 3, 'ContentType': 'text/plain'}
    def get_object(self, **kwargs): return {'Body': io.BytesIO(b'abc')}


def test_s3_scoped_read_and_pagination():
    adapter = S3(bucket='reports', prefix='approved/', client=Client())
    async def exercise():
        assert (await adapter.invoke('list_objects'))['next_page'] == 'next'
        assert (await adapter.invoke('metadata', key='approved/report.txt'))['size'] == 3
        assert (await adapter.invoke('read_object', key='approved/report.txt'))['text'] == 'abc'
        with pytest.raises(IntegrationError): await adapter.invoke('read_object', key='private/password')
        with pytest.raises(IntegrationError): await adapter.invoke('delete_object', key='approved/report.txt')
    asyncio.run(exercise())


def test_s3_driver_absence_role_credentials_and_unreadable_objects(monkeypatch):
    import sys
    from conclave.integrations import s3 as module
    monkeypatch.setitem(sys.modules, 'boto3', None)
    with pytest.raises(IntegrationError, match='boto3'):
        asyncio.run(S3(bucket='reports', prefix='public/').invoke('list_objects'))

    calls = {}
    class Body:
        def read(self, limit): return b'\xff\xfe binary'
        def close(self): calls['closed'] = True
    class Sts:
        def assume_role(self, **kwargs): calls['role'] = kwargs; return {'Credentials': {'AccessKeyId': 'a', 'SecretAccessKey': 'b', 'SessionToken': 'c'}}
    class S3Client:
        def list_objects_v2(self, **kwargs): return {'Contents': []}
        def get_object(self, **kwargs): return {'Body': Body()}
    class Session:
        def __init__(self, region_name=None): calls['region'] = region_name
        def client(self, name, **kwargs): calls[name] = kwargs; return Sts() if name == 'sts' else S3Client()
    monkeypatch.setitem(sys.modules, 'boto3', type('B', (), {'Session': Session}))
    adapter = S3(bucket='reports', prefix='public/', region='eu-west-1', role_arn='arn:aws:iam::1:role/reports')
    assert asyncio.run(adapter.invoke('list_objects')) == {'objects': [], 'next_page': None}
    assert calls['role']['RoleArn'].endswith('role/reports') and calls['region'] == 'eu-west-1'
    assert calls['s3']['aws_session_token'] == 'c' and calls['sts'] == {}
    with pytest.raises(IntegrationError, match='not UTF-8'):
        asyncio.run(S3(bucket='reports', prefix='public/', client=S3Client()).invoke('read_object', key='public/data.bin'))
    with pytest.raises(IntegrationError, match='size'):
        asyncio.run(S3(bucket='reports', prefix='public/', client=S3Client(), max_bytes=2).invoke('read_object', key='public/data.bin'))
    adapter = S3(bucket='reports', prefix='public/')
    with pytest.raises(IntegrationError):
        adapter._key('x' * 1025)
    with pytest.raises(IntegrationError, match='prefix'):
        S3(bucket='reports', prefix=7)
