"""Google sign-in, exercised without a browser, a network or Google.

The HTTP boundary is faked: the transport is a stand-in that answers the token
exchange and the key set, and the identity token is signed here with a key this
test generates. That covers what login decides — who may in, in which tenant —
which is the part that cannot be checked by looking at a page.
"""

import asyncio
import base64
import json
import time

from hashlib import sha256

import pytest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from conclave.asgi import application
from conclave.identity import (GoogleLogin, GoogleLoginError, LoginConfig, configured_login,
                               cookie_value, normalise_email, public_key_from_jwk, verify_id_token)


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class FakeHTTP:
    """The two Google endpoints login talks to, and nothing else."""

    def __init__(self, *, token=None, keys=None, fail_token=None, fail_keys=None):
        self.token, self.keys = token, keys
        self.fail_token, self.fail_keys = fail_token, fail_keys
        self.calls = []

    async def request(self, method, url, *, headers=None, body=None, params=None, raw=False):
        self.calls.append((method, url, body))
        if url.endswith("/token"):
            if self.fail_token:
                raise self.fail_token
            return self.token
        if self.fail_keys:
            raise self.fail_keys
        return {"keys": self.keys or []}


def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-key",
           "n": encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
           "e": encode(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))}
    return key, jwk


def id_token(key, claims, *, kid="test-key", algorithm="RS256"):
    header = encode(json.dumps({"alg": algorithm, "kid": kid, "typ": "JWT"}).encode())
    payload = encode(json.dumps(claims).encode())
    if algorithm == "RS256":
        signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    else:
        signature = b"not-a-signature"
    return f"{header}.{payload}.{encode(signature)}"


def claims(**overrides):
    now = int(time.time())
    base = {"iss": "https://accounts.google.com", "aud": "client-1", "sub": "1234",
            "iat": now - 5, "exp": now + 600, "email": "person@example.com",
            "email_verified": True, "nonce": "nonce-1"}
    return {**base, **overrides}


def config(**overrides):
    value = {"client_id": "client-1", "client_secret": "secret-1",
             "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
             "token_url": "https://oauth2.googleapis.com/token",
             "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
             "hosts": ["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"],
             "redirect_uris": ["https://studio.example.com/auth/google/callback"],
             "tenant": "acme", "domains": ["example.com"]}
    return LoginConfig.from_value({**value, **overrides})


def login(http=None, **overrides):
    key, jwk = keypair()
    return key, GoogleLogin(config(**overrides), http or FakeHTTP(keys=[jwk]),
                            session_key="k" * 40, wall_clock=time.time)


def sign_in(private_key, result, subject=None, *, state="state-1",
            redirect="https://studio.example.com/auth/google/callback"):
    """Run the callback with a token signed by this test's own key."""

    result.pending[state] = (redirect, "verifier-1", "nonce-1", result.clock() + 300)
    result.transport.token = {"id_token": id_token(private_key, claims(**(subject or {})))}
    return asyncio.run(result.callback(state, "code-1", redirect))


# --- Who may sign in, and as whom -------------------------------------------

def test_a_verified_address_in_an_allowed_domain_becomes_the_principal():
    key, result = login()
    result.keys = {"test-key": key.public_key()}
    principal = sign_in(key, result)
    assert (principal.name, principal.tenant, principal.source) == ("person@example.com", "acme", "google")


def test_an_address_outside_the_allowlist_is_refused():
    key, result = login(emails=[], domains=["other.test"])
    result.keys = {"test-key": key.public_key()}
    with pytest.raises(GoogleLoginError, match="not admitted"):
        sign_in(key, result)


def test_an_exact_address_is_admitted_by_an_otherwise_closed_deployment():
    key, result = login(domains=[], emails=["Person@Example.com"])
    result.keys = {"test-key": key.public_key()}
    assert sign_in(key, result).name == "person@example.com"


def test_a_deployment_that_names_nobody_is_refused_at_load():
    with pytest.raises(GoogleLoginError, match="Name the email domains or addresses"):
        LoginConfig.from_value({"client_id": "c", "client_secret": "s",
                                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                                "token_url": "https://oauth2.googleapis.com/token",
                                "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
                                "hosts": ["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"],
                                "redirect_uris": ["https://studio.example.com/auth/google/callback"], "tenant": "acme"})


def test_a_redirect_uri_must_be_https_unless_it_is_loopback():
    """Google exempts localhost, and so does this: nothing else may be plain HTTP."""

    with pytest.raises(GoogleLoginError, match="must use https"):
        config(redirect_uris=["http://studio.example.com/auth/google/callback"])
    # The exemption is what makes it possible to try this on a laptop, and it is
    # still an exact, listed URI rather than anything derived from a request.
    for loopback in ("http://127.0.0.1:8080/auth/google/callback", "http://localhost:8080/auth/google/callback",
                     "https://studio.example.com/auth/google/callback"):
        assert config(redirect_uris=[loopback]).redirect_uris == (loopback,)
    for broken in ("ftp://studio.example.com/cb", "https://user:pw@studio.example.com/cb",
                   "https://studio.example.com/cb#frag", "//studio.example.com/cb"):
        with pytest.raises(GoogleLoginError):
            config(redirect_uris=[broken])
    with pytest.raises(GoogleLoginError):
        login()[1].begin("https://elsewhere.test/auth/google/callback")


def test_where_the_browser_lands_after_signing_in_is_deployment_data():
    from conclave.identity import GoogleLoginError as Error
    default = config()
    assert default.after_login == "" and login()[1].return_url() == "/"
    assert login()[1].return_url(failed=True) == "/?signin=failed"

    elsewhere = config(after_login="https://studio.example.com/app")
    assert login(after_login="https://studio.example.com/app")[1].return_url() == "https://studio.example.com/app"
    assert login(after_login="https://studio.example.com/app")[1].return_url(failed=True) == "https://studio.example.com/app?signin=failed"
    assert login(after_login="https://studio.example.com/app?tab=plan")[1].return_url(failed=True) == "https://studio.example.com/app?tab=plan&signin=failed"
    # A page on this deployment's own address may be named as a path.
    assert login(after_login="/applications")[1].return_url() == "/applications"
    for broken in ("http://studio.example.com/", "//elsewhere.test/", "https://user:pw@studio.example.com/",
                   "https://studio.example.com/#frag", "/applications#frag"):
        with pytest.raises(Error):
            config(after_login=broken)
    assert elsewhere.after_login == "https://studio.example.com/app"


def test_a_session_cookie_follows_the_deployment_s_same_site_choice():
    from conclave.identity import GoogleLoginError as Error
    assert "SameSite=Lax" in login()[1].cookie("value", secure=True)
    assert "SameSite=None" in login(same_site="none")[1].cookie("value", secure=False)
    # None is only meaningful with Secure: a browser drops the cookie otherwise.
    assert "Secure" in login(same_site="none")[1].cookie("value", secure=False)
    assert "Secure" not in login()[1].cookie("value", secure=False)
    assert config(same_site="NONE").same_site == "none"
    with pytest.raises(Error, match="lax or none"):
        config(same_site="strict")
    # None without Secure is dropped by the browser: that is a loop, not a login.
    with pytest.raises(Error, match="needs https"):
        config(same_site="none", redirect_uris=["http://127.0.0.1:8080/auth/google/callback"])


def test_an_endpoint_outside_the_host_list_is_refused_at_load():
    with pytest.raises(GoogleLoginError, match="not usable"):
        config(jwks_url="https://keys.example.test/certs")


# --- The identity token -----------------------------------------------------

def test_every_claim_is_checked():
    key, _ = keypair()
    public = key.public_key()
    now = int(time.time())
    arguments = {"audience": "client-1", "issuers": ("https://accounts.google.com",),
                 "public_key": public, "nonce": "nonce-1", "now": now}
    assert verify_id_token(id_token(key, claims()), **arguments)["email"] == "person@example.com"
    for broken, message in [
        (claims(iss="https://accounts.evil.test"), "another issuer"),
        (claims(aud="other-client"), "another application"),
        (claims(exp=now - 10_000), "expired"),
        (claims(iat=now + 10_000), "not valid yet"),
        (claims(nonce="other-nonce"), "another sign-in attempt"),
        (claims(email_verified=False), "did not confirm"),
        (claims(email="not-an-address"), "usable address"),
    ]:
        with pytest.raises(GoogleLoginError, match=message):
            verify_id_token(id_token(key, broken), **arguments)


def test_a_token_signed_by_another_key_or_asking_for_another_algorithm_is_refused():
    key, _ = keypair()
    other, _ = keypair()
    now = int(time.time())
    arguments = {"audience": "client-1", "issuers": ("https://accounts.google.com",),
                 "public_key": key.public_key(), "nonce": "nonce-1", "now": now}
    with pytest.raises(GoogleLoginError, match="signature"):
        verify_id_token(id_token(other, claims()), **arguments)
    # A token may not choose its own algorithm, so an unsigned one is not a token.
    with pytest.raises(GoogleLoginError, match="unsupported algorithm"):
        verify_id_token(id_token(key, claims(), algorithm="none"), **arguments)
    with pytest.raises(GoogleLoginError, match="identity token"):
        verify_id_token("not-a-token", **arguments)


def test_a_public_key_is_read_only_from_a_usable_jwk():
    _, jwk = keypair()
    assert public_key_from_jwk(jwk).key_size == 2048
    for broken in ({"kty": "oct", "n": jwk["n"], "e": jwk["e"]}, {"kty": "RSA"}, {"kty": "RSA", "alg": "HS256", "n": jwk["n"], "e": jwk["e"]}):
        with pytest.raises(GoogleLoginError):
            public_key_from_jwk(broken)


# --- The flow ---------------------------------------------------------------

def test_the_authorization_url_carries_pkce_a_nonce_and_a_single_use_state():
    key, result = login()
    result.keys = {"test-key": key.public_key()}
    redirect = "https://studio.example.com/auth/google/callback"
    url = result.begin(redirect)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    for part in ("response_type=code", "code_challenge_method=S256", "scope=openid+email+profile", "nonce=", "state="):
        assert part in url, part
    state = url.split("state=")[1].split("&")[0]
    _, verifier, nonce, expiry = result.pending[state]
    assert encode(sha256(verifier.encode()).digest()) in url
    assert nonce in url and expiry > result.clock()
    # One attempt, one redemption: the same state is never valid twice.
    result.transport.token = {"id_token": id_token(key, claims(nonce=nonce))}
    assert asyncio.run(result.callback(state, "code", redirect)).name == "person@example.com"
    for reused in (state, "invented"):
        with pytest.raises(GoogleLoginError, match="no longer valid"):
            asyncio.run(result.callback(reused, "code", redirect))


def test_an_expired_or_mismatched_state_is_refused():
    _, result = login()
    redirect = "https://studio.example.com/auth/google/callback"
    result.pending["stale"] = (redirect, "verifier", "nonce", result.clock() - 1)
    with pytest.raises(GoogleLoginError, match="no longer valid"):
        asyncio.run(result.callback("stale", "code", redirect))
    result.pending["other"] = ("https://studio.example.com/elsewhere", "verifier", "nonce", result.clock() + 60)
    with pytest.raises(GoogleLoginError, match="no longer valid"):
        asyncio.run(result.callback("other", "code", redirect))


def test_a_token_exchange_that_fails_or_returns_nothing_reports_safely():
    key, _ = keypair()
    for exchange, message in [
        ({"error": "invalid_grant"}, "no identity token"),
        ({"id_token": "not-a-token"}, "identity token"),
    ]:
        result = GoogleLogin(config(), FakeHTTP(token=exchange, keys=[]), session_key="k" * 40, wall_clock=time.time)
        result.pending["state-1"] = ("https://studio.example.com/auth/google/callback", "v", "nonce-1", result.clock() + 60)
        with pytest.raises(GoogleLoginError) as error:
            asyncio.run(result.callback("state-1", "code", "https://studio.example.com/auth/google/callback"))
        # The message a person sees names no token and no upstream body.
        assert "secret" not in str(error.value) and "not-a-token" not in str(error.value)
    assert key  # the keypair is only interesting for the token cases above


def test_a_key_set_that_cannot_be_read_names_no_upstream_detail():
    key, jwk = keypair()
    from conclave.integrations.transport import IntegrationError
    result = GoogleLogin(config(), FakeHTTP(fail_keys=IntegrationError("Endpoint is not an approved HTTPS host")),
                         session_key="k" * 40, wall_clock=time.time)
    result.pending["state-1"] = ("https://studio.example.com/auth/google/callback", "v", "nonce-1", result.clock() + 60)
    result.transport.token = {"id_token": id_token(key, claims())}
    with pytest.raises(GoogleLoginError, match="keys could not be read"):
        asyncio.run(result.callback("state-1", "code", "https://studio.example.com/auth/google/callback"))

    unknown = GoogleLogin(config(), FakeHTTP(keys=[jwk]), session_key="k" * 40, wall_clock=time.time)
    unknown.keys = {"another-key": key.public_key()}
    unknown.keys_read_at = unknown.clock()
    with pytest.raises(GoogleLoginError, match="does not trust"):
        asyncio.run(unknown._public_key(id_token(key, claims())))


def test_a_token_that_names_no_key_is_refused():
    key, _ = keypair()
    _, result = login()
    token = id_token(key, claims(), kid="")
    with pytest.raises(GoogleLoginError, match="named no key"):
        asyncio.run(result._public_key(token))


# --- The session ------------------------------------------------------------

def test_a_session_round_trips_and_a_tampered_one_does_not():
    _, result = login()
    from conclave.application.principal import Principal
    value = result.issue(Principal(name="person@example.com", tenant="acme", source="google"))
    read = result.read(value)
    assert (read.name, read.tenant, read.source) == ("person@example.com", "acme", "google")
    for broken in (None, "", "nonsense", value + "x", value.split(".")[0], "a.b",
                   result.issue(Principal(name="other@example.com", tenant="acme"), now=-10 ** 6)):
        assert result.read(broken) is None, broken


def test_another_key_cannot_read_or_forge_a_session():
    _, result = login()
    other = GoogleLogin(config(), FakeHTTP(), session_key="z" * 40, wall_clock=time.time)
    from conclave.application.principal import Principal
    assert other.read(result.issue(Principal(name="person@example.com", tenant="acme"))) is None


def test_an_expired_session_is_refused():
    _, result = login()
    from conclave.application.principal import Principal
    principal = Principal(name="person@example.com", tenant="acme")
    assert result.read(result.issue(principal, now=int(time.time()) - result.config.session_seconds - 1)) is None


def test_a_short_session_key_is_refused_at_construction():
    with pytest.raises(GoogleLoginError, match="session key"):
        GoogleLogin(config(), FakeHTTP(), session_key="short")


def test_the_cookie_is_httponly_and_secure_only_over_https():
    _, result = login()
    secure = result.cookie("value", secure=True)
    plain = result.cookie("value", secure=False)
    assert "HttpOnly" in secure and "SameSite=Lax" in secure and "Path=/" in secure
    assert "Secure" in secure and "Secure" not in plain
    assert result.cleared_cookie(secure=True).endswith("Max-Age=0; Secure")
    assert cookie_value("a=1; conclave_session=value; b=2", "conclave_session") == "value"
    assert cookie_value("", "conclave_session") is None


def test_normalise_email_rejects_what_is_not_an_address():
    assert normalise_email(" Person@Example.COM ") == "person@example.com"
    for broken in ("", "no-at", "a@b", "a b@example.com", "a@example"):
        with pytest.raises(GoogleLoginError):
            normalise_email(broken)


# --- Configuration ----------------------------------------------------------

def test_nothing_is_configured_without_a_login_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CONCLAVE_GOOGLE_LOGIN_FILE", raising=False)
    assert configured_login() is None


def test_a_login_file_and_a_session_key_are_both_required(monkeypatch, tmp_path):
    _, jwk = keypair()
    path = tmp_path / "login.json"
    path.write_text(json.dumps({"client_id": "client-1", "client_secret": "secret-1",
                                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                                "token_url": "https://oauth2.googleapis.com/token",
                                "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
                                "hosts": ["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"],
                                "redirect_uris": ["https://studio.example.com/auth/google/callback"],
                                "tenant": "acme", "emails": ["person@example.com"]}))
    monkeypatch.setenv("CONCLAVE_GOOGLE_LOGIN_FILE", str(path))
    monkeypatch.delenv("CONCLAVE_SESSION_KEY", raising=False)
    with pytest.raises(GoogleLoginError, match="session key"):
        configured_login()
    monkeypatch.setenv("CONCLAVE_SESSION_KEY", "k" * 40)
    login_from_file = configured_login()
    assert login_from_file.config.tenant == "acme" and login_from_file.config.allows("person@example.com")
    assert not login_from_file.config.allows("someone@example.com")


def test_an_unreadable_login_file_reports_the_kind_of_failure(monkeypatch, tmp_path):
    path = tmp_path / "login.json"
    path.write_text("{not json")
    monkeypatch.setenv("CONCLAVE_GOOGLE_LOGIN_FILE", str(path))
    monkeypatch.setenv("CONCLAVE_SESSION_KEY", "k" * 40)
    with pytest.raises(GoogleLoginError, match="could not be read"):
        configured_login()
    monkeypatch.setenv("CONCLAVE_GOOGLE_LOGIN_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(GoogleLoginError, match="could not be read"):
        configured_login()


# --- The transport's half ---------------------------------------------------

def call(app, method, path, *, headers=None, body=None):
    """Drive the ASGI callable with no socket and no server."""

    sent = []
    scope = {"type": "http", "method": method, "path": path, "scheme": "https",
             "headers": [(key.encode(), value.encode()) for key, value in (headers or {}).items()]}
    messages = [{"type": "http.request", "body": body or b"", "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = sent[0]["status"]
    response_headers = {key.decode(): value.decode() for key, value in sent[0]["headers"]}
    payload = json.loads(sent[1]["body"]) if sent[1]["body"] else None
    return status, response_headers, payload


def app_with(login_result, **options):
    from conclave.api import TenantAPIs
    return application(apis=TenantAPIs(), login=login_result, **options)


def test_sign_in_routes_are_answered_without_a_token_and_without_a_session():
    key, result = login()
    result.keys = {"test-key": key.public_key()}
    app = app_with(result)
    status, headers, _ = call(app, "GET", "/auth/google/start", headers={"host": "studio.example.com"})
    assert status == 302 and headers["location"].startswith("https://accounts.google.com/")
    state = headers["location"].split("state=")[1].split("&")[0]
    # Google signs the nonce this deployment asked for, so the test reads it back.
    result.transport.token = {"id_token": id_token(key, claims(nonce=result.pending[state][2]))}
    # A callback with the state this deployment issued sets a session cookie.
    status, headers, _ = call(app, "GET", f"/auth/google/callback?code=code-1&state={state}",
                              headers={"host": "studio.example.com"})
    assert status == 302 and headers["location"] == "/"
    cookie = headers["set-cookie"]
    assert cookie.startswith("conclave_session=") and "HttpOnly" in cookie and "Secure" in cookie
    value = cookie.split(";")[0].split("=", 1)[1]
    # That cookie is a credential; a request with it reaches the API.
    status, _, payload = call(app, "GET", "/api/me", headers={"host": "studio.example.com", "cookie": f"conclave_session={value}"})
    assert status == 200
    assert payload["principal"] == {"name": "person@example.com", "tenant": "acme", "source": "google",
                                    "label": "person@example.com · acme"}
    assert payload["login"] == {"start": "/auth/google/start", "logout": "/auth/logout"}


def test_a_callback_that_fails_sends_the_person_back_to_the_page():
    _, result = login()
    app = app_with(result)
    status, headers, _ = call(app, "GET", "/auth/google/callback?code=c&state=invented",
                              headers={"host": "studio.example.com"})
    assert status == 302 and headers["location"] == "/?signin=failed"
    assert "set-cookie" not in headers


def test_a_start_from_a_host_this_deployment_did_not_name_is_refused():
    _, result = login()
    app = app_with(result)
    status, headers, payload = call(app, "GET", "/auth/google/start", headers={"host": "elsewhere.test"})
    assert status == 400 and "state" not in headers.get("location", "")
    assert payload["error"] in {"This deployment did not authorize that redirect URI"}


def test_the_api_refuses_an_unknown_or_forged_cookie_and_the_token_still_works():
    _, result = login()
    app = app_with(result, token="deployment-token")
    status, _, payload = call(app, "GET", "/api/me")
    assert status == 401 and payload["login"] == "/auth/google/start"
    status, _, _ = call(app, "GET", "/api/me", headers={"cookie": "conclave_session=forged.value"})
    assert status == 401
    status, _, payload = call(app, "GET", "/api/me", headers={"authorization": "Bearer deployment-token"})
    assert status == 200 and payload["principal"]["source"] == "local"


def test_signing_out_clears_the_cookie():
    key, result = login()
    app = app_with(result)
    status, headers, _ = call(app, "POST", "/auth/logout", headers={"content-type": "application/json"})
    assert status == 204 and "conclave_session=;" in headers["set-cookie"] and "Max-Age=0" in headers["set-cookie"]


def test_a_deployment_without_login_keeps_its_token_and_its_tenant():
    app = application(apis=None, token="only-token")
    status, _, payload = call(app, "GET", "/api/me", headers={"authorization": "Bearer only-token"})
    assert status == 200 and payload["login"] is None and payload["principal"]["source"] == "local"
    # Without a session there is no browser credential at all, and with one the
    # sign-in routes simply do not exist.
    status, _, payload = call(app, "GET", "/auth/google/start")
    assert status == 401 and payload["error"] == "Authentication required"
    status, _, payload = call(app, "GET", "/auth/google/start", headers={"authorization": "Bearer only-token"})
    assert status == 404 and payload["error"] == "Endpoint not found"


def test_a_deployment_that_supplies_its_own_verifier_still_wins():
    _, result = login()
    app = application(apis=None, login=result, principal_for=lambda scope, headers: __import__(
        "conclave.application.principal", fromlist=["Principal"]).Principal(name="other", tenant="other"))
    status, _, payload = call(app, "GET", "/api/me", headers={"cookie": "conclave_session=whatever"})
    assert status == 200


def test_every_field_of_the_login_file_is_checked():
    """A login file is deployment data, so a mistake in it must fail loudly."""
    base = {"client_id": "client-1", "client_secret": "secret-1",
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
            "hosts": ["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"],
            "redirect_uris": ["https://studio.example.com/auth/google/callback"],
            "tenant": "acme", "emails": ["person@example.com"]}
    for broken, message in [
        ({"domains": "example.com"}, "must be a list"),
        ({"domains": ["not a domain"]}, "bare domains"),
        ({"emails": ["not-an-address"]}, "usable address"),
        ({"tenant": "Acme"}, "lowercase identifiers"),
        ({"client_id": ""}, "must name client_id"),
        ({"session_seconds": 5}, "between a minute"),
        ({"session_seconds": True}, "between a minute"),
    ]:
        with pytest.raises(GoogleLoginError, match=message):
            LoginConfig.from_value({**base, **broken})
    with pytest.raises(GoogleLoginError, match="must contain an object"):
        LoginConfig.from_value([])
    assert LoginConfig.from_value(base).issuers == ("https://accounts.google.com", "accounts.google.com")


def test_a_callback_without_a_code_is_refused_before_any_exchange():
    _, result = login()
    redirect = "https://studio.example.com/auth/google/callback"
    result.pending["state-1"] = (redirect, "verifier", "nonce", result.clock() + 60)
    with pytest.raises(GoogleLoginError, match="authorization code"):
        asyncio.run(result.callback("state-1", "", redirect))


def test_a_token_that_cannot_be_read_at_all_is_refused():
    key, _ = keypair()
    public = key.public_key()
    now = int(time.time())
    arguments = {"audience": "client-1", "issuers": ("https://accounts.google.com",),
                 "public_key": public, "nonce": "n", "now": now}
    for broken in ("a.b.c", f"{encode(b'not json')}.{encode(b'{}')}.{encode(b'sig')}",
                   f"{encode(b'{}')}.{encode(b'{}')}.{encode(b'sig')}",
                   f"{encode(b'!!')}.{encode(b'{}')}.{encode(b'sig')}"):
        with pytest.raises(GoogleLoginError):
            verify_id_token(broken, **arguments)
    with pytest.raises(GoogleLoginError, match="identity token"):
        verify_id_token(None, **arguments)


def test_a_session_payload_that_is_not_a_principal_is_refused():
    _, result = login()
    for payload in ({"exp": int(time.time()) + 60}, {"exp": int(time.time()) + 60, "tenant": "Acme"},
                    {"name": "x"}, b"not json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        forged = f"{encode(raw)}.{encode(__import__('hmac').new(result.session_key, raw, sha256).digest())}"
        assert result.read(forged) is None, payload


def test_the_key_set_is_read_once_and_a_rotation_asks_again():
    key, jwk = keypair()
    http = FakeHTTP(keys=[jwk])
    result = GoogleLogin(config(), http, session_key="k" * 40, wall_clock=time.time, clock=lambda: 1_000_000.0)
    first = asyncio.run(result._public_key(id_token(key, claims())))
    assert first.key_size == 2048 and len(http.calls) == 1
    # A known key is not re-read.
    asyncio.run(result._public_key(id_token(key, claims())))
    assert len(http.calls) == 1
    # An unknown key asks again, but not on every request.
    result.keys_read_at = result.clock()
    with pytest.raises(GoogleLoginError, match="does not trust"):
        asyncio.run(result._public_key(id_token(key, claims(), kid="another-key")))
    assert len(http.calls) == 1
    result.keys_read_at = result.clock() - 120
    with pytest.raises(GoogleLoginError, match="does not trust"):
        asyncio.run(result._public_key(id_token(key, claims(), kid="another-key")))
    assert len(http.calls) == 2


def test_a_key_set_that_is_not_a_list_is_refused():
    _, result = login()
    result.transport.keys = "nonsense"
    result.transport.token = None
    assert result.keys == {}
    with pytest.raises(GoogleLoginError, match="keys could not be read"):
        asyncio.run(result._read_keys(force=True))


def test_principal_for_refuses_a_request_with_no_session():
    _, result = login()
    with pytest.raises(GoogleLoginError, match="Sign in to continue"):
        result.principal_for({}, {})
    assert result.authenticated({}) is None


def test_a_session_that_expires_between_the_gate_and_the_principal_is_refused():
    """The gate and the principal are two reads: a session can lapse in between."""

    from conclave.application.principal import Principal
    _, result = login()
    now = int(time.time())
    readings = [now, now + result.config.session_seconds + 1]
    result.wall_clock = lambda: readings.pop(0) if len(readings) > 1 else readings[0]
    cookie = result.issue(Principal(name="person@example.com", tenant="acme"), now=now)
    status, _, payload = call(app_with(result), "GET", "/api/me",
                              headers={"cookie": f"conclave_session={cookie}"})
    assert status == 401 and payload == {"error": "Sign in to continue", "login": "/auth/google/start"}


def test_abandoned_sign_in_attempts_do_not_accumulate():
    """Starting a sign-in is a public endpoint, so it must not grow memory."""

    _, result = login()
    redirect = "https://studio.example.com/auth/google/callback"
    result.pending["abandoned"] = (redirect, "verifier", "nonce", result.clock() - 1)
    result.begin(redirect)
    assert "abandoned" not in result.pending and len(result.pending) == 1


def test_the_configured_application_switches_login_on_only_when_it_is_configured(monkeypatch, tmp_path):
    """The deployment's real entry point: no file, no change; a file, a login."""

    from conclave.asgi import configured_application
    monkeypatch.setenv("CONCLAVE_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.delenv("CONCLAVE_GOOGLE_LOGIN_FILE", raising=False)
    monkeypatch.delenv("CONCLAVE_API_TOKEN", raising=False)
    assert configured_application().login is None

    path = tmp_path / "login.json"
    path.write_text(json.dumps({"client_id": "client-1", "client_secret": "secret-1",
                                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                                "token_url": "https://oauth2.googleapis.com/token",
                                "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
                                "hosts": ["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"],
                                "redirect_uris": ["https://studio.example.com/auth/google/callback"],
                                "tenant": "acme", "domains": ["example.com"]}))
    monkeypatch.setenv("CONCLAVE_GOOGLE_LOGIN_FILE", str(path))
    monkeypatch.setenv("CONCLAVE_SESSION_KEY", "k" * 40)
    app = configured_application()
    assert app.login is not None and app.login.config.tenant == "acme"
    # Nothing gets through without a session, and the answer says how to get one.
    status, _, payload = call(app, "GET", "/api/applications")
    assert status == 401 and payload["login"] == "/auth/google/start"
