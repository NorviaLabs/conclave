"""C1 — a schedule in the definition, validated the same way everywhere.

The cases live in tests/data/schedule_cases.json, which Studio's check reads
too, so the host and the browser cannot drift apart.
"""

import asyncio

import pytest

from schedule_support import CASES, definition, schedule
from conclave.application.definition import validate_definition
from conclave.application.workspace import Workspace


@pytest.mark.parametrize("case", CASES["valid"], ids=lambda case: case["name"])
def test_a_valid_schedule_is_accepted(case):
    from conclave.application.schedules import validate_schedule

    block = schedule(**case["change"])
    normalized = validate_schedule(block, definition())
    assert normalized["kind"] == block["kind"] and normalized["time_zone"] == block["time_zone"]
    assert normalized["catch_up"] == block.get("catch_up", "skip"), "catch-up is skip unless asked"
    validate_definition(definition(block))


@pytest.mark.parametrize("case", CASES["invalid"], ids=lambda case: case["name"])
def test_an_invalid_schedule_says_what_is_wrong(case):
    from conclave.application.schedules import validate_schedule

    block = schedule(**case["change"])
    with pytest.raises(ValueError, match=case["message"]):
        validate_schedule(block, definition())
    with pytest.raises(ValueError, match=case["message"]):
        validate_definition(definition(block))


def test_times_are_kept_in_order():
    from conclave.application.schedules import validate_schedule

    assert validate_schedule(schedule(times=["17:30", "09:00"]), definition())["times"] == ["09:00", "17:30"]
    assert validate_schedule(schedule(days=["fri", "mon"]), definition())["days"] == ["mon", "fri"]


def test_a_schedule_belongs_only_to_a_scheduled_application():
    with pytest.raises(ValueError, match="start method"):
        validate_definition(definition(schedule(), start_mode="manual"))


def test_a_scheduled_application_is_saved_with_its_schedule(tmp_path):
    workspace = Workspace(tmp_path / "applications.sqlite")
    with pytest.raises(ValueError, match="Set when this application runs"):
        workspace.save(definition(None))
    saved = workspace.save(definition(schedule()))
    assert saved["definition"]["schedule"]["time_zone"] == "Europe/London"


def test_an_older_scheduled_application_without_a_schedule_still_opens_and_runs_manually(tmp_path):
    from conclave import ToolRegistry
    from conftest import ScriptedProvider
    from conclave.application.runs import RunRepository

    older = definition(None)
    validate_definition(older)  # not refused on read: only a save asks for the block
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create({"version": 1, "application_id": "digest", "revision": 1, "definition": older,
                                "task": "Summarise", "inputs": {"team": "billing"}})
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Digest"})

    async def run():
        return [event async for event in repository.execute(run_id, provider, ToolRegistry(20000))]

    assert asyncio.run(run())[-1]["status"] == "completed"
