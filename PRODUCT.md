# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Non-technical builders: people who cannot or do not want to write agent code,
but want a team of AI agents and people to do real work for them. The current
target use cases are software and product development, such as taking a GitHub
issue to a reviewed pull request, comparing options before deciding what to
ship, and writing an evidence-backed brief. The builder states a goal, adjusts
the suggested team and plan, runs it, and takes part at the human review steps.

Conclave itself stays generic. Support, research and content starters still
exist; they are secondary audiences, not the current focus.

## Product Purpose

Conclave lets someone build, run and observe a multi-agent application without
writing code. An application is a definition: its members (AI or human), their
tools, the shared results, the workflow of steps, the request form and its
limits. Studio is the no-code builder and work surface on top of a reusable,
domain-neutral runtime; the same definition also runs from the CLI and over
REST.

Success is a run that finishes real work and shows its evidence. For example,
the software-delivery flow completed NorviaLabs/forge#857 as PR forge#866:
planned, implemented, opened, verified and reviewed.

## Positioning

Compared with LangGraph, CrewAI, AutoGen and the OpenAI Agents SDK, which are
built in code, Conclave is defined by three commitments:

- **Truthful evidence.** Results are bound to real sources, commits, test runs
  and CI checks. Nothing is invented: no sources, files, usage, cost or connected
  accounts that were not actually returned. Blockers and missing evidence are
  reported as such.
- **Uses the AI you already have.** Runs on the Codex, OpenCode or Ollama setup
  already on the machine. Provider authentication stays with that backend;
  Conclave never guesses a model name and never silently replaces a backend the
  user chose.
- **Bounded, gated runs.** A run cannot spin, delegation is budgeted, and any
  action that changes the world (writing files, committing, pushing, opening or
  merging a pull request, sending) needs an approver unless the builder
  explicitly turns on autonomous execution.

## Operating Context

- Studio is used both locally (`conclave serve` against the user's own folders
  and repositories) and hosted (Google sign-in, per-tenant workspaces). Both are
  first-class.
- One screen holds building, testing and using an application. The canvas is
  the plan while it is edited and the run while it runs.
- Runs work against a selected source folder or repository, and against
  connections (GitHub, issue trackers, chat, documents) that the builder grants
  to each application and operation.
- Human members approve, reject or request changes, or supply typed input. Their
  decisions are recorded in the run.

## Capabilities and Constraints

- Starting from a goal: Studio suggests a team, results and plan; every
  suggestion can be inspected and changed before the plan is created.
- A catalogue of starters, role presets, tools, result types and connectors,
  grouped by audience. Featured starters are currently software delivery,
  options comparison and research brief.
- Visual planning of any directed acyclic plan with multiple roots, branches and
  joins. Cycles and dangling references are rejected. Card layout never decides
  execution order.
- Step-level testing with pinned fixtures; every run keeps the definition
  version it ran.
- Plain language by default. Model configuration, state keys, raw output and
  integration settings sit behind advanced disclosures. Errors, denied actions,
  approval requirements and missing authorization are never hidden.
- Limits live in each definition's `run_config` and `run_budget`; many default
  to unlimited.
- The catalogue lists some tools and connectors that have no implementation yet.
  They must not be presented as working.

## Brand Commitments

- No characters, franchises or mascots. Members appear under the names the
  builder gives them, with an AI or Human tag; the user retired the Avengers
  crew and the office view on 15 October 2026.
- Grown-up, not cute: Studio reads as a tool trusted with real commits and
  merges.

## Evidence on Hand

- A real end-to-end run: NorviaLabs/forge#857 delivered as PR forge#866 by the
  software-delivery flow.
- `thesis.md`: the founder's comparison of LangGraph, OpenAI Agents SDK, AutoGen
  and CrewAI on a four-agent recommendation report with a human approval gate.
- No customers, testimonials, usage numbers, benchmarks or pricing exist.
  Future work must not invent them.

## Product Principles

1. Show what actually happened. Every claim in the UI traces to a recorded event,
   source or tool result; an unknown stays unknown.
2. A person stays in charge of consequences. Approval gates are part of the
   product, not friction to design away.
3. No code required, nothing hidden. Plain language first, with full technical
   detail one disclosure away.
4. One definition, everywhere. Studio never creates something the CLI and REST
   API cannot run.
5. Generic core, focused defaults. Software and product work leads today through
   catalogue data, never through domain logic in the runtime.

## Accessibility & Inclusion

Every canvas interaction has a keyboard alternative: arrow keys move a step,
ports and edges are real buttons, and Enter opens a step's settings. Text,
actions, statuses, focus and placeholders must meet contrast thresholds
(checked by `studio/scripts/check-colors.mjs`).
