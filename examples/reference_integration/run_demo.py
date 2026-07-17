"""BOUND's own reference integration entry point (v0.6 Phase 10).

Runs BOUND's real verification commands, builds a real
:class:`~bound.evidence.ExecutionEvidence` from the captured output using the
pure :mod:`bound.collectors` parsers, evaluates it via BOUND's deterministic
policy, and writes ``bound_integration/run.json`` (a real
:class:`~bound.report.RunTrace`) plus ``bound_integration/INTEGRATION_REPORT.md``
(rendered from the same trace via :func:`bound.report.render_from_trace`).

Every value in the trace comes from a real run:

* the contract is the real ``PHASE-001`` :class:`~bound.contracts.StepContract`
  (the plan id is carried verbatim);
* the evidence is gathered by running the real ``uv run pytest`` commands and
  ``git status --porcelain`` — the raw stdout/stderr/exit code are captured
  from the *same* run as the structured evidence;
* the evaluation is BOUND's deterministic output (scores, score, threshold,
  decision, ``next_action``) — never manually reconstructed;
* ``token_usage`` / ``runtime_seconds`` / agent tool-call telemetry stay ``None``
  — this environment cannot observe them, so they are never fabricated.

Recursion safety
----------------
When this module spawns ``uv run pytest`` it sets the
:data:`NESTED_GUARD_ENVVAR` environment variable in the subprocess so a nested
pytest run can detect re-entry and skip any test that would itself invoke this
integration (mirroring the benchmark's ``BOUND_INTEGRATION_NESTED`` guard). No
current BOUND test triggers that path, but the guard is documented and
defensive.

Invoke directly::

    uv run python -m examples.reference_integration.run_demo
    # or
    uv run python examples/reference_integration/run_demo.py
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import bound
from bound.collectors import (
    GitInspection,
    ServiceTestEvidence,
    parse_git_status_porcelain,
    parse_pytest_summary,
)
from bound.contracts import AcceptanceCheck, RiskCheck, StepBudget, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.integration import evaluate_agent_step
from bound.models import BoundCriteria
from bound.report import (
    DecisionHistoryEntry,
    RawCommandRecord,
    RunTrace,
    render_from_trace,
)

#: Environment variable used as a recursion guard when this integration spawns a
#: nested ``uv run pytest``. A nested run can detect re-entry and skip any test
#: that would itself invoke this integration. Project-local; never affects
#: BOUND's decisions.
NESTED_GUARD_ENVVAR: str = "BOUND_REFERENCE_NESTED"

#: Repo root (two levels up from this file), computed from ``__file__`` so the
#: run is independent of the caller's working directory.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
RUN_JSON_PATH: Path = REPO_ROOT / "bound_integration" / "run.json"
REPORT_PATH: Path = REPO_ROOT / "bound_integration" / "INTEGRATION_REPORT.md"

#: Stable plan id for the v0.6 reporting + demo step (mirrors the PHASE-NNN
#: convention; carried verbatim from plan to contract to report).
DEFAULT_PLAN_ID: str = "PHASE-001"

#: The full-suite verification command BOUND itself uses.
VERIFICATION_COMMAND: tuple[str, ...] = ("uv", "run", "pytest", "-q")

#: A service-specific verification command. Running only the calculator test
#: module is genuine *service-specific* evidence: the full suite alone cannot
#: prove the BOUND mathematical core ran, because unrelated passing tests would
#: mask an absent or empty ``tests/test_calculator.py``. Wired to the
#: ``service-tests-pass`` acceptance check (passes iff exit 0 AND >=1 test).
SERVICE_VERIFICATION_COMMAND: tuple[str, ...] = (
    "uv",
    "run",
    "pytest",
    "tests/test_calculator.py",
    "-q",
)

#: Default acceptance threshold used when evaluating the step.
DEFAULT_THRESHOLD: float = 0.75

#: Paths the v0.6 release legitimately touches (the intended change set). Any
#: path reported by ``git status --porcelain`` whose prefix is NOT in this set
#: counts as an unexpected artifact. This covers the full coordinated v0.6
#: release scope; the risk check guards against files outside that scope.
ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "src/bound",
    "tests",
    "examples",
    "scripts",
    "assets",
    "bound_integration",
    "README.md",
    "architecture",
    "docs",
    "integrations",
    "CHANGELOG.md",
    "pyproject.toml",
    "uv.lock",  # version bump legitimately updates the committed lockfile
)

#: The expected artefacts this step produces (checked against the filesystem).
EXPECTED_ARTIFACTS: tuple[str, ...] = (
    "src/bound/report.py",
    "examples/reference_integration/",
    "bound_integration/",
)

#: The honest trajectory this trace records: a single first-try evaluation.
_TRAJECTORY_TEMPLATE: tuple[str, ...] = (
    "PLAN.md (BOUND v0.6 — reporting + demo step)",
    "PHASE-001 (StepContract.id, carried verbatim from the plan)",
    "uv run pytest -q + uv run pytest tests/test_calculator.py -q (real verification)",
    "ExecutionEvidence (acceptance + risk, observed — not assumed)",
    "BOUND evaluate_agent_step (deterministic)",
    "{decision} -> {next_action}",
    "INTEGRATION_REPORT.md + run.json (post-run audit record)",
)


def _distribution_version() -> str | None:
    """Return the installed ``bound-policy`` distribution version, if resolvable.

    Best-effort metadata lookup; never required.
    """
    try:
        from importlib.metadata import version

        return version("bound-policy")
    except Exception:  # noqa: BLE001 - best-effort, never fatal
        return None


def _run_command(
    command: tuple[str, ...],
    *,
    cwd: Path,
    nested: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run *command* and return its completed-process record.

    Captures stdout/stderr as text. A non-zero exit code is *not* an error
    here: a failing test suite is exactly the signal we want to record, so we
    never raise on a non-zero exit. When *nested* is true (the pytest
    commands), the :data:`NESTED_GUARD_ENVVAR` flag is set in the subprocess
    environment so a nested pytest run can detect re-entry.

    Args:
        command: The command tuple to execute.
        cwd: Working directory in which to run the command.
        nested: Whether to set the recursion-guard env var (for pytest runs).

    Returns:
        The :class:`subprocess.CompletedProcess` with captured text output.
    """
    env = os.environ.copy()
    if nested:
        env[NESTED_GUARD_ENVVAR] = "1"
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def build_contract(plan_id: str = DEFAULT_PLAN_ID) -> StepContract:
    """Build the StepContract for the BOUND v0.6 reporting + demo step.

    The meaningful boundary BOUND evaluates: the v0.6 release is verified by a
    green full suite AND a green, non-vacuous service-specific run of the
    BOUND calculator tests, with changes scoped to the v0.6 effort. The
    contract ``id`` is the stable plan identity (carried verbatim from the plan
    into the contract and onward to the report).

    Args:
        plan_id: The stable plan id to use as the contract ``id``.

    Returns:
        A :class:`StepContract` with concrete, observable acceptance and risk
        checks and a step budget.
    """
    return StepContract(
        id=plan_id,
        description=(
            "Ship BOUND v0.6 reporting, a real demo trace, and README evidence "
            "links — verified by BOUND's own test suite."
        ),
        goal=(
            "BOUND v0.6 is verified by a green full `uv run pytest -q` suite and "
            "a green service-specific `uv run pytest tests/test_calculator.py -q` "
            "run that executed >=1 test, with changes scoped to the v0.6 effort."
        ),
        acceptance_checks=[
            AcceptanceCheck(
                id="tests-pass",
                description="`uv run pytest -q` exits 0 (the full suite is green).",
                required=True,
            ),
            AcceptanceCheck(
                id="service-tests-pass",
                description=(
                    "`uv run pytest tests/test_calculator.py -q` exits 0 AND "
                    "executed >=1 test (the calculator tests genuinely ran and "
                    "passed)."
                ),
                required=True,
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no-unexpected-files",
                description=(
                    "No files outside the v0.6 change set were modified or added."
                ),
                severity=0.6,
            ),
        ],
        expected_artifacts=list(EXPECTED_ARTIFACTS),
        budget=StepBudget(max_retries=3, max_tool_calls=40),
    )


def _expected_artifacts_present() -> list[str]:
    """Return the expected artefacts that exist on the filesystem."""
    present: list[str] = []
    for artifact in EXPECTED_ARTIFACTS:
        path = REPO_ROOT / artifact
        if path.exists():
            present.append(artifact)
    return present


def _collect_evidence_and_raw() -> tuple[ExecutionEvidence, dict[str, RawCommandRecord]]:
    """Run the real verification commands and build evidence + raw records.

    Runs ``uv run pytest -q`` (full suite), ``uv run pytest
    tests/test_calculator.py -q`` (service-specific), and ``git status
    --porcelain`` as subprocesses, parses the captured output with the pure
    :mod:`bound.collectors` parsers, and assembles a real
    :class:`ExecutionEvidence` plus the verbatim :class:`RawCommandRecord`
    outputs from the *same* run.

    Returns:
        A ``(evidence, raw_commands)`` tuple from a single verification run.
    """
    # Full suite.
    full_proc = _run_command(VERIFICATION_COMMAND, cwd=REPO_ROOT, nested=True)
    full_summary = parse_pytest_summary(full_proc.stdout)
    tests_pass = full_proc.returncode == 0
    full_source = " ".join(VERIFICATION_COMMAND)

    # Service-specific suite.
    service_proc = _run_command(
        SERVICE_VERIFICATION_COMMAND, cwd=REPO_ROOT, nested=True
    )
    service_summary = parse_pytest_summary(service_proc.stdout)
    service_evidence = ServiceTestEvidence(
        command_succeeded=service_proc.returncode == 0,
        executed_test_count=service_summary.executed_test_count,
    )
    service_source = " ".join(SERVICE_VERIFICATION_COMMAND)

    # Git inspection — parse with the pure collector; on command failure use the
    # failed-command factory so "no unexpected files" can never become a pass.
    git_proc = _run_command(("git", "status", "--porcelain"), cwd=REPO_ROOT)
    if git_proc.returncode == 0:
        git = parse_git_status_porcelain(
            git_proc.stdout, ALLOWED_PATH_PREFIXES
        )
        risk_source = "git status --porcelain"
        risk_details = (
            "no unexpected paths"
            if not git.unexpected_paths
            else "unexpected_artifacts=" + ",".join(git.unexpected_paths)
        )
    else:
        git = GitInspection.command_failed()
        risk_source = "git status --porcelain (failed)"
        risk_details = (
            "git status failed; no-unexpected-files not verifiable "
            "(unavailable evidence, not a pass)"
        )

    no_unexpected_pass = git.is_clean_proven()
    produced_artifacts = sorted(set(_expected_artifacts_present()))

    evidence = ExecutionEvidence(
        acceptance=[
            CheckEvidence(
                check_id="tests-pass",
                passed=tests_pass,
                source=full_source,
                details=(
                    f"exit_code={full_proc.returncode}; "
                    f"executed={full_summary.executed_test_count}"
                ),
            ),
            CheckEvidence(
                check_id="service-tests-pass",
                passed=service_evidence.passed,
                source=service_source,
                details=(
                    f"exit_code={service_proc.returncode}; "
                    f"executed={service_summary.executed_test_count}"
                ),
            ),
        ],
        risks=[
            CheckEvidence(
                check_id="no-unexpected-files",
                passed=no_unexpected_pass,
                source=risk_source,
                details=risk_details,
            ),
        ],
        produced_artifacts=produced_artifacts,
        unexpected_artifacts=sorted(git.unexpected_paths),
        # token_usage / runtime_seconds are NOT observable here -> stay None.
    )
    raw_commands = {
        "full_suite": RawCommandRecord(
            command=full_source,
            returncode=full_proc.returncode,
            stdout=full_proc.stdout,
            stderr=full_proc.stderr,
        ),
        "service_suite": RawCommandRecord(
            command=service_source,
            returncode=service_proc.returncode,
            stdout=service_proc.stdout,
            stderr=service_proc.stderr,
        ),
        "git_status": RawCommandRecord(
            command="git status --porcelain",
            returncode=git_proc.returncode,
            stdout=git_proc.stdout,
            stderr=git_proc.stderr,
        ),
    }
    return evidence, raw_commands


def build_run_trace(plan_id: str = DEFAULT_PLAN_ID) -> RunTrace:
    """Build a real :class:`RunTrace` from a live evaluation.

    Orchestrates the public BOUND pieces (contract -> evidence -> evaluation)
    and serializes only what was genuinely observed. Owns no policy logic: it
    calls :func:`bound.evaluate_agent_step` and records the
    :class:`~bound.integration.AgentControlResult` BOUND returned.

    Args:
        plan_id: The stable plan id (also the contract id).

    Returns:
        A :class:`RunTrace` with real observed values; telemetry that is
        unobservable stays ``None`` (never fabricated).
    """
    contract = build_contract(plan_id)
    # Ensure the output directory exists *before* collecting evidence so git
    # status and the expected-artifacts filesystem check observe it as a
    # produced artifact (the reference integration produces it).
    RUN_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    evidence, raw_commands = _collect_evidence_and_raw()
    criteria = BoundCriteria(threshold=DEFAULT_THRESHOLD)
    result = evaluate_agent_step(contract, evidence, criteria)

    decision = result.evaluation.decision
    next_action = result.next_action
    trajectory = [
        t.replace("{decision}", str(decision)).replace(
            "{next_action}", str(next_action)
        )
        for t in _TRAJECTORY_TEMPLATE
    ]

    return RunTrace(
        plan_id=plan_id,
        step_id=contract.id,
        run_id=uuid.uuid4().hex,
        bound_version=getattr(bound, "__version__", None),
        bound_distribution_version=_distribution_version(),
        timestamp=datetime.now(UTC).isoformat(),
        contract=contract,
        evidence=evidence,
        evaluation=result.evaluation,
        next_action=next_action,
        feedback=result.feedback,
        raw_commands=raw_commands,
        decision_history=[
            DecisionHistoryEntry(
                step_id=contract.id,
                attempt=1,
                decision=decision,
                next_action=next_action,
                note="first evaluation; no replan or retry",
            )
        ],
        retries=[],
        replans=[],
        trajectory=trajectory,
        # token_usage / runtime_seconds / tool_call_count / model_metadata
        # are NOT instrumented here -> stay None (never fabricated).
    )


def write_artifacts(
    plan_id: str = DEFAULT_PLAN_ID,
    *,
    run_json_path: Path = RUN_JSON_PATH,
    report_path: Path = REPORT_PATH,
) -> RunTrace:
    """Build a real trace and write ``run.json`` + ``INTEGRATION_REPORT.md``.

    Args:
        plan_id: The stable plan id.
        run_json_path: Where to write the machine-readable trace.
        report_path: Where to write the rendered report.

    Returns:
        The :class:`RunTrace` that was written.
    """
    run = build_run_trace(plan_id)
    run_json_path.parent.mkdir(parents=True, exist_ok=True)
    run_json_path.write_text(
        run.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(render_from_trace(run) + "\n", encoding="utf-8")
    return run


def main() -> None:
    """Write ``run.json`` + ``INTEGRATION_REPORT.md`` from a real run."""
    run = write_artifacts()
    ev = run.evaluation
    print(
        "Wrote bound_integration/run.json + INTEGRATION_REPORT.md: "
        f"plan_id={run.plan_id} "
        f"step_id={run.step_id} "
        f"bound_version={run.bound_version} "
        f"(dist {run.bound_distribution_version}) "
        f"decision={ev.decision} "
        f"next_action={run.next_action} "
        f"score={ev.score} "
        f"threshold={ev.threshold}"
    )


if __name__ == "__main__":
    main()





