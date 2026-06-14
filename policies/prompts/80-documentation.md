# Documentation

## Prompt - README Upgrade
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

## Prompt - API Documentation Sync
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

## Prompt - Decision Record
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
