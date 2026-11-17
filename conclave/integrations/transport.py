"""Bounded HTTPS transport for integrations; DNS addresses are pinned per call."""
import asyncio
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlencode, urlsplit


class IntegrationError(RuntimeError):
    """Public error text contains neither credentials nor upstream response bodies.

    `sent` says whether the request left this host: False when it certainly did
    not, True when it did, None when this error cannot say. `rejected` marks a
    request the service answered with a refusal, which it did not act on.
    Together they decide whether a failed write may be reported as not having
    happened (`conclave.actions.classify`).
    """

    def __init__(self, message='', *, sent=None, rejected=False, status=None):
        super().__init__(message)
        self.sent, self.rejected, self.status = sent, rejected, status


def endpoint(url, allowed_hosts):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443) or parsed.hostname not in allowed_hosts):
        raise IntegrationError('Endpoint is not an approved HTTPS host', sent=False)
    return parsed


def public_addresses(host):
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise IntegrationError('Host name could not be resolved', sent=False) from None
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise IntegrationError('Private or reserved network targets are forbidden', sent=False)
    return sorted(addresses)


PERMISSIONS = re.compile(r'[a-z_]+=[a-z]+(?:[,;] ?[a-z_]+=[a-z]+)*')


def refusal(response):
    """What a refused request needed, from headers GitHub sets for that.
    Unlike the body these hold no account data, and only the expected shape
    of a permission list or an SSO demand is repeated. GitHub names the
    accepted permissions on every response, so only a 403 is a refusal: a 404
    for a missing file would otherwise read as a credential problem."""
    if response.status != 403:
        return ''
    needed = (response.getheader('X-Accepted-GitHub-Permissions') or '').strip()
    if needed and len(needed) <= 200 and PERMISSIONS.fullmatch(needed):
        return f'; the token needs {needed}'
    if (response.getheader('X-GitHub-SSO') or '').startswith('required'):
        return '; the token needs SSO authorization for this organization'
    return ''


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


def abort(active):
    """Stop a request whose caller has gone: a worker thread cannot be killed,
    but shutting its socket fails the blocked read at once, so an abandoned
    request neither keeps talking to the provider nor holds a worker."""
    active['aborted'] = True
    connection = active.get('connection')
    sock = getattr(connection, 'sock', None) if connection is not None else None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class Transport:
    def __init__(self, hosts, *, timeout=20, max_bytes=1000000):
        self.hosts, self.timeout, self.max_bytes = frozenset(hosts), timeout, max_bytes

    async def request(self, method, url, *, headers=None, body=None, params=None, raw=False):
        parsed = endpoint(url, self.hosts)
        active = {}
        def call():
            connection = None
            # Whether the request left this host. Until `request` returns
            # nothing was sent; after it, the service may have acted.
            sent = False
            try:
                address = public_addresses(parsed.hostname)[0]
                connection = PinnedHTTPS(parsed.hostname, address, self.timeout)
                active['connection'] = connection
                if active.get('aborted'):
                    raise IntegrationError('Provider request was abandoned')
                path = parsed.path or '/'
                query = parsed.query
                if params:
                    query += ('&' if query else '') + urlencode(params, doseq=True)
                if query:
                    path += '?' + query
                # GitHub answers 403 to a request with no User-Agent, so the
                # transport identifies itself; a service may still send its own.
                request_headers = {'Accept': 'application/json',
                                   'User-Agent': 'Conclave/0.1 (authorized API integration)',
                                   **(headers or {})}
                payload = body
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload, allow_nan=False).encode()
                    request_headers['Content-Type'] = 'application/json'
                connection.request(method, path, body=payload, headers=request_headers)
                sent = True
                if active.get('aborted'):
                    raise IntegrationError('Provider request was abandoned', sent=True)
                response = connection.getresponse()
                status = response.status
                # Never follow redirects with authentication headers. A
                # redirect, a rate limit and any 4xx are answers that the
                # request was not acted on; a 5xx is not.
                if 300 <= status < 400:
                    raise IntegrationError('Provider redirect refused', sent=True, rejected=True, status=status)
                if status == 429:
                    raise IntegrationError('Provider rate limit reached; retry later', sent=True, rejected=True, status=status)
                if not 200 <= status < 300:
                    raise IntegrationError(f'Provider request failed (HTTP {status}){refusal(response)}',
                                           sent=True, rejected=status < 500, status=status)
                data = response.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise IntegrationError('Provider response exceeds the configured size limit', sent=True, status=status)
                if raw:
                    return data
                return json.loads(data) if data else {}
            except IntegrationError as error:
                # A refusal raised before the request went out (an address
                # that did not resolve, say) says nothing left this host.
                if not sent and error.sent is None:
                    error.sent = False
                raise
            except Exception:
                raise IntegrationError('Provider request could not be completed', sent=sent) from None
            finally:
                if connection:
                    connection.close()
        try:
            return await asyncio.wait_for(asyncio.to_thread(call), self.timeout + 2)
        except TimeoutError:
            abort(active)
            # The worker thread may still deliver the request after this.
            raise IntegrationError('Provider request timed out', sent=True) from None
        except asyncio.CancelledError:
            # The caller gave up (a paginated read at its time limit): so does
            # the request.
            abort(active)
            raise
