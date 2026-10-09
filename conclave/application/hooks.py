"""Signed inbound hooks: an external caller triggers one authorized run.

Verification happens where the raw request bytes exist, before JSON parsing, so
the signature covers exactly what the sender signed. Nothing here schedules
work or retries: a hook starts the same run a person could start from Studio.
"""
import hashlib
import hmac
import json
import time

MAX_SKEW = 300


def signature(secret, timestamp, body):
    """What the caller signs: the timestamp and the exact body bytes."""

    return hmac.new(secret.encode(), str(timestamp).encode() + b'.' + body, hashlib.sha256).hexdigest()


def verify(secret, headers, body, *, clock=time.time):
    """A bounded, constant-time check of one delivery, or why it was refused."""

    if not secret or len(secret) < 16:
        raise ValueError('Hook connection has no usable signing secret')
    timestamp, provided = headers.get('x-conclave-timestamp'), headers.get('x-conclave-signature')
    if not isinstance(timestamp, str) or not isinstance(provided, str) or not timestamp.isdigit():
        raise ValueError('Hook delivery is missing its signature and timestamp')
    if abs(clock() - int(timestamp)) > MAX_SKEW:
        raise ValueError('Hook delivery timestamp is outside the accepted window')
    expected = signature(secret, timestamp, body)
    # hmac.compare_digest, not ==, so a wrong signature costs the same time.
    if not hmac.compare_digest(expected, provided.strip().lower()):
        raise ValueError('Hook signature does not match')
    try:
        payload = json.loads(body or b'{}')
    except ValueError:
        raise ValueError('Hook body must be JSON') from None
    if not isinstance(payload, dict) or len(body) > 100000:
        raise ValueError('Hook body must be a bounded JSON object')
    return payload


class Deliveries:
    """Recent delivery ids, so a repeated signed request starts only one run."""

    def __init__(self, limit=200):
        self.limit, self.seen = limit, []

    def claim(self, identifier):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
            return False
        if identifier in self.seen:
            return False
        # ponytail: in-process window; move to the connection store if a
        # deployment runs several workers behind one endpoint.
        self.seen.append(identifier)
        del self.seen[:-self.limit]
        return True


def resolver(services, principal, *, deliveries=None, clock=time.time):
    """The default hook authentication for one deployment.

    The connection is resolved inside the request principal's tenant, so a
    delivery can only start a run that tenant's own connection authorized. A
    deployment serving several tenants supplies its own resolver to
    `application(hooks=...)`, because only it can say which tenant a delivery
    belongs to.
    """

    deliveries = deliveries or Deliveries()

    async def hooks(delivery, headers, body):
        api = services.for_principal(principal)
        store = getattr(getattr(api, "host", None), "connections", None)
        if store is None:
            raise ValueError("Integration connections are not configured on this host")
        try:
            record = store.hook(delivery)
        except Exception as error:
            # A refused delivery is a 400, never a 500 with host internals in it.
            raise ValueError(str(error) or "Hook connection is not available") from None
        payload = verify(record["credentials"].get("signing_secret", ""), headers, body, clock=clock)
        if not deliveries.claim(payload.get("delivery_id") or headers.get("x-conclave-delivery", "")):
            raise ValueError("Hook delivery identifier is missing or already received")
        from .principal import Principal
        return Principal(record["owner"], record["tenant"], source="hook"), {"payload": payload}

    return hooks
