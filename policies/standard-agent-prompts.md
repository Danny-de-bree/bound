# BOUND Standard Agent Prompts

Goal:
Provide many reusable prompts for common agent workflows.

Usage:
- Copy one prompt.
- Fill in placeholders.
- Run.
- Stop when acceptance criteria are met.

Principle:
Good enough + forward progress.

---

## 1) Repository Exploration

### Prompt - Quick Repo Scan
```text
You are a coding agent.
Task: Do a quick scan of this repository.
Deliver:
1. Project purpose in 3 bullet points.
2. Main folders and what they do.
3. Top 5 risks or unknowns.
Acceptance:
- Findings are concrete and file-based.
- No speculative architecture rewrite suggestions.
Stop when complete.
```

### Prompt - Architecture Map
```text
You are a coding agent.
Task: Map the current architecture.
Deliver:
1. Runtime components.
2. Data flow from input to output.
3. External dependencies and integration points.
4. One Mermaid diagram.
Acceptance:
- Diagram matches real code paths.
- Unknowns clearly labeled as assumptions.
Stop after map is actionable.
```

### Prompt - Dependency Audit
```text
You are a coding agent.
Task: Audit dependencies for risk and maintenance.
Deliver:
1. Outdated core packages.
2. Packages with known security concerns.
3. Unused or duplicate dependencies.
4. Minimal update plan.
Acceptance:
- No mass migration.
- Prioritize high-impact changes only.
Stop after high-impact list is ready.
```

---

## 2) Bug Fixing

### Prompt - Reproduce and Fix Bug
```text
You are a coding agent.
Task: Reproduce and fix the reported bug.
Bug report:
{{BUG_DESCRIPTION}}
Deliver:
1. Reproduction steps.
2. Root cause.
3. Minimal fix.
4. Verification steps.
Acceptance:
- Bug is reproducible before and gone after.
- No unrelated refactors.
Stop after verification passes.
```

### Prompt - Intermittent Bug Strategy
```text
You are a coding agent.
Task: Investigate an intermittent bug.
Context:
{{CONTEXT}}
Deliver:
1. Most likely failure modes ranked by probability.
2. Added instrumentation/logging (minimal).
3. A safe fix or mitigation.
Acceptance:
- Investigation is evidence-driven.
- Added logs are targeted and reversible.
Stop after one validated mitigation is merged.
```

### Prompt - Regression After Merge
```text
You are a coding agent.
Task: Find and fix a regression introduced recently.
Deliver:
1. Suspected commit/window.
2. Behavioral diff.
3. Backward-compatible fix.
4. Regression test.
Acceptance:
- Existing behavior restored.
- New test fails before and passes after.
Stop when regression is covered.
```

---

## 3) Testing

### Prompt - Add Missing Unit Tests
```text
You are a coding agent.
Task: Add unit tests for this module.
Target:
{{FILE_OR_MODULE}}
Deliver:
1. Happy path tests.
2. Edge case tests.
3. Failure mode tests.
Acceptance:
- Tests are deterministic.
- No over-mocking.
- Clear names and intent.
Stop when coverage is sufficient for critical paths.
```

### Prompt - Flaky Test Stabilization
```text
You are a coding agent.
Task: Stabilize flaky tests without reducing real coverage.
Deliver:
1. Root cause of flakiness.
2. Stable test changes.
3. Why reliability improved.
Acceptance:
- Test passes repeatedly.
- No disabled assertions hiding failures.
Stop when flake is resolved.
```

### Prompt - Contract Test Creation
```text
You are a coding agent.
Task: Create contract tests for API boundaries.
Boundary:
{{API_OR_SERVICE}}
Deliver:
1. Producer expectations.
2. Consumer expectations.
3. Contract test suite.
Acceptance:
- Contracts reflect current production behavior.
- Breaking changes are explicitly caught.
Stop after contract suite runs green.
```

---

## 4) Refactoring

### Prompt - Safe Refactor
```text
You are a coding agent.
Task: Refactor for readability while preserving behavior.
Scope:
{{SCOPE}}
Deliver:
1. Small refactor steps.
2. Behavior-preserving checks.
3. Final simplified code.
Acceptance:
- No API changes unless requested.
- Diff is easy to review.
Stop when readability gain is clear.
```

### Prompt - Remove Dead Code
```text
You are a coding agent.
Task: Identify and remove dead code safely.
Deliver:
1. Candidate dead code list.
2. Evidence each item is unused.
3. Minimal removal patch.
Acceptance:
- Build/tests remain green.
- No removal of uncertain paths.
Stop after high-confidence dead code is removed.
```

### Prompt - Module Boundary Cleanup
```text
You are a coding agent.
Task: Improve module boundaries in this area.
Area:
{{AREA}}
Deliver:
1. Current coupling issues.
2. Minimal boundary improvements.
3. Updated imports/interfaces.
Acceptance:
- Reduced coupling measurable in code.
- No broad architecture rewrite.
Stop after targeted boundary cleanup.
```

---

## 5) Feature Delivery

### Prompt - Small Feature Implementation
```text
You are a coding agent.
Task: Implement the requested feature.
Feature:
{{FEATURE_REQUEST}}
Deliver:
1. Implementation.
2. Tests.
3. Docs update (if needed).
Acceptance:
- Feature works end-to-end.
- Existing behavior unaffected.
Stop when acceptance criteria are met.
```

### Prompt - MVP First Slice
```text
You are a coding agent.
Task: Deliver an MVP slice for this feature.
Feature:
{{FEATURE}}
Constraints:
{{CONSTRAINTS}}
Deliver:
1. Smallest valuable version.
2. Known limitations.
3. Next optional increments.
Acceptance:
- Usable now.
- No speculative extras.
Stop after MVP is shippable.
```

### Prompt - Feature Flag Rollout
```text
You are a coding agent.
Task: Add a feature behind a flag.
Deliver:
1. Flag definition.
2. Guarded code path.
3. Safe default and rollback strategy.
Acceptance:
- Disabled by default unless requested.
- Rollback is one-step.
Stop when rollout risk is controlled.
```

---

## 6) Performance

### Prompt - Performance Bottleneck Fix
```text
You are a coding agent.
Task: Improve performance for this flow.
Flow:
{{FLOW}}
Deliver:
1. Baseline measurement.
2. Primary bottleneck.
3. Minimal optimization.
4. After measurement.
Acceptance:
- Measurable improvement on key metric.
- No readability collapse.
Stop once target improvement is reached.
```

### Prompt - Query Optimization
```text
You are a coding agent.
Task: Optimize slow data queries.
Deliver:
1. Slow query evidence.
2. Index/query improvements.
3. Before/after timings.
Acceptance:
- Correctness unchanged.
- Performance gain documented.
Stop after top bottleneck is solved.
```

### Prompt - Frontend Render Optimization
```text
You are a coding agent.
Task: Reduce unnecessary renders in the UI.
Deliver:
1. Render hotspots.
2. Focused code changes.
3. Before/after profiling notes.
Acceptance:
- UI behavior unchanged.
- Performance visibly improved.
Stop when major hotspot is reduced.
```

---

## 7) Security

### Prompt - Security Hotspot Review
```text
You are a coding agent.
Task: Review this code for security hotspots.
Target:
{{TARGET}}
Deliver:
1. Vulnerabilities ranked by severity.
2. Exploit scenario per high issue.
3. Minimal mitigation patch.
Acceptance:
- High severity issues addressed first.
- No fear-based or speculative claims.
Stop after high-risk items are mitigated.
```

### Prompt - Secret Leakage Prevention
```text
You are a coding agent.
Task: Prevent secrets from being committed or logged.
Deliver:
1. Secret exposure points.
2. Code/config fixes.
3. Guardrails (hooks/scanners/policies).
Acceptance:
- Sensitive values removed from code paths.
- Practical guardrails in place.
Stop after leak risk is reduced to acceptable.
```

### Prompt - AuthZ Gap Fix
```text
You are a coding agent.
Task: Find and fix authorization gaps.
Area:
{{ENDPOINTS_OR_ACTIONS}}
Deliver:
1. Missing authz checks.
2. Code patch.
3. Tests proving denied/allowed behavior.
Acceptance:
- Unauthorized paths are blocked.
- Legitimate access still works.
Stop when authz matrix passes.
```

---

## 8) Documentation

### Prompt - README Upgrade
```text
You are a coding agent.
Task: Improve README for onboarding.
Deliver:
1. What this project does.
2. Quick start in under 5 minutes.
3. Common commands.
4. Troubleshooting section.
Acceptance:
- New contributor can run project quickly.
- No stale commands.
Stop when README is practical and tested.
```

### Prompt - API Documentation Sync
```text
You are a coding agent.
Task: Sync API docs with current implementation.
Deliver:
1. Endpoints and schemas.
2. Auth requirements.
3. Example requests/responses.
Acceptance:
- Docs match real behavior.
- Breaking changes clearly marked.
Stop after top-level API surface is accurate.
```

### Prompt - Decision Record
```text
You are a coding agent.
Task: Write an architecture decision record.
Decision:
{{DECISION}}
Deliver:
1. Context.
2. Decision.
3. Consequences.
4. Alternatives considered.
Acceptance:
- Trade-offs are explicit.
- Easy for future maintainers to follow.
Stop when ADR is complete and concise.
```

---

## 9) DevOps and CI/CD

### Prompt - CI Failure Triage
```text
You are a coding agent.
Task: Diagnose and fix failing CI.
Deliver:
1. Failure root cause.
2. Minimal fix.
3. Validation plan.
Acceptance:
- CI passes reliably.
- No masking of real failures.
Stop when pipeline is stable.
```

### Prompt - Build Time Reduction
```text
You are a coding agent.
Task: Reduce build time for this repository.
Deliver:
1. Current build-time breakdown.
2. High-impact optimization.
3. New build-time metrics.
Acceptance:
- Measurable reduction.
- No loss of test or quality gates.
Stop after first meaningful win.
```

### Prompt - Release Readiness Check
```text
You are a coding agent.
Task: Perform release readiness check.
Deliver:
1. Blocking issues.
2. Non-blocking improvements.
3. Go/no-go recommendation.
Acceptance:
- Recommendation is evidence-based.
- Focus on release-critical risks.
Stop when decision is clear.
```

---

## 10) Data and Migration

### Prompt - Safe Schema Migration
```text
You are a coding agent.
Task: Implement a safe schema migration.
Change:
{{SCHEMA_CHANGE}}
Deliver:
1. Forward migration.
2. Backward/rollback plan.
3. Data integrity checks.
Acceptance:
- Migration is reversible or safely staged.
- No data loss.
Stop when migration plan is production-safe.
```

### Prompt - Backfill Script
```text
You are a coding agent.
Task: Create a backfill script for existing data.
Deliver:
1. Idempotent script.
2. Dry-run mode.
3. Progress and error reporting.
Acceptance:
- Script can be rerun safely.
- Partial failures are recoverable.
Stop after successful dry-run validation.
```

### Prompt - Data Quality Checks
```text
You are a coding agent.
Task: Add data quality checks for critical tables.
Deliver:
1. Invariant checks.
2. Automated validation job.
3. Alert conditions.
Acceptance:
- High-risk data issues are detectable early.
- Signal-to-noise is acceptable.
Stop when core checks are in place.
```

---

## 11) Frontend Standard Work

### Prompt - UI Bug Fix
```text
You are a coding agent.
Task: Fix this UI bug.
Bug:
{{BUG}}
Deliver:
1. Reproduction.
2. Root cause.
3. Patch.
4. Visual verification notes.
Acceptance:
- Fix works on desktop and mobile.
- No style regressions nearby.
Stop after visual validation.
```

### Prompt - Accessible Component Update
```text
You are a coding agent.
Task: Improve accessibility for this component.
Component:
{{COMPONENT}}
Deliver:
1. A11y issues found.
2. Semantic and keyboard fixes.
3. Screen-reader behavior notes.
Acceptance:
- Keyboard navigation works.
- Contrast and labels meet standard checks.
Stop when core a11y issues are fixed.
```

### Prompt - Design System Alignment
```text
You are a coding agent.
Task: Align this page with the existing design system.
Deliver:
1. Divergences from system tokens/components.
2. Minimal alignment patch.
3. Before/after summary.
Acceptance:
- Visual language is consistent.
- No wholesale redesign.
Stop when major inconsistencies are resolved.
```

---

## 12) Backend Standard Work

### Prompt - Endpoint Hardening
```text
You are a coding agent.
Task: Harden this API endpoint for production.
Endpoint:
{{ENDPOINT}}
Deliver:
1. Validation improvements.
2. Error handling improvements.
3. Rate limit/idempotency guidance if relevant.
Acceptance:
- Invalid inputs fail safely.
- Logs are useful without leaking secrets.
Stop after production-safe baseline is reached.
```

### Prompt - Retry and Timeout Policy
```text
You are a coding agent.
Task: Add robust retry and timeout behavior.
Target integration:
{{INTEGRATION}}
Deliver:
1. Timeout defaults.
2. Retry policy with backoff.
3. Failure observability.
Acceptance:
- No infinite retries.
- Failure modes are explicit.
Stop when reliability is materially improved.
```

### Prompt - Idempotent Command Handler
```text
You are a coding agent.
Task: Make this command handler idempotent.
Handler:
{{HANDLER}}
Deliver:
1. Idempotency key strategy.
2. Conflict handling.
3. Tests for duplicate requests.
Acceptance:
- Duplicate commands do not duplicate side effects.
- Behavior is documented.
Stop when duplicate safety is proven.
```

---

## 13) PR and Review Work

### Prompt - Address PR Review Comments
```text
You are a coding agent.
Task: Address open review comments in the active PR.
Deliver:
1. Implement requested fixes.
2. Explain deviations where not applied.
3. Keep diffs minimal.
Acceptance:
- Each comment is resolved or answered with rationale.
- No unrelated edits.
Stop when review threads are actionable.
```

### Prompt - PR Risk Review
```text
You are a coding agent.
Task: Review this PR for risk.
Deliver:
1. Findings ordered by severity.
2. Missing tests.
3. Merge risk summary.
Acceptance:
- Findings are specific with file evidence.
- Focus on bugs/regressions first.
Stop when reviewer can decide quickly.
```

### Prompt - PR Description Generator
```text
You are a coding agent.
Task: Generate a high-signal PR description.
Deliver:
1. What changed.
2. Why.
3. How validated.
4. Risks and rollback.
Acceptance:
- Description is concise and review-ready.
- No generic filler.
Stop when PR body is publishable.
```

---

## 14) Incident and Operations

### Prompt - Incident Triage
```text
You are a coding agent.
Task: Triage this production incident.
Incident:
{{INCIDENT}}
Deliver:
1. Impact assessment.
2. Likely causes.
3. Immediate mitigation.
4. Next verification steps.
Acceptance:
- User impact reduced quickly.
- Actions are safe and reversible.
Stop once service is stabilized.
```

### Prompt - Postmortem Draft
```text
You are a coding agent.
Task: Draft a postmortem from incident data.
Deliver:
1. Timeline.
2. Root cause.
3. Contributing factors.
4. Action items with owners.
Acceptance:
- Blameless and specific.
- Actions are testable and prioritized.
Stop when draft is ready for team review.
```

### Prompt - Alert Noise Reduction
```text
You are a coding agent.
Task: Reduce noisy alerts while preserving true positives.
Deliver:
1. Noisy alert candidates.
2. Threshold or rule changes.
3. Validation plan.
Acceptance:
- Lower alert fatigue.
- No blind spot for critical incidents.
Stop after first meaningful noise reduction.
```

---

## 15) Planning and Prioritization

### Prompt - Technical Debt Prioritization
```text
You are a coding agent.
Task: Prioritize technical debt in this repository.
Deliver:
1. Debt items with impact and effort.
2. Recommended top 5 actions.
3. Why now.
Acceptance:
- Prioritization is outcome-driven.
- No vanity refactors.
Stop when next actions are clear.
```

### Prompt - Sprint-Ready Task Breakdown
```text
You are a coding agent.
Task: Break this initiative into sprint-ready tasks.
Initiative:
{{INITIATIVE}}
Deliver:
1. Tasks with clear definition of done.
2. Dependencies.
3. Risks per task.
Acceptance:
- Tasks are executable without ambiguity.
- Scope is realistic.
Stop when backlog is ready.
```

### Prompt - Build vs Buy Analysis
```text
You are a coding agent.
Task: Evaluate build vs buy for this capability.
Capability:
{{CAPABILITY}}
Deliver:
1. Options compared.
2. Cost, risk, and time-to-value.
3. Recommendation.
Acceptance:
- Recommendation is practical and constrained.
- Assumptions are explicit.
Stop once a decision can be made.
```

---

## 16) Generic BOUND Meta Prompts

### Prompt - Minimal Change Only
```text
You are a coding agent.
Rule: Use the smallest possible change that satisfies the request.
Deliver:
1. Minimal patch.
2. Why this is sufficient.
Acceptance:
- Solves requested problem.
- Avoids unrelated edits.
Stop after threshold is met.
```

### Prompt - High-Impact First
```text
You are a coding agent.
Rule: Prioritize highest-impact, lowest-risk actions.
Deliver:
1. Ranked options.
2. Chosen action and rationale.
Acceptance:
- Decision improves outcome quickly.
- Resource usage stays bounded.
Stop after first high-impact action is done.
```

### Prompt - Evidence Before Change
```text
You are a coding agent.
Rule: Gather evidence before implementing fixes.
Deliver:
1. Signals/logs/tests confirming hypothesis.
2. Targeted patch.
Acceptance:
- Fix directly matches evidence.
- No guess-driven rewrites.
Stop after evidence-backed fix is verified.
```

### Prompt - Timeboxed Deep Work
```text
You are a coding agent.
Rule: Timebox exploration to {{TIMEBOX}}.
Deliver:
1. Findings within timebox.
2. Best next action.
Acceptance:
- Progress made within limit.
- Open questions are explicit.
Stop when timebox ends or acceptance reached.
```

### Prompt - Release Decision Prompt
```text
You are a coding agent.
Task: Decide if this change is release-ready now.
Deliver:
1. Blocking issues.
2. Acceptable residual risks.
3. Go/no-go with rationale.
Acceptance:
- Decision is clear and actionable.
- Perfection is not required.
Stop after a confident decision.
```

---

## Quick Fill Template

```text
You are a coding agent.
Task: {{TASK}}
Context: {{CONTEXT}}
Constraints: {{CONSTRAINTS}}
Deliver:
1. {{DELIVERABLE_1}}
2. {{DELIVERABLE_2}}
3. {{DELIVERABLE_3}}
Acceptance:
- {{CRITERION_1}}
- {{CRITERION_2}}
Stop when acceptance criteria are met.
```
