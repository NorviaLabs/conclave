"""Structured investigation state and the steps that update it."""

import json

from typing import Any

from .agents import complete_json
from .config import MAX_DELEGATIONS_PER_ROUND, MAX_ROUNDS
from .providers import ModelProvider


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


async def initialize_state(
    provider: ModelProvider,
    question: str,
) -> dict[str, Any]:
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
    )

    return normalize_state(
        state
    )


async def decide_next(
    provider: ModelProvider,
    question: str,
    state: dict[str, Any],
    round_number: int,
) -> dict[str, Any]:
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

{round_number}/{MAX_ROUNDS}


Current state:

{state_json(state)}


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


Choose investigations that reduce the most
important remaining uncertainty.

Do not repeat completed work.

Use at most {MAX_DELEGATIONS_PER_ROUND}
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
    )


async def update_state(
    provider: ModelProvider,
    question: str,
    previous_state: dict[str, Any],
    round_number: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
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
- Keep state compact.

Return exactly these top-level keys:

facts
evidence
hypotheses
open_questions
investigations
""".strip(),
    )

    return normalize_state(
        updated
    )


async def force_final(
    provider: ModelProvider,
    question: str,
    state: dict[str, Any],
) -> str:
    return await provider.complete(
        [
            {
                "role": "system",
                "content": """
Answer from the supplied investigation state.

Distinguish evidence, inference,
and unresolved uncertainty.
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


def print_state_summary(
    state: dict[str, Any],
) -> None:
    print(
        "\n[shared state]"
    )

    for hypothesis in state.get(
        "hypotheses",
        [],
    ):
        print(
            f"  {hypothesis.get('id', '?')} "
            f"[{hypothesis.get('status', '?')}, "
            f"{hypothesis.get('confidence', '?')}]: "
            f"{hypothesis.get('claim', '')}"
        )

    open_questions = [
        question
        for question in state.get(
            "open_questions",
            [],
        )
        if question.get(
            "status"
        )
        == "open"
    ]

    if open_questions:
        print(
            "  Open questions:"
        )

        for question in open_questions:
            print(
                f"    "
                f"{question.get('id', '?')}: "
                f"{question.get('question', '')}"
            )

    print(
        f"  Facts="
        f"{len(state.get('facts', []))} "
        f"Evidence="
        f"{len(state.get('evidence', []))} "
        f"Investigations="
        f"{len(state.get('investigations', []))}"
    )
