BOUND UI Overhaul TODO

Target: v0.9.xPrimary goal: Turn the current local lineage dashboard into a realtime execution control surface centered on the agent plan, current state, decisions, evidence, and divergence from the original plan.

0. Product direction

BOUND UI must explain execution, not merely render logs.

The first five seconds of any screen must answer:

What task is the agent trying to complete?

What is it doing right now?

Which plan step is active?

What did BOUND decide?

Why did BOUND make that decision?

What happens next?

Has execution diverged from the original plan?

The UI remains:

local-first

read-only for v0.9.x

deterministic

driven by runtime and lineage data

usable without a hosted backend

free of emojis and platform-dependent pictograms

Use a small consistent icon system based on inline SVG or CSS shapes. Do not use emoji characters.

1. Scope

In scope

Home / Runs screen

Run detail / Execution screen

plan.md discovery and rendering

Plan progress

Original plan versus actual execution

Replan visualization

Current action and next action

Decision explanation

Evidence status

Candidate-aware UI structure

Timeline and raw lineage as secondary views

Realtime updates through SSE

Responsive local dashboard

Empty, loading, incomplete, stale, and corrupt-data states

Out of scope for this release

Editing plans

Editing policies

Starting or stopping agents from the UI

Manual retry, replan, rollback, or accept buttons

Hosted accounts

Remote persistence

Collaborative use

Benchmark configuration

Candidate merge controls

Full code-diff editor

WebSocket migration unless SSE proves insufficient

2. Required information architecture

Primary navigation

Runs
System

Do not create five separate top-level pages for Execution, Evidence, Artifacts, and Replay. Those belong inside a run detail page.

Run detail navigation

Execution
Evidence
Artifacts
Replay

Default tab: Execution

Execution

task and status

plan progress

current plan step

current action

latest decision

reason

next action

execution plan

compact recent activity

Evidence

verification checks

provenance

failure details

score and threshold

collector details

missing evidence

Artifacts

checkpoints

workspace

worktree

relevant generated files

patches and diffs when available

plan artifact information

Replay

full timeline

event stream

causal metadata

raw lineage

3. Home / Runs screen

3.1 Purpose

The home screen must communicate which runs are active, which require attention, and what each active run is currently doing.

It must not render the same run twice as both a card and a table.

3.2 Page structure

BOUND                                      Local / Connected

Active Runs

[run card]
[run card]

Recent Runs

[compact list or table]

3.3 Active run card

Each active run card must show:

task title

run status

plan progress percentage or completed/total

current plan step

current action

latest decision

short decision reason

candidate label

duration

last activity time

live/stale connection state

Example:

Add registration validation                         RUNNING

Plan progress                                  3 of 6

Current step
Implement validator

Current action
Running unit tests

Decision                                      REPLAN

Reason
Coverage is below the required threshold.

Candidate A                         02:18 elapsed

3.4 Completed and failed runs

Use a compact list or table containing:

task

final status

final decision

plan completion

candidate

duration

finished time

Failed, interrupted, and incomplete runs require a clear state distinction.

3.5 Home screen filters

Add client-side filters for:

active

completed

failed/interrupted

decision

candidate

task text

Do not add advanced search in this release.

3.6 Empty state

Show:

No runs recorded

Start a BOUND-controlled execution to see plan progress,
decisions, evidence, checkpoints, and replay data here.

bound run start "your task"

3.7 Realtime behavior

Active run cards update through SSE.

Do not refresh the complete page every 15 seconds.

When SSE disconnects, show Reconnecting.

After a configurable stale threshold, show Data may be stale.

Completed runs may move from Active Runs to Recent Runs without a hard reload.

4. Run detail / Execution screen

4.1 Above-the-fold layout

The first viewport must include:

Task and status
Plan progress
Current plan step
Current action
Decision
Reason
Next action

Do not put:

run ID

policy hash

workspace path

raw JSON

complete timeline

above the fold.

4.2 Header

Show:

task

status

candidate

started time

elapsed duration

live connection state

Secondary metadata belongs in a collapsible details panel.

4.3 Primary state panel

Create one prominent state panel.

Required fields:

current phase

current plan step

current action

latest decision

reason

next action

attempt number

retry/replan budget when available

checkpoint status

Example:

CURRENT EXECUTION

Phase
Verification

Plan step
Run unit tests

Action
Running pytest

Decision
REPLAN

Reason
2 tests failed and coverage is 0.63; required threshold is 0.80.

Next
Restore checkpoint and retry the implementation.

Attempt 2 of 3

4.4 Decision presentation

The latest decision must be visually dominant but not presented without context.

Supported decisions:

ACCEPT

RETRY

REPLAN

ROLLBACK

Every decision card must answer:

candidate decision

final gated decision

assurance

triggering criterion

observed score

threshold

missing or weak evidence

next action

Do not rely only on color.

Use text labels, border treatment, and inline SVG icons.

5. Plan integration

5.1 Product requirement

plan.md is a first-class run artifact.

The UI must not independently invent plan state from arbitrary timeline events when structured plan state is available.

The preferred flow is:

Agent produces plan.md
        |
BOUND runtime loads and snapshots plan
        |
Runtime emits plan events
        |
UI renders the event-derived plan view

5.2 Plan discovery

Implement plan discovery in this order:

Explicit plan path recorded in runtime config or run metadata.

Candidate workspace <workspace>/plan.md.

Repository root plan.md.

Known alternative casing:

PLAN.md

Plan.md

No plan found.

Do not recursively scan the whole repository.

5.3 Plan snapshot

At run or candidate start:

read the plan file

calculate a content hash

persist the original content or parsed representation

record the source path relative to the workspace

record load timestamp

preserve the original plan throughout the run

The original plan must not be overwritten when a replan occurs.

5.4 Plan parser

Support a deliberately narrow Markdown subset.

Recognize:

# Plan

- [ ] Inspect code
- [x] Add validator
- [ ] Run tests

and:

1. Inspect code
2. Add validator
3. Run tests

and headings with nested checklist items:

## Implementation

- [ ] Add validator
- [ ] Add tests

Parsed plan step fields:

PlanStepView:
    step_id: str
    title: str
    description: str | None
    ordinal: int
    depth: int
    status: PlanStepStatus
    origin: PlanStepOrigin
    parent_step_id: str | None
    source_line: int | None
    linked_runtime_step_ids: list[str]
    started_at: datetime | None
    completed_at: datetime | None
    decision: str | None
    attempt_count: int

Statuses:

pending

running

completed

failed

blocked

skipped

unknown

Origins:

original

inserted

modified

replacement

5.5 Plan event model

Add or reserve these v3 event tags:

plan.loaded
plan.updated
plan.step.started
plan.step.completed
plan.step.failed
plan.step.skipped
plan.step.blocked
plan.step.inserted
plan.step.modified
plan.step.removed

Minimum fields for all plan events:

schema_version

event_id

run_id

candidate_id

timestamp

sequence

parent_event_id

plan_id

plan_version

plan_hash

Plan step events also require:

plan_step_id

title

ordinal

origin

linked_runtime_step_id when available

5.6 Backwards-compatible fallback

Older runs will not have plan events.

For those:

attempt to load a persisted plan artifact

render all steps as unknown unless status can be matched confidently

clearly label the panel Plan status unavailable for this run

do not pretend inferred state is verified

do not mark steps complete based solely on loose text similarity

5.7 Linking runtime steps to plan steps

Use explicit linkage when possible:

runtime_step.plan_step_id

or event field:

plan_step_id

Fallback linkage may use:

normalized contract ID

normalized step title

exact plan step reference reported by the adapter

Never use fuzzy matching as a source of truth.

When fallback matching is used, mark linkage as inferred.

5.8 Plan versions and replan

A replan creates a new immutable plan version.

Store:

PlanVersion:
    plan_id: str
    version: int
    hash: str
    source: str
    created_at: datetime
    reason: str | None
    parent_version: int | None
    steps: list[PlanStep]

Do not mutate the original plan in place.

5.9 Replan semantics

On REPLAN:

preserve the original plan version

create a new plan version

identify inserted, removed, modified, and retained steps

link the replan to the decision event

record the reason

record the checkpoint before or after replan when available

5.10 Plan progress calculation

Primary progress:

completed original/current executable steps
-------------------------------------------
total non-skipped executable steps

Rules:

skipped steps do not count in the denominator

blocked steps remain incomplete

parent headings do not count unless explicitly executable

inserted replan steps count toward the current-plan denominator

show both values when the plan changed:

Original plan: 3 of 5
Current plan: 4 of 7

Do not calculate progress from event count.

6. Execution plan component

6.1 Default presentation

The Execution tab must contain a prominent plan list.

Example:

EXECUTION PLAN                                      4 of 7

01  Inspect existing flow                    COMPLETE
02  Design validation                        COMPLETE
03  Implement validation                     FAILED
04  Refactor validation                      RUNNING    INSERTED
05  Add unit tests                           PENDING
06  Run verification                         PENDING
07  Update documentation                     PENDING

6.2 Step row content

Each row may show:

ordinal

title

status

origin badge when not original

attempt count

linked decision

duration

current-action indicator

evidence summary

Do not show all metadata by default.

6.3 Current plan step

The current step must be easy to find without relying only on scrolling or color.

Use:

left border

CURRENT text label

stronger weight

auto-scroll on live updates only when the user is already near the active item

6.4 Nested steps

Support two levels initially:

Implementation
  Add validator
  Add tests

Deeper nesting may be flattened with indentation.

6.5 Plan step details drawer

Selecting a plan step opens an inline drawer or side panel containing:

description

source location

runtime step links

attempt history

decisions

evidence

start/completion time

related checkpoint

origin

modification history

No navigation to a new page.

7. Plan versus reality

7.1 Purpose

Show where actual execution diverged from the original plan.

7.2 Presentation

Add a secondary view inside the Execution tab:

Plan | Plan vs Reality

7.3 Required comparison

Original plan:

Inspect
Implement
Test
Document

Actual execution:

Inspect
Implement
Test failed
REPLAN
Refactor
Implement
Test
Document

7.4 Divergence types

Recognize:

inserted step

removed step

modified step

repeated step

failed step

skipped step

rollback

candidate fork

checkpoint restore

7.5 Visual approach

Use a two-column or aligned-row comparison.

Do not create a complex graph in v0.9.x.

Example:

ORIGINAL PLAN                         ACTUAL EXECUTION

Inspect                               Inspect
Implement                             Implement
Test                                  Test failed
                                      Replan
                                      Refactor
                                      Implement
Document                              Test
                                      Document

7.6 Divergence summary

Show:

Plan divergence

Inserted steps       2
Repeated steps       1
Failed steps         1
Replans              1
Rollbacks            0

8. Candidate-aware design

8.1 Current release

Even when only one candidate exists, display:

Candidate A

This prevents redesign when branching is introduced.

8.2 Multiple candidates

When multiple candidates exist:

show candidate selector in the run header

show state per candidate

preserve per-candidate plan versions

preserve per-candidate evidence

preserve per-candidate checkpoints

allow a combined run-level overview

8.3 Candidate cards

Example:

Candidate A    RUNNING    Step 4 of 7    REPLAN
Candidate B    PENDING    Step 0 of 6    -
Candidate C    FAILED     Step 3 of 5    ROLLBACK

8.4 Candidate tree

Defer the graphical candidate tree.

For v0.9.x, use a list with parent candidate references.

9. Evidence screen

9.1 Purpose

Evidence should read like a verification dashboard, not a provenance dump.

9.2 Verification list

Example:

VERIFICATION

Tests                 FAIL
Lint                  PASS
Typecheck             MISSING
Coverage              FAIL
Security              PASS

9.3 Per-check detail

Each check shows:

status

provenance

independently verified or claimed

collector

command

exit code

score

threshold

duration

timestamp

failure summary

raw output toggle

9.4 Status distinctions

Keep these visually and semantically distinct:

PASS

FAIL

MISSING

CLAIMED

VERIFIED

UNVERIFIED

INVALID

STALE

COLLECTION FAILED

Do not map claimed to generic failure red.

9.5 Evidence related to decisions

At the top of Evidence, show:

Decision-driving evidence

Only list evidence that directly affected the latest decision.

Then show all other evidence.

10. Artifacts screen

10.1 Sections

Plan artifacts

Checkpoints

Workspace

Git state

Patches and diffs

Generated files

Logs

10.2 Plan artifacts

Show:

original plan path

current plan path

original plan hash

current plan hash

plan version count

last replan reason

raw Markdown toggle

10.3 Checkpoints

Each checkpoint shows:

checkpoint ID

plan step

runtime step

git HEAD

timestamp

reason

restored status

diff statistics

11. Replay screen

11.1 Timeline

The timeline remains available but is not the default primary screen.

Group events by:

plan step

runtime step

attempt

candidate

11.2 Event row

Show:

time

event type

human-readable summary

candidate

plan step

runtime step

causal parent link

11.3 Raw lineage

Keep raw JSON:

collapsed by default

searchable

copyable

sorted by sequence

warning banner for corrupt or truncated lineage

12. System screen

Show only operational details:

BOUND version

UI version

lineage store path

store readability

SSE state

active run count

corrupt line count

latest event timestamp

local-only privacy statement

Do not turn this into a settings page.

13. View-model architecture

Do not continue adding presentation logic directly into the HTML string builder.

Introduce view models.

13.1 Suggested files

src/bound/ui/
    __init__.py
    server.py
    routes.py
    view_models.py
    plan_parser.py
    plan_diff.py
    selectors.py
    render.py
    assets.py

A smaller split is acceptable, but separate:

runtime/lineage selection

plan parsing

plan comparison

UI view models

HTML rendering

server/SSE

13.2 Core view models

RunOverviewView
RunExecutionView
CandidateView
CurrentStateView
DecisionView
PlanView
PlanVersionView
PlanStepView
PlanDiffView
EvidenceCheckView
CheckpointView
TimelineEventView
SystemView

13.3 RunExecutionView

Minimum fields:

@dataclass(frozen=True)
class RunExecutionView:
    run_id: str
    task: str
    status: str
    candidate: CandidateView
    current_state: CurrentStateView
    decision: DecisionView | None
    original_plan: PlanView | None
    current_plan: PlanView | None
    plan_diff: PlanDiffView | None
    evidence: tuple[EvidenceCheckView, ...]
    checkpoints: tuple[CheckpointView, ...]
    recent_events: tuple[TimelineEventView, ...]
    started_at: datetime | None
    finished_at: datetime | None
    is_live: bool
    is_stale: bool

13.4 State derivation

Implement pure selector functions:

select_current_candidate(log)
select_current_runtime_step(log, candidate_id)
select_latest_decision(log, candidate_id)
select_current_action(log, candidate_id)
select_current_plan_version(log, candidate_id)
select_current_plan_step(log, candidate_id)
select_decision_reason(log, candidate_id)
select_next_action(log, candidate_id)
select_plan_progress(plan)
select_plan_diff(original, current, execution)

Add unit tests for every selector.

14. Realtime API and SSE

14.1 API endpoints

Suggested endpoints:

GET /api/runs
GET /api/runs/{run_id}
GET /api/runs/{run_id}/events
GET /api/runs/{run_id}/plan
GET /api/runs/{run_id}/evidence
GET /api/system
GET /api/events

14.2 SSE event types

run.created
run.updated
run.finished
candidate.updated
plan.loaded
plan.updated
plan.step.updated
current_action.updated
decision.updated
evidence.updated
checkpoint.updated
system.updated

14.3 SSE payload rules

include run ID

include candidate ID

include sequence

include changed entity

include minimal data needed for patching

allow client to fetch complete state after a gap

send heartbeat

detect missed sequence numbers

14.4 Client behavior

patch active components when possible

refetch full run view after sequence gaps

preserve scroll position

preserve selected tab

preserve selected candidate

do not auto-scroll if the user is inspecting older activity

15. Visual design system

15.1 Direction

Use a restrained developer-tool aesthetic.

Reference qualities:

Linear: hierarchy and spacing

GitHub Actions: checks and execution status

Vercel: deployment clarity

modern terminal tooling: density without clutter

Do not imitate their branding.

15.2 No emojis

Prohibited:

emoji characters in HTML

OS-dependent colored glyphs

decorative emoji in empty states

emoji timeline markers

Use:

inline SVG

CSS status dots

text labels

borders

typographic hierarchy

15.3 Typography

Use:

system sans-serif for navigation, headings, tasks, descriptions

monospace only for:

IDs

commands

hashes

paths

raw lineage

Do not render the entire product in monospace.

15.4 Color

Color must reinforce status, not carry meaning alone.

Required status treatment includes:

text label

shape or icon

border/background

accessible contrast

15.5 Density

8px spacing base

maximum content width approximately 1440px

avoid five equal summary cards

avoid redundant statistics

place secondary metadata behind disclosure controls

16. Responsive behavior

Desktop

primary execution content: 65–72%

contextual panel: 28–35%

plan remains the dominant component

Tablet

stack contextual panel below execution

candidate selector remains visible

tabs remain horizontal where possible

Mobile

single column

state panel first

plan second

evidence third

tabs may scroll horizontally

metadata collapsed

no horizontal page overflow

17. Accessibility

keyboard navigation for tabs, plan rows, filters, and disclosures

visible focus states

semantic headings

status text in addition to color

sufficient contrast

aria-live for current action and decision updates

reduced-motion support

no auto-scroll when prefers-reduced-motion is enabled

SVG icons require accessible labels or must be decorative

18. Implementation phases

Phase 1 — View-model foundation

Create UI package/module split.

Extract current run summary logic from HTML rendering.

Add RunOverviewView.

Add RunExecutionView.

Add selector unit tests.

Keep current UI working while selectors are introduced.

Acceptance

Rendering no longer walks raw lineage directly for primary state.

State derivation is testable without HTML snapshots.

Phase 2 — Plan model and parser

Define plan domain/view types.

Implement bounded plan.md discovery.

Implement Markdown checklist parser.

Preserve source lines and hierarchy.

Calculate stable plan/step IDs.

Add parser fixtures.

Add malformed-plan tests.

Add no-plan state.

Acceptance

Supported plan formats parse deterministically.

Parsing the same plan always produces the same IDs.

No recursive repository scan occurs.

Phase 3 — Runtime plan events

Add plan event schemas.

Emit plan.loaded.

Link plan to candidate.

Link runtime steps to plan steps.

Emit plan step state transitions.

Persist original plan snapshot.

Preserve backwards compatibility for old lineage.

Update lineage parser and replay.

Acceptance

A new run produces a replayable plan state.

UI does not need to repeatedly read a live mutable plan file.

Phase 4 — Home screen redesign

Remove duplicate card-and-table overview.

Add Active Runs section.

Add Recent Runs section.

Add plan progress.

Add current step/action.

Add concise decision reason.

Add filters.

Replace page refresh with SSE-driven updates.

Add stale/reconnecting states.

Acceptance

A user can identify the most important active run and its current activity within five seconds.

Phase 5 — Execution screen redesign

Implement task/status header.

Implement primary state panel.

Implement decision explanation.

Implement next action.

Implement execution plan list.

Implement current plan-step highlight.

Implement plan-step detail drawer.

Add compact recent activity.

Move metadata below primary content.

Acceptance

The first viewport answers task, progress, current step, action, decision, reason, and next action.

Phase 6 — Replan and Plan vs Reality

Implement immutable plan versions.

Implement plan diff.

Mark inserted/modified/removed steps.

Link REPLAN decision to new plan version.

Add Plan vs Reality view.

Add divergence summary.

Add fixtures with multiple replans.

Acceptance

The UI clearly shows what changed after REPLAN and why.

Phase 7 — Evidence, Artifacts, Replay

Add Evidence tab.

Separate decision-driving evidence.

Add provenance/status distinctions.

Add Artifacts tab.

Add plan artifact metadata.

Add checkpoint list.

Add Replay tab.

Group events by plan step and attempt.

Keep raw lineage collapsed.

Acceptance

Deep debugging remains available without dominating the normal execution view.

Phase 8 — Candidate support

Add candidate label for single candidate.

Add candidate selector.

Scope plan/evidence/checkpoints by candidate.

Add parent candidate reference.

Add multi-candidate fixtures.

Ensure per-candidate SSE updates.

Acceptance

No layout redesign is required when candidate branching becomes active.

Phase 9 — Polish and conformance

Replace emoji characters with inline SVG/CSS icons.

Introduce system sans-serif typography.

Validate responsive layouts.

Add accessibility checks.

Add reduced-motion support.

Add corrupt/truncated lineage warnings.

Add screenshot-based UI regression tests if feasible.

Update README screenshots.

Update CHANGELOG.

Document plan.md support.

Document event schema additions.

19. Test matrix

Plan parser

checklist plan

numbered plan

nested headings

duplicate titles

empty items

malformed Markdown

UTF-8 content

no plan

very large plan

changed plan hash

Plan execution

plan loaded before steps

plan loaded after run start

explicit plan-step linkage

inferred linkage

missing linkage

step completed

step failed

step skipped

step blocked

repeated step

replan

multiple replans

rollback after replan

Home

no runs

one active run

many active runs

completed runs

failed runs

long task names

missing plan

stale SSE

corrupted summary

Detail

active before first step

active during plan step

decision without outcome

outcome without latest action

complete ACCEPT

complete failure

no evidence

collection failure

missing checkpoint

multiple candidates

truncated lineage

Realtime

heartbeat

disconnect

reconnect

sequence gap

run completion

plan update

candidate update

preserved selected tab

preserved scroll position

20. Definition of done

The overhaul is complete when:

The home screen clearly separates active and historical runs.

Active run cards show current plan step and action.

The detail screen defaults to Execution, not raw timeline.

plan.md is loaded, parsed, snapshotted, and represented in the runtime.

Original and current plan versions remain available after replanning.

The UI shows plan progress based on plan steps, not event count.

REPLAN visibly explains inserted, removed, and modified steps.

Plan vs Reality shows divergence from original execution intent.

Decisions always include reason and next action.

Evidence status and provenance remain distinguishable.

Candidate is present in the UI model from day one.

SSE updates active state without full-page refresh.

Timeline and raw lineage remain available as secondary debugging tools.

No emoji characters are used anywhere in the UI.

Primary UI state is generated from tested selectors/view models.

Legacy runs remain inspectable.

README, CHANGELOG, and UI documentation are updated.

The complete test suite passes.

21. Agent implementation instruction

Implement this work incrementally.

Before changing rendering:

inspect the current runtime, lineage schema, UI server, and tests

identify existing plan-generation behavior and actual plan.md location

confirm whether the plan is currently persisted in lineage

write or update view-model tests

preserve existing CLI and local-dashboard behavior

Do not:

redesign the runtime beyond what plan integration requires

introduce a frontend framework unless clearly justified

silently infer plan completion from arbitrary event text

remove raw lineage support

break old v0.8.x run logs

add interactive execution controls in this release

use emoji characters in markup, CSS content, JavaScript, or fixtures

Prefer small reviewable commits in this order:

1. view models
2. plan parser
3. plan events
4. home screen
5. execution screen
6. replan comparison
7. evidence/artifacts/replay
8. candidate support
9. polish and docs