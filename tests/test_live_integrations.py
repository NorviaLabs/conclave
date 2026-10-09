"""Opt-in live provider checks. Skipped by default; never needed for the suite.

These are the only tests that touch a real provider. They require credentials
the operator supplies, so they are skipped when those are absent and are never
described as passing unless they actually ran against the live service.
"""
import asyncio
import json
import os

import pytest
from conclave.integrations.connections import ConnectionStore
from conclave.integrations.transport import IntegrationError, Transport

pytestmark = pytest.mark.skipif(
    os.environ.get("CONCLAVE_LIVE_TESTS", "").lower() not in {"1", "true", "yes"},
    reason="Set CONCLAVE_LIVE_TESTS=1 and provider credentials to run live checks",
)


def live_value(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"Set {name} to run this live check")
    return value


def run_connection(identifier, operations, credentials, scopes=(), config=None, key=None):
    import tempfile
    from pathlib import Path

    from cryptography.fernet import Fernet

    store = ConnectionStore(Path(tempfile.mkdtemp()) / "live.sqlite",
                            key or Fernet.generate_key(),
                            hosts=filter(None, os.environ.get("CONCLAVE_LIVE_HOSTS", "").split(",")))
    store.create({"id": identifier, "integration": identifier, "name": f"Live {identifier}",
                  "applications": ["live"], "operations": list(operations), "scopes": list(scopes),
                  "credentials": credentials, "config": config or {}}, "live", "operator")
    return store


def test_live_brave_web_search_returns_results():
    store = run_connection("web_search", ["search"], {"api_key": live_value("CONCLAVE_LIVE_BRAVE_KEY")})
    result = json.loads(asyncio.run(store.invoke("web_search", "search", {"query": "conclave agent runtime"},
                                                 "live", "operator", "live")))
    assert result["results"], "Brave returned no results for a plain query"


def test_live_slack_authorized_read():
    store = run_connection("slack", ["search_messages"], {"token": live_value("CONCLAVE_LIVE_SLACK_TOKEN")},
                           scopes=["search:read"])
    result = json.loads(asyncio.run(store.invoke("slack", "search_messages", {"query": "has:link"},
                                                 "live", "operator", "live")))
    assert "messages" in result or "results" in result


def test_live_github_read_repository():
    store = run_connection("github", ["search_repositories"],
                           {"token": live_value("CONCLAVE_LIVE_GITHUB_TOKEN")})
    result = json.loads(asyncio.run(store.invoke("github", "search_repositories", {"query": "conclave"},
                                                 "live", "operator", "live")))
    assert "items" in result or "total_count" in result


def test_live_feed_and_unreachable_host_are_both_bounded():
    store = run_connection("rss", ["read_feed"], {"token": "unused"},
                           config={"base_url": live_value("CONCLAVE_LIVE_FEED_URL")})
    result = json.loads(asyncio.run(store.invoke("rss", "read_feed", {}, "live", "operator", "live")))
    assert "entries" in result
    # A private address must stay refused even with a live transport.
    transport = Transport({"127.0.0.1"})
    with pytest.raises(IntegrationError):
        asyncio.run(transport.request("GET", "https://127.0.0.1/feed"))
