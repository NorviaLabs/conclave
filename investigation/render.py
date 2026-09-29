"""Turning the event stream into a progress log.

This module owns every byte the command line prints. The library never
prints: it emits events, and *this* decides what a human sees. That is the
whole point of the split — a different front end renders the same run as
JSON, a progress bar or a web page.

A top-level agent reports once, when it finishes: its result already carries
what it noted and what it delegated, so nothing has to be buffered while a
round fans out.
"""

import sys

from typing import (
    Any,
    TextIO,
)

from conclave import (
    DIMENSION_LABELS,
    AgentFinished,
    AgentResult,
    BudgetExceeded,
    RunBudget,
    ToolCalled,
    ToolDenied,
)


class Renderer:
    def __init__(
        self,
        stream: TextIO | None = None,
    ):
        self.stream = (
            stream
            if stream is not None
            else sys.stdout
        )

    # =====================================================================
    # Whole-run output
    # =====================================================================

    def write(
        self,
        text: str = "",
    ) -> None:
        print(
            text,
            file=self.stream,
        )

    def header(
        self,
        question: str,
        repo: Any,
        provider: str,
        model: str | None = None,
    ) -> None:
        self.write(
            f"QUESTION\n{question}\n"
        )

        self.write(
            f"WORKSPACE\n{repo}\n"
        )

        self.write(
            f"MODEL PROVIDER\n{provider}\n"
        )

        self.write(
            f"MODEL\n{model or 'provider default'}\n"
        )

    def round_header(
        self,
        round_number: int,
    ) -> None:
        self.write(
            f"\n{'=' * 70}"
        )

        self.write(
            f"ROUND {round_number}"
        )

        self.write(
            "=" * 70
        )

    def reason(
        self,
        text: str,
    ) -> None:
        self.write(f"\nReason: {text}")

    def notice(
        self,
        text: str,
    ) -> None:
        self.write(f"\n[{text}]\n")

    def planned(
        self,
        agent: str,
        task: str,
    ) -> None:
        self.write(
            f"  -> {agent}: {task}"
        )

    def skipped(
        self,
        reason: str,
    ) -> None:
        self.write(f"  x  {reason}")

    def final(
        self,
        answer: str,
    ) -> None:
        self.write("\n[coordinator -> done]\n")

        self.write(answer)

    def state_summary(
        self,
        state: dict[str, Any],
    ) -> None:
        self.write("\n[shared state]")

        for hypothesis in state.get(
            "hypotheses",
            [],
        ):
            self.write(
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
            if question.get("status")
            == "open"
        ]

        if open_questions:
            self.write("  Open questions:")

            for question in open_questions:
                self.write(
                    f"    {question.get('id', '?')}: "
                    f"{question.get('question', '')}"
                )

        self.write(
            f"  Facts="
            f"{len(state.get('facts', []))} "
            f"Evidence="
            f"{len(state.get('evidence', []))} "
            f"Investigations="
            f"{len(state.get('investigations', []))}"
        )

    def store_changed(
        self,
        event: Any,
    ) -> None:
        self.write(
            f"\n[state v{event.version} by "
            f"{event.writer}: "
            f"{', '.join(event.keys)}]"
        )

    # =====================================================================
    # Event stream
    # =====================================================================

    def handle(
        self,
        event: Any,
    ) -> None:
        if isinstance(event, ToolCalled):
            self._tool(event)

        elif isinstance(event, ToolDenied):
            self.write(
                f"  ! {event.agent} "
                f"{event.tool}: {event.reason}"
            )

        elif isinstance(event, BudgetExceeded):
            self.write(
                f"  ! budget: "
                f"{event.dimension} exhausted "
                f"({event.used:g}/"
                f"{event.limit:g})"
            )

        elif (
            isinstance(event, AgentFinished)
            and event.parent_run_id is None
        ):
            self.agent_report(
                event.agent,
                event.result,
            )

    def _tool(
        self,
        event: ToolCalled,
    ) -> None:
        if not event.ok:
            # A bare "failed" leaves the reader with nothing to act on;
            # the tool's own message is the whole diagnosis.
            self.write(
                f"  ! {event.agent} {event.tool}: "
                f"{event.observation.splitlines()[0]}"
            )

            return

        outcome = f"{event.output_chars} chars"

        if event.truncated:
            outcome += " (truncated)"

        self.write(
            f"  · {event.agent} {event.tool} "
            f"-> {outcome}"
        )

    def agent_report(
        self,
        agent: str,
        result: AgentResult,
    ) -> None:
        self.write(
            f"\n[{agent}]\n"
            f"{result.content}"
        )

        for note in result.private.notes:
            self.write(f"  note: {note}")

        for record in result.nested:
            self.write(
                f"  nested: "
                f"{record['delegated_by']} "
                f"-> {record['agent']} "
                f"[{record['status']}] "
                f"{record['task'][:70]}"
            )

            if record["reason"]:
                self.write(
                    f"    {record['reason']}"
                )

    # =====================================================================
    # Budgets
    # =====================================================================

    def budget_plan(
        self,
        budget: RunBudget,
    ) -> None:
        """Only the dimensions this run actually bounds are worth showing."""

        active = [
            f"{dimension} <= "
            f"{budget.limit(dimension):g}"
            for dimension in DIMENSION_LABELS
            if budget.limit(dimension)
            is not None
        ]

        if not active:
            return

        self.write(
            "BUDGET\n"
            + "\n".join(active)
            + "\n"
        )
