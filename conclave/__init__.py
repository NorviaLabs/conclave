"""Build multi-agent systems out of bounded agent runs.

The core ships no agents, no tools and no domain model. You supply the
agents, the tools, what they share between them and the control flow that
connects them; this library supplies the loop and the guards that keep a run
inside the limits you set.

`conclave.tools` is deliberately not imported here: importing the core never
pulls a network dependency or shells out to anything.

    runtime = ToolAgentRuntime(provider, tools, RunBudget(), agents)
    async for event in runtime.stream(AGENTS["researcher"], task):
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
from .budget import (
    DIMENSION_LABELS,
    RunBudget,
)
from .config import (
    DEFAULT_CONFIG,
    RunConfig,
)
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
    Completion,
    ModelProvider,
    OllamaProvider,
    OpenCodeProvider,
    Usage,
)
from .registry import (
    TRUNCATION_MARKER,
    ToolRegistry,
)
from .runtime import (
    ToolAgentRuntime,
    fan_out,
)
from .spec import (
    AgentSpec,
    Capability,
    Finding,
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
    "Completion",
    "DEFAULT_CONFIG",
    "DIMENSION_LABELS",
    "Delegation",
    "Event",
    "Finding",
    "JSON_ONLY_RULE",
    "MergeFunc",
    "MergeResult",
    "ModelOutputError",
    "ModelProvider",
    "NoteRecorded",
    "OllamaProvider",
    "OpenCodeProvider",
    "PrivateState",
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
    "ToolFunc",
    "ToolRegistry",
    "Usage",
    "append_merge",
    "approve_all",
    "as_list",
    "complete_json",
    "deny_all",
    "extract_json",
    "fan_out",
    "refusal_text",
    "replace_merge",
    "report_usage",
    "request_approval",
]
