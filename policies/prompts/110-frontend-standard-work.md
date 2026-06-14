# Frontend Standard Work

## Prompt - UI Bug Fix
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

## Prompt - Accessible Component Update
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

## Prompt - Design System Alignment
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
