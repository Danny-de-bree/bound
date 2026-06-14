# Backend Standard Work

## Prompt - Endpoint Hardening
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

## Prompt - Retry and Timeout Policy
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

## Prompt - Idempotent Command Handler
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
