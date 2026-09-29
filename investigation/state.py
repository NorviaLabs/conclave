"""Structured investigation state and the steps that update it.

The shape of this state — facts, evidence, hypotheses, open questions,
investigations — is what makes this an *investigation* application. It is
declared to the core as a `StoreSchema`, and the core only ever sees five
named keys and how to merge them.
"""

import json

from typing import (
    Any,
    Callable,
)

from conclave import (
    ModelOutputError,
    RunBudget,
    SharedStore,
    StoreSchema,
    Usage,
    complete_json,
)

from .agents import AGENTS
from .config import (
    MAX_DELEGATIONS_PER_ROUND,
    MAX_ROUNDS,
)


# =============================================================================
# Structured investigation state
# =============================================================================


EMPTY_STATE = {
    "facts": [],
    "evidence": [],
    "hypotheses": [],
    "open_questions": [],
    "investigations": [],
}


STATE_SCHEMA = StoreSchema(
    keys=frozenset(EMPTY_STATE)
)


def make_store(
    on_change: Any = None,
) -> SharedStore:
    return SharedStore(
        STATE_SCHEMA,
        EMPTY_STATE,
        on_change=on_change,
    )


def state_json(
    state: dict[str, Any],
) -> str:
    return json.dumps(
        state,
        indent=2,
        ensure_ascii=False,
    )


def normalize_state(
    state: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: (
            state.get(
                key,
                [],
            )
            if isinstance(
                state.get(
                    key,
                    [],
                ),
                list,
            )
            else []
        )
        for key in EMPTY_STATE
    }


def charge_usage(
    budget: RunBudget,
) -> Callable[[Usage], None]:
    """Charge coordinator calls to the same budget as agent work."""

    def collect(
        usage: Usage,
    ) -> None:
        budget.settle(
            "tokens",
            usage.total_tokens,
        )

    return collect


def delegation_budget(
    budget: RunBudget,
) -> str:
    """Tell the coordinator what it can still afford.

    Without this it keeps asking for the agent that already ran its
    maximum, one wasted round at a time.
    """

    lines = [
        f"{budget.used}/{budget.max_delegations} "
        "delegations used in this run.",
        "",
        (
            "Per-agent limit "
            f"{budget.max_per_agent}. Used so far:"
        ),
    ]

    for name in AGENTS:
        used = budget.per_agent.get(name, 0)

        if budget.max_per_agent is None:
            state = "unbounded"
        elif used >= budget.max_per_agent:
            state = "exhausted"
        else:
            state = f"{budget.max_per_agent - used} left"

        lines.append(
            f"  {name}: {used}/"
            f"{budget.max_per_agent} ({state})"
        )

    return "\n".join(lines)


async def initialize_state(
    provider: Any,
    question: str,
    budget: RunBudget,
    render: Any,
) -> dict[str, Any]:
    try:
        state = await complete_json(
            provider,
            """
You initialize structured investigation state.

Return JSON only.
""".strip(),
            f"""
Question:

{question}

Create 2-4 plausible, testable hypotheses
and the important open questions.

There is no evidence yet.

Therefore:
- facts must be empty
- evidence must be empty
- investigations must be empty

Confidence is a heuristic from 0.0 to 1.0
and should initially be low.

Return:

{{
  "facts": [],
  "evidence": [],
  "hypotheses": [
    {{
      "id": "H1",
      "claim": "...",
      "confidence": 0.2,
      "status": "open",
      "evidence_for": [],
      "evidence_against": []
    }}
  ],
  "open_questions": [
    {{
      "id": "Q1",
      "question": "...",
      "priority": "high",
      "status": "open",
      "related_hypotheses": ["H1"],
      "answer": null,
      "evidence": []
    }}
  ],
  "investigations": []
}}
""".strip(),
            on_usage=charge_usage(budget),
        )

        return normalize_state(
            state
        )

    except ModelOutputError:
        render.notice(
            "state initialization was unreadable; "
            "continuing with an empty state"
        )

        return normalize_state({})


async def decide_next(
    provider: Any,
    question: str,
    state: dict[str, Any],
    round_number: int,
    budget: RunBudget,
    render: Any,
    *,
    max_rounds: int = MAX_ROUNDS,
    max_delegations_per_round: int = MAX_DELEGATIONS_PER_ROUND,
) -> dict[str, Any]:
    try:
        return await complete_json(
            provider,
            """
You coordinate a multi-agent investigation.

Return JSON only.
""".strip(),
            f"""
Original question:

{question}


Round:

{round_number}/{max_rounds}


Current state:

{state_json(state)}


Delegation budget left:

{delegation_budget(budget)}


Available specialists:

repo-researcher
    local files and Git history

github-researcher
    GitHub issues, PRs and history

web-researcher
    current external research

architect
    architectural reasoning over supplied evidence

critic
    challenges conclusions and assumptions

Specialists may delegate limited follow-up work to
each other while they run. They report what they
delegated, including refusals.


Choose investigations that reduce the most
important remaining uncertainty.

Do not repeat completed work.

Use at most {max_delegations_per_round}
independent delegations in this round.

Finish when remaining uncertainty is not
important enough to justify more investigation.


Delegate:

{{
  "status": "delegate",
  "reason": "...",
  "delegations": [
    {{
      "agent": "repo-researcher",
      "task": "specific investigation",
      "purpose": "what uncertainty this reduces",
      "targets_hypotheses": ["H1"],
      "targets_questions": ["Q1"],
      "expected_evidence": "what evidence would discriminate"
    }}
  ]
}}


Finish:

{{
  "status": "done",
  "answer": "evidence-grounded final answer"
}}
""".strip(),
            on_usage=charge_usage(budget),
        )

    except ModelOutputError:
        render.notice(
            "coordinator reply was unreadable; "
            "stopping the investigation and answering "
            "from the state gathered so far"
        )

        return {
            "status": "delegate",
            "reason": (
                "coordinator reply was unreadable"
            ),
            "delegations": [],
        }


async def update_state(
    provider: Any,
    question: str,
    previous_state: dict[str, Any],
    round_number: int,
    results: list[dict[str, Any]],
    budget: RunBudget,
    render: Any,
) -> dict[str, Any]:
    try:
        updated = await complete_json(
            provider,
            """
You maintain canonical investigation state.

Return JSON only.
""".strip(),
            f"""
Question:

{question}


Round:

{round_number}


Previous state:

{state_json(previous_state)}


New specialist results:

{json.dumps(
    results,
    indent=2,
    ensure_ascii=False,
)}


Update the COMPLETE state.

Rules:

- Preserve existing IDs.
- Add F/E/H/Q IDs using the next unused number.
- Facts must be directly supported by evidence.
- Evidence must preserve concrete provenance.
- Evidence may support or contradict hypotheses.
- Update evidence_for and evidence_against.
- Change confidence only when evidence justifies it.
- Hypothesis status:
  open | supported | weakened | rejected
- Question status:
  open | resolved
- Never delete resolved questions.
- Record completed and failed investigations.
- Do not invent paths, commits, URLs,
  issues, PRs or claims.

- Results may include "findings" (claims with
  provenance), "open_leads", "checked" (what the
  specialist already inspected) and
  "nested_delegations" (follow-up work a
  specialist delegated, including refusals).
- Treat delegated findings as evidence with their
  own provenance. Do not record refusals as
  findings.
- Do not propose work that "checked" already
  covers.

- Keep state compact.

Return exactly these top-level keys:

facts
evidence
hypotheses
open_questions
investigations
""".strip(),
            on_usage=charge_usage(budget),
        )

        return normalize_state(
            updated
        )

    except ModelOutputError:
        render.notice(
            "state update was unreadable; "
            "keeping the previous state"
        )

        return normalize_state(
            previous_state
        )


async def force_final(
    provider: Any,
    question: str,
    state: dict[str, Any],
) -> str:
    completion = await provider.complete(
        [
            {
                "role": "system",
                "content": """
Answer from the supplied investigation state.

Distinguish evidence, inference,
and unresolved uncertainty.

Write the answer as prose. The Python runtime owns
all tools: never emit tool-call markup (DSML, antml,
invoke or parameter elements), and do not ask to run
commands.
""".strip(),
            },
            {
                "role": "user",
                "content": f"""
Question:

{question}


State:

{state_json(state)}


Give the best concise final answer.

Do not request more research.
""".strip(),
            },
        ]
    )

    return completion.text
