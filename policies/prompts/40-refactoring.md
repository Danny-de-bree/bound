# Refactoring

## Prompt - Safe Refactor
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

## Prompt - Remove Dead Code
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

## Prompt - Module Boundary Cleanup
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
