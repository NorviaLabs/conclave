"""Tests for the run-scoped delegation budget and its recursion guards."""

import pytest

from conftest import make_budget

from conclave.budget import (
    DIMENSION_LABELS,
    RunBudget,
)


def test_the_caps_are_whatever_the_caller_said():
    """There is nothing to inherit: a budget is only what it was told."""

    budget = make_budget(
        max_delegations=7,
        max_per_agent=2,
        max_depth=3,
    )

    assert budget.max_delegations == 7
    assert budget.max_per_agent == 2
    assert budget.max_depth == 3

    assert budget.used == 0
    assert budget.per_agent == {}
    assert budget.remaining == 7


def test_a_budget_without_caps_is_a_type_error():
    """No silent shape: the caller must say how big the system is."""

    with pytest.raises(TypeError):
        RunBudget()

    with pytest.raises(TypeError):
        RunBudget(max_delegations=1)


def test_reserve_charges_once():
    budget = make_budget(
        max_delegations=3
    )

    assert (
        budget.reserve(
            "repo-researcher",
            1,
            (),
        )
        is None
    )

    assert (
        budget.reserve(
            "web-researcher",
            1,
            (),
        )
        is None
    )

    assert budget.used == 2
    assert budget.remaining == 1

    assert budget.per_agent == {
        "repo-researcher": 1,
        "web-researcher": 1,
    }


def test_may_delegate_only_below_max_depth():
    budget = make_budget(
        max_depth=2
    )

    assert budget.may_delegate(1)
    assert not budget.may_delegate(2)


def test_depth_guard_refuses_without_charging():
    budget = make_budget(
        max_depth=2
    )

    refusal = budget.reserve(
        "architect",
        3,
        ("repo-researcher", "critic"),
    )

    assert "maximum depth is 2" in refusal
    assert budget.used == 0
    assert budget.remaining == 15


def test_cycle_guard_names_the_ancestry():
    budget = make_budget()

    refusal = budget.reserve(
        "repo-researcher",
        2,
        (
            "repo-researcher",
            "architect",
        ),
    )

    assert "cycle detected" in refusal

    assert (
        "repo-researcher -> architect"
        in refusal
    )

    assert budget.used == 0


def test_total_guard():
    budget = make_budget(
        max_delegations=1,
        max_per_agent=10,
    )

    assert (
        budget.reserve(
            "critic",
            1,
            (),
        )
        is None
    )

    refusal = budget.reserve(
        "architect",
        1,
        (),
    )

    assert "already used 1/1" in refusal
    assert budget.remaining == 0


def test_per_agent_guard():
    budget = make_budget(
        max_delegations=10,
        max_per_agent=2,
    )

    for _ in range(2):
        assert (
            budget.reserve(
                "critic",
                1,
                (),
            )
            is None
        )

    refusal = budget.reserve(
        "critic",
        1,
        (),
    )

    assert "critic already ran 2/2" in refusal
    assert budget.used == 2
    assert budget.remaining == 8


def test_empty_budget_cannot_go_negative():
    budget = make_budget(
        max_delegations=0
    )

    assert budget.remaining == 0

    refusal = budget.reserve(
        "critic",
        1,
        (),
    )

    assert "already used 0/0" in refusal
    assert budget.used == 0
    assert budget.remaining == 0


# =============================================================================
# Dimensions other than delegations
# =============================================================================


def test_dimensions_are_labelled_for_humans():
    assert DIMENSION_LABELS == {
        "tool_calls": "tool-call",
        "wall_clock_seconds": "wall-clock",
        "tokens": "token",
        "cost_usd": "cost",
    }


def test_an_unknown_dimension_is_a_bug_not_a_budget():
    budget = make_budget()

    with pytest.raises(
        ValueError,
        match="Unknown budget dimension: vibes",
    ):
        budget.limit("vibes")

    with pytest.raises(
        ValueError,
        match="Unknown budget dimension: vibes",
    ):
        budget.refusal("vibes")


def test_an_unset_dimension_is_unbounded():
    budget = make_budget()

    assert budget.limit("tokens") is None
    assert budget.remaining_on("tokens") is None

    assert budget.reserve_dimension("tokens") is None

    assert budget.spent_on("tokens") == 1


def test_a_set_dimension_reports_what_is_left():
    budget = make_budget(tool_calls=3)

    assert budget.limit("tool_calls") == 3
    assert budget.remaining_on("tool_calls") == 3

    budget.reserve_dimension("tool_calls")

    assert budget.remaining_on("tool_calls") == 2


def test_remaining_never_goes_negative():
    budget = make_budget(cost_usd=1.0)

    budget.settle("cost_usd", 5.0)

    assert budget.remaining_on("cost_usd") == 0.0


def test_reserve_dimension_refuses_once_the_limit_is_reached():
    budget = make_budget(tool_calls=1)

    assert budget.reserve_dimension("tool_calls") is None

    refusal = budget.reserve_dimension("tool_calls")

    assert "tool-call budget is spent" in refusal
    assert "(1/1)" in refusal

    # A refusal does not charge for work that never ran.
    assert budget.spent_on("tool_calls") == 1
    assert budget.exceeded("tool_calls")


def test_settle_reports_an_overspend():
    budget = make_budget(tokens=10)

    assert budget.settle("tokens", 4) is None
    assert not budget.exceeded("tokens")

    refusal = budget.settle("tokens", 8)

    assert "token budget is spent" in refusal
    assert "(12/10)" in refusal
    assert budget.spent_on("tokens") == 12


def test_settle_ignores_unmeasurable_work():
    budget = make_budget(tokens=10)

    assert budget.settle("tokens", None) is None
    assert budget.settle("tokens", 0) is None
    assert budget.spent_on("tokens") == 0


def test_an_unbounded_dimension_can_never_be_exceeded():
    budget = make_budget()

    budget.settle("wall_clock_seconds", 10_000)

    assert budget.spent_on(
        "wall_clock_seconds"
    ) == 10_000

    assert not budget.exceeded(
        "wall_clock_seconds"
    )
