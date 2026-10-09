"""Bounded HTTPS transport for integrations; DNS addresses are pinned per call."""
import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlencode, urlsplit


class IntegrationError(RuntimeError):
    """Public error text contains neither credentials nor upstream response bodies."""


def endpoint(url, allowed_hosts):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443) or parsed.hostname not in allowed_hosts):
        raise IntegrationError('Endpoint is not an approved HTTPS host')
    return parsed


def public_addresses(host):
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise IntegrationError('Host name could not be resolved') from None
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise IntegrationError('Private or reserved network targets are forbidden')
    return sorted(addresses)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class Transport:
    def __init__(self, hosts, *, timeout=20, max_bytes=1000000):
        self.hosts, self.timeout, self.max_bytes = frozenset(hosts), timeout, max_bytes

    async def request(self, method, url, *, headers=None, body=None, params=None, raw=False):
        parsed = endpoint(url, self.hosts)
        def call():
            connection = None
            try:
                address = public_addresses(parsed.hostname)[0]
                connection = PinnedHTTPS(parsed.hostname, address, self.timeout)
                path = parsed.path or '/'
                query = parsed.query
                if params:
                    query += ('&' if query else '') + urlencode(params, doseq=True)
                if query:
                    path += '?' + query
                request_headers = {'Accept': 'application/json', **(headers or {})}
                payload = body
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload, allow_nan=False).encode()
                    request_headers['Content-Type'] = 'application/json'
                connection.request(method, path, body=payload, headers=request_headers)
                response = connection.getresponse()
                # Never follow redirects with authentication headers.
                if 300 <= response.status < 400:
                    raise IntegrationError('Provider redirect refused')
                if response.status == 429:
                    raise IntegrationError('Provider rate limit reached; retry later')
                if not 200 <= response.status < 300:
                    raise IntegrationError(f'Provider request failed (HTTP {response.status})')
                data = response.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise IntegrationError('Provider response exceeds the configured size limit')
                if raw:
                    return data
                return json.loads(data) if data else {}
            except IntegrationError:
                raise
            except Exception:
                raise IntegrationError('Provider request could not be completed') from None
            finally:
                if connection:
                    connection.close()
        try:
            return await asyncio.wait_for(asyncio.to_thread(call), self.timeout + 2)
        except TimeoutError:
            raise IntegrationError('Provider request timed out') from None
