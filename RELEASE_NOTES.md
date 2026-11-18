# Release notes

Twelve features for Conclave Studio and its host, each with its own tests,
Studio checks, security review and performance review. Full request and response
details are in [REST_API.md](REST_API.md); each feature's section there is named
below.

## Upgrading

- **Storage:** the run store gains migrations `0007_review_rounds` to
  `0011_run_files`, and the application store gains tables for people and roles.
  Both are applied automatically when a store is opened; existing runs and
  applications are kept.
- **Applications are now restricted to their people.** An application created
  from now on belongs to whoever creates it, and only the people it names can see
  it or its runs. Applications saved before this release name nobody and stay
  open to everyone in the workspace until someone limits access (see 12).
- **New run events:** `ModelCalled`, `RetryScheduled`, `RetryCancelled`,
  `StageSkipped`, `RevisionRequested`, `BudgetWarning` (extended),
  `OutputFileStored`, `OutputFileSkipped` and `ContinueWithheld`. `StageFinished`
  now carries the step's `usage`, and may have the status `timed_out`.
- **Feature flags** in `GET /api/runtime` → `features`: `event_triggers`,
  `budget_warnings`, `run_usage`, `run_compare`, `output_files`,
  `step_time_limits` and `application_roles`.
- **Tools that run commands** (git, test runs, pull requests) now run them
  through `conclave/tools/processes.py`, so a stopped step stops its command too.

## 1. A reviewer's requested changes are made, then reviewed again

When a reviewer answers **changes requested** with a comment, the step they
reviewed runs again with that comment, and the same review is asked again. The
whole run no longer stops.

- Each round is recorded and survives a restart, a stop and a Continue.
- Steps can name which earlier steps a review sends back (`revises`) and how many
  rounds are allowed (`max_revisions`).
- **Rejected** still ends the run.

Studio shows each round, the comment and what it sent back.
REST_API.md: "Revision rounds".

## 2. Runs continue by themselves after a backend outage

A run stopped by a model backend's timeout, outage or usage limit is tried again
automatically after a backoff, instead of waiting for someone to press Continue.

- The attempts and waits are set with the `CONCLAVE_RETRY_*` settings.
- The run record says when the next attempt is due.
- **Don't retry** (`POST /api/runs/{id}/retry/cancel`) stops it, and a pause
  stops it too.
- Failures that aren't caused by the backend are never retried.

REST_API.md: "Why a run stopped".

## 3. Sources are checked against what the step actually read

Each source a record cites is checked against what its step (and any member it
asked for help) actually read or fetched. Records carry `source_checks`, one per
source:

- **found**: the step read it;
- **not_found**: it didn't, including a link seen only in search results;
- **not_checkable**: free text, a person's citation, or a read through a tool
  whose reads can't be interpreted.

Agents can't supply their own checks, and tool output is never stored.
REST_API.md: "Source checks".

## 4. Steps that run only when an earlier result says so

A step can have a `condition` on an earlier step's output (equals, not equals,
in, not in, contains). When it doesn't hold:

- the step is skipped, along with anything only it leads to;
- a skip costs nothing and is recorded in words (`StageSkipped`);
- a skipped review blocks the steps after it.

Studio has a condition editor on the step and shows skipped steps and why they
were skipped. REST_API.md: "Conditional steps".

## 5. Who may answer reviews

An application's `review_policy` names who may answer its reviews (people or
`@domain` entries) and whether the person who started a run may answer. A review
step can require several people to answer (`answers_required`, 1–50).

The run continues automatically once enough people have answered, acting as the
person who started it. Named reviewers can open the run they're asked to review.
REST_API.md: "Who may answer reviews".

## 6. Application versions

Every saved change to an application is kept as a version, with who saved it and
a one-line summary.

- Two versions can be compared area by area, in words ("was 1, now 3").
- Restoring a version saves its content as a new version.
- Versions from before this release are recovered from the runs that used them.
- Each run links to the version it ran.

Studio has a Versions dialog. REST_API.md: "Application versions".

## 7. Runs started by an event

An application can be started by a signed event sent to its own address
(`POST /api/events/{endpoint}`).

- The sender signs each event with a secret, which is shown only once.
- Old and repeated deliveries are refused; an event already seen returns the run
  it started.
- Recent events are listed with what happened to each.
- Rotating the secret is owner-only.

Studio has the setup, the address, the event log and "started by an event" in
run history. REST_API.md: "Runs started by an event".

## 8. What a run used, and warnings before a limit

Each model call is recorded (`ModelCalled`: member, backend, measured tokens and
time, never the prompt or reply). Every finished step carries its own totals, and
the run record adds them up per step (`usage`).

A run with a limit warns once, at 80% of it, unless the application sets its own
threshold (`budget_warnings`; `null` turns the warning off). Studio shows the
usage table, limit bars, per-step usage and the warning; the CLI prints it.
REST_API.md: "What a run used".

## 9. Compare two runs

Pick two runs of the same application in Run history and compare them step by
step (`GET /api/runs/compare?a=&b=`). It covers:

- the version each run used, and what changed between them;
- how the requests differed;
- steps skipped by routing;
- output changes: a line diff for text, record by record otherwise;
- how each review went;
- what each step used.

Large text diffs are bounded so a comparison can't tie up the host.
REST_API.md: "Comparing two runs".

## 10. Results stored as files

A step whose output is a document, dataset or artifact leaves a real file, made
by the host from the step's checked output: Markdown, CSV or JSON. The model
never names a file, path or link.

- Files are stored beside the run with their size and SHA-256.
- They are served only from a checked link (`GET /api/runs/{run}/files/{file}`):
  the file must belong to that run and still be exactly what was stored,
  otherwise the answer is `410`.
- CSV cells that a spreadsheet would run as formulas are prefixed with `'`.
- An output can choose another format or no file.

Studio shows each file with its first lines, a download button and earlier
versions. Addresses an agent wrote into an artifact are shown as text, never as
links. REST_API.md: "Stored output files".

## 11. A step's own time limit

A step can have `time_limit_seconds` (10 seconds to 24 hours). Time spent
waiting for a person to approve an action doesn't count.

- A step that runs over is stopped while it works, together with any command it
  started, and is recorded as timed out.
- The run stops once the steps running alongside it finish.
- Continue runs the step again with its full limit; a timeout is never retried
  automatically.

Studio has the setting, a clock label on the step card and a timed-out notice.
REST_API.md: "A step's time limit".

## 12. People and roles on each application

Each application has people with a role, and every application and run route
checks it:

| Role | Can |
| --- | --- |
| viewer | read the application and its runs |
| runner | also run it, decide actions and answer reviews |
| editor | also change and save it |
| owner | also choose who has access, turn events on and delete it |

- Someone with no role is answered as if the application didn't exist (`404`).
- Someone whose role is too low is told why (`403`).
- Schedules, events, hooks and continuing after a review act only for someone who
  can still run the application.
- Names are matched ignoring capitals.
- An application always keeps at least one owner.

Studio has a People dialog, your role beside the application's name, and
controls you can't use are disabled with the reason.
REST_API.md: "People and roles".

## Known limits

- Stored files are kept as long as their run. There is no quota or clean-up yet.
- For an agent loop running in a sandbox, a step's clock doesn't pause while a
  person approves an action.
- Three Studio checks (`check-approval-browser`, `check-browser` and
  `check-canvas-browser`) need a Playwright browser and weren't run here.
