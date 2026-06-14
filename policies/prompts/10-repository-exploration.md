# Repository Exploration

## Prompt - Quick Repo Scan
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

## Prompt - Architecture Map
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

## Prompt - Dependency Audit
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
