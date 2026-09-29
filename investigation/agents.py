"""The five specialists this application investigates with.

These are application data, not library defaults: the core knows nothing
about repositories, GitHub or web research. Swap this catalog and the same
runtime builds something else entirely.

The handoff schema — `findings` with their provenance, and `open_leads` —
is declared here too. The runtime passes those fields through without
knowing what they mean, which is what lets a different application ask for
`patches` or `translations` instead.
"""

from conclave import AgentSpec


HANDOFF = """
Finish with a final action carrying:

- content: your findings, in prose
- findings: a list of claims, each with a
  claim, its provenance (path, commit, issue, PR or URL),
  and a confidence from 0.0 to 1.0
- open_leads: a list of what you could not resolve,
  empty when there is nothing left open

Gather evidence rather than guessing, and never invent
tool results.
""".strip()


def specialist(
    name: str,
    instructions: str,
    tools: set[str] | None = None,
) -> AgentSpec:
    """One agent, with this application's handoff bolted on."""

    return AgentSpec(
        name=name,
        instructions=(
            instructions.strip()
            + "\n\n"
            + HANDOFF
        ),
        tools=set(tools or ()),
    )


AGENTS = {
    "repo-researcher": specialist(
        "repo-researcher",
        """
Investigate the local repository and Git history.

Gather concrete evidence.
Do not modify anything.
""".strip(),
        {
            "list_files",
            "read_file",
            "search_files",
            "git_status",
            "git_log",
            "git_diff",
            "git_show",
        },
    ),
    "github-researcher": specialist(
        "github-researcher",
        """
Investigate GitHub issues, pull requests,
reviews, comments, and history.

Gather evidence.
Never mutate GitHub.
""".strip(),
        {
            "github_issue",
            "github_pr",
            "github_search_issues",
            "github_search_prs",
        },
    ),
    "web-researcher": specialist(
        "web-researcher",
        """
Research current external information.

Prefer primary and official sources.
Preserve URLs and provenance.
""".strip(),
        {
            "web_search",
            "web_fetch",
        },
    ),
    "architect": specialist(
        "architect",
        """
Reason about architecture and tradeoffs
using only supplied evidence.

Call out missing evidence rather than guessing.
""".strip(),
    ),
    "critic": specialist(
        "critic",
        """
Challenge hypotheses and conclusions.

Look for contradictions,
unsupported assumptions,
and missing evidence.
""".strip(),
    ),
}
