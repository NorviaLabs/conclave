"""Build multi-agent systems out of bounded agent runs.

The core ships no agents, no tools and no domain model. You supply the
agents, the tools, what they share between them and the control flow that
connects them; this library supplies the loop and the guards that keep a run
inside the limits you set.

`conclave.tools` is deliberately not imported here: importing the core never
pulls a network dependency or shells out to anything.

    config = RunConfig(
        max_tool_steps=8,
        max_notes=12,
        max_note_chars=300,
        max_tool_output_chars=20_000,
        fan_out_limit=3,
    )

    budget = RunBudget(
        max_delegations=15,
        max_per_agent=4,
        max_depth=2,
    )

    runtime = ToolAgentRuntime(
        provider, tools, budget, agents, config=config
    )

    async for event in runtime.stream(AGENTS["writer"], task):
        print(event)
"""

from .approval import (
    Approval,
    ApprovalRequest,
    Approver,
    approve_all,
    deny_all,
    refusal_text,
    request_approval,
)
from .authority import (
    InProcessAuthority,
    ModelCall,
    RunAuthority,
)
from .budget import (
    DIMENSION_LABELS,
    RunBudget,
)
from .config import RunConfig
from .events import (
    AgentFinished,
    AgentStarted,
    AgentStep,
    BudgetExceeded,
    Delegation,
    Event,
    NoteRecorded,
    event_payload,
    rebuild_event,
    RunFinished,
    RunStarted,
    StoreChanged,
    ToolCalled,
    ToolDenied,
)
from .protocol import (
    JSON_ONLY_RULE,
    AgentResult,
    ModelOutputError,
    PrivateState,
    as_list,
    complete_json,
    extract_json,
    report_usage,
)
from .providers import (
    CodexProvider,
    Completion,
    ModelProvider,
    OllamaProvider,
    OpenCodeProvider,
    Usage,
    resolve_provider,
)
from .registry import (
    TRUNCATION_MARKER,
    ToolExecutor,
    ToolInvocation,
    ToolRegistry,
)
from .runtime import (
    ToolAgentRuntime,
    fan_out,
)
from .spec import (
    AgentSpec,
    Capability,
    Tool,
    ToolFunc,
)
from .store import (
    MergeFunc,
    MergeResult,
    SharedStore,
    StoreSchema,
    append_merge,
    replace_merge,
)


__all__ = [
    "AgentFinished",
    "AgentResult",
    "AgentSpec",
    "AgentStarted",
    "AgentStep",
    "Approval",
    "ApprovalRequest",
    "Approver",
    "BudgetExceeded",
    "Capability",
    "CodexProvider",
    "InProcessAuthority",
    "Completion",
    "DIMENSION_LABELS",
    "Delegation",
    "Event",
    "JSON_ONLY_RULE",
    "MergeFunc",
    "MergeResult",
    "ModelCall",
    "ModelOutputError",
    "ModelProvider",
    "NoteRecorded",
    "OllamaProvider",
    "OpenCodeProvider",
    "PrivateState",
    "RunAuthority",
    "RunBudget",
    "RunConfig",
    "RunFinished",
    "RunStarted",
    "SharedStore",
    "StoreChanged",
    "StoreSchema",
    "TRUNCATION_MARKER",
    "Tool",
    "ToolAgentRuntime",
    "ToolCalled",
    "ToolDenied",
    "ToolExecutor",
    "ToolFunc",
    "ToolInvocation",
    "ToolRegistry",
    "Usage",
    "append_merge",
    "approve_all",
    "as_list",
    "complete_json",
    "deny_all",
    "event_payload",
    "extract_json",
    "fan_out",
    "rebuild_event",
    "refusal_text",
    "replace_merge",
    "report_usage",
    "request_approval",
    "resolve_provider",
]
