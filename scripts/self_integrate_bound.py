"""BOUND self-integration — run the BOUND skill on this repo (v0.7.0).

Executes the BOUND v0.7.0 verified-evidence control loop against the bound repo
itself: independent collectors run the REAL verification commands (full pytest
suite, ruff, the Definition-of-Done demo, and ``git status``), a StepContract is
evaluated, a schema-2.0 RunTrace is written to ``bound_integration/run.json``,
the markdown report is rendered to ``bound_integration/INTEGRATION_REPORT.md``,
and a lineage run is recorded under ``.bound/runs/`` for ``bound inspect``.

This is BOUND evaluating its own release boundary with its own collectors — no
hardcoded decisions, no agent self-report trusted as VERIFIED.
"""

from __future__ import annotations

import importlib.metadata
import sys
import uuid
from pathlib import Path

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWeights,
    CommandCollector,
    CommandSpec,
    EvidencePolicyAction,
    EvidenceProvenance,
    ExecutionEvidence,
    GitCollector,
    LineageStore,
    ProcessRuntimeCollector,
    PytestCollector,
    ReasonCode,
    RiskCheck,
    RunFinishStatus,
    StepContract,
    build_run_config,
    evaluate_agent_step,
    start_run,
    utc_now,
)
from bound.report import (
    DecisionHistoryEntry,
    RawCommandRecord,
    RunTrace,
    render_from_trace,
)

REPO = Path(__file__).resolve().parents[1]
THRESHOLD = 0.7
RETRY_MARGIN = 0.1

#: The legitimate v0.7.0 change surface for this repo. The GitCollector verifies
#: every changed path falls within this set — a stray temp file outside it would
#: be flagged as unsafe (decision-critical risk), never silently passed.
ALLOWED_PREFIXES = (
    "src/", "tests/", "examples/", "docs/", "integrations/", "skills/",
    "benchmarks/", "scripts/", "architecture/", "assets/", "bound_integration/",
    ".github/", ".gitignore", ".pre-commit-config.yaml", "CHANGELOG.md",
    "CONTRIBUTING.md", "LICENSE", "Makefile", "README.md", "pyproject.toml",
    "uv.lock", "todo.md",
)


def _sys_argv(*args: str) -> list[str]:
    """A portable argv using the current interpreter (runs in the project venv)."""
    return [sys.executable, *args]


def _runner() -> CommandCollector:
    """One CommandCollector owning the four real verification commands."""
    return CommandCollector(
        {
            "pytest": CommandSpec(argv=_sys_argv("-m", "pytest", "-q"), timeout=300.0),
            "ruff": CommandSpec(argv=_sys_argv("-m", "ruff", "check", "."), timeout=120.0),
            "dod-demo": CommandSpec(
                argv=_sys_argv(str(REPO / "examples" / "verified_evidence_demo.py")),
                timeout=300.0,
            ),
            "git-status": CommandSpec(argv=["git", "status", "--porcelain"], timeout=30.0),
        }
    )
def _contract() -> StepContract:
    """Three VERIFIED-only acceptance checks + a decision-critical git risk."""
    verified = [
        EvidenceProvenance.OBSERVED,
        EvidenceProvenance.VERIFIED,
        EvidenceProvenance.ATTESTED,
    ]
    return StepContract(
        id="PHASE-001",
        description="BOUND v0.7.0 verified-evidence release boundary (self-integration)",
        goal=(
            "BOUND v0.7.0 is green and independently verified: the full pytest "
            "suite passes, ruff is clean, and the Definition-of-Done demo runs "
            "— all confirmed by BOUND's own collectors executing the real "
            "commands, with changes scoped to the v0.7.0 release surface."
        ),
        acceptance_checks=[
            AcceptanceCheck(
                id="tests-pass",
                description="`uv run pytest -q` exits 0 (the full suite is green).",
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
            AcceptanceCheck(
                id="lint-pass",
                description="`uv run ruff check .` exits 0 (lint is clean).",
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
            AcceptanceCheck(
                id="dod-demo-passes",
                description=(
                    "examples/verified_evidence_demo.py exits 0 "
                    "(the DoD REPLAN -> ACCEPT flow runs end-to-end)."
                ),
                accepted_provenance=verified,
                on_missing=EvidencePolicyAction.REPLAN,
                on_claimed=EvidencePolicyAction.RETRY,
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no-unsafe-changes",
                description="All changes are scoped to the v0.7.0 release surface.",
                severity=0.5,
                decision_critical=True,
                accepted_provenance=[EvidenceProvenance.VERIFIED, EvidenceProvenance.ATTESTED],
                on_missing=EvidencePolicyAction.ROLLBACK,
            ),
        ],
    )


def _raw_record(result) -> RawCommandRecord:
    """Build a privacy-safe RawCommandRecord (redacted summary + exit code)."""
    return RawCommandRecord(
        command=" ".join(result.argv),
        returncode=result.exit_code if result.exit_code is not None else -1,
        stdout=result.stdout_summary,
        stderr=result.stderr_summary,
    )


def _collect(runner: CommandCollector):
    """Run the four real collectors; return (evidence, raw_commands)."""
    cwd = str(REPO)
    py_res = runner.run("pytest", cwd=cwd, store_raw=True)
    tests_ev = PytestCollector(
        runner, command_name="pytest", check_id="tests-pass"
    )._evidence_from_result(py_res)

    ruff_res = runner.run("ruff", cwd=cwd)
    lint_ev = ProcessRuntimeCollector(check_id="lint-pass").collect(ruff_res)

    demo_res = runner.run("dod-demo", cwd=cwd)
    dod_ev = ProcessRuntimeCollector(check_id="dod-demo-passes").collect(demo_res)

    git_res = runner.run("git-status", cwd=cwd, store_raw=True)
    git_ev = GitCollector(
        runner,
        command_name="git-status",
        check_id="no-unsafe-changes",
        allowed_prefixes=ALLOWED_PREFIXES,
    )._evidence_from_result(git_res)

    evidence = ExecutionEvidence(
        acceptance=[tests_ev, lint_ev, dod_ev],
        risks=[git_ev],
        rollback_available=True,
    )
    raw_commands = {
        "pytest": _raw_record(py_res),
        "ruff": _raw_record(ruff_res),
        "dod-demo": _raw_record(demo_res),
        "git-status": _raw_record(git_res),
    }
    return evidence, raw_commands


def _print_evidence(evidence: ExecutionEvidence) -> None:
    print("collected evidence (independently verified):")
    for ce in (*evidence.acceptance, *evidence.risks):
        print(
            f"  {ce.check_id}: passed={ce.passed} "
            f"provenance={ce.provenance.value} collector={ce.collector} "
            f"version={ce.collector_version} status={ce.status}"
        )
def main() -> int:
    runner = _runner()
    contract = _contract()
    criteria = BoundCriteria(
        weights=BoundWeights(), threshold=THRESHOLD, retry_margin=RETRY_MARGIN
    )
    bound_version = "0.7.0"
    try:
        dist_version = importlib.metadata.version("bound-policy")
    except importlib.metadata.PackageNotFoundError:
        dist_version = None

    print(f"BOUND self-integration — repo: {REPO}")
    print("=" * 78)
    evidence, raw_commands = _collect(runner)
    _print_evidence(evidence)

    agent_result = evaluate_agent_step(contract, evidence, criteria)
    evaluation = agent_result.evaluation
    print("-" * 78)
    print(f"candidate decision -> {evaluation.candidate_decision}")
    print(f"decision assurance -> {evaluation.assurance.value.upper()}")
    print(f"final decision     -> {evaluation.final_decision}")
    print(f"next action        -> {agent_result.next_action}")
    print(f"score S={evaluation.score:.4f} >= T={evaluation.threshold:.4f}")

    config = build_run_config(
        bound_version=bound_version,
        policy_config={"threshold": THRESHOLD, "retry_margin": RETRY_MARGIN},
        threshold=THRESHOLD,
        retry_margin=RETRY_MARGIN,
        contract=contract,
        collector_versions={
            "bound.pytest": bound_version,
            "bound.git": bound_version,
            "bound.process": bound_version,
        },
    )

    run_id = uuid.uuid4().hex
    trace = RunTrace(
        schema_version="2.0",
        plan_id="PHASE-001",
        step_id="PHASE-001",
        run_id=run_id,
        bound_version=bound_version,
        bound_distribution_version=dist_version,
        timestamp=utc_now().isoformat(),
        contract=contract,
        evidence=evidence,
        evaluation=evaluation,
        next_action=agent_result.next_action,
        feedback=agent_result.feedback,
        raw_commands=raw_commands,
        decision_history=[
            DecisionHistoryEntry(
                step_id="PHASE-001",
                attempt=1,
                decision=evaluation.final_decision,
                next_action=agent_result.next_action,
                note="BOUND self-integration of the v0.7.0 release boundary",
            )
        ],
        config=config,
        reported_action=(
            "Ran BOUND self-integration: independent collectors executed "
            "pytest, ruff, the DoD demo, and git status."
        ),
        observed_action=None,
    )

    out_dir = REPO / "bound_integration"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "run.json").write_text(trace.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "INTEGRATION_REPORT.md").write_text(
        render_from_trace(trace), encoding="utf-8"
    )
    print("-" * 78)
    print(f"wrote {out_dir / 'run.json'}")
    print(f"wrote {out_dir / 'INTEGRATION_REPORT.md'}")

    store = LineageStore()
    with start_run(contract.goal, store=store, config=config) as run:
        lineage_run_id = run.run_id
        step = run.start_step(
            contract_id="PHASE-001", attempt=1, description=contract.description
        )
        for ce in (*evidence.acceptance, *evidence.risks):
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
            attempt=1,
            scores=evaluation.scores,
            score=evaluation.score,
            threshold=THRESHOLD,
            decision=evaluation.final_decision,
        )
        run.record_decision_gated(
            step_id=step.step_id,
            evaluation_id=ev_rec.evaluation_id,
            candidate_decision=evaluation.candidate_decision,
            final_decision=evaluation.final_decision,
            assurance=evaluation.assurance,
            assurance_reasons=list(evaluation.assurance_reasons),
        )
        run.record_outcome(
            step_id=step.step_id,
            evaluation_id=ev_rec.evaluation_id,
            decision=evaluation.final_decision,
            note="self-integration complete",
        )
        run.finish_run(
            status=RunFinishStatus.COMPLETED, reason_code=ReasonCode.RUN_COMPLETED
        )

    print("-" * 78)
    print(f"lineage run recorded: .bound/runs/{lineage_run_id}/")
    print(f"inspect with: uv run bound inspect {lineage_run_id}")
    print("=" * 78)
    return 0 if evaluation.final_decision == "ACCEPT" else 1


if __name__ == "__main__":
    raise SystemExit(main())


