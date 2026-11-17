"""P1: how many steps may run at once — the application's limit, under the host's cap.

`run_config.fan_out_limit` ("Steps at once" in Studio) is the application's
choice; `null` means "as many as the host allows". The host bounds it with
`CONCLAVE_MAX_PARALLEL_STEPS` (default 8), because a blank limit must not mean
"as many as there are steps".
"""

import asyncio

import pytest

from conclave import ToolRegistry
from conclave.application.definition import run_application
from parallel_support import Gated, collect, plan, start, store_run, until

SIX = ["a", "b", "c", "d", "e", "f"]


def peak(limit, *, cap=None, steps=SIX):
    definition = plan(steps, limit=limit)
    provider = Gated(definition, delays={step: 0.03 for step in steps})
    extra = {} if cap is None else {"max_parallel_steps": cap}

    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), "Work", **extra)]

    events = asyncio.run(go())
    assert events[-1]["status"] == "completed"
    return provider.peak


@pytest.mark.parametrize("limit, cap, expected", [
    (3, None, 3),     # the application's limit
    (None, None, 6),  # blank: as many as the default cap (8) allows
    (None, 4, 4),     # blank, under a smaller host cap
    (10, 4, 4),       # the host cap wins over a larger limit
    (1, None, 1),     # one at a time
])
def test_the_number_running_never_passes_the_limit_or_the_host_cap(limit, cap, expected):
    assert peak(limit, cap=cap) == expected


@pytest.mark.parametrize("cap", [0, 65, -1])
def test_run_application_refuses_a_cap_outside_its_range(cap):
    definition = plan(["a"])

    async def go():
        return [event async for event in run_application(definition, Gated(definition), ToolRegistry(20000), "Work",
                                                         max_parallel_steps=cap)]

    with pytest.raises(ValueError, match="parallel"):
        asyncio.run(go())


def test_a_new_step_starts_only_when_a_slot_frees():
    definition = plan(SIX, limit=2)
    provider = Gated(definition, gated=SIX)

    async def go():
        run = start(definition, provider)
        await until(lambda: len(provider.started) == 2, what="two steps to start")
        await asyncio.sleep(0.05)
        assert provider.started == ["a", "b"], "only two start while both are working"
        provider.release("b")
        await until(lambda: len(provider.started) == 3, what="a third step once b is done")
        assert provider.started[2] == "c"
        provider.release(*SIX)
        await run.done()
        return run

    run = asyncio.run(go())
    assert run.events[-1]["status"] == "completed"
    assert provider.peak == 2


def test_the_run_store_passes_the_hosts_cap_on(tmp_path):
    definition = plan(SIX, limit=None)
    repository, run_id = store_run(tmp_path, definition)
    repository.max_parallel_steps = 2
    provider = Gated(definition, delays={step: 0.03 for step in SIX})
    events = collect(repository.execute(run_id, provider, ToolRegistry(20000)))
    assert events[-1]["status"] == "completed"
    assert provider.peak == 2


@pytest.mark.parametrize("value, ok", [(None, True), ("3", True), ("64", True), ("0", False), ("65", False), ("abc", False)])
def test_the_host_setting_is_checked_at_start_up(tmp_path, monkeypatch, value, ok):
    from conclave.application.service import configured_host

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    if value is None:
        monkeypatch.delenv("CONCLAVE_MAX_PARALLEL_STEPS", raising=False)
    else:
        monkeypatch.setenv("CONCLAVE_MAX_PARALLEL_STEPS", value)
    if not ok:
        with pytest.raises(ValueError, match="CONCLAVE_MAX_PARALLEL_STEPS"):
            configured_host()
        return
    host = configured_host()
    expected = 8 if value is None else int(value)
    assert host.repository.max_parallel_steps == expected
    _, runtime = asyncio.run(host.handle("runtime", {}))
    assert runtime["max_parallel_steps"] == expected, "Studio is told the cap, to offer it in Steps at once"
    asyncio.run(host.close())
