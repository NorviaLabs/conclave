"""Google sign-in for a hosted deployment: who a person is, not what they may reach.

A signed-in account becomes the `Principal` the transport hands to application
code. It is deliberately separate from the Google *integration* connections:
those hold service access tokens for a specific connection, while this holds
nothing but a verified email in a session cookie.

Only two things are new here. The authorization-code/PKCE exchange reuses the
integration `Transport` with its host allowlist, pinned address, no-redirect and
size limits, and the same PKCE construction the connection flow uses. What login
adds is ID-token verification and a signed session cookie:

- the token is verified with an RSA key from the deployment's JWKS URL, `alg` is
  pinned to RS256 (a token that asks to be verified with a shared secret is
  refused), and `iss`, `aud`, `exp`, `iat`, the per-login `nonce` and
  `email_verified` must all agree;
- the session is a signed cookie with an expiry, `HttpOnly`, and `SameSite=Lax`
  (or `None` with `Secure`, for a page on another site) — `Secure` as well
  whenever the request arrived over HTTPS.

Who may sign in is deployment data, and it is deny-by-default: the configuration
must name allowed email domains or exact addresses, and anything else is refused
before a principal exists. So are the redirect URIs, which are exact and HTTPS —
loopback excepted, because Google exempts localhost — and where the browser lands
afterward. The tenant that stores the person's work is fixed by
the deployment, so `CONCLAVE_GOOGLE_LOGIN_FILE` is the only place a tenant can
come from.

Nothing is enabled unless the deployment names a login file and a session key.
"""

import base64
import binascii
import hmac
import json
import secrets
import time

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .application.principal import Principal, require_tenant
from .application.service import setting
from .integrations.transport import IntegrationError, Transport, endpoint


SESSION_COOKIE = "conclave_session"

# A session outlives a working day but not much more; signing out clears the
# cookie, and rotating the session key invalidates every session at once.
DEFAULT_SESSION_SECONDS = 12 * 3600

# The window a state, nonce or token is good for, matching the connection flow.
PENDING_SECONDS = 300

# Clock skew allowed on a freshly minted token.
LEEWAY_SECONDS = 120

MINIMUM_SESSION_KEY = 32

# Google requires HTTPS for a redirect URI and exempts loopback addresses, which
# is what makes it possible to try this on a developer's own machine:
# "Redirect URIs must use the HTTPS scheme, not plain HTTP. Localhost URIs
# (including localhost IP address URIs) are exempt from this rule."
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

SAME_SITE = {"lax": "Lax", "none": "None"}


class GoogleLoginError(RuntimeError):
    """Public error text names neither tokens nor other people's addresses."""


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    padding_needed = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding_needed)
    except (binascii.Error, ValueError):
        raise GoogleLoginError("Sign-in response could not be read") from None


def _integer(value: str) -> int:
    return int.from_bytes(_unb64(value), "big")


def public_key_from_jwk(jwk: dict):
    """One RSA public key out of a JWKS entry, or a refusal."""

    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA" or jwk.get("alg") not in (None, "RS256"):
        raise GoogleLoginError("Sign-in key is not an RSA signing key")
    if not isinstance(jwk.get("n"), str) or not isinstance(jwk.get("e"), str):
        raise GoogleLoginError("Sign-in key is incomplete")
    return rsa.RSAPublicNumbers(_integer(jwk["e"]), _integer(jwk["n"])).public_key()


def verify_id_token(token, *, audience, issuers, public_key, nonce, now, leeway=LEEWAY_SECONDS) -> dict:
    """The claims of a verified Google ID token, or a refusal.

    Pure and strict on purpose: it is the only place identity is decided, so it
    is exercised directly rather than through a browser.
    """

    if not isinstance(token, str) or token.count(".") != 2:
        raise GoogleLoginError("Sign-in response was not an identity token")
    header_segment, payload_segment, signature_segment = token.split(".")
    try:
        header = json.loads(_unb64(header_segment))
        claims = json.loads(_unb64(payload_segment))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise GoogleLoginError("Sign-in response could not be read") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise GoogleLoginError("Sign-in response could not be read")
    # A token may not choose its own algorithm: only RS256 against a JWKS key.
    if header.get("alg") != "RS256":
        raise GoogleLoginError("Sign-in token used an unsupported algorithm")
    try:
        public_key.verify(_unb64(signature_segment), f"{header_segment}.{payload_segment}".encode(),
                          padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError):
        raise GoogleLoginError("Sign-in token signature did not verify") from None

    if claims.get("iss") not in issuers:
        raise GoogleLoginError("Sign-in token came from another issuer")
    if claims.get("aud") != audience:
        raise GoogleLoginError("Sign-in token was issued for another application")
    issued, expires = claims.get("iat"), claims.get("exp")
    if not isinstance(issued, int) or not isinstance(expires, int):
        raise GoogleLoginError("Sign-in token has no validity window")
    if expires + leeway <= now:
        raise GoogleLoginError("Sign-in token has expired")
    if issued - leeway > now:
        raise GoogleLoginError("Sign-in token is not valid yet")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
        raise GoogleLoginError("Sign-in token belongs to another sign-in attempt")

    email = claims.get("email")
    if claims.get("email_verified") is not True or not isinstance(email, str):
        raise GoogleLoginError("Google did not confirm this address")
    email = normalise_email(email)
    return {**claims, "email": email}


def check_address(url: str, what: str, *, relative: bool = False) -> str:
    """A URI this deployment may return a browser to, or a refusal.

    An exact match is what Google itself requires of a redirect URI, so these are
    listed rather than derived. HTTP is accepted only for a loopback address,
    which is the one case Google exempts; anything else must be HTTPS, because a
    session cookie or an authorization code over plain HTTP is a leak.
    """

    if not isinstance(url, str) or not url:
        raise GoogleLoginError(f"Name {what}")
    parsed = urlsplit(url)
    if parsed.fragment:
        raise GoogleLoginError(f"{what} cannot carry a fragment")
    if relative and url.startswith("/") and not url.startswith("//"):
        # A path on this deployment's own address, which is where a page served
        # from the same origin wants to return to.
        return url
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise GoogleLoginError(f"{what} must be an https address")
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise GoogleLoginError(f"{what} must use https: http is accepted only for localhost or 127.0.0.1, "
                               "which is the one case Google exempts")
    return url


def normalise_email(value: str) -> str:
    """A verified address, lowercased, with the shape a principal name needs."""

    email = value.strip().lower()
    local, _, domain = email.partition("@")
    if not local or not domain or " " in email or "." not in domain or len(email) > 100:
        raise GoogleLoginError("Google did not return a usable address")
    return email


@dataclass(frozen=True)
class LoginConfig:
    """The deployment's Google client, its allowlist and its tenant."""

    client_id: str
    # Never in a repr: an exception message or a debug print must not carry it.
    client_secret: str = field(repr=False)
    authorize_url: str
    token_url: str
    jwks_url: str
    issuers: tuple[str, ...]
    redirect_uris: tuple[str, ...]
    hosts: frozenset[str]
    tenant: str
    domains: frozenset[str]
    emails: frozenset[str]
    session_seconds: int = DEFAULT_SESSION_SECONDS
    # Where the browser goes after sign-in. A deployment whose page is served
    # from another origin needs this: without it the person lands on the API's
    # own root and never sees the application again.
    after_login: str = ""
    # `none` is for a page on another *site*: SameSite=Lax cookies are not sent
    # on a cross-site request at all. It forces Secure, as browsers require.
    same_site: str = "lax"

    @classmethod
    def from_value(cls, value: dict) -> "LoginConfig":
        if not isinstance(value, dict):
            raise GoogleLoginError("The login file must contain an object")

        def text(name, *, required=True):
            item = value.get(name)
            if not isinstance(item, str) or not item.strip():
                if required:
                    raise GoogleLoginError(f"The login file must name {name}")
                return ""
            return item.strip()

        def listed(name):
            item = value.get(name, [])
            if not isinstance(item, list) or any(not isinstance(entry, str) or not entry.strip() for entry in item):
                raise GoogleLoginError(f"{name} must be a list of strings")
            return tuple(entry.strip().lower() for entry in item)

        try:
            tenant = require_tenant(text("tenant"))
        except ValueError as error:
            # A login file is deployment data, so every refusal reads the same way.
            raise GoogleLoginError(str(error)) from None
        domains, emails = listed("domains"), listed("emails")
        # Deny by default: a deployment that names nobody would accept every
        # Google account, so it is refused at load rather than at first sign-in.
        if not domains and not emails:
            raise GoogleLoginError("Name the email domains or addresses that may sign in")
        for address in emails:
            normalise_email(address)
        if any("@" in domain or "." not in domain for domain in domains):
            raise GoogleLoginError("Allowed domains are bare domains such as example.com")

        redirects = listed("redirect_uris")
        if not redirects:
            raise GoogleLoginError("Name every redirect URI this login may return to")
        for item in redirects:
            check_address(item, "Every redirect URI")
        hosts = frozenset(item.lower() for item in listed("hosts"))
        authorize_url, token_url, jwks_url = text("authorize_url"), text("token_url"), text("jwks_url")
        try:
            for url in (authorize_url, token_url, jwks_url):
                # Every endpoint, including the key set, is an approved https host.
                endpoint(url, hosts)
        except IntegrationError as error:
            raise GoogleLoginError(f"The login file names an endpoint that is not usable: {error}") from None
        seconds = value.get("session_seconds", DEFAULT_SESSION_SECONDS)
        if type(seconds) is not int or not 60 <= seconds <= 30 * 24 * 3600:
            raise GoogleLoginError("session_seconds must be between a minute and 30 days")
        after_login = (check_address(value["after_login"], "after_login", relative=True)
                       if value.get("after_login") else "")
        same_site = str(value.get("same_site", "lax")).lower()
        if same_site not in SAME_SITE:
            raise GoogleLoginError("same_site must be lax or none")
        if same_site == "none" and any(url.startswith("http://") for url in redirects):
            # A browser drops a SameSite=None cookie that is not Secure, so this
            # combination would sign a person in and then forget them in a loop.
            raise GoogleLoginError("same_site none needs https: a browser drops that cookie over plain http")
        return cls(client_id=text("client_id"), client_secret=text("client_secret"),
                   authorize_url=authorize_url, token_url=token_url, jwks_url=jwks_url,
                   issuers=listed("issuers") or ("https://accounts.google.com", "accounts.google.com"),
                   redirect_uris=redirects, hosts=hosts, tenant=tenant,
                   domains=frozenset(domains), emails=frozenset(emails), session_seconds=seconds,
                   after_login=after_login, same_site=same_site)

    def allows(self, email: str) -> bool:
        """Whether this deployment admits the address. Deny unless it says so."""

        return email in self.emails or email.rpartition("@")[2] in self.domains


class GoogleLogin:
    """The whole sign-in: one authorization-code flow and one session cookie."""

    def __init__(self, config: LoginConfig, transport, *, session_key, clock=time.monotonic, wall_clock=time.time):
        if not isinstance(session_key, str) or len(session_key) < MINIMUM_SESSION_KEY:
            raise GoogleLoginError(f"A session key of at least {MINIMUM_SESSION_KEY} characters is required")
        self.config, self.transport, self.clock, self.wall_clock = config, transport, clock, wall_clock
        self.session_name = SESSION_COOKIE
        self.session_key = session_key.encode()
        self.pending = {}
        self.keys = {}
        self.keys_read_at = None

    # --- The authorization-code flow, with PKCE and a single-use state ---

    def begin(self, redirect_uri: str) -> str:
        """The URL to send the browser to, remembering this attempt."""

        if redirect_uri not in self.config.redirect_uris:
            raise GoogleLoginError("This deployment did not authorize that redirect URI")
        state, verifier, nonce = (secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(24))
        challenge = _b64(sha256(verifier.encode()).digest())
        # This endpoint is public, so it must not be a place to grow memory: an
        # attempt that can no longer be redeemed is dropped as the next one starts.
        now = self.clock()
        self.pending = {key: value for key, value in self.pending.items() if value[3] >= now}
        self.pending[state] = (redirect_uri, verifier, nonce, now + PENDING_SECONDS)
        params = {"response_type": "code", "client_id": self.config.client_id, "redirect_uri": redirect_uri,
                  "scope": "openid email profile", "state": state, "nonce": nonce,
                  "code_challenge": challenge, "code_challenge_method": "S256",
                  "prompt": "select_account"}
        return self.config.authorize_url + "?" + urlencode(params)

    async def callback(self, state: str, code: str, redirect_uri: str) -> Principal:
        """Exchange the code, verify the identity token, and name the person."""

        item = self.pending.pop(state, None) if isinstance(state, str) else None
        if item is None or item[0] != redirect_uri or item[3] < self.clock():
            # One attempt, one redemption: a state is never replayed or reused.
            raise GoogleLoginError("This sign-in attempt is no longer valid")
        if not isinstance(code, str) or not code:
            raise GoogleLoginError("Google did not return an authorization code")
        _, verifier, nonce, _ = item
        result = await self._token({"grant_type": "authorization_code", "code": code,
                                    "redirect_uri": redirect_uri, "code_verifier": verifier})
        token = result.get("id_token")
        claims = verify_id_token(token, audience=self.config.client_id, issuers=self.config.issuers,
                                 public_key=await self._public_key(token), nonce=nonce, now=self.wall_clock())
        if not self.config.allows(claims["email"]):
            raise GoogleLoginError("This account is not admitted to this deployment")
        return Principal(name=claims["email"], tenant=self.config.tenant, source="google")

    async def _token(self, params: dict) -> dict:
        body = {**params, "client_id": self.config.client_id, "client_secret": self.config.client_secret}
        try:
            result = await self.transport.request("POST", self.config.token_url,
                                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                                  body=urlencode(body).encode())
        except IntegrationError as error:
            raise GoogleLoginError("Google refused the sign-in exchange") from error
        if not isinstance(result, dict) or not isinstance(result.get("id_token"), str):
            raise GoogleLoginError("Google returned no identity token")
        return result

    async def _public_key(self, token):
        """The JWKS key this token names, read once and remembered."""

        try:
            header = json.loads(_unb64(str(token).split(".")[0]))
        except (json.JSONDecodeError, IndexError, UnicodeDecodeError):
            raise GoogleLoginError("Sign-in response could not be read") from None
        kid = header.get("kid") if isinstance(header, dict) else None
        if not isinstance(kid, str) or not kid:
            raise GoogleLoginError("Sign-in token named no key")
        if kid not in self.keys:
            # An unknown key may be a rotation, so ask again — but not on every
            # request, or an attacker could drive the key fetches.
            await self._read_keys(force=self.keys_read_at is None or self.clock() - self.keys_read_at > 60)
        if kid not in self.keys:
            raise GoogleLoginError("Sign-in token named a key this deployment does not trust")
        return self.keys[kid]

    async def _read_keys(self, *, force=False):
        # A key set that was read once is kept until it is a day old, which is far
        # inside the window Google rotates keys on.
        if not force and self.keys and self.keys_read_at is not None and self.clock() - self.keys_read_at < 86400:
            return
        try:
            document = await self.transport.request("GET", self.config.jwks_url)
        except IntegrationError as error:
            raise GoogleLoginError("Sign-in keys could not be read") from error
        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list):
            raise GoogleLoginError("Sign-in keys could not be read")
        self.keys = {jwk["kid"]: public_key_from_jwk(jwk) for jwk in keys
                     if isinstance(jwk, dict) and isinstance(jwk.get("kid"), str)}
        self.keys_read_at = self.clock()

    # --- The session cookie: a signed principal with an expiry ---

    def issue(self, principal: Principal, *, now=None) -> str:
        issued = int(self.wall_clock() if now is None else now)
        payload = json.dumps({"name": principal.name, "tenant": principal.tenant,
                              "iat": issued, "exp": issued + self.config.session_seconds},
                             separators=(",", ":"), sort_keys=True).encode()
        return f"{_b64(payload)}.{_b64(hmac.new(self.session_key, payload, sha256).digest())}"

    def read(self, value: str | None) -> Principal | None:
        """The principal this cookie names, or None when it is not one of ours."""

        if not isinstance(value, str) or value.count(".") != 1:
            return None
        payload_segment, signature_segment = value.split(".")
        try:
            payload = _unb64(payload_segment)
            signature = _unb64(signature_segment)
        except GoogleLoginError:
            return None
        if not hmac.compare_digest(hmac.new(self.session_key, payload, sha256).digest(), signature):
            return None
        try:
            claims = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int):
            return None
        if claims["exp"] <= self.wall_clock():
            return None
        name = claims.get("name")
        if not isinstance(name, str) or not name or len(name) > 100:
            return None
        try:
            return Principal(name=name, tenant=require_tenant(str(claims.get("tenant", ""))), source="google")
        except ValueError:
            return None

    def cookie(self, value: str, *, secure: bool, seconds: int | None = None) -> str:
        same_site = SAME_SITE[self.config.same_site]
        attributes = [f"{SESSION_COOKIE}={value}", "Path=/", "HttpOnly", f"SameSite={same_site}",
                      f"Max-Age={self.config.session_seconds if seconds is None else seconds}"]
        # None is only meaningful with Secure: a browser drops the cookie otherwise.
        if secure or same_site == "None":
            attributes.append("Secure")
        return "; ".join(attributes)

    def cleared_cookie(self, *, secure: bool) -> str:
        return self.cookie("", secure=secure, seconds=0)

    def authenticated(self, headers) -> Principal | None:
        """The principal a request's cookie names, when it names a valid one."""

        value = cookie_value(headers.get("cookie", ""), SESSION_COOKIE)
        return self.read(value) if value else None

    def return_url(self, *, failed: bool = False) -> str:
        """Where the browser lands: the deployment's page, or the API's root."""

        base = self.config.after_login or "/"
        if not failed:
            return base
        parts = urlsplit(base)
        query = parse_qs(parts.query)
        query["signin"] = ["failed"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))

    def principal_for(self, scope, headers) -> Principal:
        """The `principal_for` a deployment hands to `application()`."""

        principal = self.authenticated(headers)
        if principal is None:
            raise GoogleLoginError("Sign in to continue")
        return principal


def cookie_value(header: str, name: str) -> str | None:
    """One cookie out of a Cookie header, without decoding what it holds."""

    for part in str(header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip()
    return None


def configured_login(*, transport=None, clock=time.monotonic, wall_clock=time.time) -> GoogleLogin | None:
    """The deployment's login, or None when it named no login file.

    Nothing is enabled by accident: both a login file and a session key are
    required, and a file that admits nobody is refused rather than opened.
    """

    location = setting("GOOGLE_LOGIN_FILE", "")
    if not location:
        return None
    try:
        value = json.loads(Path(location).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise GoogleLoginError(f"The login file could not be read: {type(error).__name__}") from error
    config = LoginConfig.from_value(value)
    if transport is None:
        transport = Transport(config.hosts, timeout=15)
    return GoogleLogin(config, transport, session_key=setting("SESSION_KEY", ""),
                       clock=clock, wall_clock=wall_clock)
