"""Tests for the run-scoped delegation budget and its recursion guards."""

from conclave import config
from conclave.budget import RunBudget


def test_defaults_follow_config():
    budget = RunBudget()

    assert budget.max_delegations == (
        config.MAX_DELEGATIONS
    )

    assert budget.max_per_agent == (
        config.MAX_DELEGATIONS_PER_AGENT
    )

    assert budget.max_depth == config.MAX_DEPTH

    assert budget.used == 0
    assert budget.per_agent == {}

    assert budget.remaining == (
        config.MAX_DELEGATIONS
    )


def test_reserve_charges_once():
    budget = RunBudget(
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
    budget = RunBudget(
        max_depth=2
    )

    assert budget.may_delegate(1)
    assert not budget.may_delegate(2)


def test_depth_guard_refuses_without_charging():
    budget = RunBudget(
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
    budget = RunBudget()

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
    budget = RunBudget(
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
    budget = RunBudget(
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
    budget = RunBudget(
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
