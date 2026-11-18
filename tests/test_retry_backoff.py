"""A run a backend stopped continues by itself, after a backoff.

A timeout, an outage or a usage limit is a condition of the moment. The run
stops with its checkpoint, as before; the host then records when it will try
again and does, a bounded number of times, without anyone pressing Continue.
Nothing else is retried: a failure, a spent budget or an uncertain action
still waits for a person.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from conclave import ProviderError, ToolRegistry, Usage
from conclave.application.definition import run_application
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost, RetryPolicy, public_record, retry_policy_from_settings
from conftest import ScriptedProvider
from test_studio_runs import request

FAST = RetryPolicy(attempts=3, base_seconds=0.01, max_seconds=0.02)


def single_step(steps=("draft",)):
    value = request()
    definition = value["definition"]
    definition["agents"] = definition["agents"][:1]
    definition["inputs"], value["inputs"] = [], {}
    definition["workflow"] = [{"id": step, "agent": "writer", "task": "Work on " + step, "output": "brief"} for step in steps]
    return value


class Flaky:
    """Raises the queued errors on its first calls, then answers."""

    def __init__(self, *errors, usage=None):
        self.errors = list(errors)
        self.inner = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Result"}, usage=usage)
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return await self.inner.complete(messages)


def timeout():
    return ProviderError("timeout", "no answer in 300 seconds")


def host_for(tmp_path, provider, policy=FAST, **options):
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, ToolRegistry(20000), reviewer="ana", **options)
    host.retry_policy = policy
    return host


async def start(host, value):
    _, record = await host.handle("start", value)
    return record["id"]


async def until(predicate, seconds=5.0):
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def types(record):
    return [item["event"]["type"] for item in record["events"]]


def scheduled(record):
    return [item["event"] for item in record["events"] if item["event"]["type"] == "RetryScheduled"]


# --- The stop says it can be retried ----------------------------------------------


@pytest.mark.parametrize("kind", ["timeout", "unreachable", "rate_limited"])
def test_a_transient_stop_is_tagged_for_retry(kind):
    async def go():
        return [event async for event in run_application(single_step()["definition"], Flaky(ProviderError(kind, "x")),
                                                         ToolRegistry(20000), "Work")]
    finished = asyncio.run(go())[-1]
    assert finished["status"] == "stopped" and finished["transient"] == kind


def test_a_failure_is_not_tagged_for_retry():
    async def go():
        return [event async for event in run_application(single_step()["definition"], Flaky(ProviderError("backend_error", "x")),
                                                         ToolRegistry(20000), "Work")]
    finished = asyncio.run(go())[-1]
    assert finished["status"] == "failed" and "transient" not in finished


# --- The backoff ------------------------------------------------------------------


def test_backoff_delays_double_and_are_capped():
    policy = RetryPolicy(attempts=5, base_seconds=30, max_seconds=100)
    assert [policy.delay(attempt) for attempt in range(1, 6)] == [30, 60, 100, 100, 100]
    assert RetryPolicy().attempts == 5 and RetryPolicy().base_seconds == 30 and RetryPolicy().max_seconds == 900


@pytest.mark.parametrize("settings", [
    {"attempts": -1}, {"attempts": True}, {"base_seconds": 0}, {"base_seconds": float("nan")},
    {"max_seconds": 1, "base_seconds": 2}, {"attempts": 101}, {"max_seconds": 86401},
])
def test_backoff_policy_refuses_values_outside_its_range(settings):
    with pytest.raises(ValueError, match="retry"):
        RetryPolicy(**settings)


def test_retry_settings_are_read_and_validated(monkeypatch):
    assert retry_policy_from_settings() == RetryPolicy()
    monkeypatch.setenv("CONCLAVE_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("CONCLAVE_RETRY_BASE_SECONDS", "1.5")
    monkeypatch.setenv("CONCLAVE_RETRY_MAX_SECONDS", "10")
    assert retry_policy_from_settings() == RetryPolicy(attempts=2, base_seconds=1.5, max_seconds=10)
    monkeypatch.setenv("CONCLAVE_RETRY_ATTEMPTS", "0")
    assert retry_policy_from_settings().attempts == 0, "0 turns automatic retry off"
    for name, value in (("CONCLAVE_RETRY_ATTEMPTS", "many"), ("CONCLAVE_RETRY_BASE_SECONDS", "soon"),
                        ("CONCLAVE_RETRY_MAX_SECONDS", "-5")):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError, match="CONCLAVE_RETRY"):
            retry_policy_from_settings()
        monkeypatch.delenv(name)


def test_a_configured_host_uses_the_retry_settings(tmp_path, monkeypatch):
    from conclave.application.service import configured_host
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONCLAVE_RETRY_ATTEMPTS", "1")
    assert configured_host().retry_policy.attempts == 1


# --- The host retries -------------------------------------------------------------


def test_retry_resumes_a_stopped_run_by_itself(tmp_path):
    async def exercise():
        provider = Flaky(timeout())
        host = host_for(tmp_path, provider)
        run_id = await start(host, single_step())
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "completed"
        retry = scheduled(record)
        assert len(retry) == 1
        assert retry[0]["attempt"] == 1 and retry[0]["attempts"] == 3 and retry[0]["reason"] == "timeout"
        assert retry[0]["delay_seconds"] == pytest.approx(0.01)
        assert datetime.fromisoformat(retry[0]["retry_at"]).tzinfo is not None
        order = types(record)
        assert order.index("RetryScheduled") < order.index("ApplicationResumed")
        assert host.retries == {}
        await host.close()
    asyncio.run(exercise())


def test_retry_gives_up_after_the_last_attempt(tmp_path):
    async def exercise():
        provider = Flaky(*[timeout() for _ in range(10)])
        host = host_for(tmp_path, provider, RetryPolicy(attempts=2, base_seconds=0.01, max_seconds=0.01))
        run_id = await start(host, single_step())
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "stopped"
        assert [event["attempt"] for event in scheduled(record)] == [1, 2]
        assert provider.calls == 3
        shown = public_record(record)
        assert shown["retry"] is None and shown["resumable"] is True
        await host.close()
    asyncio.run(exercise())


def test_retry_attempts_zero_turns_automatic_retry_off(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), RetryPolicy(attempts=0))
        run_id = await start(host, single_step())
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "stopped" and scheduled(record) == []
        await host.close()
    asyncio.run(exercise())


class FailsEachStepOnce:
    """Times out the first time each step asks, then answers."""

    def __init__(self):
        self.seen = set()
        self.inner = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Result"})

    async def complete(self, messages):
        step = messages[-1]["content"].split("STAGE TASK:\n")[1].split("\n")[0]
        if step not in self.seen:
            self.seen.add(step)
            raise timeout()
        return await self.inner.complete(messages)


def test_retry_counter_resets_after_a_step_completes(tmp_path):
    async def exercise():
        host = host_for(tmp_path, FailsEachStepOnce(), RetryPolicy(attempts=1, base_seconds=0.01, max_seconds=0.01))
        run_id = await start(host, single_step(("first", "second")))
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "completed"
        assert [event["attempt"] for event in scheduled(record)] == [1, 1]
        await host.close()
    asyncio.run(exercise())


def test_retry_is_not_scheduled_for_a_failure(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(ProviderError("backend_error", "x")))
        run_id = await start(host, single_step())
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "failed" and scheduled(record) == []
        await host.close()
    asyncio.run(exercise())


def test_retry_is_not_scheduled_for_a_spent_budget(tmp_path):
    async def exercise():
        value = single_step(("first", "second"))
        value["definition"]["run_budget"]["tokens"] = 5
        host = host_for(tmp_path, Flaky(usage=Usage(input_tokens=5, output_tokens=5)))
        run_id = await start(host, value)
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "stopped" and scheduled(record) == []
        await host.close()
    asyncio.run(exercise())


def test_retry_waits_for_a_person_while_an_action_is_uncertain(tmp_path, monkeypatch):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()))
        monkeypatch.setattr(host.repository.journal, "uncertain", lambda run_id: ["action-1"])
        run_id = await start(host, single_step())
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "stopped" and scheduled(record) == []
        await host.close()
    asyncio.run(exercise())


SLOW = RetryPolicy(attempts=3, base_seconds=600, max_seconds=600)


async def stopped_with_pending_retry(host, value=None):
    run_id = await start(host, value or single_step())
    await until(lambda: run_id in host.retries)
    return run_id


def test_public_record_reports_a_pending_retry(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(host)
        shown = public_record(host.repository.get(run_id))
        assert shown["retry"]["attempt"] == 1 and shown["retry"]["attempts"] == 3
        assert shown["retry"]["reason"] == "timeout"
        at = datetime.fromisoformat(shown["retry"]["at"])
        assert timedelta(seconds=590) < at - datetime.now(timezone.utc) <= timedelta(seconds=600)
        await host.close()
        assert host.retries == {}
        # Closing the host is not a decision: the retry stays on the record.
        assert public_record(host.repository.get(run_id))["retry"] is not None
    asyncio.run(exercise())


def test_continue_during_backoff_cancels_the_retry_and_resumes_now(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(host)
        await host.handle("resume", {"run_id": run_id})
        assert run_id not in host.retries
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "completed" and public_record(record)["retry"] is None
        await host.close()
    asyncio.run(exercise())


def test_dont_retry_cancels_a_pending_retry_and_keeps_continue(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(host)
        _, shown = await host.handle("cancel_retry", {"run_id": run_id})
        assert run_id not in host.retries
        assert shown["status"] == "stopped" and shown["retry"] is None and shown["resumable"] is True
        cancelled = [item["event"] for item in shown["events"] if item["event"]["type"] == "RetryCancelled"]
        assert cancelled[0]["reason"] == "cancelled" and cancelled[0]["by"] == "ana"
        with pytest.raises(ValueError, match="retry"):
            await host.handle("cancel_retry", {"run_id": run_id})
        await host.close()
    asyncio.run(exercise())


def test_pause_during_backoff_cancels_the_retry(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(host)
        _, shown = await host.handle("pause", {"run_id": run_id})
        assert run_id not in host.retries and shown["retry"] is None and shown["status"] == "stopped"
        assert [item["event"]["reason"] for item in shown["events"] if item["event"]["type"] == "RetryCancelled"] == ["paused"]
        await host.close()
    asyncio.run(exercise())


def test_cancel_during_backoff_cancels_the_retry_and_the_run(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(host)
        _, shown = await host.handle("cancel", {"run_id": run_id})
        assert run_id not in host.retries and shown["status"] == "cancelled" and shown["retry"] is None
        await host.close()
    asyncio.run(exercise())


def test_a_retry_that_fires_after_the_run_moved_on_does_nothing(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(host)
        task = host.retries.pop(run_id)
        task.cancel()
        host.repository.cancel_waiting(run_id)
        host.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=1)  # due now
        await host._retry_when_due(run_id, scheduled(host.repository.get(run_id))[-1]["retry_at"])
        assert host.repository.get(run_id)["status"] == "cancelled"
        await host.close()
    asyncio.run(exercise())


def test_a_serving_host_rearms_a_pending_retry_after_restart(tmp_path):
    async def exercise():
        first = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(first)
        await first.close()

        # Still waiting: armed again for the time that is left.
        waiting = host_for(tmp_path, Flaky(), SLOW)
        assert waiting.rearm_retries() == [run_id]
        assert run_id in waiting.retries
        await waiting.close()

        # Past due by the time the host is back: it runs at once.
        later = host_for(tmp_path, Flaky(), SLOW)
        later.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=1)
        assert later.rearm_retries() == [run_id]
        await asyncio.wait_for(later.until_idle(run_id), 5)
        record = later.repository.get(run_id)
        assert record["status"] == "completed" and len(scheduled(record)) == 1
        assert later.rearm_retries() == []
        await later.close()
    asyncio.run(exercise())


def test_the_rest_api_stops_retrying_and_begin_schedules_rearms(tmp_path):
    from conclave.api import ApplicationAPI
    from conclave.application.workspace import Workspace

    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()), SLOW)
        api = ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))
        run_id = await stopped_with_pending_retry(host)
        status, shown = await api.dispatch("POST", f"/api/runs/{run_id}/retry/cancel", {})
        assert status == 200 and shown["retry"] is None
        assert (await api.dispatch("POST", f"/api/runs/{run_id}/retry/cancel", {}))[0] == 400
        await host.close()

        again = host_for(tmp_path, Flaky(timeout()), SLOW)
        other = await stopped_with_pending_retry(again)
        await again.close()
        serving = host_for(tmp_path, Flaky(), SLOW)
        api = ApplicationAPI(serving, Workspace(tmp_path / "applications.sqlite"))
        api.begin_schedules()
        assert other in serving.retries and run_id not in serving.retries
        await api.stop_schedules()
        await serving.close()
    asyncio.run(exercise())


def test_the_terminal_waits_through_a_retry(tmp_path, monkeypatch, capsys):
    from applications import cli

    definition = tmp_path / "app.json"
    definition.write_text(json.dumps(single_step()["definition"]))
    monkeypatch.setattr(cli, "configured_host", lambda **kwargs: host_for(tmp_path, Flaky(timeout())))
    cli.run(["run", str(definition), "--task", "Work"])
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "completed"
    assert [item["event"]["type"] for item in record["events"]].count("RetryScheduled") == 1


# --- Security audit (S1, S2, S4) ----------------------------------------------------


def test_retry_does_not_act_for_an_owner_the_deployment_no_longer_admits(tmp_path):
    from types import SimpleNamespace

    async def exercise():
        provider = Flaky(timeout())
        host = host_for(tmp_path, provider)
        host.schedules = SimpleNamespace(admits=lambda owner: False)
        run_id = await start(host, single_step())
        await asyncio.wait_for(host.until_idle(run_id), 5)
        record = host.repository.get(run_id)
        assert record["status"] == "stopped" and provider.calls == 1, "nothing ran as someone no longer admitted"
        cancelled = [item["event"] for item in record["events"] if item["event"]["type"] == "RetryCancelled"]
        assert cancelled[-1]["reason"] == "owner_not_admitted"
        assert public_record(record)["retry"] is None and public_record(record)["resumable"] is True
        host.schedules = None
        await host.close()
    asyncio.run(exercise())


def test_retry_of_a_scheduled_run_still_refuses_writes(tmp_path):
    async def exercise():
        host = host_for(tmp_path, Flaky(timeout()))
        value = single_step()
        value["trigger"] = {"kind": "schedule", "scheduled_for": "2026-10-20T09:00:00+00:00"}
        run_id = host.repository.create(value, actor="ana")
        calls = []
        schedule = host._schedule

        async def recording(run, **options):
            calls.append(options)
            return await schedule(run, **options)
        host._schedule = recording
        await host._schedule(run_id, approvals="refuse")
        await asyncio.wait_for(host.until_idle(run_id), 5)
        assert host.repository.get(run_id)["status"] == "completed"
        assert calls[-1] == {"resume": True, "approvals": "refuse"}, "nobody is watching a scheduled run's retry either"
        await host.close()
    asyncio.run(exercise())


def test_retry_rearm_skips_an_unreadable_retry_time(tmp_path, caplog):
    async def exercise():
        first = host_for(tmp_path, Flaky(timeout()), SLOW)
        run_id = await stopped_with_pending_retry(first)
        await first.close()
        with first.repository._connect() as connection:
            connection.execute("UPDATE studio_run_events SET payload = json_set(payload, '$.retry_at', 'soon') "
                               "WHERE run_id = ? AND json_extract(payload, '$.type') = 'RetryScheduled'", (run_id,))
        later = host_for(tmp_path, Flaky(), SLOW)
        assert later.rearm_retries() == []
        assert "retry" in caplog.text.lower()
        await later.close()
    asyncio.run(exercise())


# --- Performance audit (P1, P4) -------------------------------------------------------


def long_stopped_run(repository, events=2000, size=20000):
    """A stopped run with a long record: every event carries a large snapshot."""
    run_id = repository.create(single_step())
    state = {"brief": "x" * size}
    with repository._connect() as connection:
        connection.execute("UPDATE studio_runs SET status = 'stopped' WHERE id = ?", (run_id,))
        connection.executemany("INSERT INTO studio_run_events VALUES (?, ?, ?)",
                               [(run_id, sequence, json.dumps({"type": "ToolCalled", "stage": "draft", "state": state}))
                                for sequence in range(1, events + 1)])
        connection.execute("INSERT INTO studio_run_events VALUES (?, ?, ?)", (run_id, events + 1, json.dumps(
            {"type": "ApplicationFinished", "status": "stopped", "transient": "timeout", "reason": "x"})))
    return run_id


def test_retry_decision_does_not_read_the_whole_run_record(tmp_path, monkeypatch):
    import time

    async def exercise():
        host = host_for(tmp_path, Flaky(), SLOW)
        run_id = long_stopped_run(host.repository)

        def whole_record(*args, **kwargs):
            raise AssertionError("the retry decision read every event of the run")
        monkeypatch.setattr(host.repository, "get", whole_record)
        started = time.perf_counter()
        host._schedule_retry(run_id)
        elapsed = time.perf_counter() - started
        assert run_id in host.retries
        assert elapsed < 0.2, f"{elapsed:.2f}s to decide one retry"
        # A timer for a retry the run moved past looks at the end of the record only.
        host.repository._append(run_id, {"type": "RetryCancelled", "reason": "cancelled", "by": "ana"})
        host.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=1)
        await host._retry_when_due(run_id, "2026-01-01T00:00:00+00:00")
        monkeypatch.undo()
        assert host.repository.get(run_id)["status"] == "stopped"
        await host.close()
    asyncio.run(exercise())


def test_retry_rearm_spaces_out_runs_that_are_overdue(tmp_path):
    async def exercise():
        first = host_for(tmp_path, Flaky(*[timeout() for _ in range(3)]), SLOW)
        runs = [await stopped_with_pending_retry(first) for _ in range(3)]
        await first.close()
        later = host_for(tmp_path, Flaky(), SLOW)
        now = datetime.now(timezone.utc) + timedelta(hours=1)
        later.clock = lambda: now
        armed = []
        later._arm_retry = lambda run_id, due, start=None: armed.append((run_id, datetime.fromisoformat(start or due)))
        assert sorted(later.rearm_retries()) == sorted(runs)
        times = sorted(due for _, due in armed)
        assert times[0] <= now + timedelta(seconds=0.01)
        assert all(b - a >= timedelta(seconds=2) for a, b in zip(times, times[1:])), "overdue retries do not all start at once"
        await later.close()
    asyncio.run(exercise())
