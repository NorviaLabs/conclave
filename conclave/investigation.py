"""Round-based orchestration of the specialist agents."""

import asyncio
import json

from typing import Any

from .agents import AGENTS, ToolAgentRuntime
from .config import MAX_DELEGATIONS, MAX_ROUNDS
from .providers import ModelProvider
from .registry import ToolRegistry
from .state import (
    decide_next,
    force_final,
    initialize_state,
    print_state_summary,
    update_state,
)


# =============================================================================
# Main orchestration
#
# Nothing below this line knows or cares whether the provider is
# OpenCode, Ollama, Anthropic, OpenAI, etc.
# =============================================================================


async def run_investigation(
    provider: ModelProvider,
    tools: ToolRegistry,
    question: str,
) -> None:
    runtime = ToolAgentRuntime(
        provider,
        tools,
    )

    state = await initialize_state(
        provider,
        question,
    )

    print_state_summary(
        state
    )

    seen_tasks: set[
        tuple[str, str]
    ] = set()

    total_delegations = 0

    for round_number in range(
        1,
        MAX_ROUNDS + 1,
    ):
        print(
            f"\n{'=' * 70}"
        )

        print(
            f"ROUND {round_number}"
        )

        print(
            "=" * 70
        )

        decision = await decide_next(
            provider,
            question,
            state,
            round_number,
        )

        if (
            decision.get("status")
            == "done"
        ):
            print(
                "\n[coordinator -> done]\n"
            )

            print(
                decision.get(
                    "answer",
                    "",
                )
            )

            return

        if (
            decision.get("status")
            != "delegate"
        ):
            raise RuntimeError(
                "Invalid coordinator decision: "
                f"{decision}"
            )

        print(
            "\nReason: "
            f"{decision.get('reason', '')}"
        )

        valid: list[
            dict[str, Any]
        ] = []

        for delegation in decision.get(
            "delegations",
            [],
        ):
            if (
                total_delegations
                >= MAX_DELEGATIONS
            ):
                break

            agent_name = (
                delegation.get(
                    "agent"
                )
            )

            task = str(
                delegation.get(
                    "task",
                    "",
                )
            ).strip()

            if (
                agent_name
                not in AGENTS
            ):
                continue

            if not task:
                continue

            key = (
                agent_name,
                " ".join(
                    task
                    .lower()
                    .split()
                ),
            )

            if key in seen_tasks:
                continue

            seen_tasks.add(
                key
            )

            total_delegations += 1

            valid.append(
                delegation
            )

        if not valid:
            break

        for delegation in valid:
            print(
                f"  -> "
                f"{delegation['agent']}: "
                f"{delegation['task']}"
            )

        async def execute_delegation(
            delegation: dict[str, Any],
        ) -> dict[str, Any]:
            name = delegation[
                "agent"
            ]

            spec = AGENTS[
                name
            ]

            context = {
                "original_question": (
                    question
                ),
                "purpose": (
                    delegation.get(
                        "purpose",
                        "",
                    )
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
                "current_state": (
                    state
                ),
            }

            task = (
                delegation["task"]
                + "\n\n"
                + "Investigation context:\n"
                + json.dumps(
                    context,
                    indent=2,
                    ensure_ascii=False,
                )
            )

            try:
                result = (
                    await runtime.run(
                        spec,
                        task,
                    )
                )

                status = (
                    "completed"
                )

            except Exception as exc:
                result = (
                    f"Agent failed: {exc}"
                )

                status = "failed"

            print(
                f"\n[{name}]\n"
                f"{result}"
            )

            return {
                "agent": name,
                "task": delegation[
                    "task"
                ],
                "purpose": (
                    delegation.get(
                        "purpose",
                        "",
                    )
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
                "result": result,
            }

        results = await asyncio.gather(
            *(
                execute_delegation(
                    delegation
                )
                for delegation in valid
            )
        )

        state = await update_state(
            provider,
            question,
            state,
            round_number,
            results,
        )

        print_state_summary(
            state
        )

    print(
        "\n[investigation budget exhausted -> final]\n"
    )

    print(
        await force_final(
            provider,
            question,
            state,
        )
    )
