"""A team member, or a single step, may name the backend that runs it.

Resolution is application policy; serving it is the host's. Both halves are
checked here: the precedence a step gets, and the refusal when this machine
cannot run what the definition asks for.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from conftest import CONFIG
from conclave import Completion, ModelProvider, RunBudget, ToolRegistry, Usage
from conclave.application.definition import run_application, stage_backend, validate_definition
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.providers import LocalBackends


def definition(**overrides):
    base = {
        "version": 1, "name": "Backends",
        "agents": [{"id": "writer", "name": "Writer", "instructions": "Write", "tools": []},
                   {"id": "reviewer", "name": "Reviewer", "kind": "human", "instructions": "Review", "tools": []}],
        "state": [{"key": "brief", "type": "text", "merge": "replace", "statuses": []}],
        "workflow": [{"id": "draft", "name": "Draft", "agent": "writer", "task": "Draft", "output": "brief"},
                     {"id": "review", "name": "Review", "agent": "reviewer", "task": "Review", "output": "brief"}],
        "views": [{"id": "brief_view", "collection": "brief", "layout": "prose"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }
    base.update(overrides)
    return base


def member(**fields):
    return {"id": "writer", "name": "Writer", "instructions": "Write", "tools": [], **fields}


# ---------------------------------------------------------------------------
# Which backend one step runs on
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent,step,expected", [
    (member(), {}, ("", "")),
    (member(provider="codex", model="gpt-5"), {}, ("codex", "gpt-5")),
    (member(provider="codex", model="gpt-5"), {"model": "gpt-5-mini"}, ("codex", "gpt-5-mini")),
    (member(provider="codex", model="gpt-5"), {"provider": "ollama"}, ("ollama", "")),
    (member(provider="codex", model="gpt-5"), {"provider": "ollama", "model": "llama3.1:8b"}, ("ollama", "llama3.1:8b")),
    (member(provider="codex", model="gpt-5"), {"provider": "codex", "model": ""}, ("codex", "gpt-5")),
    (member(), {"provider": "codex"}, ("codex", "")),
])
def test_a_step_overrides_what_it_names_and_inherits_the_rest(agent, step, expected):
    assert stage_backend(agent, step) == expected


def test_a_model_never_follows_a_provider_it_was_not_chosen_for():
    """Moving one step to another backend uses that backend's own default."""

    agent = member(provider="codex", model="gpt-5")
    assert stage_backend(agent, {"provider": "ollama"})[1] == ""
    assert stage_backend(agent, {"provider": "codex"})[1] == "gpt-5"


def test_the_host_default_fills_an_undeclared_half():
    assert stage_backend(member(), {}, "codex") == ("codex", "")
    assert stage_backend(member(provider="ollama", model="llama3"), {}, "codex") == ("ollama", "llama3")


# ---------------------------------------------------------------------------
# What a definition may declare
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,value", [
    (("agents", 0, "provider"), "not a backend!"),
    (("agents", 0, "provider"), "a" * 65),
    (("agents", 0, "model"), "with space"),
    (("agents", 0, "model"), 7),
    (("agents", 0, "provider"), " backend"),
    (("agents", 1, "provider"), "codex"),
    (("agents", 1, "model"), "gpt-5"),
    (("workflow", 0, "provider"), "bad/name"),
    (("workflow", 0, "model"), "x" * 121),
    (("workflow", 1, "provider"), "codex"),
])
def test_backend_refuses_a_backend_that_is_not_a_name(path, value):
    value_definition = definition()
    entry = value_definition[path[0]][path[1]]
    entry[path[2]] = value
    with pytest.raises(ValueError):
        validate_definition(value_definition)


def test_backend_accepts_a_member_and_a_step_choice():
    value = definition()
    value["agents"][0].update(provider="codex", model="gpt-5")
    value["workflow"][0].update(provider="ollama", model="llama3.1:8b")
    validate_definition(value)


def test_backend_accepts_an_explicitly_empty_choice():
    """A picker's default writes empty, which means nothing was declared."""

    value = definition()
    value["agents"][0].update(provider="", model="")
    validate_definition(value)


# ---------------------------------------------------------------------------
# Serving the choice
# ---------------------------------------------------------------------------

class RecordingProvider(ModelProvider):
    """Answers with the one reply an agent loop accepts, and counts the calls."""

    def __init__(self, name):
        self.name = name
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        return Completion(text=json.dumps({"type": "final", "content": f"served by {self.name}"}), usage=Usage(1, 1))


class Router(ModelProvider):
    """Stands in for a host that can serve more than one backend."""

    def __init__(self):
        self.served = {}
        self.asked = []

    async def complete(self, messages):
        return await self.served[""].complete(messages)

    def for_call(self, provider, model):
        self.asked.append((provider, model))
        return self.served.get(provider)


def test_each_step_reaches_the_backend_it_named():
    router = Router()
    router.served = {"": RecordingProvider("default"), "codex": RecordingProvider("codex"), "ollama": RecordingProvider("ollama")}
    value = definition()
    value["agents"][0].update(provider="codex")
    value["workflow"][0]["provider"] = "ollama"

    events = asyncio.run(_events(value, router))

    assert set(router.asked) == {("ollama", "")}, "the step's own backend is the one asked for"
    assert router.served["ollama"].calls == 1 and router.served["codex"].calls == 0
    started = next(event for event in events if event["type"] == "StageStarted")
    assert (started["provider"], started["model"]) == ("ollama", "")


def test_a_member_backend_is_used_when_its_step_names_none():
    router = Router()
    router.served = {"": RecordingProvider("default"), "codex": RecordingProvider("codex")}
    value = definition()
    value["agents"][0].update(provider="codex", model="gpt-5")

    events = asyncio.run(_events(value, router))

    assert set(router.asked) == {("codex", "gpt-5")}
    assert router.served["codex"].calls == 1
    assert next(event for event in events if event["type"] == "StageStarted")["model"] == "gpt-5"


def test_a_host_that_cannot_choose_refuses_every_declared_backend():
    """A single-backend host has no router, so it serves only the unnamed call.

    It cannot tell whether a name matches the backend it runs, so it refuses
    the choice rather than guessing: the host refuses the run before it starts,
    and this is the backstop if a caller skipped that check.
    """

    from conclave.authority import InProcessAuthority, ModelCall

    default = RecordingProvider("only")
    authority = InProcessAuthority(default, RunBudget(None, None, None))

    async def check():
        for name in ("ollama", "only"):
            with pytest.raises(ValueError, match="cannot run the model backend " + name):
                await authority.complete(ModelCall(run_id="r", agent="writer", messages=[], provider=name))

        return await authority.complete(ModelCall(run_id="r", agent="writer", messages=[]))

    assert "served by only" in asyncio.run(check()).text
    assert default.calls == 1


def test_a_run_that_names_an_unreachable_backend_fails_rather_than_switching():
    router = Router()
    router.served = {"": RecordingProvider("default")}
    value = definition()
    value["agents"][0].update(provider="codex", model="gpt-5")

    events = asyncio.run(_events(value, router))

    assert router.served[""].calls == 0, "nothing is sent to another backend to keep the run going"
    assert events[-1]["status"] == "failed"


# ---------------------------------------------------------------------------
# What this machine offers
# ---------------------------------------------------------------------------

@pytest.fixture
def machine(monkeypatch):
    """The backends this machine has: codex and two Ollama models."""

    monkeypatch.setattr("conclave.providers.installed_ollama_models", lambda base_url, timeout=1.0: ["local-a", "local-b"])
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name if name == "codex" else None)
    return LocalBackends(Path("/repo"), RecordingProvider("codex"), "codex", timeout=5.0)


def test_the_catalogue_reports_what_can_be_chosen(machine):
    entries = {item["id"]: item for item in machine.catalogue()}

    assert entries["codex"]["available"] is True and entries["codex"]["default"] is True
    assert entries["codex"]["models"] == [], "a command backend reports no model list"
    assert entries["opencode"]["available"] is False and "not installed" in entries["opencode"]["reason"]
    assert entries["ollama"]["models"] == ["local-a", "local-b"] and entries["ollama"]["default_model"] == "local-a"


def test_a_model_only_follows_its_own_backend(machine):
    """An unnamed backend is the host's default; a named model rebuilds it."""

    assert machine.for_call("", "") is machine.default
    assert machine.for_call("", "gpt-5") is not machine.default
    assert machine.for_call("ollama", "local-b") is not None


def test_a_backend_or_model_this_machine_lacks_is_not_served(machine):
    assert machine.for_call("claude", "") is None
    assert machine.for_call("opencode", "") is None, "not installed"
    assert machine.for_call("ollama", "not-pulled") is None, "the server does not report that model"
    assert machine.for_call("", "") is machine.default


def test_a_backend_is_built_once_and_kept(machine):
    first = machine.for_call("ollama", "local-a")
    assert machine.for_call("ollama", "local-a") is first


# ---------------------------------------------------------------------------
# Refusing before the run starts
# ---------------------------------------------------------------------------

def host_for(machine):
    # The router a run gets, and the backend that answers calls naming none.
    return LocalRunHost(_repository(), machine.default, _tools(), reviewer="ada", provider_name="codex", backends=machine)


def test_a_run_that_asks_for_an_unavailable_backend_is_refused_by_name(machine):
    value = definition()
    value["agents"][0].update(provider="opencode")

    with pytest.raises(ValueError) as refused:
        host_for(machine)._validate_backends(value)

    assert "Step “Draft” (Writer)" in str(refused.value) and "OpenCode" in str(refused.value)


def test_a_run_that_asks_for_a_model_the_backend_lacks_is_refused_by_name(machine):
    value = definition()
    value["agents"][0].update(provider="ollama", model="not-pulled")

    with pytest.raises(ValueError) as refused:
        host_for(machine)._validate_backends(value)

    assert "not-pulled" in str(refused.value) and "local-a" in str(refused.value)


def test_a_host_that_runs_one_backend_refuses_a_choice_it_cannot_offer():
    value = definition()
    value["agents"][0].update(provider="ollama")

    with pytest.raises(ValueError) as refused:
        LocalRunHost(_repository(), RecordingProvider("only"), _tools(), reviewer="ada", provider_name="codex")._validate_backends(value)

    assert "cannot choose" in str(refused.value)


def test_a_team_that_names_nothing_is_accepted(machine):
    host_for(machine)._validate_backends(definition())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _events(definition_value, provider):
    return [event async for event in run_application(definition_value, provider, _tools(), "Do it")]


def _tools():
    return ToolRegistry(1000)


def _repository():
    return RunRepository(Path(tempfile.mkdtemp()) / "runs.sqlite")
