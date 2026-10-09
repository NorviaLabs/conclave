"""Opt-in S3 SDK operations with explicit bucket/prefix boundaries."""
import asyncio
import json
from .transport import IntegrationError


class S3:
    def __init__(self, *, bucket, prefix, region=None, role_arn=None, client=None, max_bytes=1000000):
        if not bucket or not isinstance(prefix, str):
            raise IntegrationError('Configure one authorized bucket and prefix')
        self.bucket, self.prefix, self.max_bytes = bucket, prefix, max_bytes
        self.region, self.role_arn, self.client = region, role_arn, client

    def _client(self):
        if self.client is None:
            try:
                import boto3
            except ImportError:
                raise IntegrationError('Install boto3 on the trusted host to enable S3') from None
            session = boto3.Session(region_name=self.region)
            if self.role_arn:
                credentials = session.client('sts').assume_role(RoleArn=self.role_arn, RoleSessionName='conclave-integration')['Credentials']
                self.client = session.client('s3', aws_access_key_id=credentials['AccessKeyId'], aws_secret_access_key=credentials['SecretAccessKey'], aws_session_token=credentials['SessionToken'])
            else:
                self.client = session.client('s3')
        return self.client

    def _key(self, key):
        if not isinstance(key, str) or not key.startswith(self.prefix) or len(key) > 1024:
            raise IntegrationError('Object is outside the authorized prefix')
        return key

    async def invoke(self, operation, *, key=None, continuation_token=None):
        def call():
            client = self._client()
            try:
                if operation == 'list_objects':
                    params = {'Bucket': self.bucket, 'Prefix': self.prefix, 'MaxKeys': 100}
                    if continuation_token:
                        params['ContinuationToken'] = continuation_token
                    result = client.list_objects_v2(**params)
                    return {'objects': [{'key': item['Key'], 'size': item['Size']} for item in result.get('Contents', [])], 'next_page': result.get('NextContinuationToken')}
                if operation == 'metadata':
                    result = client.head_object(Bucket=self.bucket, Key=self._key(key))
                    return {'size': result['ContentLength'], 'content_type': result.get('ContentType'), 'etag': result.get('ETag')}
                if operation == 'read_object':
                    result = client.get_object(Bucket=self.bucket, Key=self._key(key), Range=f'bytes=0-{self.max_bytes}')
                    stream = result['Body']
                    try:
                        data = stream.read(self.max_bytes + 1)
                    finally:
                        stream.close()
                    if len(data) > self.max_bytes:
                        raise IntegrationError('Object exceeds the permitted read size')
                    try:
                        text = data.decode('utf-8')
                    except UnicodeDecodeError:
                        raise IntegrationError('Object is not UTF-8 text') from None
                    return {'key': key, 'text': text}
                raise IntegrationError('Unknown S3 read operation')
            except IntegrationError:
                raise
            except Exception:
                raise IntegrationError('S3 request failed') from None
        return await asyncio.wait_for(asyncio.to_thread(call), 25)
