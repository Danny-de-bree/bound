# Bug Fixing

## Prompt - Reproduce and Fix Bug
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

## Prompt - Intermittent Bug Strategy
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

## Prompt - Regression After Merge
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
