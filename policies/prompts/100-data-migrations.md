# Data and Migration

## Prompt - Safe Schema Migration
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

## Prompt - Backfill Script
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

## Prompt - Data Quality Checks
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
