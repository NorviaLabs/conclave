"""The round loop: this application's control flow over core primitives.

Rounds, a coordinator that decides what deserves investigating next, and a
round that ends by merging what came back into shared state. All of it is
plain Python over the library's pieces — `ToolAgentRuntime.stream` for one
agent, `fan_out` for one round, `SharedStore.merge` for state — with no
topology engine involved.
"""

import json

from functools import partial
from typing import Any

from conclave import (
    AgentFinished,
    AgentResult,
    AgentSpec,
    Approver,
    PrivateState,
    RunBudget,
    RunConfig,
    ToolAgentRuntime,
    ToolRegistry,
    as_list,
    fan_out,
)

from .agents import AGENTS
from .config import (
    MAX_ROUNDS,
    RUN_CONFIG,
    make_budget,
)
from .render import Renderer
from .state import (
    decide_next,
    force_final,
    initialize_state,
    make_store,
    update_state,
)


async def run_investigation(
    provider: Any,
    tools: ToolRegistry,
    question: str,
    *,
    budget: RunBudget | None = None,
    approver: Approver | None = None,
    config: RunConfig = RUN_CONFIG,
    render: Renderer | None = None,
) -> dict[str, Any]:
    """Investigate a question, printing progress, returning final state."""

    budget = (
        budget
        if budget is not None
        else make_budget()
    )

    render = (
        render
        if render is not None
        else Renderer()
    )

    store = make_store(
        on_change=render.store_changed
    )

    runtime = ToolAgentRuntime(
        provider,
        tools,
        budget,
        AGENTS,
        config=config,
        approver=approver,
    )

    state = await initialize_state(
        provider,
        question,
        budget,
        render,
    )

    await store.merge(
        state,
        writer="coordinator",
    )

    state = store.snapshot()

    render.state_summary(state)

    seen_tasks: set[
        tuple[str, str]
    ] = set()

    for round_number in range(
        1,
        MAX_ROUNDS + 1,
    ):
        render.round_header(
            round_number
        )

        decision = await decide_next(
            provider,
            question,
            state,
            round_number,
            budget,
            render,
        )

        if decision.get("status") == "done":
            render.final(
                str(
                    decision.get(
                        "answer",
                        "",
                    )
                )
            )

            return store.snapshot()

        if (
            decision.get("status")
            != "delegate"
        ):
            raise RuntimeError(
                "Invalid coordinator decision: "
                f"{decision}"
            )

        render.reason(
            str(
                decision.get(
                    "reason",
                    "",
                )
            )
        )

        valid: list[
            dict[str, Any]
        ] = []

        for delegation in decision.get(
            "delegations",
            [],
        ):
            if budget.remaining == 0:
                render.skipped(
                    "delegation budget exhausted"
                )

                break

            agent_name = delegation.get(
                "agent"
            )

            task = str(
                delegation.get(
                    "task",
                    "",
                )
            ).strip()

            if agent_name not in AGENTS:
                continue

            if not task:
                continue

            key = (
                agent_name,
                " ".join(
                    task.lower().split()
                ),
            )

            if key in seen_tasks:
                continue

            refusal = budget.reserve(
                str(agent_name),
                1,
                (),
            )

            if refusal:
                render.skipped(refusal)

                continue

            seen_tasks.add(key)

            valid.append(delegation)

        if not valid:
            break

        for delegation in valid:
            render.planned(
                str(delegation["agent"]),
                str(delegation["task"]),
            )

        results = await fan_out(
            valid,
            partial(
                run_delegation,
                runtime,
                question,
                state,
                render,
            ),
            limit=config.fan_out_limit,
        )

        state = await update_state(
            provider,
            question,
            state,
            round_number,
            results,
            budget,
            render,
        )

        await store.merge(
            state,
            writer="coordinator",
        )

        state = store.snapshot()

        render.state_summary(state)

    render.write(
        "\n[investigation budget exhausted -> final]\n"
    )

    render.write(
        await force_final(
            provider,
            question,
            state,
        )
    )

    return store.snapshot()


async def run_delegation(
    runtime: ToolAgentRuntime,
    question: str,
    state: dict[str, Any],
    render: Renderer,
    delegation: dict[str, Any],
) -> dict[str, Any]:
    """Investigate one delegation, reporting events as they happen."""

    name = str(delegation["agent"])

    spec: AgentSpec = AGENTS[name]

    task = (
        str(delegation["task"])
        + "\n\n"
        + "Investigation context:\n"
        + json.dumps(
            {
                "original_question": (
                    question
                ),
                "purpose": delegation.get(
                    "purpose",
                    "",
                ),
                "targets_hypotheses": (
                    delegation.get(
                        "targets_hypotheses",
                        [],
                    )
                ),
                "targets_questions": (
                    delegation.get(
                        "targets_questions",
                        [],
                    )
                ),
                "expected_evidence": (
                    delegation.get(
                        "expected_evidence",
                        "",
                    )
                ),
                "current_state": state,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    result: AgentResult | None = None

    status = "completed"

    try:
        async for event in runtime.stream(
            spec,
            task,
        ):
            render.handle(event)

            if isinstance(
                event,
                AgentFinished,
            ):
                result = event.result

    except Exception as exc:
        result = AgentResult(
            content=f"Agent failed: {exc}",
            private=PrivateState(
                runtime.config
            ),
        )

        status = "failed"

    # Either the run finished with a handoff, or it raised above.
    assert result is not None

    if status == "failed":
        render.agent_report(
            name,
            result,
        )

    return {
        "agent": name,
        "task": delegation["task"],
        "purpose": delegation.get(
            "purpose",
            "",
        ),
        "targets_hypotheses": (
            delegation.get(
                "targets_hypotheses",
                [],
            )
        ),
        "targets_questions": (
            delegation.get(
                "targets_questions",
                [],
            )
        ),
        "status": status,
        "result": result.content,
        "findings": as_list(
            result.data.get("findings")
        ),
        "open_leads": as_list(
            result.data.get("open_leads")
        ),
        "checked": result.private.checked,
        "nested_delegations": [
            {
                "agent": record["agent"],
                "delegated_by": (
                    record["delegated_by"]
                ),
                "task": record["task"],
                "status": record["status"],
                "reason": record["reason"],
            }
            for record in result.nested
        ],
    }
