"""One bounded agent run, and the primitive that runs several at once.

The runtime owns the loop, the guards and the dispatch; it owns no domain.
It is handed a provider, a tool registry, a budget and the specs of the
agents it may delegate to, and it turns one task into either a handoff or a
refusal it can explain.

Nested delegation stays serial by design — a child runs inside its parent's
step — so a run is a plain call tree. Where breadth is wanted, a caller
fans out top-level work instead.
"""

import asyncio
import json
import time
import uuid

from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    TypeVar,
)

from .approval import (
    Approval,
    Approver,
    refusal_text,
)
from .authority import (
    InProcessAuthority,
    ModelCall,
    RunAuthority,
)
from .budget import RunBudget
from .config import RunConfig
from .events import (
    AgentFinished,
    AgentStarted,
    AgentStep,
    BudgetExceeded,
    Delegation,
    Event,
    NoteRecorded,
    RunFinished,
    RunStarted,
    ToolCalled,
    ToolDenied,
)
from .protocol import (
    JSON_ONLY_RULE,
    AgentResult,
    ModelOutputError,
    PrivateState,
    as_list,
    extract_json,
)
from .providers import ModelProvider
from .registry import (
    TRUNCATION_MARKER,
    ToolRegistry,
)
from .spec import AgentSpec


R = TypeVar("R")
T = TypeVar("T")


async def fan_out(
    items: Iterable[T],
    worker: Callable[[T], Awaitable[R]],
    *,
    limit: int,
) -> list[R]:
    """Run one worker per item, at most `limit` of them at a time.

    The limit is required: "how many at once" is a decision about your
    system, and an unbounded default would be a footgun dressed as
    convenience.
    """

    semaphore = asyncio.Semaphore(max(1, limit))

    async def bounded(
        item: T,
    ) -> R:
        async with semaphore:
            return await worker(item)

    return list(
        await asyncio.gather(
            *(
                bounded(item)
                for item in items
            )
        )
    )


@dataclass
class _Delegation:
    """One delegation, before anything has been done about it."""

    record: dict[str, Any]
    spec: AgentSpec | None = None
    task: str = ""
    descendants: list[dict[str, Any]] = field(
        default_factory=list
    )


class ToolAgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        budget: RunBudget,
        agents: Mapping[
            str,
            AgentSpec,
        ]
        | None = None,
        *,
        config: RunConfig,
        approver: Approver | None = None,
        authority: RunAuthority | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.budget = budget
        self.agents = dict(agents or {})
        self.config = config
        self.approver = approver

        self._approval_lock = asyncio.Lock()

        # The provider, the budget and the approver answer through one object,
        # so a loop that runs somewhere else asks the same questions over a
        # transport instead of holding any of them.
        self.authority = (
            authority
            if authority is not None
            else InProcessAuthority(
                provider,
                budget,
                approver,
                lock=self._approval_lock,
            )
        )

    # =====================================================================
    # Running one agent
    # =====================================================================

    async def run(
        self,
        spec: AgentSpec,
        task: str,
        *,
        depth: int = 1,
        ancestry: tuple[str, ...] = (),
    ) -> AgentResult:
        """Drain the event stream and return the handoff."""

        result: AgentResult | None = None

        async for event in self.stream(
            spec,
            task,
            depth=depth,
            ancestry=ancestry,
        ):
            if isinstance(
                event,
                AgentFinished,
            ):
                result = event.result

        # The stream always ends with the agent's own handoff, unless it
        # raised instead of finishing.
        assert result is not None
        return result

    async def stream(
        self,
        spec: AgentSpec,
        task: str,
        *,
        depth: int = 1,
        ancestry: tuple[str, ...] = (),
        parent_run_id: str | None = None,
    ) -> AsyncIterator[Event]:
        """Run one agent, yielding what happens as it happens."""

        run_id = uuid.uuid4().hex[:12]

        private = PrivateState(
            self.config
        )

        nested: list[
            dict[str, Any]
        ] = []

        if depth == 1:
            yield RunStarted(
                run_id=run_id,
                task=task,
                config=self.config,
            )

        yield AgentStarted(
            run_id=run_id,
            parent_run_id=parent_run_id,
            agent=spec.name,
            depth=depth,
            task=task,
        )

        messages: list[
            dict[str, str]
        ] = [
            {
                "role": "system",
                "content": "",
            },
            {
                "role": "user",
                "content": task,
            },
        ]

        started = time.monotonic()

        last_check = started

        step = 0
        while self.config.max_tool_steps is None or step < self.config.max_tool_steps:
            messages[0][
                "content"
            ] = self.system_prompt(
                spec,
                depth,
                private,
            )

            completion = (
                await self.authority.complete(
                    ModelCall(
                        run_id=run_id,
                        agent=spec.name,
                        messages=messages,
                    )
                )
            )

            response = completion.text

            now = time.monotonic()

            for event in await self.charge(
                run_id,
                [
                    (
                        "tokens",
                        (
                            completion.usage.total_tokens
                            if completion.usage
                            else None
                        ),
                    ),
                    (
                        "wall_clock_seconds",
                        now - last_check,
                    ),
                ],
            ):
                yield event

            last_check = now

            try:
                action = extract_json(
                    response
                )

            except ModelOutputError:
                action = None

            kind = (
                action.get("type")
                if action is not None
                else None
            )

            yield AgentStep(
                run_id=run_id,
                agent=spec.name,
                step=step,
                reply=response,
                action=(
                    str(kind)
                    if kind is not None
                    else None
                ),
            )

            step += 1

            if kind == "final":
                result = AgentResult(
                    content=str(
                        action.get(
                            "content",
                            "",
                        )
                    ).strip(),
                    private=private,
                    data={
                        key: value
                        for key, value in action.items()
                        if key
                        not in {
                            "type",
                            "content",
                        }
                    },
                    nested=nested,
                )

                yield AgentFinished(
                    run_id=run_id,
                    parent_run_id=(
                        parent_run_id
                    ),
                    agent=spec.name,
                    status="completed",
                    result=result,
                )

                if depth == 1:
                    yield RunFinished(
                        run_id=run_id,
                        agent=spec.name,
                        status="completed",
                        result=result,
                    )

                return

            if kind == "note":
                label = "NOTE RESULT"

                result_text = private.note(
                    str(
                        action.get(
                            "content",
                            "",
                        )
                    )
                )

                yield NoteRecorded(
                    run_id=run_id,
                    agent=spec.name,
                    note=(
                        private.notes[-1]
                        if private.notes
                        else ""
                    ),
                    total=len(private.notes),
                    result=result_text,
                )

                observation = result_text

            elif kind == "delegate":
                plan = await self.plan_delegation(
                    spec,
                    action,
                    depth,
                    ancestry,
                )

                label = (
                    "DELEGATION RESULT for "
                    f"{plan.record['agent']}"
                )

                if plan.spec is None:
                    yield self.delegation_event(
                        run_id,
                        spec.name,
                        plan,
                    )

                    nested.append(plan.record)

                else:
                    yield self.delegation_event(
                        run_id,
                        spec.name,
                        plan,
                        status="started",
                        reason="",
                    )

                    async for event in self.delegate(
                        plan,
                        depth,
                        ancestry,
                        run_id,
                    ):
                        yield event

                    nested.append(plan.record)

                    nested.extend(
                        plan.descendants
                    )

                    yield self.delegation_event(
                        run_id,
                        spec.name,
                        plan,
                    )

                observation = plan.record[
                    "observation"
                ]

            elif kind == "tool":
                name = action.get("name")

                label = (
                    f"TOOL RESULT for {name}"
                )

                event = await self.use_tool(
                    spec,
                    run_id,
                    name,
                    action.get(
                        "arguments"
                    )
                    or {},
                    private,
                )

                observation = event.observation

                yield event

            elif kind is None:
                label = "FORMAT ERROR"

                observation = JSON_ONLY_RULE

            else:
                # A misspelled or invented action type is a bad reply, not
                # a reason to throw away everything the agent has done so
                # far. It costs one step and the model gets corrected.
                label = "FORMAT ERROR"

                observation = (
                    f"No such action type: "
                    f"{kind!r}. Valid types are "
                    "tool, note, delegate and "
                    "final.\n\n"
                    + JSON_ONLY_RULE
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"{label}:\n"
                        f"{observation}\n\n"
                        "Continue. Return only "
                        "the next JSON action."
                    ),
                }
            )

            if await self.authority.exceeded(
                "wall_clock_seconds"
            ):
                yield await self.budget_event(
                    run_id,
                    "wall_clock_seconds",
                )

                result = self.exhausted(
                    private,
                    nested,
                    "Wall-clock",
                )

                break

        else:
            result = self.exhausted(
                private,
                nested,
                "Tool-step",
            )

        yield AgentFinished(
            run_id=run_id,
            parent_run_id=parent_run_id,
            agent=spec.name,
            status="exhausted",
            result=result,
        )

        if depth == 1:
            yield RunFinished(
                run_id=run_id,
                agent=spec.name,
                status="exhausted",
                result=result,
            )

    # =====================================================================
    # Guards and accounting
    # =====================================================================

    async def charge(
        self,
        run_id: str,
        measurements: list[
            tuple[str, float | None]
        ],
    ) -> list[BudgetExceeded]:
        """Settle what could only be measured after a model call."""

        events: list[
            BudgetExceeded
        ] = []

        for dimension, amount in measurements:
            if (
                await self.authority.settle(
                    dimension,
                    amount,
                )
                is not None
            ):
                events.append(
                    await self.budget_event(
                        run_id,
                        dimension,
                    )
                )

        return events

    async def budget_event(
        self,
        run_id: str,
        dimension: str,
    ) -> BudgetExceeded:
        limit, used = await self.authority.numbers(
            dimension
        )

        return BudgetExceeded(
            run_id=run_id,
            dimension=dimension,
            limit=float(
                limit or 0.0
            ),
            used=used,
        )

    def exhausted(
        self,
        private: PrivateState,
        nested: list[dict[str, Any]],
        label: str,
    ) -> AgentResult:
        return AgentResult(
            content=(
                f"{label} budget exhausted "
                "before the agent produced "
                "a final answer."
            ),
            private=private,
            nested=nested,
        )

    # =====================================================================
    # Delegation
    # =====================================================================

    def delegation_event(
        self,
        run_id: str,
        delegated_by: str,
        plan: _Delegation,
        status: str | None = None,
        reason: str | None = None,
    ) -> Delegation:
        record = plan.record

        return Delegation(
            run_id=run_id,
            agent=str(
                record["agent"]
            ),
            delegated_by=delegated_by,
            depth=int(record["depth"]),
            task=str(record["task"]),
            status=(
                status
                or str(record["status"])
            ),
            reason=(
                reason
                if reason is not None
                else str(record["reason"])
            ),
        )

    async def plan_delegation(
        self,
        spec: AgentSpec,
        action: dict[str, Any],
        depth: int,
        ancestry: tuple[str, ...],
    ) -> _Delegation:
        """Decide whether a delegation may run, and describe it."""

        target = action.get("agent")

        task = str(
            action.get(
                "task",
                "",
            )
        ).strip()

        purpose = str(
            action.get(
                "purpose",
                "",
            )
        ).strip()

        record: dict[str, Any] = {
            "agent": target,
            "delegated_by": spec.name,
            "task": task,
            "purpose": purpose,
            "depth": depth + 1,
            "status": "refused",
            "reason": "",
            "observation": "",
        }

        if (
            not isinstance(target, str)
            or target not in self.agents
        ):
            return self.refuse(
                record,
                f"Unknown agent: {target}",
            )

        if not task:
            return self.refuse(
                record,
                "Refused: the delegated "
                "task was empty.",
            )

        refusal = await self.authority.reserve_delegation(
            str(target),
            depth + 1,
            ancestry + (spec.name,),
        )

        if refusal:
            return self.refuse(
                record,
                refusal,
            )

        plan = _Delegation(
            record=record,
            spec=self.agents[
                str(target)
            ],
        )

        plan.task = (
            task
            + "\n\n"
            + "Nested delegation:\n"
            + json.dumps(
                {
                    "delegated_by": (
                        spec.name
                    ),
                    "purpose": purpose,
                    "depth": depth + 1,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        return plan

    async def delegate(
        self,
        plan: _Delegation,
        depth: int,
        ancestry: tuple[str, ...],
        parent_run_id: str,
    ) -> AsyncIterator[Event]:
        """Run the delegated agent, propagating everything it reports."""

        record = plan.record

        target = str(record["agent"])

        child_result: AgentResult | None = None

        try:
            async for event in self.stream(
                plan.spec,
                plan.task,
                depth=depth + 1,
                ancestry=(
                    ancestry + (str(record["delegated_by"]),)
                ),
                parent_run_id=parent_run_id,
            ):
                if isinstance(
                    event,
                    AgentFinished,
                ):
                    child_result = event.result

                yield event

        except Exception as exc:
            record["status"] = "failed"
            record["reason"] = str(exc)

            record["observation"] = (
                f"{target} failed: {exc}\n\n"
                "Continue without it. "
                "Return only the next "
                "JSON action."
            )

            return

        record["status"] = "completed"
        record["content"] = child_result.content
        record["data"] = child_result.data

        plan.descendants = child_result.nested

        record["observation"] = (
            f"{target} reported:\n"
            f"{child_result.content if self.config.max_tool_output_chars is None else child_result.content[:self.config.max_tool_output_chars]}"
            "\n\nStructured result:\n"
            f"{json.dumps(child_result.data, ensure_ascii=False)}"
        )

    def refuse(
        self,
        record: dict[str, Any],
        reason: str,
    ) -> _Delegation:
        record["reason"] = reason

        record["observation"] = (
            f"{reason}\n\n"
            "Do not repeat this delegation. "
            "Continue with what you already "
            "have. Return only the "
            "next JSON action."
        )

        return _Delegation(record=record)

    # =====================================================================
    # Tools
    # =====================================================================

    async def use_tool(
        self,
        spec: AgentSpec,
        run_id: str,
        name: Any,
        arguments: dict[str, Any],
        private: PrivateState,
    ) -> ToolCalled | ToolDenied:
        """Execute one tool call, or refuse it and say why."""

        tool = self.tools.tool(name)

        if tool is None or name not in spec.tools:
            return ToolDenied(
                run_id=run_id,
                agent=spec.name,
                tool=str(name),
                reason="not-permitted",
                capabilities=(
                    self.tools.capabilities(
                        str(name)
                    )
                ),
                observation=(
                    "Permission denied: "
                    f"{name} is not available "
                    f"to {spec.name}."
                ),
            )

        refusal = (
            await self.authority.reserve(
                "tool_calls"
            )
        )

        if refusal:
            return ToolDenied(
                run_id=run_id,
                agent=spec.name,
                tool=tool.name,
                reason="budget",
                capabilities=(
                    tool.capabilities
                ),
                observation=(
                    f"{refusal} Continue with "
                    "what you already have. "
                    "Return only the next JSON "
                    "action."
                ),
            )

        approval = await self.authority.approve(
            tool,
            spec.name,
            arguments,
            run_id,
        )

        if approval is Approval.REFUSED:
            return ToolDenied(
                run_id=run_id,
                agent=spec.name,
                tool=tool.name,
                reason="approval-refused",
                capabilities=(
                    tool.capabilities
                ),
                observation=(
                    refusal_text(tool.name)
                    + " Return only the next "
                    "JSON action."
                ),
            )

        rendered = json.dumps(
            arguments,
            ensure_ascii=False,
        )

        summary = f"{name}({rendered})"

        ok = True

        try:
            observation = (
                await self.tools.execute(
                    name,
                    arguments,
                    run_id=run_id,
                    agent=spec.name,
                )
            )

        except Exception as exc:
            ok = False

            private.check(
                summary + " -> failed"
            )

            observation = f"Tool error: {exc}"

        else:
            private.check(summary)

        return ToolCalled(
            run_id=run_id,
            agent=spec.name,
            tool=tool.name,
            arguments=arguments,
            capabilities=tool.capabilities,
            approval=approval,
            observation=observation,
            output_chars=len(observation),
            truncated=observation.endswith(
                TRUNCATION_MARKER
            ),
            ok=ok,
        )

    # =====================================================================
    # Prompts
    # =====================================================================

    def system_prompt(
        self,
        spec: AgentSpec,
        depth: int,
        private: PrivateState,
    ) -> str:
        available = self.tools.describe(
            spec.tools
        )

        delegation = ""

        if self.authority.may_delegate(depth):
            delegation = f"""
To delegate follow-up work:

{{
  "type": "delegate",
  "agent": "agent-name",
  "task": "self-contained task",
  "purpose": "what uncertainty this removes"
}}

Delegation rules:

- Delegations are limited and counted
  against the whole run.
- The task must be self-contained: the other
  agent sees only your task text, not
  your notes, your transcript or the shared
  state.
- Never delegate work you already did.
- You stay responsible for the final answer.
- Available agents:
  {", ".join(sorted(self.agents))}.
"""

        return f"""
You are {spec.name} inside a larger
multi-agent system.

{spec.instructions}

You are at depth {depth} of
{self.authority.max_depth}.

The Python runtime owns all tools.
You cannot invoke tools directly.

At every step, return exactly one JSON object.

To use a tool:

{{
  "type": "tool",
  "name": "tool_name",
  "arguments": {{}}
}}

To record a private note:

{{
  "type": "note",
  "content": "what you checked and what it showed"
}}

Notes are private to you. They are never shared
with any other agent.
{delegation}
When finished:

{{
  "type": "final",
  "content": "the answer, in prose"
}}

Your instructions may name extra fields to add to that
object. The runtime returns them to the caller exactly as
you wrote them, so use the names and shapes you were
given and no others.

Your private scratchpad:

{private.render()}

Available tools:

{json.dumps(available, indent=2)}

Rules:

- Use only listed tools.
- Follow the fields your instructions specify for the
  final action.
- Do not invent tool results.
- {JSON_ONLY_RULE}
""".strip()
