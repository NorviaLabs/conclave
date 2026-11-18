"""Two runs of one application, compared step by step.

`GET /api/runs/compare?a=&b=` says, for every step, what happened in each run
(completed, skipped by routing, failed, not reached…), how its output changed,
how its review went and what it used; and, around the steps, which version each
run used and how the requests differed. The earlier run is A unless the caller
asks for the order it gave.
"""

import asyncio
import copy
import json

import pytest

from conclave import ToolRegistry, Usage
from conclave.application.run_compare import MAX_DIFF_LINES, MAX_RECORD_CHANGES, compare_runs
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost, public_record
from conftest import ScriptedProvider
from test_api import make_api
from test_conditional_steps import plan as routing_plan, provider_for, triage
from test_revision_loop import decision, reviewed
from test_studio_runs import collect, request


def text(value):
    return {"type": "final", "content": "Done", "text": value}


def drafts(*values, usage=None):
    return ScriptedProvider({"writer": [text(value) for value in values]}, usage=usage)


class Runs:
    """Runs kept in one store, each read back the way the API returns it."""

    def __init__(self, tmp_path):
        self.repository = RunRepository(tmp_path / "runs.sqlite")
        self.tools = ToolRegistry(20000)

    def run(self, value, provider, *answers):
        run_id = self.repository.create(copy.deepcopy(value))
        collect(self.repository.execute(run_id, provider, self.tools))
        for output in answers:
            review = next(item for item in self.repository.get(run_id)["reviews"] if item["status"] == "pending")
            self.repository.submit_review(run_id, review["id"], reviewer="priya", output=output)
            collect(self.repository.resume(run_id, provider, self.tools))
        return public_record(self.repository.get(run_id))


def brief(**changes):
    """draft (AI) -> review (person), as a text brief."""
    value = request()
    value.update(changes)
    return value


def step(comparison, step_id):
    return next(item for item in comparison["steps"] if item["id"] == step_id)


def kinds(item):
    return [tag["kind"] for tag in item["tags"]]


# --- Two runs that did the same thing ------------------------------------------------------


def test_compare_two_runs_that_did_the_same_thing(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("The brief"))
    b = runs.run(brief(), drafts("The brief"))
    comparison = compare_runs(a, b)
    assert comparison["application_id"] == "research-brief" and comparison["name"] == "Research brief"
    assert (comparison["runs"]["a"]["id"], comparison["runs"]["b"]["id"]) == (a["id"], b["id"])
    assert comparison["runs"]["a"]["version"] == 3 and comparison["runs"]["a"]["status"] == "waiting_for_human"
    assert comparison["version"] == {"a": 3, "b": 3, "same": True, "changes": None, "link": None}
    assert comparison["request"] == {"task": None, "inputs": []}
    draft = step(comparison, "draft")
    assert draft["presence"] == "both" and draft["kind"] == "ai" and draft["differs"] is False and draft["agent"] == "writer"
    assert draft["status"] == {"a": {"status": "completed", "reason": ""}, "b": {"status": "completed", "reason": ""}}
    assert draft["output"] is None and draft["tags"] == []
    assert step(comparison, "review")["status"]["a"]["status"] == "waiting"
    assert comparison["summary"]["differing"] == 0 and comparison["summary"]["steps"] == 2
    assert comparison["summary"]["text"] == "No step differs between the two runs."


# --- What a step produced --------------------------------------------------------------------


def test_compare_text_output_as_a_line_diff(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("Title\nFirst line\nSecond line\nEnd"))
    b = runs.run(brief(), drafts("Title\nFirst line\nSecond line, revised\nNew line\nEnd"))
    draft = step(compare_runs(a, b), "draft")
    assert draft["differs"] is True and kinds(draft) == ["output_changed"]
    assert draft["output"]["kind"] == "text"
    assert draft["output"]["lines"] == [
        {"kind": "same", "text": "Title"}, {"kind": "same", "text": "First line"},
        {"kind": "del", "text": "Second line"}, {"kind": "add", "text": "Second line, revised"},
        {"kind": "add", "text": "New line"}, {"kind": "same", "text": "End"}]
    assert draft["output"]["more"] == 0


def test_compare_text_diff_counts_the_unchanged_lines_it_leaves_out(tmp_path):
    runs = Runs(tmp_path)
    body = [f"Line {number}" for number in range(30)]
    a = runs.run(brief(), drafts("\n".join(body)))
    b = runs.run(brief(), drafts("\n".join(body[:15] + ["Changed"] + body[16:])))
    lines = step(compare_runs(a, b), "draft")["output"]["lines"]
    assert lines[0] == {"kind": "gap", "text": "", "count": 13}
    assert {"kind": "del", "text": "Line 15"} in lines and {"kind": "add", "text": "Changed"} in lines
    assert lines[-1] == {"kind": "gap", "text": "", "count": 12}


def test_compare_text_diff_is_capped_and_counts_the_rest(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("\n".join(f"A {number}" for number in range(600))))
    b = runs.run(brief(), drafts("\n".join(f"B {number}" for number in range(600))))
    output = step(compare_runs(a, b), "draft")["output"]
    assert len(output["lines"]) == MAX_DIFF_LINES == 400
    assert output["more"] == 1200 - 400


def routing(kind, *extra, status="open"):
    records = [{"id": "t1", "title": "Issue", "body": "Triaged", "status": status, "type": kind, "source_refs": []}, *extra]
    return {"type": "final", "content": "Done", "records": records}


def routed(**conditions):
    return {"version": 1, "application_id": "routing", "revision": 1, "task": "Handle the issue", "inputs": {},
            "definition": routing_plan(**conditions)}


def routing_provider(first, kind="bug"):
    provider = provider_for(kind)
    provider.scripts["triager"] = [first]
    return provider


def test_compare_records_added_removed_and_changed_fields(tmp_path):
    runs = Runs(tmp_path)
    old = {"id": "t9", "title": "Old issue", "body": "Gone", "status": "open", "type": "bug", "source_refs": []}
    new = {"id": "t2", "title": "New issue", "body": "Found", "status": "open", "type": "bug", "source_refs": []}
    a = runs.run(routed(), routing_provider(routing("bug", old)))
    b = runs.run(routed(), routing_provider(routing("regression", new, status="closed")))
    output = step(compare_runs(a, b), "triage")["output"]
    assert output["kind"] == "records" and output["count"] == 3 and output["more"] == 0
    changes = {change["id"]: change for change in output["changes"]}
    assert changes["t2"] == {"id": "t2", "title": "New issue", "kind": "added", "fields": []}
    assert changes["t9"] == {"id": "t9", "title": "Old issue", "kind": "removed", "fields": []}
    assert changes["t1"]["kind"] == "changed"
    assert changes["t1"]["fields"] == [{"field": "status", "label": "Status", "was": "open", "now": "closed"},
                                       {"field": "type", "label": "Type", "was": "bug", "now": "regression"}]


def test_compare_records_ignore_provenance(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(routed(), routing_provider(routing("bug")))
    b = runs.run(routed(), routing_provider(routing("bug")))
    # Each record says which run wrote it; that is not a change in what it says.
    assert step(compare_runs(a, b), "triage")["output"] is None


def test_compare_record_changes_are_capped_and_counted(tmp_path):
    # One output holds at most 200 records; 150 replaced by 150 others is 300 changes.
    runs = Runs(tmp_path)
    many = lambda prefix: [{"id": f"{prefix}{number}", "title": f"R{number}", "body": "Found", "status": "open", "type": "bug",
                            "source_refs": []} for number in range(150)]
    a = runs.run(routed(), routing_provider(routing("bug", *many("a"))))
    b = runs.run(routed(), routing_provider(routing("bug", *many("b"))))
    output = step(compare_runs(a, b), "triage")["output"]
    assert len(output["changes"]) == MAX_RECORD_CHANGES == 200
    assert output["count"] == 300 and output["more"] == 100
    assert output["changes"][0] == {"id": "b0", "title": "R0", "kind": "added", "fields": []}


def test_compare_a_long_field_is_named_without_its_values(tmp_path):
    runs = Runs(tmp_path)
    long = {"id": "t2", "title": "Issue", "body": "Same", "status": "open", "type": "bug", "source_refs": [], "area": "x" * 81}
    a = runs.run(routed(), routing_provider(routing("bug", long)))
    b = runs.run(routed(), routing_provider(routing("bug", {**long, "area": "y" * 81, "body": "Other"})))
    (change,) = step(compare_runs(a, b), "triage")["output"]["changes"]
    assert change["fields"] == [{"field": "body", "label": "Body", "was": None, "now": None},
                                {"field": "area", "label": "Area", "was": None, "now": None}]


# --- What happened to each step -----------------------------------------------------------------


def test_compare_a_step_skipped_by_routing_in_one_run(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(routed(), provider_for("bug"))
    b = runs.run(routed(), provider_for("feature"))
    comparison = compare_runs(a, b)
    reproduce = step(comparison, "reproduce")
    assert reproduce["status"]["a"] == {"status": "completed", "reason": ""}
    assert reproduce["status"]["b"]["status"] == "skipped" and "Triage the issue" in reproduce["status"]["b"]["reason"]
    assert "skipped_only_in_b" in kinds(reproduce) and reproduce["differs"] is True
    proposal = step(comparison, "proposal")
    assert proposal["status"]["a"]["status"] == "skipped" and "skipped_only_in_a" in kinds(proposal)
    assert "skipped in B" in comparison["summary"]["text"]


def test_compare_failed_and_not_reached_steps(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(routed(), provider_for("bug"))
    broken = provider_for("bug")
    broken.scripts["engineer"] = ["not json"] * 6
    b = runs.run(routed(), broken)
    comparison = compare_runs(a, b)
    reproduce = step(comparison, "reproduce")
    assert reproduce["status"]["b"]["status"] in {"failed", "stopped"} and reproduce["status"]["b"]["reason"]
    assert "status_changed" in kinds(reproduce)
    assert step(comparison, "summary")["status"]["b"]["status"] == "not_reached"
    assert "not_reached_in_b" in kinds(step(comparison, "summary"))


def test_compare_a_run_still_going_is_compared_as_it_stands(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("The brief"))
    b = copy.deepcopy(a)
    b.update(id="later", status="running", created_at="2999-01-01T00:00:00+00:00")
    b["events"] = [item for item in b["events"] if item["event"]["type"] in {"ApplicationStarted", "StageStarted"}]
    comparison = compare_runs(a, b)
    assert comparison["runs"]["b"]["in_progress"] is True and comparison["runs"]["a"]["in_progress"] is True
    assert step(comparison, "draft")["status"]["b"]["status"] == "running"
    assert step(comparison, "review")["status"]["b"]["status"] == "not_reached"


# --- Reviews ---------------------------------------------------------------------------------


def test_compare_review_decisions_rounds_and_comments(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(reviewed(), drafts("One"), decision("approved", "Looks good."))
    b = runs.run(reviewed(), drafts("One", "Two"), decision("changes_requested", "Cite the price list."), decision("approved", "Good now."))
    comparison = compare_runs(a, b)
    review = step(comparison, "review")
    assert review["kind"] == "human"
    assert review["review"]["a"] == {"decision": "approved", "reviewers": ["priya"], "rounds": 1, "changes_requested": False,
                                     "comment": "Looks good."}
    assert review["review"]["b"] == {"decision": "approved", "reviewers": ["priya"], "rounds": 2, "changes_requested": True,
                                     "comment": "Good now.", "first_comment": "Cite the price list."}
    assert "review_differs" in kinds(review) and review["differs"] is True
    assert "review" in comparison["summary"]["text"]


def test_compare_review_comment_is_shortened(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(reviewed(), drafts("One"), decision("approved", "x" * 1000))
    b = runs.run(reviewed(), drafts("One"), decision("approved", "y"))
    comment = step(compare_runs(a, b), "review")["review"]["a"]["comment"]
    assert len(comment) == 280 and comment.endswith("…")


# --- Usage ----------------------------------------------------------------------------------


def test_compare_usage_per_step_and_the_change_in_tokens(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("The brief", usage=Usage(input_tokens=100, output_tokens=0)))
    b = runs.run(brief(), drafts("The brief", usage=Usage(input_tokens=130, output_tokens=0)))
    comparison = compare_runs(a, b)
    usage = step(comparison, "draft")["usage"]
    assert (usage["a"]["tokens"], usage["b"]["tokens"], usage["a"]["model_calls"]) == (100, 130, 1)
    assert usage["delta"]["tokens"] == 30 and usage["delta"]["tokens_percent"] == 30 and usage["delta"]["model_calls"] == 0
    draft = step(comparison, "draft")
    assert {"kind": "usage", "text": "Usage +30%"} in draft["tags"]
    assert draft["differs"] is False, "using more is not a different outcome"
    assert comparison["summary"]["token_change"] == 30
    assert comparison["summary"]["text"] == "No step differs between the two runs. B used 30% more tokens."


def test_compare_usage_of_a_run_that_did_not_record_it(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("The brief", usage=Usage(input_tokens=100, output_tokens=0)))
    b = runs.run(brief(), drafts("The brief", usage=Usage(input_tokens=130, output_tokens=0)))
    a["usage"] = {**a["usage"], "recorded": False, "steps": {}}
    comparison = compare_runs(a, b)
    usage = step(comparison, "draft")["usage"]
    assert usage["a"] is None and usage["b"]["tokens"] == 130 and usage["delta"] is None
    assert comparison["summary"]["token_change"] is None


# --- Around the steps: versions and requests -------------------------------------------------


def test_compare_runs_of_different_versions(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("The brief"))
    later = brief(revision=4)
    later["definition"]["workflow"].insert(1, {"id": "check", "name": "Check the brief", "agent": "writer", "task": "Check", "output": "brief"})
    b = runs.run(later, drafts("The brief", "Checked"))
    comparison = compare_runs(a, b)
    version = comparison["version"]
    assert (version["a"], version["b"], version["same"]) == (3, 4, False)
    assert version["changes"]["count"] >= 1 and any(area["area"] == "Steps" for area in version["changes"]["areas"])
    assert version["link"] == "/api/applications/research-brief/versions/compare?from=3&to=4"
    check = step(comparison, "check")
    assert check["presence"] == "only_b" and check["status"]["a"]["status"] == "not_in_version"
    assert kinds(check) == ["only_in_b"] and check["names"] == {"a": None, "b": "Check the brief"}
    assert "ran only in B" in comparison["summary"]["text"]


def test_compare_a_renamed_step_keeps_both_names(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("The brief"))
    renamed = brief(revision=4)
    renamed["definition"]["workflow"][0]["name"] = "Write the brief"
    b = runs.run(renamed, drafts("The brief"))
    draft = step(compare_runs(a, b), "draft")
    assert draft["names"] == {"a": "draft", "b": "Write the brief"} and draft["name"] == "Write the brief"


def test_compare_requests_that_differed(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("The brief"))
    b = runs.run(brief(task="Review the new evidence", inputs={"sources": "Other evidence", "count": "3"}), drafts("The brief"))
    assert compare_runs(a, b)["request"] == {
        "task": {"a": "Review the supplied evidence", "b": "Review the new evidence"},
        "inputs": [{"key": "sources", "label": "Sources", "a": "Real supplied evidence", "b": "Other evidence"}]}


# --- Order and what cannot be compared -----------------------------------------------------------


def test_compare_puts_the_earlier_run_first_unless_asked(tmp_path):
    runs = Runs(tmp_path)
    earlier = runs.run(brief(), drafts("One"))
    later = runs.run(brief(), drafts("Two"))
    assert compare_runs(later, earlier)["runs"]["a"]["id"] == earlier["id"]
    swapped = compare_runs(later, earlier, order="given")
    assert swapped["runs"]["a"]["id"] == later["id"] and swapped["runs"]["b"]["id"] == earlier["id"]
    with pytest.raises(ValueError):
        compare_runs(earlier, later, order="sideways")


def test_compare_refuses_runs_of_different_applications(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("One"))
    b = runs.run(routed(), provider_for("bug"))
    with pytest.raises(ValueError, match="different applications"):
        compare_runs(a, b)
    with pytest.raises(ValueError, match="itself"):
        compare_runs(a, a)


def test_compare_never_carries_checkpoints(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("One"))
    b = runs.run(brief(), drafts("Two"))
    assert "checkpoint" not in json.dumps(compare_runs(a, b))


# --- Through the host and REST -------------------------------------------------------------------


def test_compare_through_the_host_checks_each_run(tmp_path):
    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), drafts("One", "Two"), ToolRegistry(20000), reviewer="operator")
        first = host.repository.create(brief())
        second = host.repository.create(brief())
        status, comparison = await host.handle("compare", {"a": second, "b": first})
        assert status == 200 and comparison["runs"]["a"]["id"] == first
        status, given = await host.handle("compare", {"a": second, "b": first, "order": "given"})
        assert given["runs"]["a"]["id"] == second
        with pytest.raises(KeyError):
            await host.handle("compare", {"a": first, "b": "missing"})
        checked = []

        def owner(run_id, principal, *, reviewing=False, permission="view"):
            checked.append((run_id, reviewing))
            if run_id == second:
                raise ValueError("Integration run belongs to another principal")
        host._require_run_owner = owner
        with pytest.raises(ValueError, match="another principal"):
            await host.handle("compare", {"a": first, "b": second})
        assert checked == [(first, False), (second, False)], "a reviewer's access is not enough"
        for bad in ({"a": first}, {"a": first, "b": second, "extra": 1}, {"a": first, "b": 2}):
            with pytest.raises(ValueError):
                await host.handle("compare", bad)
        _, runtime = await host.handle("runtime", {})
        assert runtime["features"]["run_compare"] is True
        await host.close()
    asyncio.run(exercise())


def test_compare_rest_route(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        first = api.host.repository.create(brief())
        second = api.host.repository.create(brief())
        status, comparison = await api.dispatch("GET", f"/api/runs/compare?a={second}&b={first}")
        assert status == 200 and comparison["runs"]["a"]["id"] == first
        status, given = await api.dispatch("GET", f"/api/runs/compare?a={second}&b={first}&order=given")
        assert status == 200 and given["runs"]["a"]["id"] == second
        for query in (f"a={first}", f"a={first}&b={second}&b={first}", f"a={first}&b={second}&order=up"):
            assert (await api.dispatch("GET", f"/api/runs/compare?{query}"))[0] == 400
        assert (await api.dispatch("GET", f"/api/runs/compare?a={first}&b=missing"))[0] == 404
        assert (await api.dispatch("POST", f"/api/runs/compare?a={first}&b={second}", {}))[0] in {404, 405}
        await api.host.close()
    asyncio.run(exercise())


def test_compare_through_the_hosted_transport(tmp_path, monkeypatch):
    from conclave.application.principal import Principal
    from conclave.asgi import application
    from test_asgi import call, make_services

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, built = make_services(tmp_path)
    app = application(apis=services, fallback=Principal("ada", "acme"))

    async def exercise():
        assert (await call(app, "GET", "/api/runs/compare?a=one&b=two"))[0] == 404
        repository = built["acme"].host.repository
        first, second = repository.create(brief()), repository.create(brief())
        status, _, body = await call(app, "GET", f"/api/runs/compare?a={second}&b={first}")
        assert status == 200 and json.loads(body)["runs"]["a"]["id"] == first
        assert (await call(app, "GET", f"/api/runs/compare?a={first}"))[0] == 400
        await app.close()
    asyncio.run(exercise())


# --- Bounded work ------------------------------------------------------------------------------------


def test_compare_a_long_repetitive_diff_is_bounded_in_time():
    """Many repeated short lines used to make the line diff quadratic, freezing the host."""
    import subprocess
    import sys
    import time
    program = ("import json, time\n"
               "from conclave.application.run_compare import _text_diff\n"
               "a = '\\n'.join('a' if i % 2 else 'b' for i in range(100000))\n"
               "b = '\\n'.join('b' if i % 2 else 'a' for i in range(100000))\n"
               "start = time.perf_counter(); output = _text_diff(a, b)\n"
               "print(json.dumps({'seconds': time.perf_counter() - start, 'coarse': output['coarse'],\n"
               "                  'lines': len(output['lines']), 'more': output['more']}))\n")
    try:
        done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=30, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail("comparing two long repetitive outputs did not finish in 30 seconds")
    result = json.loads(done.stdout)
    assert result["seconds"] < 2 and result["coarse"] is True
    assert result["lines"] == MAX_DIFF_LINES and result["more"] == 200000 - MAX_DIFF_LINES


def test_compare_a_mid_sized_repetitive_diff_stays_fast():
    import time
    from conclave.application.run_compare import _text_diff
    a = "\n".join("a" if index % 2 else "b" for index in range(6000))
    b = "\n".join("b" if index % 2 else "a" for index in range(6000))
    start = time.perf_counter()
    output = _text_diff(a, b)
    assert time.perf_counter() - start < 0.5
    assert output["coarse"] is True


def test_compare_a_long_text_with_one_change_is_still_compared_line_by_line():
    from conclave.application.run_compare import _text_diff
    lines = [f"Line {number}" for number in range(20000)]
    output = _text_diff("\n".join(lines), "\n".join(lines[:9000] + ["Changed"] + lines[9001:]))
    assert output["coarse"] is False
    assert output["lines"] == [{"kind": "gap", "text": "", "count": 8998}, {"kind": "same", "text": "Line 8998"},
                               {"kind": "same", "text": "Line 8999"}, {"kind": "del", "text": "Line 9000"},
                               {"kind": "add", "text": "Changed"}, {"kind": "same", "text": "Line 9001"},
                               {"kind": "same", "text": "Line 9002"}, {"kind": "gap", "text": "", "count": 10997}]


def test_compare_a_short_diff_is_not_coarse(tmp_path):
    runs = Runs(tmp_path)
    a = runs.run(brief(), drafts("One\nTwo"))
    b = runs.run(brief(), drafts("One\nThree"))
    assert step(compare_runs(a, b), "draft")["output"]["coarse"] is False


def test_compare_cannot_reach_another_tenants_runs(tmp_path, monkeypatch):
    from conclave.application.principal import Principal
    from conclave.asgi import application
    from test_asgi import call, make_services

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, built = make_services(tmp_path)
    tenant = {"value": "acme"}
    app = application(apis=services, token="secret", principal_for=lambda scope, headers: Principal("ada", tenant["value"]))
    auth = [("authorization", "Bearer secret")]

    async def exercise():
        assert (await call(app, headers=auth))[0] == 200
        repository = built["acme"].host.repository
        first, second = repository.create(brief()), repository.create(brief())
        assert (await call(app, "GET", f"/api/runs/compare?a={first}&b={second}", headers=auth))[0] == 200
        tenant["value"] = "beta"
        status, _, body = await call(app, "GET", f"/api/runs/compare?a={first}&b={second}", headers=auth)
        assert status == 404 and first not in body.decode()
        await app.close()
    asyncio.run(exercise())


def test_compare_does_not_hold_up_the_host(tmp_path, monkeypatch):
    import time
    from conclave.application import service

    def slow(*records, order="time"):
        time.sleep(0.5)
        return {"compared": [record["id"] for record in records], "order": order}
    monkeypatch.setattr(service, "compare_runs", slow)

    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), drafts("One"), ToolRegistry(20000), reviewer="operator")
        first, second = host.repository.create(brief()), host.repository.create(brief())
        start = time.perf_counter()
        comparing = asyncio.create_task(host.handle("compare", {"a": first, "b": second}))
        await asyncio.sleep(0.05)
        status, _ = await host.handle("runtime", {})
        answered = time.perf_counter() - start
        assert status == 200 and answered < 0.3, f"a request made during a comparison answered after {answered:.2f}s"
        assert (await comparing)[1] == {"compared": [first, second], "order": "time"}
        await host.close()
    asyncio.run(exercise())


def synthetic(run_id, created, texts):
    """A published record of 24 text steps, without running them."""
    value = brief()
    value["definition"]["workflow"] = [{"id": f"s{index}", "name": f"Step {index}", "agent": "writer", "task": "Write", "output": "brief"}
                                       for index in range(len(texts))]
    events = [{"sequence": 0, "event": {"type": "ApplicationStarted", "usage": 1}}]
    for index, output in enumerate(texts):
        events += [{"sequence": len(events), "event": {"type": "StageStarted", "stage": f"s{index}", "agent": "writer"}},
                   {"sequence": len(events) + 1, "event": {"type": "ApplicationStateChanged", "stage": f"s{index}", "patch": output}}]
    events.append({"sequence": len(events), "event": {"type": "ApplicationFinished", "status": "completed", "metrics": {}}})
    return public_record({"id": run_id, "status": "completed", "created_at": created, "request": value, "reviews": [], "events": events})


def timed(function, *arguments):
    """The result, and the best of five timings: a busy machine slows a run, not the work."""
    import time
    function(*arguments)
    best = None
    for _ in range(5):
        start = time.perf_counter()
        result = function(*arguments)
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return result, best


def test_compare_diff_work_is_bounded_across_the_whole_comparison():
    from conclave.application.run_compare import MAX_COMPARISON_WORK, MAX_DIFF_WORK
    first = ["\n".join("a" if index % 2 else "b" for index in range(500))] * 24
    second = ["\n".join("b" if index % 2 else "a" for index in range(500))] * 24
    comparison, seconds = timed(compare_runs, synthetic("a", "2026-01-01", first), synthetic("b", "2026-01-02", second))
    # Without the shared bound these take over 0.55 s; with it about 0.1 s, with room for a busy machine.
    assert seconds < 0.4, f"24 hard diffs took {seconds:.2f}s"
    coarse = [item["output"]["coarse"] for item in comparison["steps"]]
    weighed = MAX_COMPARISON_WORK // MAX_DIFF_WORK
    assert coarse == [False] * weighed + [True] * (24 - weighed)


def test_compare_unchanged_lines_are_counted_not_listed():
    texts = ["\n".join(f"line {index}" for index in range(5000))] * 24
    changed = [text.replace("line 2500\n", "changed\n") for text in texts]
    comparison, seconds = timed(compare_runs, synthetic("a", "2026-01-01", texts), synthetic("b", "2026-01-02", changed))
    # About 0.05 s; materialising every unchanged line, as before, took over 0.25 s.
    assert seconds < 0.15, f"24 long outputs with one change each took {seconds:.2f}s"
    assert comparison["steps"][0]["output"]["lines"][0] == {"kind": "gap", "text": "", "count": 2498}


def test_compare_a_very_long_line_is_shortened():
    from conclave.application.run_compare import MAX_LINE_CHARS, _text_diff
    output = _text_diff("same\n" + "a" * 150000, "same\n" + "b" * 150000)
    longest = max(len(line["text"]) for line in output["lines"])
    assert longest == MAX_LINE_CHARS == 1000
    assert output["lines"][1] == {"kind": "del", "text": "a" * 999 + "…", "shortened": True}
    assert "shortened" not in output["lines"][0]
