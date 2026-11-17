"""N1 — the optional `notifications` block: the same rules in Python and in the
browser (studio/scripts/check-notifications.mjs reads the same cases)."""

import copy

import pytest

from notification_support import CASES, definition, slack


@pytest.mark.parametrize("case", CASES["valid"], ids=lambda case: case["name"])
def test_a_valid_setting_is_accepted(case):
    from conclave.application.definition import validate_definition
    from conclave.application.notices import validate_notifications

    assert validate_notifications(copy.deepcopy(case["notifications"])) == case["notifications"]
    value = definition()
    value["notifications"] = copy.deepcopy(case["notifications"])
    validate_definition(value)


@pytest.mark.parametrize("case", CASES["invalid"], ids=lambda case: case["name"])
def test_an_invalid_setting_is_refused_with_its_reason(case):
    from conclave.application.definition import validate_definition
    from conclave.application.notices import validate_notifications

    with pytest.raises(ValueError, match=case["message"]):
        validate_notifications(copy.deepcopy(case["notifications"]))
    value = definition()
    value["notifications"] = copy.deepcopy(case["notifications"])
    with pytest.raises(ValueError, match=case["message"]):
        validate_definition(value)


def test_a_definition_without_the_setting_is_unchanged(tmp_path):
    from conclave.application.definition import validate_definition
    from conclave.application.notices import validate_notifications  # noqa: F401  the module exists
    from conclave.application.workspace import Workspace

    value = definition()
    before = copy.deepcopy(value)
    validate_definition(value)
    assert value == before and "notifications" not in value
    saved = Workspace(tmp_path / "applications.sqlite").save(value)
    assert "notifications" not in saved["definition"]


def test_the_setting_is_saved_and_exported_as_written(tmp_path):
    from conclave.application.notices import validate_notifications  # noqa: F401
    from conclave.application.workspace import Workspace

    workspace = Workspace(tmp_path / "applications.sqlite")
    saved = workspace.save(definition(slack()))
    assert workspace.get(saved["id"])["definition"]["notifications"] == {"waiting": slack()}
