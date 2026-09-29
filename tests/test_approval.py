"""Tests for the capability gate: what may change the world, and who says so."""

import asyncio

from conclave import (
    Approval,
    ApprovalRequest,
    Tool,
    approve_all,
    deny_all,
    refusal_text,
    request_approval,
)
from conclave.spec import Capability


async def ok() -> str:
    return "done"


def tool(
    *capabilities: Capability,
) -> Tool:
    return Tool(
        name="tool",
        description="d",
        parameters={},
        func=ok,
        capabilities=frozenset(capabilities),
    )


def test_a_tool_that_declares_nothing_is_destructive():
    assert tool().destructive


def test_a_writing_tool_is_destructive():
    assert tool(Capability.WRITE).destructive


def test_reading_tools_are_not_destructive():
    assert not tool(Capability.READ).destructive

    assert not tool(
        Capability.READ,
        Capability.NETWORK,
        Capability.SHELL,
    ).destructive


# =============================================================================
# Asking
# =============================================================================


def ask(
    target: Tool,
    approver=None,
    lock=None,
) -> Approval:
    return asyncio.run(
        request_approval(
            target,
            "repo-researcher",
            {"path": "a.py"},
            approver,
            lock,
        )
    )


def test_harmless_calls_are_approved_without_asking():
    calls = []

    async def approver(request):
        calls.append(request)

        return False

    assert (
        ask(tool(Capability.READ), approver)
        is Approval.NOT_REQUIRED
    )

    assert calls == []


def test_destructive_calls_are_refused_without_an_approver():
    assert ask(tool(Capability.WRITE)) is Approval.REFUSED


def test_an_approver_is_told_what_it_is_approving():
    seen = []

    def approver(request):
        seen.append(request)

        return True

    assert (
        ask(tool(Capability.WRITE), approver)
        is Approval.APPROVED
    )

    assert seen == [
        ApprovalRequest(
            agent="repo-researcher",
            tool="tool",
            arguments={"path": "a.py"},
            capabilities=frozenset(
                {Capability.WRITE}
            ),
        )
    ]


def test_an_async_approver_works():
    async def approver(request):
        return True

    assert (
        ask(tool(Capability.WRITE), approver)
        is Approval.APPROVED
    )


def test_a_refusing_approver_refuses():
    def approver(request):
        return False

    assert (
        ask(tool(Capability.WRITE), approver)
        is Approval.REFUSED
    )


def test_the_bundled_helpers():
    async def run():
        assert not await deny_all(None)
        assert await approve_all(None)

    asyncio.run(run())


def test_the_lock_serialises_questions():
    """Two agents asking at once must not overlap in the approver."""

    inside = 0
    overlapped = False

    async def approver(request):
        nonlocal inside, overlapped

        inside += 1

        if inside > 1:
            overlapped = True

        await asyncio.sleep(0.01)

        inside -= 1

        return True

    async def main():
        lock = asyncio.Lock()

        await asyncio.gather(
            *(
                request_approval(
                    tool(Capability.WRITE),
                    f"agent-{index}",
                    {},
                    approver,
                    lock,
                )
                for index in range(3)
            )
        )

    asyncio.run(main())

    assert not overlapped


def test_refusal_text_tells_the_model_to_stop():
    text = refusal_text("save_report")

    assert "save_report" in text
    assert "Do not repeat this call" in text
    assert "evidence" in text
