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
import copy
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

from .actions import (
    ActionLog,
    ActionStart,
    ActionStopped,
    classify,
    current_action,
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
from .budget import RUN_WIDE_LIMITS, RunBudget
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
    LAST_ACTION_RULE,
    TIME_LEFT_RULE,
    WRITE_UP_RULE,
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
from .spec import AgentSpec, ToolRefused


R = TypeVar("R")
T = TypeVar("T")

# How little time has to be left before an agent is told to answer with what it
# has. Roughly one action's worth on a slow backend, which is what it is for.
TIME_LEFT_WINDOW = 60.0


# The run-wide limits a further call is weighed against before it is made.
PROJECTED_LIMITS = (
    "tokens",
    "cost_usd",
)


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
        log: ActionLog | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.budget = budget
        self.agents = dict(agents or {})
        self.config = config
        self.approver = approver

        self._approval_lock = asyncio.Lock()

        # Whether anything records this run's actions, asked once: when
        # nothing does, no progress snapshot is ever built.
        self._records: bool | None = None

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
                log=log,
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
        scope: str = "",
        resume: list[dict[str, Any]] | None = None,
        parents: tuple[dict[str, Any], ...] = (),
    ) -> AsyncIterator[Event]:
        """Run one agent, yielding what happens as it happens.

        `scope` names what this run works on behalf of (an application's step)
        and travels to every agent it delegates to, so each recorded action
        says where it belongs. `resume` continues from saved progress instead
        of starting over: the frames of this agent and of any agent it was
        waiting on, as recorded with an action. `parents` are the frames of the
        agents waiting on this one.
        """

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

        # Messages holding a result over the run's tool output limit, to be
        # shortened once the agent has acted on them.
        oversized: list[tuple[int, str, str, str]] = []

        step = 0
        reviewed = False

        # This agent's conversation log, kept across a resume, so a run store
        # appends what is new instead of saving the whole conversation again.
        log = str((resume[0].get("log") if resume else None) or run_id)

        def frame(
            response: str,
            action: dict[str, Any],
            *,
            action_id: str | None = None,
            observation: str | None = None,
            delegation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """This agent, as it stands when it takes `action`: enough to go on from."""

            return {
                "version": 1,
                "agent": spec.name,
                "log": log,
                "task": task,
                "depth": depth,
                "messages": copy.deepcopy(messages),
                "reply": response,
                "action": copy.deepcopy(action),
                "action_id": action_id,
                "observation": observation,
                "notes": list(private.notes),
                "checked": list(private.checked),
                "step": step,
                "reviewed": reviewed,
                "nested": copy.deepcopy(nested),
                "delegation": copy.deepcopy(delegation),
            }

        if resume:
            # Continue where the saved progress left off: the conversation,
            # the notes and the delegations so far, then the action that was
            # under way, settled rather than asked for again. The action
            # allowance starts afresh, as it does whenever a step is resumed.
            restored = resume[0]
            messages[:] = copy.deepcopy(restored["messages"])
            private.notes[:] = list(restored.get("notes") or [])
            private.checked[:] = list(restored.get("checked") or [])
            nested[:] = copy.deepcopy(restored.get("nested") or [])
            reviewed = bool(restored.get("reviewed"))

            async for event in self.resume_action(
                spec,
                run_id,
                restored,
                resume[1:],
                private,
                nested,
                messages,
                depth,
                ancestry,
                scope,
                parents,
            ):
                yield event

        # Run-wide limits this agent has already reported, so a limit that
        # stops it is reported once.
        reported: set[str] = set()

        while self.config.max_tool_steps is None or step < self.config.max_tool_steps:
            # A run-wide limit is a real stop: once it is reached, by this
            # agent or by any other working in the same run, no further call
            # is made, apart from the write-up of what this agent has. An
            # agent's first call is its start, which the caller decides.
            reached = await self.run_limit_reached() if step else None

            if reached is not None:
                if reached not in reported:
                    reported.add(reached)
                    yield await self.budget_event(
                        run_id,
                        reached,
                    )

                spent = reached

                break

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
                        provider=spec.provider,
                        model=spec.model,
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
                reported.add(event.dimension)
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

            review = None
            if kind == "final" and spec.review_final is not None and not reviewed:
                reviewed = True
                review = spec.review_final(dict(action))

            if kind == "final" and not review:
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
                        scope=scope,
                        parents=(
                            (
                                *parents,
                                frame(
                                    response,
                                    action,
                                    delegation={**plan.record, "prompt": plan.task},
                                ),
                            )
                            if await self.records_actions()
                            else parents
                        ),
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
                    scope=scope,
                    snapshot=(
                        lambda action_id, observation, response=response, action=action: [
                            *parents,
                            frame(
                                response,
                                action,
                                action_id=action_id,
                                observation=observation,
                            ),
                        ]
                    ),
                )

                observation = event.observation

                yield event

            elif review:
                label = "FINAL CHECK"

                observation = review

            elif action is not None and "type" not in action:
                # An object that leaves out "type" is usually the final
                # answer's fields on their own. Repeating the JSON-only rule
                # does not say what is wrong, and the model resends it.
                label = "FORMAT ERROR"

                observation = (
                    'Your reply had no "type" field. '
                    "Every reply needs one of tool, note, "
                    'delegate and final. When finished, add '
                    '"type": "final" and "content" to the '
                    "fields your instructions name.\n\n"
                    + JSON_ONLY_RULE
                )

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

            # An agent was told to continue right up to the wall, so one that
            # kept working had no way to know its next reply was its last. It is
            # told now, while there is still an action left to answer with.
            remaining = (
                None
                if self.config.max_tool_steps is None
                else self.config.max_tool_steps - step
            )

            left = await self.seconds_left()

            closing = (
                TIME_LEFT_RULE
                if left is not None
                and left <= TIME_LEFT_WINDOW
                else LAST_ACTION_RULE
                if remaining == 1
                else None
            )

            # The agent is told where it stands after every action, not only when
            # it is nearly out: an agent that cannot see its own budget cannot
            # plan with it. The private scratchpad is already counted this way.
            counted = (
                ""
                if self.config.max_tool_steps is None
                else (
                    f"Action {step} of "
                    f"{self.config.max_tool_steps}.\n"
                )
            )

            tail = f"{counted}" + (
                closing
                if closing is not None
                else "Continue. Return only "
                "the next JSON action."
            )

            # A result longer than the run's tool output limit (only a tool
            # allowed more for one call, such as a read that followed pages,
            # returns one) is read in full once. After the agent's next action
            # its copy here is cut back to the limit, or every later request
            # would carry every long result again.
            limit = self.config.max_tool_output_chars

            for index, earlier_label, earlier, earlier_tail in oversized:
                messages[index]["content"] = (
                    f"{earlier_label}:\n"
                    f"{earlier[:limit]}\n"
                    f"...[earlier result shortened from "
                    f"{len(earlier):,} characters]\n\n"
                    f"{earlier_tail}"
                )

            oversized.clear()

            long = (
                limit is not None
                and len(observation) > limit
            )

            warning = (
                "This result is longer than this run's tool "
                f"output limit of {limit:,} characters; after your "
                "next action it will be shortened to that limit, so "
                "record anything you will need in a note.\n\n"
                if long
                else ""
            )

            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"{label}:\n"
                        f"{observation}\n\n"
                        f"{warning}"
                        f"{tail}"
                    ),
                }
            )

            if long:
                oversized.append(
                    (
                        len(messages) - 1,
                        label,
                        observation,
                        tail,
                    )
                )

            if await self.authority.exceeded(
                "wall_clock_seconds"
            ):
                if "wall_clock_seconds" not in reported:
                    reported.add("wall_clock_seconds")
                    yield await self.budget_event(
                        run_id,
                        "wall_clock_seconds",
                    )

                spent = "wall_clock_seconds"

                break

        else:
            spent = "max_tool_steps"

        if spent is not None:
            # A budget that ends without an answer is asked for the answer,
            # because a spent budget should not cost the work: it bounds the
            # effort, not what comes back. The write-up is one call beyond the
            # limit, and the record says which limit it went past.
            result, charged, answered = await self.write_up(
                run_id,
                spec,
                messages,
                private,
                nested,
                last_check,
                spent,
            )

            for event in charged:
                yield event

        status = (
            "completed"
            if answered
            else "exhausted"
        )

        yield AgentFinished(
            run_id=run_id,
            parent_run_id=parent_run_id,
            agent=spec.name,
            status=status,
            result=result,
        )

        if depth == 1:
            yield RunFinished(
                run_id=run_id,
                agent=spec.name,
                status=status,
                result=result,
            )

    async def seconds_left(
        self,
    ) -> float | None:
        """How long this run has left, when it is on a clock at all."""

        numbers = getattr(
            self.authority,
            "numbers",
            None,
        )

        if numbers is None:
            return None

        limit, spent = await numbers(
            "wall_clock_seconds"
        )

        return (
            None
            if limit is None
            else max(
                0.0,
                limit - spent,
            )
        )

    async def write_up(
        self,
        run_id: str,
        spec: AgentSpec,
        messages: list[dict[str, Any]],
        private: PrivateState,
        nested: list[dict[str, Any]],
        last_check: float,
        spent: str,
    ) -> tuple[AgentResult, list[Any], bool]:
        """One last call, with nothing left to offer, for the answer itself.

        The agent is asked for the answer it would have given, from what it has
        already gathered. The call is charged like any other, and the record
        still says which budget was spent, so a spent budget is visible rather
        than disguised as a stage that simply took its time. The caller yields
        what the charge records; this returns the answer, those events, and
        whether an answer was actually produced.
        """

        completion = await self.authority.complete(
            ModelCall(
                run_id=run_id,
                agent=spec.name,
                messages=[
                    *messages,
                    {
                        "role": "user",
                        "content": WRITE_UP_RULE,
                    },
                ],
                provider=spec.provider,
                model=spec.model,
            )
        )

        now = time.monotonic()

        charged = list(
            await self.charge(
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
            )
        )

        try:
            action = extract_json(
                completion.text
            )

        except ModelOutputError:
            action = None

        if (
            not isinstance(action, dict)
            or action.get("type") != "final"
        ):
            return (
                self.exhausted(
                    private,
                    nested,
                    spent,
                ),
                charged,
                False,
            )

        return (
            AgentResult(
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
                # Recorded either way: the answer came after the budget ran out,
                # which is a different thing from an agent that finished early.
                exhausted_by=spent,
            ),
            charged,
            True,
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

    async def run_limit_reached(
        self,
    ) -> str | None:
        """The run-wide limit that has been reached, if any."""

        for dimension in RUN_WIDE_LIMITS:
            if await self.authority.exceeded(
                dimension
            ):
                return dimension

        # Spend measured per call is also weighed before it happens: a call
        # that, with those already under way, would likely pass the limit is
        # not made. Time is not projected; it warns the agent itself.
        fits = getattr(
            self.authority,
            "call_fits",
            None,
        )

        if fits is not None:
            for dimension in PROJECTED_LIMITS:
                if not await fits(
                    dimension
                ):
                    return dimension

        return None

    def exhausted(
        self,
        private: PrivateState,
        nested: list[dict[str, Any]],
        budget: str,
    ) -> AgentResult:
        label = {
            "wall_clock_seconds": "Wall-clock",
            "tokens": "Token",
            "cost_usd": "Cost",
        }.get(budget, "Tool-step")

        return AgentResult(
            content=(
                f"{label} budget exhausted "
                "before the agent produced "
                "a final answer."
            ),
            private=private,
            nested=nested,
            exhausted_by=budget,
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
        *,
        scope: str = "",
        resume: list[dict[str, Any]] | None = None,
        parents: tuple[dict[str, Any], ...] = (),
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
                scope=scope,
                resume=resume,
                parents=parents,
            ):
                if isinstance(
                    event,
                    AgentFinished,
                ):
                    child_result = event.result

                yield event

        except ActionStopped:
            # A delegated agent stopped on an action nobody settled: the run
            # stops, it does not carry on as if the delegation just failed.
            raise

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
        *,
        scope: str = "",
        snapshot: Any = None,
    ) -> ToolCalled | ToolDenied:
        """Execute one tool call, or refuse it and say why.

        An approved destructive call is recorded before it runs, when the run
        keeps an action log: `snapshot(action_id, observation)` is this
        agent's progress to record with it.
        """

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

        preview = None

        if tool.destructive and tool.precheck is not None:
            # Checked before anyone is asked: a call that cannot succeed is
            # refused without troubling the approver or running anything.
            try:
                preview = await tool.precheck(
                    arguments
                )

            except ToolRefused as exc:
                detail = (
                    str(exc)
                    or "The call was refused before approval."
                )

            except Exception:
                detail = (
                    "The call was refused before approval: "
                    "it could not be checked."
                )

            else:
                detail = None

            if detail is not None:
                return ToolDenied(
                    run_id=run_id,
                    agent=spec.name,
                    tool=tool.name,
                    reason="refused",
                    capabilities=(
                        tool.capabilities
                    ),
                    observation=(
                        f"Refused before approval: {detail}. "
                        "Return only the next JSON action."
                    ),
                    detail=detail,
                )

        approval = await self.authority.approve(
            tool,
            spec.name,
            arguments,
            run_id,
            preview,
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

        if tool.destructive and snapshot is not None and await self.records_actions():
            action_id = uuid.uuid4().hex
            begin = getattr(self.authority, "begin_action", None)

            if begin is not None and await begin(
                ActionStart(
                    action_id=action_id,
                    scope=scope,
                    agent=spec.name,
                    tool=tool.name,
                    arguments=arguments,
                    approval=str(approval),
                    frames=snapshot(action_id, None),
                    checkable=tool.check is not None,
                )
            ):
                return await self.send(
                    spec,
                    run_id,
                    tool,
                    arguments,
                    approval,
                    action_id,
                    private,
                    snapshot,
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

    async def records_actions(
        self,
    ) -> bool:
        """Whether this run's authority records external actions. Asked once."""

        if self._records is None:
            ask = getattr(self.authority, "records_actions", None)
            self._records = bool(await ask()) if ask is not None else False

        return self._records

    async def send(
        self,
        spec: AgentSpec,
        run_id: str,
        tool: Any,
        arguments: dict[str, Any],
        approval: Approval,
        action_id: str,
        private: PrivateState,
        snapshot: Any,
        *,
        resent: bool = False,
    ) -> ToolCalled:
        """Run a recorded action, and record how it ended.

        A clear failure is reported to the agent, as any tool error is. An
        unclear one is checked with the service: found, and the agent is told
        so; proved absent, and it is sent once more under the same action;
        anything else stops the run for a person. A call cancelled mid-flight
        stays recorded as being sent, for whoever ends the run to settle.
        """

        summary = f"{tool.name}({json.dumps(arguments, ensure_ascii=False)})"

        while True:
            try:
                observation = await self.execute_action(
                    tool.name,
                    arguments,
                    run_id,
                    spec.name,
                    action_id,
                )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                if classify(exc) == "failed":
                    observation = f"Tool error: {exc}"
                    private.check(summary + " -> failed")

                    await self.authority.finish_action(action_id, "failed", snapshot(action_id, observation))

                    return self.called(spec, run_id, tool, arguments, approval, observation, False, action_id, resent)

                await self.authority.finish_action(
                    action_id,
                    "uncertain",
                    snapshot(action_id, None),
                    reason=f"{tool.name} ended without a clear answer ({type(exc).__name__})",
                )

                if resent:
                    # Sent again and still no clear answer: a person decides.
                    raise ActionStopped(action_id, f"The outcome of {tool.name} is uncertain") from None

                resolution = await self.authority.check_action(action_id, tool, arguments, approval)

                if resolution.kind == "done":
                    private.check(summary)

                    return self.called(spec, run_id, tool, arguments, approval, resolution.observation, True, action_id, resent)

                if resolution.kind != "resend":
                    raise ActionStopped(action_id, resolution.reason or f"The outcome of {tool.name} is uncertain") from None

                await self.authority.begin_resend(action_id)
                resent = True

                continue

            private.check(summary)

            await self.authority.finish_action(action_id, "completed", snapshot(action_id, observation))

            return self.called(spec, run_id, tool, arguments, approval, observation, True, action_id, resent)

    async def execute_action(
        self,
        name: str,
        arguments: dict[str, Any],
        run_id: str,
        agent: str,
        action_id: str,
    ) -> str:
        """Run a tool as one action: it can see which, for an idempotency key."""

        token = current_action.set(action_id)

        try:
            return await self.tools.execute(
                name,
                arguments,
                run_id=run_id,
                agent=agent,
            )

        finally:
            current_action.reset(token)

    def called(
        self,
        spec: AgentSpec,
        run_id: str,
        tool: Any,
        arguments: dict[str, Any],
        approval: Approval,
        observation: str,
        ok: bool,
        action_id: str,
        resent: bool,
    ) -> ToolCalled:
        return ToolCalled(
            run_id=run_id,
            agent=spec.name,
            tool=tool.name,
            arguments=arguments,
            capabilities=tool.capabilities,
            approval=approval,
            observation=observation,
            output_chars=len(observation),
            truncated=observation.endswith(TRUNCATION_MARKER),
            ok=ok,
            action_id=action_id,
            resent=resent,
        )

    async def resume_action(
        self,
        spec: AgentSpec,
        run_id: str,
        restored: dict[str, Any],
        rest: list[dict[str, Any]],
        private: PrivateState,
        nested: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        depth: int,
        ancestry: tuple[str, ...],
        scope: str,
        parents: tuple[dict[str, Any], ...],
    ) -> AsyncIterator[Event]:
        """Finish the action saved progress stopped at, without asking the model again.

        A recorded result is used as it is. A write that was uncertain is
        whatever its settlement says: done (the agent is told, nothing is
        sent), or resend (sent once, with no new approval — the settlement was
        the approval). A delegation resumes inside the agent it was waiting
        on. Then the conversation continues as if the action had just ended.
        """

        action = restored.get("action") or {}
        kind = action.get("type")
        observation = restored.get("observation")
        label = (
            f"DELEGATION RESULT for {action.get('agent')}"
            if kind == "delegate"
            else f"TOOL RESULT for {action.get('name')}"
        )

        if observation is None and kind == "delegate":
            record = dict(restored.get("delegation") or {})
            prompt = str(record.pop("prompt", record.get("task", "")))
            plan = _Delegation(record=record, spec=self.agents.get(str(record.get("agent"))), task=prompt)

            if plan.spec is None:
                raise ActionStopped("", f"{record.get('agent')} is not an agent of this run any more")

            async for event in self.delegate(
                plan,
                depth,
                ancestry,
                run_id,
                scope=scope,
                resume=rest or None,
                parents=(*parents, restored),
            ):
                yield event

            nested.append(plan.record)
            nested.extend(plan.descendants)

            yield self.delegation_event(run_id, spec.name, plan)

            observation = plan.record["observation"]

        elif observation is None:
            action_id = str(restored.get("action_id") or "")
            settled = await self.authority.resolve_action(action_id)

            if settled.kind == "done":
                observation = settled.observation

            elif settled.kind == "resend":
                # What is sent is the recorded call — what was shown and
                # approved — never whatever the saved progress says; progress
                # that names a different tool is not trusted at all.
                if settled.arguments is None or (settled.tool and settled.tool != action.get("name")):
                    raise ActionStopped(action_id, "The recorded call to send again is not available")

                tool = self.tools.tool(settled.tool or action.get("name"))

                if tool is None:
                    raise ActionStopped(action_id, f"{action.get('name')} is not available to send again")

                await self.authority.begin_resend(action_id)

                event = await self.send(
                    spec,
                    run_id,
                    tool,
                    dict(settled.arguments),
                    Approval.APPROVED,
                    action_id,
                    private,
                    lambda action_id, observation: [*parents, {**restored, "observation": observation,
                                                               "action_id": action_id}],
                    resent=True,
                )

                observation = event.observation

                yield event

            else:
                raise ActionStopped(action_id, settled.reason)

        messages.append(
            {
                "role": "assistant",
                "content": str(restored.get("reply") or ""),
            }
        )

        messages.append(
            {
                "role": "user",
                "content": (
                    f"{label}:\n"
                    f"{observation}\n\n"
                    "Continue. Return only the next JSON action."
                ),
            }
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

        actions = (
            ""
            if self.config.max_tool_steps is None
            else (
                f"You have at most {self.config.max_tool_steps} actions for this stage, and no more.\n"
                "Every tool call, note or delegation is one action. Spend them on what the answer\n"
                "needs, and keep enough of them to write it.\n"
            )
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
{actions}
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
