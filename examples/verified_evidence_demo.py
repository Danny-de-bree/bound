"""BOUND v0.7.0 — Verified Evidence demo (REPLAN -> ACCEPT, independently collected).

Runs the real BOUND collector + policy surface to produce the canonical v0.7
"Verified Evidence" Definition-of-Done flow end-to-end, with *no hardcoded
decisions*: an agent claims "all tests pass", but BOUND's independent
:class:`~bound.command_collector.PytestCollector` actually runs pytest and a
:class:`~bound.command_collector.GitCollector` actually inspects the tree.

    Attempt 1 -> pytest 1/3 passed (VERIFIED), git clean (VERIFIED) -> REPLAN
    Agent changes strategy.
    Attempt 2 -> pytest 3/3 passed (VERIFIED), git safe changes (VERIFIED) -> ACCEPT

Every number is then proven by a local append-only lineage trace that records,
per check: who/what delivered it (collector), how it was collected (provenance),
which collector version was used, which policy config governed the run (hash),
and that the final decision did NOT depend on CLAIMED evidence. No LLM, no
network — only deterministic scoring and real subprocess collectors.

A captured version of the resulting events.jsonl ships alongside the v0.7
lineage demo as ``examples/lineage_demo_events.jsonl``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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
from bound.lineage import build_run_config

GOAL = "Add input validation to the registration endpoint."

#: Threshold 0.7 / retry margin 0.1 with default unit weights. With influence
#: DEFAULTED to 0.0, no budget (C=0) and no violated risk (R=0), the score S
#: equals acceptance A, so A=3/3=1.0 -> ACCEPT and A=1/3=0.3333 -> REPLAN.
THRESHOLD = 0.7
RETRY_MARGIN = 0.1



def _sys_argv(*args: str) -> list[str]:
    """A portable argv using the current interpreter (works on any platform)."""
    return [sys.executable, *args]


def _contract() -> StepContract:
    """Three VERIFIED-only acceptance checks + a decision-critical git risk."""
    verified = [
        EvidenceProvenance.OBSERVED,
        EvidenceProvenance.VERIFIED,
        EvidenceProvenance.ATTESTED,
    ]
    return StepContract(
        id="PHASE-001",
        description="Validated registration input",
        goal=GOAL,
        acceptance_checks=[
            AcceptanceCheck(
                id="tests-a",
                description="test a passes",
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
            AcceptanceCheck(
                id="tests-b",
                description="test b passes",
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
            AcceptanceCheck(
                id="tests-c",
                description="test c passes",
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no-unsafe-changes",
                description="no unexpected files changed",
                severity=0.5,
                decision_critical=True,
                accepted_provenance=[
                    EvidenceProvenance.VERIFIED,
                    EvidenceProvenance.ATTESTED,
                ],
                on_missing=EvidencePolicyAction.ROLLBACK,
            )
        ],
    )


def _runners(repo: Path) -> CommandCollector:
    """One CommandCollector owning the three pytest commands + git status."""
    suite = repo / "suite"
    return CommandCollector(
        {
            "test-a": CommandSpec(
                argv=_sys_argv(
                    "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    str(suite / "test_a.py"),
                ),
                timeout=60.0,
            ),
            "test-b": CommandSpec(
                argv=_sys_argv(
                    "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    str(suite / "test_b.py"),
                ),
                timeout=60.0,
            ),
            "test-c": CommandSpec(
                argv=_sys_argv(
                    "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    str(suite / "test_c.py"),
                ),
                timeout=60.0,
            ),
            "git-status": CommandSpec(
                argv=["git", "status", "--porcelain"], timeout=30.0
            ),
        }
    )



def _init_repo(repo: Path) -> None:
    """Create a throwaway git repo so the git collector has a real tree to read."""
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "bound@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "BOUND demo"], check=True
    )


def _write_tests(repo: Path, *, b_pass: bool, c_pass: bool) -> None:
    """Write three test files; test_a always passes, b/c pass or fail."""
    suite = repo / "suite"
    suite.mkdir(parents=True, exist_ok=True)
    (suite / "test_a.py").write_text("def test_a():\n    assert 1 + 1 == 2\n")
    b_body = "    assert True\n" if b_pass else "    assert False, 'boom'\n"
    (suite / "test_b.py").write_text("def test_b():\n" + b_body)
    c_body = "    assert True\n" if c_pass else "    assert False, 'boom'\n"
    (suite / "test_c.py").write_text("def test_c():\n" + c_body)
    # Commit so attempt 1 has a clean tree and attempt 2 shows tracked "safe"
    # modifications (within the allowed ``suite`` prefix).
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "tests"], check=True
    )


def _collect(runner: CommandCollector, repo: Path) -> ExecutionEvidence:
    """Run the three pytest collectors + the git collector; build evidence."""
    py_a = PytestCollector(runner, command_name="test-a", check_id="tests-a")
    py_b = PytestCollector(runner, command_name="test-b", check_id="tests-b")
    py_c = PytestCollector(runner, command_name="test-c", check_id="tests-c")
    git = GitCollector(
        runner, command_name="git-status", check_id="no-unsafe-changes",
        allowed_prefixes=("suite",),
    )
    return ExecutionEvidence(
        acceptance=[
            py_a.collect(cwd=str(repo)),
            py_b.collect(cwd=str(repo)),
            py_c.collect(cwd=str(repo)),
        ],
        risks=[git.collect(cwd=str(repo))],
        rollback_available=True,
    )


def _record_collected(run, step_id: str, evidence: ExecutionEvidence) -> None:
    """Append an evidence.collected audit event for each collected check."""
    for ce in [*evidence.acceptance, *evidence.risks]:
        run.record_evidence_collected(
            step_id=step_id,
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



def _attempt(
    run, runner, repo, contract, criteria, *, attempt: int, contract_id: str,
    b_pass: bool, c_pass: bool, note: str,
):
    """Run one collected attempt, record its lineage, and return the result."""
    step = run.start_step(
        contract_id=contract_id, attempt=attempt, description="implement validation"
    )
    _write_tests(repo, b_pass=b_pass, c_pass=c_pass)
    evidence = _collect(runner, repo)
    _record_collected(run, step.step_id, evidence)

    result = BoundWorkflow().evaluate_step(
        contract=contract, evidence=evidence, criteria=criteria
    )
    evaluation = run.record_evaluation(
        step_id=step.step_id,
        attempt=attempt,
        scores=result.scores,
        score=result.score,
        threshold=THRESHOLD,
        decision=result.final_decision,
    )
    run.record_decision_gated(
        step_id=step.step_id,
        evaluation_id=evaluation.evaluation_id,
        candidate_decision=result.candidate_decision,
        final_decision=result.final_decision,
        assurance=result.assurance,
        assurance_reasons=list(result.assurance_reasons),
    )
    run.record_outcome(
        step_id=step.step_id,
        evaluation_id=evaluation.evaluation_id,
        decision=result.final_decision,
        note=note,
    )
    return result, evidence


def _print_attempt(label: str, result, evidence: ExecutionEvidence) -> None:
    """Print one attempt's per-number proof to stdout."""
    acc = {ce.check_id: ce for ce in evidence.acceptance}
    passed = sum(1 for ce in evidence.acceptance if ce.passed)
    total = len(evidence.acceptance)
    print(f"  pytest collector   -> {passed}/{total} passed        VERIFIED")
    for cid in ("tests-a", "tests-b", "tests-c"):
        ce = acc[cid]
        print(
            f"    {cid}: passed={ce.passed} provenance={ce.provenance.value} "
            f"collector={ce.collector} version={ce.collector_version}"
        )
    git_ce = evidence.risks[0]
    safe = "safe changes" if git_ce.passed else "unsafe changes"
    print(f"  git collector      -> {safe:<14} VERIFIED")
    print(f"  candidate decision -> {result.candidate_decision}")
    print(f"  decision assurance -> {result.assurance.value.upper()}")
    print(f"  final decision     -> {result.final_decision}")


def main() -> int:
    """Run the REPLAN -> ACCEPT flow with live collectors and print the proof."""
    repo = Path(tempfile.mkdtemp(prefix="bound-verified-demo-"))
    runs_dir = Path(tempfile.mkdtemp(prefix="bound-verified-runs-")) / "runs"
    store = LineageStore(base_dir=runs_dir)
    try:
        _init_repo(repo)
        runner = _runners(repo)
        contract = _contract()
        criteria = BoundCriteria(
            weights=BoundWeights(), threshold=THRESHOLD, retry_margin=RETRY_MARGIN
        )
        config = build_run_config(
            bound_version="0.7.0",
            policy_config={"threshold": THRESHOLD, "retry_margin": RETRY_MARGIN},
            threshold=THRESHOLD,
            retry_margin=RETRY_MARGIN,
            contract=contract,
            collector_versions={"bound.pytest": "0.7.0", "bound.git": "0.7.0"},
        )

        print(f"goal: {GOAL}")
        print("=" * 78)
        with start_run(GOAL, store=store, config=config) as run:
            run_id = run.run_id
            print(f"started run: {run_id}")
            print(f"policy config hash: {config.policy_config_hash}")

            print("attempt 1: agent says 'all tests pass'")
            r1, ev1 = _attempt(
                run, runner, repo, contract, criteria, attempt=1,
                contract_id="PHASE-001", b_pass=False, c_pass=False,
                note="switched strategy to validator + parametrized tests",
            )
            _print_attempt("1", r1, ev1)
            assert r1.final_decision == "REPLAN"

            print("agent changes strategy.")
            print("attempt 2:")
            r2, ev2 = _attempt(
                run, runner, repo, contract, criteria, attempt=2,
                contract_id="PHASE-001-R1", b_pass=True, c_pass=True,
                note="continued to next step",
            )
            _print_attempt("2", r2, ev2)
            assert r2.final_decision == "ACCEPT"

            run.finish_run(
                status=RunFinishStatus.COMPLETED, reason_code=ReasonCode.RUN_COMPLETED
            )

        print("=" * 78)
        _print_proof(store, run_id)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(runs_dir.parent, ignore_errors=True)
    return 0


def _print_proof(store: LineageStore, run_id: str) -> None:
    """Read the trace back and prove each number from the append-only log."""
    log = store.read_run(run_id)
    collected = [e for e in log.events if e.event == "evidence.collected"]
    gated = [e for e in log.events if e.event == "decision.gated"]
    print("trace proof (append-only events.jsonl):")
    print(f"  policy config hash: {log.run.config.policy_config_hash}")
    print(f"  collector versions: {log.run.config.collector_versions}")
    for e in collected:
        print(
            f"  {e.check_id}: collector={e.collector} version={e.collector_version} "
            f"provenance={e.provenance.value} passed={e.passed}"
        )
    for e in gated:
        depended_on_claimed = e.assurance.value == "claimed"
        print(
            f"  decision.gated: candidate={e.candidate_decision} "
            f"final={e.final_decision} assurance={e.assurance.value} "
            f"depended_on_claimed={depended_on_claimed}"
        )
    print("the final decision did NOT depend on CLAIMED evidence: "
          f"{all(e.provenance is not EvidenceProvenance.CLAIMED for e in collected)}")


if __name__ == "__main__":
    raise SystemExit(main())

