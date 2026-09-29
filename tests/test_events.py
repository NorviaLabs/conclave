"""Tests for the event vocabulary: the framework's contract with callers."""

import dataclasses
import typing

import pytest

from conclave import events
from conclave.events import Event


EXPECTED = {
    "RunStarted",
    "RunFinished",
    "AgentStarted",
    "AgentStep",
    "AgentFinished",
    "ToolCalled",
    "ToolDenied",
    "NoteRecorded",
    "Delegation",
    "StoreChanged",
    "BudgetExceeded",
}


FIELDS = {
    "RunStarted": [
        "run_id",
        "task",
        "config",
    ],
    "RunFinished": [
        "run_id",
        "agent",
        "status",
        "result",
    ],
    "AgentStarted": [
        "run_id",
        "parent_run_id",
        "agent",
        "depth",
        "task",
    ],
    "AgentStep": [
        "run_id",
        "agent",
        "step",
        "reply",
        "action",
    ],
    "AgentFinished": [
        "run_id",
        "parent_run_id",
        "agent",
        "status",
        "result",
    ],
    "ToolCalled": [
        "run_id",
        "agent",
        "tool",
        "arguments",
        "capabilities",
        "approval",
        "observation",
        "output_chars",
        "truncated",
        "ok",
    ],
    "ToolDenied": [
        "run_id",
        "agent",
        "tool",
        "reason",
        "observation",
        "capabilities",
    ],
    "NoteRecorded": [
        "run_id",
        "agent",
        "note",
        "total",
        "result",
    ],
    "Delegation": [
        "run_id",
        "agent",
        "delegated_by",
        "depth",
        "task",
        "status",
        "reason",
    ],
    "StoreChanged": [
        "keys",
        "writer",
        "version",
    ],
    "BudgetExceeded": [
        "run_id",
        "dimension",
        "limit",
        "used",
    ],
}


def test_the_union_holds_exactly_these_events():
    assert {
        event.__name__
        for event in typing.get_args(Event)
    } == EXPECTED


def test_every_event_is_a_frozen_dataclass():
    for name in EXPECTED:
        cls = getattr(events, name)

        assert dataclasses.is_dataclass(cls)

        assert (
            cls.__dataclass_params__.frozen
        ), f"{name} must be frozen"


def test_field_names_are_the_documented_ones():
    for name, expected in FIELDS.items():
        assert [
            field.name
            for field in dataclasses.fields(
                getattr(events, name)
            )
        ] == expected


def test_events_cannot_be_mutated():
    event = events.ToolDenied(
        run_id="r",
        agent="a",
        tool="t",
        reason="approval-refused",
        observation="Refused.",
        capabilities=frozenset(),
    )

    with pytest.raises(
        dataclasses.FrozenInstanceError
    ):
        event.reason = "budget"


def test_events_compare_by_value():
    def build():
        return events.AgentStep(
            run_id="r",
            agent="a",
            step=0,
            reply="{}",
            action="final",
        )

    assert build() == build()
