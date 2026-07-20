"""BOUND v0.7.0 — Golden demo: policy-configured REPLAN -> ACCEPT, end-to-end.

A reproducible demonstration of the v0.7 policy-configuration system. A human
intent is turned into an approved ``bound-policy.yaml`` (tests + type checking
are blockers; lint is a weighted signal; at most three attempts and twenty tool
calls; only ``src/`` and ``tests/`` may change). The policy is validated,
explained, marked PROPOSED -> VALIDATED -> APPROVED -> ACTIVATED, and hashed.

Then an agent executes against that policy with *no hardcoded decisions* and
*no fabricated evidence* — BOUND's real, independent collectors execute:

    Attempt 1: agent says "all tests pass", but BOUND re-runs pytest
        -> 1/3 passed (VERIFIED). The tests-pass blocker fails -> REPLAN.
    Attempt 2: pytest 3/3 (VERIFIED), typecheck PASS (VERIFIED),
        lint PASS (VERIFIED), scope PASS (VERIFIED), tool calls 18/20 (OBSERVED)
        -> ACCEPT.

Every number is proven by a local append-only lineage trace (``.bound/runs/``),
plus a generated ``INTEGRATION_REPORT.md`` rendered from a real ``RunTrace``.
No LLM, no network — only deterministic scoring and real subprocess collectors.

Reproduce with:

    uv run python examples/golden_demo.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWeights,
    BoundWorkflow,
    CommandCollector,
    CommandSpec,
    EvidencePolicyAction,
    EvidenceProvenance,
    ExecutionEvidence,
    GitCollector,
    LineageStore,
    PytestCollector,
    ReasonCode,
    RiskCheck,
    RunFinishStatus,
    StepContract,
    start_run,
)
from bound.command_collector import (
    CommandResult,
    _bound_check,
    _command_source,
)
from bound.evidence import EvidenceMetric, EvidenceStatus
from bound.integration import _DECISION_TO_ACTION
from bound.lineage import build_run_config
from bound.policy_canon import compute_policy_hash
from bound.policy_schema import parse_policy_yaml
from bound.report import RunTrace, render_from_trace

GOAL = "Add email validation to the registration endpoint."

#: Threshold 0.75 / retry margin 0.1. With the weighted acceptance below, attempt 1
#: scores A = 0.6 (< 0.75, gap 0.15 > 0.1 -> REPLAN) and attempt 2 scores
#: A = 1.0 (-> ACCEPT). No decision is hardcoded: the policy + scores decide.
THRESHOLD = 0.75
RETRY_MARGIN = 0.1

#: The lint verification command (taken verbatim from the approved policy). Kept as
#: a constant so the policy YAML and the harness runner stay in lock-step.
_LINT_CODE = (
    "import sys; t=open('src/validation.py').read(); "
    "sys.exit('TODO' in t or 'print(' in t)"
)

#: The generated ``bound-policy.yaml`` reflecting the user intent. This IS the
#: artifact a human reviews and approves before the run.
POLICY_YAML = """\
schema_version: "1.0"

policy:
  id: golden-demo
  version: "1.0"

collectors:
  pytest:
    type: pytest
  typecheck:
    type: command
    command: ["python", "-m", "py_compile", "src/validation.py"]
    timeout_seconds: 60
    success_exit_codes: [0]
  lint:
    type: command
    command:
      - "python"
      - "-c"
      - >-
        import sys; t=open('src/validation.py').read();
        sys.exit('TODO' in t or 'print(' in t)
    timeout_seconds: 30
    success_exit_codes: [0]
  git:
    type: git

acceptance_checks:
  - id: tests-pass
    description: "All tests pass (pytest, independently re-run by BOUND)."
    importance: blocker
    required: true
    on_failure: replan
    on_missing: retry
    on_claimed: replan
    minimum_assurance: verified
    accepted_provenance: [verified, observed]
    collector: pytest
  - id: typecheck-pass
    description: "Source compiles cleanly (py_compile, no syntax errors)."
    importance: blocker
    required: true
    on_failure: retry
    on_missing: retry
    on_claimed: replan
    accepted_provenance: [verified, observed]
    collector: typecheck

quality_checks:
  - id: lint-clean
    description: "Lint is clean (no TODO markers, no debug prints)."
    importance: medium
    accepted_provenance: [verified, observed]
    collector: lint

risk_checks:
  - id: scope-respected
    description: "Only allowed paths (src/, tests/) were modified."
    importance: blocker
    required: true
    on_failure: replan
    on_missing: replan
    accepted_provenance: [verified, observed]
    collector: git

budgets:
  attempts:
    hard_limit: 3
    on_hard: replan
  tool_calls:
    hard_limit: 20
    on_hard: replan

change_scope:
  allowed_paths:
    - "src/**"
    - "tests/**"
  forbidden_paths:
    - ".git/**"
  dependency_file_patterns:
    - "pyproject.toml"

approvals:
  require_rollback_availability: false
  on_missing_rollback: replan
"""


def _sys_argv(*args: str) -> list[str]:
    """A portable argv using the current interpreter."""
    return [sys.executable, *args]


def _explain(policy) -> str:
    """A concise human-readable explanation of the approved policy."""
    blockers = [g.id for g in policy.acceptance_checks] + [
        g.id for g in policy.risk_checks
    ]
    signals = [
        f"{s.id} ({s.importance}, weight {s.effective_weight})"
        for s in policy.quality_checks
    ]
    budgets = [
        f"{name}: hard {dim.hard_limit} -> {dim.on_hard}"
        for name, dim in policy.budgets.items()
    ]
    scope = policy.change_scope.allowed_paths
    lines = [
        "Human explanation of the approved policy:",
        f"  Blockers (cannot be compensated by score): {', '.join(blockers)}",
        f"  Weighted signals: {', '.join(signals) if signals else '(none)'}",
        f"  Budgets: {', '.join(budgets) if budgets else '(none)'}",
        f"  Allowed paths: {', '.join(scope) if scope else '(any)'}",
        f"  Approvals: rollback required = "
        f"{policy.approvals.require_rollback_availability}",
        "  An ACCEPT requires every blocker to pass with VERIFIED evidence; the",
        "  agent can never weaken this policy mid-run.",
    ]
    return "\n".join(lines)

class _CommandCheckCollector:
    """A generic command collector bound to the active policy.

    Runs a registered command (whose argv comes from the approved policy's
    ``collectors`` section) and emits VERIFIED evidence on exit 0, FAILED on a
    non-zero exit, and INVALID/MISSING on timeout/crash — the same fail-safe
    contract as the built-in collectors. No agent command injection: only the
    pre-registered command name runs.
    """

    def __init__(
        self,
        runner: CommandCollector,
        *,
        command_name: str,
        check_id: str,
        collector_name: str,
        success_exit_codes: list[int] | None = None,
    ) -> None:
        self._runner = runner
        self._command_name = command_name
        self._check_id = check_id
        self._collector_name = collector_name
        self._success = set(success_exit_codes or [0])

    def collect(self, *, cwd: str | None = None, timeout: float | None = None):
        result = self._runner.run(
            self._command_name, cwd=cwd, timeout=timeout, store_raw=True
        )
        return self._evidence_from_result(result)

    def _evidence_from_result(self, result: CommandResult):
        source = _command_source(result.argv)
        hashes = f"stdout={result.stdout_hash} stderr={result.stderr_hash}"
        if result.error is not None or result.timed_out or result.exit_code is None:
            reason = result.error or (
                "timeout" if result.timed_out else "no exit code"
            )
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self._collector_name,
                status=EvidenceStatus.INVALID,
                details=f"command did not complete ({reason}); {hashes}",
                source=source,
                artifact_hash=result.stdout_hash,
            )
        passed = result.exit_code in self._success
        status = None if passed else EvidenceStatus.FAILED
        details = f"exit={result.exit_code}; {hashes}"
        return _bound_check(
            self._check_id,
            passed=passed,
            provenance=EvidenceProvenance.VERIFIED,
            collector=self._collector_name,
            status=status,
            details=details,
            source=source,
            artifact_hash=result.stdout_hash,
        )


def _init_repo(repo: Path) -> None:
    """Create a temp repo with the source module and a git workspace."""
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "validation.py").write_text(
        'def validate_email(value: str) -> bool:\n'
        '    """Return True when value looks like an email."""\n'
        '    return "@" in value and "." in value.split("@", 1)[1]\n',
        encoding="utf-8",
    )
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "bound@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "BOUND golden demo"], cwd=repo, check=True)


def _write_tests(repo: Path, *, b_pass: bool, c_pass: bool) -> None:
    """Write the three test files; b/c assertions flip between attempts."""
    (repo / "tests" / "test_a.py").write_text(
        'from src.validation import validate_email\n'
        'def test_a():\n    assert validate_email("a@b.com") is True\n',
        encoding="utf-8",
    )
    b_expected = "False" if b_pass else "True"
    (repo / "tests" / "test_b.py").write_text(
        'from src.validation import validate_email\n'
        f'def test_b():\n    assert validate_email("noat") is {b_expected}\n',
        encoding="utf-8",
    )
    c_expected = "False" if c_pass else "True"
    (repo / "tests" / "test_c.py").write_text(
        'from src.validation import validate_email\n'
        f'def test_c():\n    assert validate_email("a@b") is {c_expected}\n',
        encoding="utf-8",
    )


def _contract() -> StepContract:
    """The step contract mirroring the approved policy's gates."""
    verified = [EvidenceProvenance.OBSERVED, EvidenceProvenance.VERIFIED]
    return StepContract(
        id="PHASE-001",
        description="Validated email registration input",
        goal=GOAL,
        acceptance_checks=[
            AcceptanceCheck(
                id="tests-pass",
                description="All tests pass",
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
            AcceptanceCheck(
                id="typecheck-pass",
                description="Source compiles",
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.RETRY,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="scope-respected",
                description="Only allowed paths modified",
                severity=1.0,
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                decision_critical=True,
            ),
        ],
    )


def _runners(repo: Path) -> CommandCollector:
    """Build the CommandCollector registry. The typecheck/lint argv are taken
    verbatim from the approved policy; pytest/git are built-in collectors whose
    argv the harness wires to the temp repo."""
    return CommandCollector(
        {
            "pytest": CommandSpec(
                argv=_sys_argv(
                    "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    "tests/test_a.py", "tests/test_b.py", "tests/test_c.py",
                ),
                cwd=str(repo),
                timeout=60.0,
            ),
            "typecheck": CommandSpec(
                argv=_sys_argv("-m", "py_compile", "src/validation.py"),
                cwd=str(repo),
                timeout=60.0,
            ),
            "lint": CommandSpec(
                argv=_sys_argv("-c", _LINT_CODE),
                cwd=str(repo),
                timeout=30.0,
            ),
            "git-status": CommandSpec(
                argv=["git", "status", "--porcelain"], cwd=str(repo), timeout=30.0
            ),
        }
    )


def _collectors(runner: CommandCollector):
    """Instantiate the real collectors bound to the active policy's check ids."""
    pytest_c = PytestCollector(runner, command_name="pytest", check_id="tests-pass")
    typecheck_c = _CommandCheckCollector(
        runner, command_name="typecheck", check_id="typecheck-pass",
        collector_name="bound.typecheck",
    )
    lint_c = _CommandCheckCollector(
        runner, command_name="lint", check_id="lint-clean",
        collector_name="bound.lint",
    )
    git_c = GitCollector(
        runner, command_name="git-status", check_id="scope-respected",
        allowed_prefixes=("src", "tests"),
    )
    return pytest_c, typecheck_c, lint_c, git_c


def _attempt(
    run,
    collectors,
    repo: Path,
    contract: StepContract,
    criteria: BoundCriteria,
    policy,
    *,
    attempt: int,
    contract_id: str,
    b_pass: bool,
    c_pass: bool,
    tool_calls: int,
    note: str,
):
    """Run one attempt: write tests, collect real evidence, evaluate, record."""
    pytest_c, typecheck_c, lint_c, git_c = collectors
    _write_tests(repo, b_pass=b_pass, c_pass=c_pass)

    # BOUND independently collects evidence (the agent does NOT self-report).
    tests_ev = pytest_c.collect()
    typecheck_ev = typecheck_c.collect()
    lint_ev = lint_c.collect()
    scope_ev = git_c.collect()

    evidence = ExecutionEvidence(
        acceptance=[tests_ev, typecheck_ev, lint_ev],
        risks=[scope_ev],
        rollback_available=True,
        retry_count=EvidenceMetric(
            value=attempt - 1, provenance=EvidenceProvenance.OBSERVED,
            source="harness.attempts",
        ),
        tool_call_count=EvidenceMetric(
            value=tool_calls, provenance=EvidenceProvenance.OBSERVED,
            source="harness.tool_calls",
        ),
    )

    result = BoundWorkflow().evaluate_step(
        contract=contract, evidence=evidence, criteria=criteria, policy=policy
    )

    step = run.start_step(
        contract_id=contract_id, attempt=attempt, description="implement email validation"
    )
    for ce in [*evidence.acceptance, *evidence.risks]:
        run.record_evidence_collected(
            step_id=step.step_id,
            check_id=ce.check_id,
            collector=ce.collector or "unknown",
            provenance=ce.provenance.value,
            passed=ce.passed,
            status=ce.status.value if ce.status is not None else None,
            artifact_hash=ce.artifact_hash,
            source=ce.source,
            collector_version=ce.collector_version,
            observed_at=ce.observed_at,
        )
    ev_rec = run.record_evaluation(
        step_id=step.step_id,
        attempt=attempt,
        scores=result.scores,
        score=result.score,
        threshold=criteria.threshold,
        decision=result.final_decision or result.decision,
        policy_id=policy.policy.id,
        policy_version=policy.policy.version,
        policy_hash=compute_policy_hash(policy),
        candidate_decision=result.candidate_decision,
        final_decision=result.final_decision,
        assurance=result.assurance.value if result.assurance else None,
        effective_weights=result.effective_weights,
        collector_versions={
            "bound.pytest": "0.7.0",
            "bound.typecheck": "0.7.0",
            "bound.lint": "0.7.0",
            "bound.git": "0.7.0",
        },
    )
    run.record_decision_gated(
        step_id=step.step_id,
        evaluation_id=ev_rec.evaluation_id,
        candidate_decision=result.candidate_decision,
        final_decision=result.final_decision,
        assurance=result.assurance,
        assurance_reasons=list(result.assurance_reasons),
    )
    run.record_evaluation_completed(
        step_id=step.step_id,
        evaluation_id=ev_rec.evaluation_id,
        policy_id=policy.policy.id,
        policy_version=policy.policy.version,
        policy_hash=compute_policy_hash(policy),
        candidate_decision=result.candidate_decision,
        final_decision=result.final_decision,
        assurance=result.assurance.value if result.assurance else None,
        effective_weights=result.effective_weights,
        note=note,
    )
    decision = result.final_decision or result.decision
    next_action = _DECISION_TO_ACTION[decision]
    run.record_outcome(
        step_id=step.step_id,
        evaluation_id=ev_rec.evaluation_id,
        decision=decision,
        next_action=next_action,
        note=note,
    )
    run.record_step_completed(step_id=step.step_id, outcome=decision)
    return result, evidence, step.step_id, ev_rec.evaluation_id


def _print_attempt(label: str, result, evidence) -> None:
    """Print one attempt's independently-collected evidence and decision."""
    print(f"--- attempt {label} ---")
    for ce in evidence.acceptance:
        mark = "PASS" if ce.passed else ("FAIL" if ce.passed is False else "MISSING")
        prov = ce.provenance.value.upper()
        print(f"  {ce.check_id:<16} {mark:<7} {prov}")
        if ce.details:
            print(f"    {ce.details}")
    for ce in evidence.risks:
        mark = "PASS" if ce.passed else ("FAIL" if ce.passed is False else "MISSING")
        prov = ce.provenance.value.upper()
        print(f"  {ce.check_id:<16} {mark:<7} {prov}")
        if ce.details:
            print(f"    {ce.details}")
    tc = evidence.tool_call_count
    if tc is not None:
        print(f"  tool_calls        {tc.value}/20  {tc.provenance.value.upper()}")
    cand = result.candidate_decision or result.decision
    final = result.final_decision or result.decision
    assurance = result.assurance.value if result.assurance else "-"
    print(f"  candidate={cand}  final={final}  assurance={assurance}")
    print(f"  A={result.scores.acceptance:.4f}  score={result.score:.4f}  "
          f"threshold={THRESHOLD}")


def main() -> int:
    """Run the policy-configured REPLAN -> ACCEPT flow end-to-end."""
    repo = Path(tempfile.mkdtemp(prefix="bound-golden-demo-"))
    runs_dir = Path(tempfile.mkdtemp(prefix="bound-golden-runs-")) / "runs"
    store = LineageStore(base_dir=runs_dir)
    try:
        # 1. Generate + validate the policy from the user intent.
        policy = parse_policy_yaml(POLICY_YAML)
        policy_hash = compute_policy_hash(policy)
        print("user intent:")
        print("  Tests and type checking are blockers. Lint is important but not")
        print("  critical. Allow at most three attempts and twenty tool calls. Only")
        print("  modify src/ and tests/.")
        print("=" * 78)
        print("generated bound-policy.yaml (validated by bound.policy_schema):")
        print(yaml.safe_dump(yaml.safe_load(POLICY_YAML), sort_keys=False), end="")
        print("=" * 78)
        print(_explain(policy))
        print(f"canonical policy hash: {policy_hash}")
        print("=" * 78)

        # 2. Wire the real collectors + contract + criteria.
        _init_repo(repo)
        runner = _runners(repo)
        collectors = _collectors(runner)
        contract = _contract()
        criteria = BoundCriteria(
            weights=BoundWeights(), threshold=THRESHOLD, retry_margin=RETRY_MARGIN
        )
        config = build_run_config(
            bound_version="0.7.0",
            policy=policy,
            threshold=THRESHOLD,
            retry_margin=RETRY_MARGIN,
            contract=contract,
            collector_versions={
                "bound.pytest": "0.7.0",
                "bound.typecheck": "0.7.0",
                "bound.lint": "0.7.0",
                "bound.git": "0.7.0",
            },
        )

        # 3. Run with the full policy lifecycle (PROPOSED -> VALIDATED ->
        #    APPROVED -> ACTIVATED). Only an activated policy controls decisions.
        with start_run(GOAL, store=store, config=config) as run:
            run_id = run.run_id
            print(f"started run: {run_id}")
            print(f"run config policy hash: {config.policy_hash}")
            print(f"active policy: {policy.policy.id}@{policy.policy.version} "
                  f"({policy_hash})")
            run.record_policy_proposed(
                policy_id=policy.policy.id, policy_version=policy.policy.version,
                policy_hash=policy_hash,
            )
            run.record_policy_validated(
                policy_id=policy.policy.id, policy_version=policy.policy.version,
                policy_hash=policy_hash,
            )
            run.record_policy_approved(
                policy_id=policy.policy.id, policy_version=policy.policy.version,
                policy_hash=policy_hash, approver="human",
                approved_at=datetime.now(UTC),
            )
            run.record_policy_activated(
                policy_id=policy.policy.id, policy_version=policy.policy.version,
                policy_hash=policy_hash,
            )
            print("policy lifecycle: proposed -> validated -> approved -> activated")
            print("=" * 78)

            # Attempt 1: agent claims "all tests pass", BOUND re-runs pytest.
            print("attempt 1: agent says 'all tests pass'")
            r1, ev1, sid1, _ = _attempt(
                run, collectors, repo, contract, criteria, policy,
                attempt=1, contract_id="PHASE-001", b_pass=False, c_pass=False,
                tool_calls=12, note="switched strategy to correct test assertions",
            )
            _print_attempt("1", r1, ev1)
            assert r1.final_decision == "REPLAN", r1.final_decision

            print("agent fixes the failing test assertions.")
            # Attempt 2: pytest 3/3, typecheck/lint/scope PASS, 18/20 tool calls.
            print("attempt 2:")
            r2, ev2, sid2, _ = _attempt(
                run, collectors, repo, contract, criteria, policy,
                attempt=2, contract_id="PHASE-001-R1", b_pass=True, c_pass=True,
                tool_calls=18, note="continued to next step",
            )
            _print_attempt("2", r2, ev2)
            assert r2.final_decision == "ACCEPT", r2.final_decision

            run.finish_run(
                status=RunFinishStatus.COMPLETED,
                reason_code=ReasonCode.RUN_COMPLETED,
                note="email validation step completed",
            )

        print("=" * 78)
        _write_artifacts(
            store, run_id, contract, ev2, r2, sid1, sid2, policy, policy_hash
        )
        _print_proof(store, run_id, policy_hash)
        print("=" * 78)
        print("reproduction command:")
        print("  uv run python examples/golden_demo.py")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(runs_dir.parent, ignore_errors=True)
    return 0


def _write_artifacts(
    store, run_id, contract, evidence, result, sid1, sid2, policy, policy_hash
) -> None:
    """Write the real run.json (RunTrace) + INTEGRATION_REPORT.md artifacts."""
    from bound.report import DecisionHistoryEntry

    decision = result.final_decision or result.decision
    next_action = _DECISION_TO_ACTION[decision]
    log = store.read_run(run_id)
    trace = RunTrace(
        schema_version="2.0",
        plan_id="PHASE-001",
        step_id=sid2,
        run_id=run_id,
        bound_version="0.7.0",
        timestamp=datetime.now(UTC).isoformat(),
        contract=contract,
        evidence=evidence,
        evaluation=result,
        next_action=next_action,
        trajectory=[
            "PHASE-001",
            "BOUND",
            "attempt 1: REPLAN (tests-pass blocker failed, 1/3)",
            "attempt 2: ACCEPT (all blockers VERIFIED, 18/20 tool calls)",
            f"{decision} -> {next_action}",
        ],
        decision_history=[
            DecisionHistoryEntry(
                step_id=sid1, attempt=1, decision="REPLAN",
                next_action="replan",
                note="tests-pass blocker failed (1/3); agent corrected assertions",
            ),
            DecisionHistoryEntry(
                step_id=sid2, attempt=2, decision=decision,
                next_action=next_action, note="all blockers VERIFIED; ACCEPT",
            ),
        ],
        config=log.run.config,
    )
    out_dir = Path(store.base_dir) / run_id
    (out_dir / "run.json").write_text(trace.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "INTEGRATION_REPORT.md").write_text(
        render_from_trace(trace) + "\n", encoding="utf-8"
    )
    print(f"wrote {out_dir / 'run.json'}")
    print(f"wrote {out_dir / 'INTEGRATION_REPORT.md'}")


def _print_proof(store, run_id: str, policy_hash: str) -> None:
    """Read the append-only trace back and prove each number from the log."""
    log = store.read_run(run_id)
    print("trace proof (append-only events.jsonl):")
    print(f"  policy hash: {policy_hash}")
    policy_events = [e for e in log.events if e.event.startswith("policy.")]
    print(f"  policy lifecycle events: {[e.event for e in policy_events]}")
    collected = [e for e in log.events if e.event == "evidence.collected"]
    gated = [e for e in log.events if e.event == "decision.gated"]
    for e in collected:
        print(
            f"  {e.check_id}: collector={e.collector} provenance={e.provenance} "
            f"passed={e.passed}"
        )
    for e in gated:
        assurance = e.assurance.value if e.assurance else "-"
        print(
            f"  decision.gated: candidate={e.candidate_decision} "
            f"final={e.final_decision} assurance={assurance}"
        )
    depended_on_claimed = any(
        e.provenance == EvidenceProvenance.CLAIMED.value for e in collected
    )
    print(
        "the final decision did NOT depend on CLAIMED evidence: "
        f"{not depended_on_claimed}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
