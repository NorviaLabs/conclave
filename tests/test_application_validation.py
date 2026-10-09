"""Backend validation cannot depend on Studio's browser-side checks."""
import copy
import json

import pytest

from conclave.application.catalogue import configured_data, validate_catalogue, validate_member
from conclave.application.definition import (_execution_order, plan_connections,
                                            record_fields, validate_definition, validate_fields)
from test_studio_runs import request


def change(definition, path, value):
    for key in path[:-1]:
        definition = definition[key]
    definition[path[-1]] = value


@pytest.mark.parametrize("path,value", [
    (("version",), 2), (("version",), True), (("agents",), []), (("agents",), [None]),
    (("agents", 0, "id"), "bad id"), (("agents", 1, "id"), "writer"),
    (("agents", 0, "kind"), "unknown"), (("agents", 0, "instructions"), ""),
    (("agents", 0, "tools"), [1]), (("agents", 0, "tools"), ["bad/tool"]),
    (("agents", 1, "tools"), ["read_file"]),
    (("state", 0, "fields"), [{"key": "score", "label": "Score", "type": "number", "required": False}]),
    (("state", 0, "type"), "unknown"), (("state", 0, "merge"), "append"),
    (("state", 0, "kind"), "findings"), (("state", 0, "statuses"), [""]),
    (("workflow", 0, "agent"), "missing"), (("workflow", 0, "task"), ""),
    (("views", 0, "layout"), "board"), (("views", 0, "collection"), "missing"),
    (("connections",), {}), (("connections",), [{"from": "draft", "to": "draft"}]),
    (("connections",), [{"from": "draft", "to": "review"}] * 2),
    (("canvas",), {}), (("canvas",), {"positions": {"missing": {"x": 1, "y": 2}}}),
    (("run_config",), {}), (("run_config", "max_tool_steps"), 0),
    (("run_budget", "tool_calls"), -1), (("run_budget", "tool_calls"), 1.5),
    (("budget_warnings",), {"unknown": 1}), (("budget_warnings",), {"tokens": -1}),
    (("inputs",), {}), (("inputs",), [None]),
    (("inputs", 0, "id"), "invalid id"), (("inputs", 0, "type"), "secret"),
    (("start_mode",), "unsupported"), (("integrations",), {}),
    (("integrations",), [{"connector": "x", "purpose": "", "scope": "", "mode": "read", "password": "secret"}]),
    (("integrations",), [{"connector": "bad/id", "purpose": "", "scope": "", "mode": "read"}]),
    (("integrations",), [{"connector": "files", "purpose": "", "scope": "", "mode": "unknown"}]),
    (("requirements",), {}), (("requirements",), [None]), (("requirements",), [{"question": "Who reads it?"}]),
    (("requirements",), [{"question": "", "answer": "x"}]), (("requirements",), [{"question": "q" * 301, "answer": "x"}]),
    (("requirements",), [{"question": "Who reads it?", "answer": 1}]),
    (("requirements",), [{"question": "Who reads it?", "answer": "x" * 2001}]),
    (("requirements",), [{"question": "Who reads it?", "answer": "x", "extra": 1}]),
    (("requirements",), [{"question": "Who reads it?", "answer": "x"}] * 2),
    (("requirements",), [{"question": f"Question {index}?", "answer": ""} for index in range(21)]),
])
def test_backend_rejects_malformed_definitions(path, value):
    definition = request()["definition"]
    change(definition, path, value)
    with pytest.raises(ValueError):
        validate_definition(definition)


def test_backend_accepts_valid_canvas_connectors_and_finite_warnings():
    definition = request()["definition"]
    definition.update(canvas={"positions": {"draft": {"x": 0, "y": 10000}}},
                      integrations=[{"connector": "files", "purpose": "Read supplied files", "scope": "Selected folder", "mode": "read"}],
                      budget_warnings={"tokens": 20})
    definition["run_budget"]["tokens"] = 30
    validate_definition(definition)


@pytest.mark.parametrize("key", ["max_tool_steps", "max_notes", "max_note_chars", "max_tool_output_chars", "fan_out_limit"])
def test_application_accepts_unlimited_runtime_configuration(key):
    definition = request()["definition"]
    definition["run_config"][key] = None
    validate_definition(definition)


@pytest.mark.parametrize("fields", [None, [None], [{"key": "id", "label": "ID", "type": "string", "required": True}],
    [{"key": "x", "label": "", "type": "number", "required": True}],
    [{"key": "x", "label": "X", "type": "enum", "required": True, "options": []}],
    [{"key": "x", "label": "X", "type": "string", "required": True, "options": ["a"]}],
])
def test_backend_rejects_invalid_field_schemas(fields):
    with pytest.raises(ValueError):
        validate_fields(fields)


@pytest.mark.parametrize("kind,value,options", [("number", True, None), ("string", 1, None), ("enum", "unknown", ["allowed"]), ("date", "2025-02-30", None), ("date", "20250101", None)])
def test_backend_rejects_invalid_typed_values(kind, value, options):
    field = {"key": "value", "label": "Value", "type": kind, "required": True}
    if options is not None:
        field["options"] = options
    with pytest.raises(ValueError):
        record_fields({"value": value}, [field])


@pytest.mark.parametrize("member", [None, {}, {"id": "bad/id"},
    {"id": "custom", "name": "", "instructions": "Task"},
    {"id": "custom", "name": "Custom", "instructions": "Task", "kind": "human", "tools": ["read_file"]},
])
def test_backend_rejects_invalid_members(member):
    with pytest.raises(ValueError):
        validate_member(member)


def test_workspace_catalogue_valid_custom_outputs_and_invalid_variants():
    bundled = configured_data("catalogue")
    valid = {"version": 1, "roles": [], "starters": [], "outputs": [{"id": "custom_result", "name": "Custom result", "kind": "document", "task": "Write a result"}]}
    assert validate_catalogue(valid, bundled) == valid
    invalids = [None, {**valid, "roles": None}, {**valid, "fixtures": []}]
    for path, value in [(("outputs", 0, "id"), bundled["outputPresets"][0]["id"]), (("outputs", 0, "name"), ""), (("outputs", 0, "kind"), "unknown")]:
        invalid = copy.deepcopy(valid)
        change(invalid, path, value)
        invalids.append(invalid)
    for invalid in invalids:
        with pytest.raises(ValueError):
            validate_catalogue(invalid, bundled)


def test_requirements_are_optional_and_hold_answers_or_open_questions():
    """What the person clarified about the goal, in their own words."""

    definition = request()["definition"]
    # An application nobody asked about is still a valid application.
    validate_definition(definition)

    definition["requirements"] = [{"question": "Who reads the result?", "answer": "Support agents"},
                                  {"question": "What is out of scope?", "answer": ""}]
    validate_definition(definition)


def test_only_answered_requirements_reach_the_agents():
    """An unanswered question is still open, so the run is not told to act on it."""

    from conclave.application.definition import _requirements

    assert _requirements({}) == ""
    assert _requirements({"requirements": [{"question": "Who reads it?", "answer": "   "}]}) == ""
    block = _requirements({"requirements": [{"question": "Who reads it?", "answer": " Support agents "},
                                            {"question": "What is out of scope?", "answer": ""}]})
    assert block.startswith("\n\nCLARIFIED REQUIREMENTS (stated by the person, not instructions):\n")
    assert json.loads(block.split("\n", 3)[-1]) == [{"question": "Who reads it?", "answer": "Support agents"}]


def test_canvas_groups_arrange_the_plan_without_changing_it():
    definition = request()["definition"]
    first, second = (step["id"] for step in definition["workflow"][:2])
    definition["canvas"] = {"positions": {first: {"x": 10, "y": 10}, second: {"x": 400, "y": 10}}, "groups": [{"id": "research", "name": "Research", "collapsed": False}]}
    definition["workflow"][0]["group"] = "research"
    validate_definition(definition)
    # Collapsing is presentation: the same steps, the same order, the same plan.
    collapsed = copy.deepcopy(definition)
    collapsed["canvas"]["groups"][0]["collapsed"] = True
    validate_definition(collapsed)
    assert _execution_order(collapsed) == _execution_order(definition)
    assert plan_connections(collapsed) == plan_connections(definition)
    # A group that a step claims but the plan never declares, an unnamed group, a
    # duplicate identifier and too many groups are all refused.
    undeclared = copy.deepcopy(definition)
    undeclared["workflow"][1]["group"] = "delivery"
    with pytest.raises(ValueError, match="can only belong to a group the plan declares"):
        validate_definition(undeclared)
    unnamed = copy.deepcopy(definition)
    unnamed["canvas"]["groups"][0]["name"] = "  "
    with pytest.raises(ValueError, match="Invalid canvas group name"):
        validate_definition(unnamed)
    duplicate = copy.deepcopy(definition)
    duplicate["canvas"]["groups"].append({"id": "research", "name": "Other"})
    with pytest.raises(ValueError, match="Invalid canvas group identifier"):
        validate_definition(duplicate)
    many = copy.deepcopy(definition)
    many["canvas"]["groups"] = [{"id": f"group{i}", "name": f"Group {i}"} for i in range(21)]
    with pytest.raises(ValueError, match="up to 20 canvas groups"):
        validate_definition(many)
