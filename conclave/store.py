"""A shared store whose keys and merge rules the caller decides.

The mechanism is the same for every multi-agent system: named state, versioned
writes, merge functions per key, and a snapshot that agents read. What the keys
mean — claims, tasks, a shopping list — is the caller's business and never
appears here.

Writes are the orchestrator's job in this version. Agents receive
`snapshot()`, a copy they can read and cannot change, which is what keeps
concurrent agents from racing on state the caller has not designed for.
"""

import asyncio
import copy

from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    Callable,
    Mapping,
)

from .events import StoreChanged


MergeFunc = Callable[
    [Any, Any],
    Any,
]


def replace_merge(
    current: Any,
    patch: Any,
) -> Any:
    return patch


def append_merge(
    current: Any,
    patch: Any,
) -> Any:
    existing = (
        list(current)
        if isinstance(current, list)
        else []
    )

    incoming = (
        list(patch)
        if isinstance(patch, list)
        else [patch]
    )

    return existing + incoming


@dataclass(frozen=True)
class StoreSchema:
    """The keys a store accepts, and how each one merges a patch."""

    keys: frozenset[str]

    merges: Mapping[
        str,
        MergeFunc,
    ] = field(
        default_factory=dict
    )

    def merge(
        self,
        key: str,
        current: Any,
        patch: Any,
    ) -> Any:
        return self.merges.get(
            key,
            replace_merge,
        )(current, patch)


@dataclass(frozen=True)
class MergeResult:
    version: int
    changed_keys: tuple[str, ...]


class SharedStore:
    def __init__(
        self,
        schema: StoreSchema,
        initial: Mapping[str, Any]
        | None = None,
        on_change: Callable[
            [StoreChanged],
            None,
        ]
        | None = None,
    ):
        self.schema = schema

        self.on_change = on_change

        self._lock = asyncio.Lock()

        self._version = 0

        self._state: dict[str, Any] = {
            key: copy.deepcopy(
                (initial or {}).get(key)
            )
            for key in schema.keys
        }

    @property
    def version(
        self,
    ) -> int:
        return self._version

    def snapshot(
        self,
    ) -> dict[str, Any]:
        """A copy: agents read state, they do not hold a handle on it."""

        return copy.deepcopy(
            self._state
        )

    async def merge(
        self,
        patch: Mapping[str, Any],
        writer: str = "orchestrator",
    ) -> MergeResult:
        unknown = set(
            patch
        ) - set(
            self.schema.keys
        )

        if unknown:
            raise ValueError(
                "Unknown store keys: "
                + ", ".join(
                    sorted(unknown)
                )
            )

        async with self._lock:
            changed: list[str] = []

            for key, value in patch.items():
                merged = self.schema.merge(
                    key,
                    self._state[key],
                    value,
                )

                if merged == self._state[key]:
                    continue

                self._state[key] = merged

                changed.append(key)

            if not changed:
                return MergeResult(
                    version=self._version,
                    changed_keys=(),
                )

            self._version += 1

            event = StoreChanged(
                keys=tuple(changed),
                writer=writer,
                version=self._version,
            )

        if self.on_change is not None:
            self.on_change(event)

        return MergeResult(
            version=event.version,
            changed_keys=event.keys,
        )
