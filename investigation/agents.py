"""The five specialists this application investigates with.

These are application data, not library defaults: the core knows nothing
about repositories, GitHub or web research. Swap this catalog and the same
runtime builds something else entirely.
"""

from conclave import AgentSpec


AGENTS = {
    "repo-researcher": AgentSpec(
        name="repo-researcher",
        instructions="""
Investigate the local repository and Git history.

Gather concrete evidence.
Do not modify anything.
""".strip(),
        tools={
            "list_files",
            "read_file",
            "search_files",
            "git_status",
            "git_log",
            "git_diff",
            "git_show",
        },
    ),
    "github-researcher": AgentSpec(
        name="github-researcher",
        instructions="""
Investigate GitHub issues, pull requests,
reviews, comments, and history.

Gather evidence.
Never mutate GitHub.
""".strip(),
        tools={
            "github_issue",
            "github_pr",
            "github_search_issues",
            "github_search_prs",
        },
    ),
    "web-researcher": AgentSpec(
        name="web-researcher",
        instructions="""
Research current external information.

Prefer primary and official sources.
Preserve URLs and provenance.
""".strip(),
        tools={
            "web_search",
            "web_fetch",
        },
    ),
    "architect": AgentSpec(
        name="architect",
        instructions="""
Reason about architecture and tradeoffs
using only supplied evidence.

Call out missing evidence rather than guessing.
""".strip(),
        tools=set(),
    ),
    "critic": AgentSpec(
        name="critic",
        instructions="""
Challenge hypotheses and conclusions.

Look for contradictions,
unsupported assumptions,
and missing evidence.
""".strip(),
        tools=set(),
    ),
}
