"""Step 9 — performance fixes for run notifications (P1, P2; P3 is in
studio/scripts/check-notifications.mjs).

P1: a run of an application without the setting costs nothing extra when it stops.
P2: the hourly count reads the last hour's notices, not all of history.
"""

import asyncio
import sqlite3

from notification_support import ANA, FakeConnections, api, definition, settle, slack


def principal(name=ANA):
    from conclave.application.principal import Principal
    return Principal(name)


def test_a_run_without_the_setting_starts_no_delivery_when_it_stops(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=FakeConnections().add("team_slack", owner=ANA, integration="slack"))
        calls = []
        original = service.host.notifier.deliver

        async def recording(run_id):
            calls.append(run_id)
            return await original(run_id)

        service.host.notifier.deliver = recording
        _, plain = await service.dispatch("POST", "/api/applications", {"definition": definition(None)}, principal=principal())
        _, told = await service.dispatch("POST", "/api/applications", {"definition": definition(slack())}, principal=principal())
        runs = {}
        for name, saved in (("plain", plain), ("told", told)):
            code, run = await service.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                                     "task": "x", "inputs": {"team": "x"}}, principal=principal())
            assert code == 201, run
            runs[name] = run["id"]
        await settle(service.host)
        assert calls == [runs["told"]], "only the run whose application says where to tell someone is looked at"
        await service.host.close()

    asyncio.run(exercise())


def test_the_hourly_count_uses_an_index_on_when_notices_were_sent(tmp_path):
    from conclave.application.notices import TOLD_SINCE
    from conclave.application.runs import RunRepository

    RunRepository(tmp_path / "runs.sqlite")
    with sqlite3.connect(tmp_path / "runs.sqlite") as connection:
        plan = " ".join(row[-1] for row in connection.execute("EXPLAIN QUERY PLAN " + TOLD_SINCE, ("", "", 0, "")))
    assert "studio_run_notices_sent_at" in plan, plan
    assert "SCAN n" not in plan and "SCAN studio_run_notices" not in plan, plan
