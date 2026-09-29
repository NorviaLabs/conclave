"""Tests for the shared store: schema, merges, versions and isolation."""

import asyncio

import pytest

from conclave import (
    SharedStore,
    StoreSchema,
    append_merge,
    replace_merge,
)


SCHEMA = StoreSchema(
    keys=frozenset(
        {
            "draft",
            "notes",
        }
    ),
    merges={
        "notes": append_merge,
    },
)


def test_replace_merge_takes_the_patch():
    assert replace_merge("old", "new") == "new"


def test_append_merge_extends_lists():
    assert append_merge(
        ["a"],
        ["b"],
    ) == ["a", "b"]


def test_append_merge_wraps_scalars():
    assert append_merge(["a"], "b") == [
        "a",
        "b",
    ]


def test_append_merge_starts_from_nothing():
    assert append_merge(None, "b") == ["b"]


def test_schema_merge_defaults_to_replace():
    assert SCHEMA.merge("draft", "old", "new") == (
        "new"
    )


def test_schema_merge_uses_the_declared_function():
    assert SCHEMA.merge(
        "notes",
        ["a"],
        ["b"],
    ) == ["a", "b"]


def test_store_starts_from_the_initial_state():
    store = SharedStore(
        SCHEMA,
        {
            "draft": "first",
            "notes": ["a"],
        },
    )

    assert store.snapshot() == {
        "draft": "first",
        "notes": ["a"],
    }

    assert store.version == 0


def test_store_initial_state_is_copied():
    initial = {"draft": ["x"]}

    store = SharedStore(SCHEMA, initial)

    initial["draft"].append("y")

    assert store.snapshot()["draft"] == ["x"]


def test_store_rejects_unknown_keys():
    store = SharedStore(SCHEMA)

    with pytest.raises(
        ValueError,
        match="Unknown store keys: hypothesis",
    ):
        asyncio.run(
            store.merge({"hypothesis": []})
        )


def test_merge_returns_the_changed_keys():
    store = SharedStore(SCHEMA)

    result = asyncio.run(
        store.merge(
            {
                "draft": "text",
                "notes": ["a"],
            },
            writer="coordinator",
        )
    )

    assert sorted(result.changed_keys) == [
        "draft",
        "notes",
    ]

    assert result.version == 1
    assert store.version == 1


def test_unchanged_patch_does_not_bump_the_version():
    store = SharedStore(
        SCHEMA,
        {"draft": "same"},
    )

    result = asyncio.run(
        store.merge({"draft": "same"})
    )

    assert result.changed_keys == ()
    assert result.version == 0
    assert store.version == 0


def test_snapshot_is_a_copy():
    store = SharedStore(SCHEMA)

    snapshot = store.snapshot()

    snapshot["draft"] = "mutated"

    assert store.snapshot()["draft"] is None


def test_on_change_reports_the_write():
    seen = []

    store = SharedStore(
        SCHEMA,
        on_change=seen.append,
    )

    asyncio.run(
        store.merge(
            {"draft": "text"},
            writer="coordinator",
        )
    )

    assert len(seen) == 1
    assert seen[0].keys == ("draft",)
    assert seen[0].writer == "coordinator"
    assert seen[0].version == 1


def test_on_change_is_not_called_without_a_change():
    seen = []

    store = SharedStore(
        SCHEMA,
        {"draft": "same"},
        on_change=seen.append,
    )

    asyncio.run(
        store.merge({"draft": "same"})
    )

    assert seen == []


def test_concurrent_merges_do_not_lose_a_write():
    store = SharedStore(SCHEMA)

    async def main():
        await asyncio.gather(
            *(
                store.merge(
                    {"notes": [letter]},
                    writer="coordinator",
                )
                for letter in "abcd"
            )
        )

    asyncio.run(main())

    assert store.snapshot()["notes"] == [
        "a",
        "b",
        "c",
        "d",
    ]

    assert store.version == 4
