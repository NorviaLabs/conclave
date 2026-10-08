import asyncio

import pytest

from conclave.application.service import request_body_limit
from conclave.asgi import read_body, TooLarge


@pytest.mark.parametrize('value,expected', [('5', 5), ('unlimited', None), ('none', None)])
def test_host_request_body_policy(monkeypatch, value, expected):
    monkeypatch.setenv('CONCLAVE_MAX_REQUEST_BYTES', value)
    assert request_body_limit() == expected

    async def receive():
        return {'body': b'123456', 'more_body': False}

    if expected is None:
        assert asyncio.run(read_body(receive)) == b'123456'
    else:
        with pytest.raises(TooLarge, match='5 bytes'):
            asyncio.run(read_body(receive))


@pytest.mark.parametrize('value', ['0', '-1', 'invalid'])
def test_invalid_host_request_body_policy(monkeypatch, value):
    monkeypatch.setenv('CONCLAVE_MAX_REQUEST_BYTES', value)
    with pytest.raises(ValueError):
        request_body_limit()
