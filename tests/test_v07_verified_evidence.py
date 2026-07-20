"""BOUND v0.7.0 — Verified Evidence & Decision Lineage critical tests (todo §16).

These tests pin the honesty invariants of the v0.7 "verified evidence"
model: trust provenance is never fabricated from weaker provenance, missing is
never silently coerced to zero, an independent collector's observation always
outranks an agent self-report, and the decision assurance/gating layer blocks an
ACCEPT that rests only on CLAIMED or missing/invalid evidence. They also cover
backwards compatibility (schema-1.0 traces), the lineage config hash, default
raw-output redaction, and decision/assurance determinism.

The Definition-of-Done REPLAN -> ACCEPT flow (todo §16 "Nieuwe Definition of
Done") is covered both as a fast parametrised-style test (constructed VERIFIED
evidence) and by executing the real ``examples/verified_evidence_demo.py``
script end-to-end with live pytest + git collectors.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWeights,
    BoundWorkflow,
    BudgetCollector,
    CheckEvidence,
    CommandCollector,
    CommandSpec,
    ContractEvaluator,
    DecisionAssurance,
    EvidencePolicyAction,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
    JUnitCollector,
    LineageStore,
    PytestCollector,
    ReasonCode,
    RiskCheck,
    RunFinishStatus,
    RunStartedEvent,
    StepBudget,
    StepContract,
    default_redactor,
    generate_run_id,
    parse_lineage_event,
    start_run,
)
from bound.lineage import build_run_config, compute_policy_config_hash
from tests.conftest import REPO_ROOT

#: Provenance allow-list for a check that must be *independently* verified: it
#: accepts BOUND-collected evidence but rejects bare agent self-report (CLAIMED)
#: and missing/invalid evidence.
_VERIFIED_ACCEPTED: list[EvidenceProvenance] = [
    EvidenceProvenance.OBSERVED,
    EvidenceProvenance.VERIFIED,
    EvidenceProvenance.ATTESTED,
]

#: Threshold 0.7 / retry margin 0.1 with default unit weights. With influence
#: DEFAULTED to 0.0, no budget (C=0) and no violated risk (R=0), the score S
#: equals acceptance A, so A=1.0 -> ACCEPT and A=1/3 -> REPLAN (gap 0.367 > 0.1).
_CRITERIA = BoundCriteria(weights=BoundWeights(), threshold=0.7, retry_margin=0.1)


def _sys_argv(*args: str) -> list[str]:
    """A portable argv using the current interpreter (works on any platform)."""
    return [sys.executable, *args]


def _ev(
    check_id: str,
    *,
    passed: bool | None,
    provenance: EvidenceProvenance,
    source: str = "",
    status: EvidenceStatus | None = None,
    collector: str | None = None,
) -> CheckEvidence:
    """Build a :class:`CheckEvidence` with an explicit trust provenance."""
    return CheckEvidence(
        check_id=check_id,
        passed=passed,
        provenance=provenance,
        source=source,
        status=status,
        collector=collector,
    )


def _verified_check(
    check_id: str,
    description: str,
    *,
    on_missing: EvidencePolicyAction = EvidencePolicyAction.REPLAN,
    on_claimed: EvidencePolicyAction = EvidencePolicyAction.RETRY,
) -> AcceptanceCheck:
    """A required acceptance check that only accepts independent evidence."""
    return AcceptanceCheck(
        id=check_id,
        description=description,
        accepted_provenance=list(_VERIFIED_ACCEPTED),
        on_missing=on_missing,
        on_claimed=on_claimed,
    )


def _contract(
    *,
    acceptance: list[AcceptanceCheck] | None = None,
    risks: list[RiskCheck] | None = None,
    budget: StepBudget | None = None,
    cid: str = "PHASE-001",
) -> StepContract:
    """Build a minimal valid provenance-aware :class:`StepContract`."""
    return StepContract(
        id=cid,
        description="A verified-evidence step",
        goal="Pin the v0.8 honesty invariants",
        acceptance_checks=acceptance if acceptance is not None else [],
        risk_checks=risks or [],
        budget=budget,
    )


def _evaluate(
    contract: StepContract,
    evidence: ExecutionEvidence,
) -> object:
    """Run the full contract -> policy pipeline and return the result."""
    return BoundWorkflow().evaluate_step(
        contract=contract, evidence=evidence, criteria=_CRITERIA
    )


# ---------------------------------------------------------------------------
# (a) Agent claims "tests pass" but pytest actually fails -> no VERIFIED ACCEPT
# ---------------------------------------------------------------------------


def _failing_pytest_evidence(tmp_path: Path) -> CheckEvidence:
    """Run the real PytestCollector against a failing test; return VERIFIED fail."""
    (tmp_path / "test_fail.py").write_text("def test_fail():\n    assert False, 'boom'\n")
    runner = CommandCollector(
        {
            "pytest": CommandSpec(
                argv=_sys_argv(
                    "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)
                ),
                timeout=60.0,
            )
        }
    )
    return PytestCollector(runner, check_id="tests-pass").collect()


class TestClaimedPassVsVerifiedFail:
    """An agent's CLAIMED pass cannot override a collector's VERIFIED failure."""

    def test_no_verified_accept(self, tmp_path: Path) -> None:
        """Agent says "tests pass" (CLAIMED) but pytest fails (VERIFIED) -> not ACCEPT.

        Intent (todo §16 a): the agent's self-report is CLAIMED; BOUND's
        independent pytest collector shows a real failure with VERIFIED
        provenance. The VERIFIED record outranks the CLAIMED one, so the check
        fails (conservative all-must-pass dedup) and the decision is never ACCEPT
        — and the assurance is VERIFIED, proving the decision rested on the
        independently collected failure, not the claim.
        """
        verified_fail = _failing_pytest_evidence(tmp_path)
        assert verified_fail.passed is False
        assert verified_fail.provenance is EvidenceProvenance.VERIFIED

        agent_claim = _ev(
            "tests-pass",
            passed=True,
            provenance=EvidenceProvenance.CLAIMED,
            source="agent.self-report",
        )
        contract = _contract(acceptance=[_verified_check("tests-pass", "suite green")])
        evidence = ExecutionEvidence(
            acceptance=[agent_claim, verified_fail], rollback_available=True
        )
        result = _evaluate(contract, evidence)

        # The VERIFIED failure overrides the CLAIMED pass: never an ACCEPT.
        assert result.candidate_decision != "ACCEPT"
        assert result.final_decision != "ACCEPT"
        # Assurance is VERIFIED (CLAIMED did NOT win): had the claim won, the
        # check's strongest provenance would be CLAIMED, outside the
        # accepted_provenance, yielding CLAIMED assurance rather than VERIFIED.
        assert result.assurance is DecisionAssurance.VERIFIED
        assert result.candidate_decision == result.final_decision


# ---------------------------------------------------------------------------
# (b) Agent claims zero tool-calls; harness observes twelve -> observed wins
# ---------------------------------------------------------------------------


class TestObservedToolCallsOverrideClaimedZero:
    """A harness-observed count is authoritative; an agent's CLAIMED zero is not."""

    def test_observed_metric_is_twelve_not_zero(self) -> None:
        """The BudgetCollector stamps the observed 12 as OBSERVED, never CLAIMED 0."""
        metrics = BudgetCollector().metrics(tool_call_count=12)
        tool = metrics.tool_call_count
        assert tool.value == 12
        assert tool.provenance is EvidenceProvenance.OBSERVED
        assert tool.collector == "bound.budget"
        # The agent's hypothetical "zero tool-calls" is simply not the stored
        # metric: a CLAIMED 0 would carry provenance CLAIMED, not OBSERVED 12.
        assert (tool.value, tool.provenance) != (0, EvidenceProvenance.CLAIMED)

    def test_cost_uses_observed_twelve_not_claimed_zero(self) -> None:
        """The cost dimension uses the observed 12 (over budget), not the claim."""
        metrics = BudgetCollector().metrics(tool_call_count=12)
        contract = _contract(
            acceptance=[_verified_check("tests-pass", "tests pass")],
            budget=StepBudget(
                max_retries=2,
                max_tool_calls=10,
                max_tokens=100,
                max_runtime_seconds=10.0,
            ),
        )
        evidence = ExecutionEvidence(
            acceptance=[
                _ev(
                    "tests-pass",
                    passed=True,
                    provenance=EvidenceProvenance.VERIFIED,
                    collector="bound.pytest",
                )
            ],
            tool_call_count=metrics.tool_call_count,
            token_usage=metrics.token_usage,
            retry_count=metrics.retry_count,
            runtime_seconds=metrics.runtime_seconds,
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        cost_records = {r.source: r for r in evaluator.provenance["cost"]}
        tool_cost = cost_records["tool_cost"]
        # The observed 12 flowed into cost (over the 10 budget -> 1.0), stamped
        # OBSERVED — never the agent's claimed 0.
        assert tool_cost.value == 12
        assert tool_cost.provenance is EvidenceProvenance.OBSERVED



# ---------------------------------------------------------------------------
# (c) No token meter -> token_usage is MISSING (value None), never 0
# ---------------------------------------------------------------------------


class TestMissingTelemetryIsMissingNotZero:
    """An unmeasured telemetry signal is MISSING (value None), never a silent 0."""

    def test_unmeasured_token_metric_is_missing(self) -> None:
        """BudgetCollector with no token_usage -> value None / MISSING, not 0."""
        metrics = BudgetCollector().metrics()
        token = metrics.token_usage
        assert token.value is None
        assert token.provenance is EvidenceProvenance.MISSING

    def test_measured_zero_is_distinct_from_missing(self) -> None:
        """A measured 0 (OBSERVED) is a different signal from an unmeasured None."""
        measured_zero = BudgetCollector().metrics(token_usage=0).token_usage
        missing = BudgetCollector().metrics().token_usage
        assert measured_zero.value == 0
        assert measured_zero.provenance is EvidenceProvenance.OBSERVED
        assert missing.value is None
        assert missing.provenance is EvidenceProvenance.MISSING
        assert measured_zero != missing

    def test_missing_token_saturates_cost_with_missing_provenance(self) -> None:
        """A declared token budget with no meter saturates, stamped MISSING not 0."""
        metrics = BudgetCollector().metrics()
        contract = _contract(
            acceptance=[AcceptanceCheck(id="tests-pass", description="tests pass")],
            budget=StepBudget(max_tokens=100, max_tool_calls=10),
        )
        evidence = ExecutionEvidence(
            token_usage=metrics.token_usage,
            tool_call_count=metrics.tool_call_count,
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        cost_records = {r.source: r for r in evaluator.provenance["cost"]}
        token_cost = cost_records["token_cost"]
        # Missing telemetry is conservatively saturated, and crucially stamped
        # MISSING so a reader can never mistake it for a measured zero.
        assert token_cost.provenance is EvidenceProvenance.MISSING


# ---------------------------------------------------------------------------
# (d) No influence source -> DEFAULTED, never VERIFIED
# ---------------------------------------------------------------------------


class TestInfluenceDefaultsHonestly:
    """Missing downstream-influence evidence is DEFAULTED, never VERIFIED."""

    def test_influence_provenance_is_defaulted(self) -> None:
        """With no influence source, I is DEFAULTED (raw None, effective 0.0)."""
        contract = _contract(acceptance=[_verified_check("tests-pass", "tests pass")])
        evidence = ExecutionEvidence(
            acceptance=[
                _ev(
                    "tests-pass",
                    passed=True,
                    provenance=EvidenceProvenance.VERIFIED,
                    collector="bound.pytest",
                )
            ],
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        influence = evaluator.provenance["influence"][-1]
        assert influence.provenance is EvidenceProvenance.DEFAULTED
        assert influence.raw_value is None
        assert influence.effective_value == 0.0
        # DEFAULTED must never be presented as VERIFIED.
        assert influence.provenance is not EvidenceProvenance.VERIFIED
        assert "no evidence source" in (influence.reason or "")



# ---------------------------------------------------------------------------
# (e) PytestCollector finds ZERO tests -> no proven PASS
# ---------------------------------------------------------------------------


def _zero_test_pytest_evidence(tmp_path: Path) -> CheckEvidence:
    """Run the real PytestCollector against a test file that collects nothing."""
    (tmp_path / "test_empty.py").write_text("\n")
    runner = CommandCollector(
        {
            "pytest": CommandSpec(
                argv=_sys_argv(
                    "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)
                ),
                timeout=60.0,
            )
        }
    )
    return PytestCollector(runner, check_id="tests-pass").collect()


class TestZeroTestsIsNoProvenPass:
    """A pytest run that executes zero tests is not a proven PASS."""

    def test_zero_tests_not_passed(self, tmp_path: Path) -> None:
        """Zero collected tests -> passed is not True, UNVERIFIED (item 8)."""
        evidence = _zero_test_pytest_evidence(tmp_path)
        assert evidence.passed is not True
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.UNVERIFIED

    def test_zero_tests_yields_no_accept(self, tmp_path: Path) -> None:
        """A zero-test VERIFIED record backing a check never yields ACCEPT."""
        evidence = _zero_test_pytest_evidence(tmp_path)
        contract = _contract(acceptance=[_verified_check("tests-pass", "tests pass")])
        result = _evaluate(
            contract,
            ExecutionEvidence(acceptance=[evidence], rollback_available=True),
        )
        assert result.candidate_decision != "ACCEPT"
        assert result.final_decision != "ACCEPT"


# ---------------------------------------------------------------------------
# (f) JUnit stale -> evidence rejected (INVALID, not VERIFIED)
# ---------------------------------------------------------------------------


def _junit(tests: int, failures: int = 0) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="s" tests="{tests}" failures="{failures}">\n'
        "</testsuite>\n"
    )


class TestStaleJUnitIsRejected:
    """A stale JUnit artefact is INVALID evidence, never a VERIFIED pass."""

    def test_stale_artefact_invalid(self, tmp_path: Path) -> None:
        """An artefact older than the freshness window is INVALID (item 8)."""
        path = tmp_path / "junit.xml"
        path.write_text(_junit(tests=5))
        future = datetime.now(UTC) + timedelta(hours=1)
        evidence = JUnitCollector(max_age_seconds=1.0).collect(path, now=future)
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING
        assert evidence.provenance is not EvidenceProvenance.VERIFIED

    def test_stale_evidence_blocks_verified_accept(self, tmp_path: Path) -> None:
        """Stale INVALID evidence backing a restricted check -> INSUFFICIENT."""
        path = tmp_path / "junit.xml"
        path.write_text(_junit(tests=5))
        future = datetime.now(UTC) + timedelta(hours=1)
        stale = JUnitCollector(max_age_seconds=1.0).collect(path, now=future)
        contract = _contract(acceptance=[_verified_check("tests-pass", "tests pass")])
        evaluator = ContractEvaluator()
        evaluator.evaluate(
            contract,
            ExecutionEvidence(acceptance=[stale], rollback_available=True),
        )
        assert evaluator.assurance_assessment.assurance is DecisionAssurance.INSUFFICIENT


# ---------------------------------------------------------------------------
# (g) Collector crashes -> INSUFFICIENT (MISSING/INVALID), never PASS
# ---------------------------------------------------------------------------


def _crashed_pytest_evidence() -> CheckEvidence:
    """A PytestCollector whose command cannot start yields INVALID/MISSING."""
    runner = CommandCollector({"pytest": CommandSpec(argv=["no-such-pytest-binary-zz"])})
    return PytestCollector(runner, check_id="tests-pass").collect()


class TestCollectorCrashIsInsufficient:
    """A crashed collector yields INVALID/MISSING evidence -> INSUFFICIENT."""

    def test_crash_is_invalid_missing(self) -> None:
        """A collector whose command cannot start -> INVALID, MISSING, never pass."""
        evidence = _crashed_pytest_evidence()
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_crash_yields_insufficient_assurance(self) -> None:
        """INVALID evidence backing a restricted check -> INSUFFICIENT, never ACCEPT."""
        evidence = _crashed_pytest_evidence()
        contract = _contract(acceptance=[_verified_check("tests-pass", "tests pass")])
        evaluator = ContractEvaluator()
        evaluator.evaluate(
            contract,
            ExecutionEvidence(acceptance=[evidence], rollback_available=True),
        )
        assessment = evaluator.assurance_assessment
        assert assessment.assurance is DecisionAssurance.INSUFFICIENT
        assert assessment.accept_block_action is EvidencePolicyAction.REPLAN
        # The candidate is not ACCEPT (the check failed on None evidence), so the
        # gate does not need to fire — but the assurance is still INSUFFICIENT.
        assert assessment.assurance is not DecisionAssurance.VERIFIED



# ---------------------------------------------------------------------------
# (h) Risk evidence only CLAIMED -> minimal assurance blocks ACCEPT
# ---------------------------------------------------------------------------


class TestClaimedRiskBlocksAccept:
    """A decision-critical risk backed only by agent self-report blocks ACCEPT."""

    def test_claimed_risk_downgrades_accept_to_replan(self) -> None:
        """CLAIMED-only risk evidence -> CLAIMED assurance -> ACCEPT gated to REPLAN.

        Intent (todo §16 h): the acceptance check is unrestricted, so the agent's
        CLAIMED pass makes A=1.0 -> candidate ACCEPT. But the decision-critical
        risk check is backed only by a CLAIMED record, outside its
        accepted_provenance, so assurance is CLAIMED and the candidate ACCEPT is
        downgraded to the contract's on_claimed action (REPLAN). The final
        decision thus did NOT rest on verified evidence.
        """
        contract = _contract(
            acceptance=[AcceptanceCheck(id="tests-pass", description="tests pass")],
            risks=[
                RiskCheck(
                    id="no-critical-vulns",
                    description="no critical security findings",
                    severity=0.5,
                    decision_critical=True,
                    accepted_provenance=[
                        EvidenceProvenance.VERIFIED,
                        EvidenceProvenance.ATTESTED,
                    ],
                    on_missing=EvidencePolicyAction.ROLLBACK,
                    on_claimed=EvidencePolicyAction.REPLAN,
                )
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[
                _ev(
                    "tests-pass",
                    passed=True,
                    provenance=EvidenceProvenance.CLAIMED,
                    source="agent.self-report",
                )
            ],
            risks=[
                _ev(
                    "no-critical-vulns",
                    passed=True,
                    provenance=EvidenceProvenance.CLAIMED,
                    source="agent.self-report",
                )
            ],
            rollback_available=True,
        )
        result = _evaluate(contract, evidence)
        assert result.candidate_decision == "ACCEPT"
        assert result.assurance is DecisionAssurance.CLAIMED
        # The ACCEPT was blocked and downgraded to the on_claimed action.
        assert result.final_decision == "REPLAN"
        assert result.final_decision != result.candidate_decision



# ---------------------------------------------------------------------------
# (i) Verified acceptance + EVALUATED (subjective) UX -> MIXED
# ---------------------------------------------------------------------------


class TestVerifiedPlusEvaluatedIsMixed:
    """Verified objective evidence plus EVALUATED subjective evidence -> MIXED."""

    def test_mixed_assurance(self) -> None:
        """VERIFIED tests + EVALUATED UX -> MIXED assurance, ACCEPT still allowed.

        Intent (todo §16 i): the objective check is independently VERIFIED; the
        subjective UX check is only EVALUATED (derived, not independently
        verified). The worst contributor is EVALUATED, so assurance is MIXED,
        which does not block the candidate ACCEPT.
        """
        contract = _contract(
            acceptance=[
                _verified_check("tests-pass", "tests pass"),
                AcceptanceCheck(
                    id="ux-quality",
                    description="subjective UX quality",
                    accepted_provenance=[EvidenceProvenance.EVALUATED],
                ),
            ]
        )
        evidence = ExecutionEvidence(
            acceptance=[
                _ev(
                    "tests-pass",
                    passed=True,
                    provenance=EvidenceProvenance.VERIFIED,
                    collector="bound.pytest",
                ),
                _ev(
                    "ux-quality",
                    passed=True,
                    provenance=EvidenceProvenance.EVALUATED,
                    source="ux.evaluator",
                ),
            ],
            rollback_available=True,
        )
        result = _evaluate(contract, evidence)
        assert result.candidate_decision == "ACCEPT"
        assert result.assurance is DecisionAssurance.MIXED
        # MIXED does not block ACCEPT.
        assert result.final_decision == "ACCEPT"


# ---------------------------------------------------------------------------
# (j) All critical evidence VERIFIED -> VERIFIED
# ---------------------------------------------------------------------------


class TestAllVerifiedIsVerified:
    """When every restricted check is backed by verified-tier evidence -> VERIFIED."""

    def test_all_critical_verified(self) -> None:
        """VERIFIED acceptance + VERIFIED decision-critical risk -> VERIFIED.

        Intent (todo §16 j): both the restricted acceptance check and the
        decision-critical risk check are backed by VERIFIED evidence, so the
        assurance is VERIFIED and the candidate ACCEPT is allowed through.
        """
        contract = _contract(
            acceptance=[_verified_check("tests-pass", "tests pass")],
            risks=[
                RiskCheck(
                    id="no-secrets-leaked",
                    description="no secrets in the diff",
                    severity=0.5,
                    decision_critical=True,
                    accepted_provenance=[
                        EvidenceProvenance.VERIFIED,
                        EvidenceProvenance.ATTESTED,
                    ],
                )
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[
                _ev(
                    "tests-pass",
                    passed=True,
                    provenance=EvidenceProvenance.VERIFIED,
                    collector="bound.pytest",
                )
            ],
            risks=[
                _ev(
                    "no-secrets-leaked",
                    passed=True,
                    provenance=EvidenceProvenance.VERIFIED,
                    collector="bound.git",
                )
            ],
            rollback_available=True,
        )
        result = _evaluate(contract, evidence)
        assert result.candidate_decision == "ACCEPT"
        assert result.assurance is DecisionAssurance.VERIFIED
        assert result.final_decision == "ACCEPT"



# ---------------------------------------------------------------------------
# (k) An old schema-1.0 trace can still be read
# ---------------------------------------------------------------------------


class TestSchemaOneTraceIsReadable:
    """Legacy schema-1.0 lineage traces and telemetry still load unchanged."""

    def test_shipped_schema1_events_parse(self) -> None:
        """The shipped schema-1.0 events.jsonl parses line-by-line."""
        events_path = REPO_ROOT / "examples" / "lineage_demo_events.jsonl"
        lines = [ln for ln in events_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 8
        parsed = [parse_lineage_event(ln) for ln in lines]
        # The run_started event from schema 1.0 carries no config snapshot.
        run_started = next(e for e in parsed if e.event == "run_started")
        assert run_started.config is None
        assert run_started.sequence is None  # schema 1.0 has no ordering fields

    def test_legacy_bare_number_telemetry_migrates_to_missing(self) -> None:
        """Old bare-number telemetry loads as MISSING, never upgraded to VERIFIED."""
        legacy = {
            "acceptance": [],
            "retry_count": 0,
            "tool_call_count": 12,
            "token_usage": 800,
            "runtime_seconds": 1.5,
        }
        evidence = ExecutionEvidence.model_validate(legacy)
        # Each legacy bare number is wrapped as a metric with MISSING provenance:
        # a legacy trace cannot retroactively prove independent observation.
        assert evidence.tool_call_count.value == 12
        assert evidence.tool_call_count.provenance is EvidenceProvenance.MISSING
        assert evidence.token_usage.provenance is EvidenceProvenance.MISSING
        # A bare 0 in the legacy trace is preserved as a measured-looking 0 but
        # stamped MISSING, so it is never confused with an OBSERVED zero.
        assert evidence.retry_count.value == 0
        assert evidence.retry_count.provenance is EvidenceProvenance.MISSING


# ---------------------------------------------------------------------------
# (l) A schema-2.0 trace contains the policy/config hash (RunConfigSnapshot)
# ---------------------------------------------------------------------------


class TestTraceCarriesConfigHash:
    """A schema-2.0 run logs a policy/config hash on run_started (item 11)."""

    def test_policy_config_hash_is_stable_sha256(self) -> None:
        """compute_policy_config_hash is a 64-hex sha256, deterministic per config."""
        config = {"threshold": 0.7, "retry_margin": 0.1, "weights": {"acceptance": 1.0}}
        h1 = compute_policy_config_hash(config)
        h2 = compute_policy_config_hash(config)
        assert h1 == h2
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)
        # A different config yields a different hash.
        assert compute_policy_config_hash({"threshold": 0.8}) != h1

    def test_run_started_carries_config_snapshot(self) -> None:
        """A RunStartedEvent round-trips with its config hash + collector versions."""
        snapshot = build_run_config(
            bound_version="0.7.0",
            policy_config={"threshold": 0.7},
            threshold=0.7,
            retry_margin=0.1,
            collector_versions={"bound.pytest": "0.7.0", "bound.git": "0.7.0"},
        )
        assert snapshot.policy_config_hash is not None
        assert len(snapshot.policy_config_hash) == 64
        assert snapshot.collector_versions == {"bound.pytest": "0.7.0", "bound.git": "0.7.0"}
        event = RunStartedEvent(
            event_id="evt_test_1",
            timestamp=datetime(2026, 7, 18, tzinfo=UTC),
            run_id="run_test_1",
            task="ship parser",
            config=snapshot,
        )
        round_tripped = RunStartedEvent.model_validate_json(event.model_dump_json())
        assert round_tripped.config is not None
        assert round_tripped.config.policy_config_hash == snapshot.policy_config_hash
        assert round_tripped.config.collector_versions == snapshot.collector_versions



# ---------------------------------------------------------------------------
# (m) Raw command output is REDACTED by default (not stored; hash + summary only)
# ---------------------------------------------------------------------------


def _secret_runner(*, store_raw: bool = False) -> CommandCollector:
    """A collector that runs a command printing a secret-looking token."""
    return CommandCollector(
        {
            "leak": CommandSpec(
                argv=_sys_argv(
                    "-c", "print('api_key=supersecret42 and password=hunter2')"
                ),
                timeout=30.0,
            )
        },
        store_raw=store_raw,
    )


class TestRawOutputRedactedByDefault:
    """Raw command output is not stored by default; secrets are always masked."""

    def test_default_redactor_masks_secrets(self) -> None:
        """The default redactor masks credential-looking key=value tokens."""
        redacted = default_redactor("api_key=supersecret42 password=hunter2 ok=9")
        assert "supersecret42" not in redacted
        assert "hunter2" not in redacted
        assert "***REDACTED***" in redacted
        # A non-credential key=value is left intact.
        assert "ok=9" in redacted

    def test_raw_not_stored_by_default_and_secret_absent(self) -> None:
        """With store_raw=False (default), stdout_raw is None and no secret leaks."""
        result = _secret_runner().run("leak")
        assert result.stdout_raw is None  # raw output not retained by default
        assert result.stderr_raw is None
        # A sha256 hash of the *redacted* full output is always kept.
        assert result.stdout_hash is not None
        assert result.stdout_hash.startswith("sha256:")
        # The secret never reaches the retained summary.
        assert "supersecret42" not in result.stdout_summary
        assert "hunter2" not in result.stdout_summary
        assert "***REDACTED***" in result.stdout_summary

    def test_raw_retained_is_still_redacted(self) -> None:
        """Even with store_raw=True, the retained output has secrets masked."""
        result = _secret_runner(store_raw=True).run("leak")
        assert result.stdout_raw is not None
        assert "supersecret42" not in result.stdout_raw
        assert "hunter2" not in result.stdout_raw
        assert "***REDACTED***" in result.stdout_raw



# ---------------------------------------------------------------------------
# (n) Same inputs + config -> same decision AND same assurance (determinism)
# ---------------------------------------------------------------------------


class TestDeterminismAndReplay:
    """Identical inputs and config yield identical decisions, assurance, and ids."""

    def _green_case(self) -> tuple[StepContract, ExecutionEvidence]:
        contract = _contract(
            acceptance=[_verified_check("tests-pass", "tests pass")],
            risks=[
                RiskCheck(
                    id="no-secrets",
                    description="no secrets",
                    severity=0.5,
                    decision_critical=True,
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                )
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[
                _ev(
                    "tests-pass",
                    passed=True,
                    provenance=EvidenceProvenance.VERIFIED,
                    collector="bound.pytest",
                )
            ],
            risks=[
                _ev(
                    "no-secrets",
                    passed=True,
                    provenance=EvidenceProvenance.VERIFIED,
                    collector="bound.git",
                )
            ],
            rollback_available=True,
        )
        return contract, evidence

    def test_same_inputs_same_decision_and_assurance(self) -> None:
        """Two fresh evaluations of the same inputs agree on decision + assurance."""
        contract, evidence = self._green_case()
        r1 = _evaluate(contract, evidence)
        r2 = _evaluate(contract, evidence)
        assert r1.score == r2.score
        assert r1.candidate_decision == r2.candidate_decision
        assert r1.final_decision == r2.final_decision
        assert r1.assurance == r2.assurance
        assert list(r1.assurance_reasons) == list(r2.assurance_reasons)

    def test_claimed_case_is_deterministically_gated(self) -> None:
        """A CLAIMED-risk case replays to the same gated decision every time."""
        contract = _contract(
            acceptance=[AcceptanceCheck(id="tests-pass", description="tests pass")],
            risks=[
                RiskCheck(
                    id="no-critical-vulns",
                    description="no vulns",
                    severity=0.5,
                    decision_critical=True,
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                    on_claimed=EvidencePolicyAction.REPLAN,
                )
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[
                _ev(
                    "tests-pass",
                    passed=True,
                    provenance=EvidenceProvenance.CLAIMED,
                    source="agent",
                )
            ],
            risks=[
                _ev(
                    "no-critical-vulns",
                    passed=True,
                    provenance=EvidenceProvenance.CLAIMED,
                    source="agent",
                )
            ],
            rollback_available=True,
        )
        decisions = {
            (
                _evaluate(contract, evidence).candidate_decision,
                _evaluate(contract, evidence).final_decision,
                _evaluate(contract, evidence).assurance,
            )
            for _ in range(3)
        }
        assert decisions == {("ACCEPT", "REPLAN", DecisionAssurance.CLAIMED)}

    def test_lineage_ids_and_config_hash_are_deterministic(self) -> None:
        """Replayable inputs yield the same run id and policy config hash."""
        t0 = datetime(2026, 7, 18, tzinfo=UTC)
        assert generate_run_id(task="ship parser", started_at=t0) == generate_run_id(
            task="ship parser", started_at=t0
        )
        cfg = {"threshold": 0.7, "retry_margin": 0.1}
        assert compute_policy_config_hash(cfg) == compute_policy_config_hash(cfg)



# ---------------------------------------------------------------------------
# Definition of Done (todo §16): the REPLAN -> ACCEPT flow, proven by the trace
# ---------------------------------------------------------------------------

GOAL = "Add input validation to the registration endpoint."
_DOD_T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


def _dod_contract() -> StepContract:
    """The DoD contract: three VERIFIED-only acceptance checks + a critical risk."""
    return _contract(
        cid="PHASE-001",
        acceptance=[
            _verified_check("tests-a", "test a passes"),
            _verified_check("tests-b", "test b passes"),
            _verified_check("tests-c", "test c passes"),
        ],
        risks=[
            RiskCheck(
                id="no-unsafe-changes",
                description="no unexpected files changed",
                severity=0.5,
                decision_critical=True,
                accepted_provenance=[EvidenceProvenance.VERIFIED],
                on_missing=EvidencePolicyAction.ROLLBACK,
            )
        ],
    )


def _dod_check(check_id: str, *, passed: bool, collector: str) -> CheckEvidence:
    """A VERIFIED CheckEvidence carrying full collector audit metadata."""
    return CheckEvidence(
        check_id=check_id,
        passed=passed,
        provenance=EvidenceProvenance.VERIFIED,
        source=collector,
        collector=collector,
        collector_version="0.7.0",
        observed_at=_DOD_T0,
        artifact_hash="sha256:" + "a" * 64,
    )


def _dod_evidence(*, a: bool, b: bool, c: bool) -> ExecutionEvidence:
    """Evidence for one DoD attempt: three pytest checks + a git clean check."""
    return ExecutionEvidence(
        acceptance=[
            _dod_check("tests-a", passed=a, collector="bound.pytest"),
            _dod_check("tests-b", passed=b, collector="bound.pytest"),
            _dod_check("tests-c", passed=c, collector="bound.pytest"),
        ],
        risks=[_dod_check("no-unsafe-changes", passed=True, collector="bound.git")],
        rollback_available=True,
    )


def _record_collected(run, step_id: str, evidence: ExecutionEvidence) -> None:
    """Append an evidence.collected event for every CheckEvidence in ``evidence``."""
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



class TestDefinitionOfDoneReplanToAccept:
    """The canonical v0.8 DoD: 1/3 -> REPLAN, then 3/3 -> ACCEPT, proven by trace."""

    def test_replan_then_accept_proven_by_trace(self, tmp_path: Path) -> None:
        """Attempt 1 (1/3) -> REPLAN; attempt 2 (3/3) -> ACCEPT; trace proves it.

        Mirrors todo §16 "Nieuwe Definition of Done" and asserts the trace proves
        per-number: who/what delivered each piece of evidence, how it was
        collected (provenance), which collector version was used, which policy
        config governed the run, and that the final ACCEPT did NOT depend on
        CLAIMED evidence.
        """
        store = LineageStore(base_dir=tmp_path / "runs")
        config = build_run_config(
            bound_version="0.7.0",
            policy_config={"threshold": 0.7, "retry_margin": 0.1},
            threshold=0.7,
            retry_margin=0.1,
            collector_versions={"bound.pytest": "0.7.0", "bound.git": "0.7.0"},
        )
        contract = _dod_contract()
        wf = BoundWorkflow()

        with start_run(GOAL, store=store, config=config) as run:
            run_id = run.run_id

            # Attempt 1 -> 1/3 VERIFIED -> REPLAN.
            step1 = run.start_step(
                contract_id="PHASE-001", attempt=1, description="implement"
            )
            evidence1 = _dod_evidence(a=True, b=False, c=False)
            _record_collected(run, step1.step_id, evidence1)
            result1 = wf.evaluate_step(
                contract=contract, evidence=evidence1, criteria=_CRITERIA
            )
            eval1 = run.record_evaluation(
                step_id=step1.step_id,
                attempt=1,
                scores=result1.scores,
                score=result1.score,
                threshold=0.7,
                decision=result1.final_decision,
            )
            run.record_decision_gated(
                step_id=step1.step_id,
                evaluation_id=eval1.evaluation_id,
                candidate_decision=result1.candidate_decision,
                final_decision=result1.final_decision,
                assurance=result1.assurance,
                assurance_reasons=list(result1.assurance_reasons),
            )
            run.record_outcome(
                step_id=step1.step_id,
                evaluation_id=eval1.evaluation_id,
                decision=result1.final_decision,
                note="switched strategy to validator + parametrized tests",
            )
            assert result1.candidate_decision == "REPLAN"
            assert result1.assurance is DecisionAssurance.VERIFIED
            assert result1.final_decision == "REPLAN"

            # Attempt 2 (replan -> -R1 contract id) -> 3/3 VERIFIED -> ACCEPT.
            step2 = run.start_step(
                contract_id="PHASE-001-R1", attempt=2, description="implement (replan)"
            )
            evidence2 = _dod_evidence(a=True, b=True, c=True)
            _record_collected(run, step2.step_id, evidence2)
            result2 = wf.evaluate_step(
                contract=contract, evidence=evidence2, criteria=_CRITERIA
            )
            eval2 = run.record_evaluation(
                step_id=step2.step_id,
                attempt=2,
                scores=result2.scores,
                score=result2.score,
                threshold=0.7,
                decision=result2.final_decision,
            )
            run.record_decision_gated(
                step_id=step2.step_id,
                evaluation_id=eval2.evaluation_id,
                candidate_decision=result2.candidate_decision,
                final_decision=result2.final_decision,
                assurance=result2.assurance,
                assurance_reasons=list(result2.assurance_reasons),
            )
            run.record_outcome(
                step_id=step2.step_id,
                evaluation_id=eval2.evaluation_id,
                decision=result2.final_decision,
                note="continued to next step",
            )
            assert result2.candidate_decision == "ACCEPT"
            assert result2.assurance is DecisionAssurance.VERIFIED
            assert result2.final_decision == "ACCEPT"

            run.finish_run(
                status=RunFinishStatus.COMPLETED, reason_code=ReasonCode.RUN_COMPLETED
            )

        self._assert_trace_proves_every_number(store, run_id)


    @staticmethod
    def _assert_trace_proves_every_number(store: LineageStore, run_id: str) -> None:
        log = store.read_run(run_id)
        # Which policy config governed the run (item 11).
        assert log.run.config is not None
        assert log.run.config.policy_config_hash is not None
        assert len(log.run.config.policy_config_hash) == 64
        assert log.run.config.collector_versions == {
            "bound.pytest": "0.7.0",
            "bound.git": "0.7.0",
        }
        # Who/what delivered each number and how it was collected (item 10).
        collected = [e for e in log.events if e.event == "evidence.collected"]
        assert len(collected) == 8  # four checks per attempt
        assert all(e.provenance is EvidenceProvenance.VERIFIED for e in collected)
        assert all(e.collector_version == "0.7.0" for e in collected)
        assert {e.collector for e in collected} == {"bound.pytest", "bound.git"}
        assert all(
            e.artifact_hash and e.artifact_hash.startswith("sha256:") for e in collected
        )
        # The candidate vs final decision and the assurance level (item 12).
        gated = [e for e in log.events if e.event == "decision.gated"]
        assert len(gated) == 2
        assert (gated[0].candidate_decision, gated[0].final_decision) == (
            "REPLAN",
            "REPLAN",
        )
        assert (gated[1].candidate_decision, gated[1].final_decision) == (
            "ACCEPT",
            "ACCEPT",
        )
        # Whether the final decision depended on CLAIMED evidence: it did not.
        assert gated[1].assurance is DecisionAssurance.VERIFIED
        assert all(e.provenance is not EvidenceProvenance.CLAIMED for e in collected)



# ---------------------------------------------------------------------------
# Definition of Done: the real examples/verified_evidence_demo.py runs end-to-end
# ---------------------------------------------------------------------------


class TestVerifiedEvidenceDemoScript:
    """The canonical demo script runs the real REPLAN -> ACCEPT flow with collectors."""

    def test_demo_runs_replan_to_accept_with_live_collectors(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The demo executes live pytest + git collectors and prints the proof.

        Intent: validate the shipped ``examples/verified_evidence_demo.py``
        end-to-end — the agent claims "all tests pass", BOUND's independent
        PytestCollector shows 1/3 (REPLAN), then 3/3 (ACCEPT), and the trace
        proves every number. No decision is hardcoded; pytest + git actually run.
        """
        examples_dir = Path(__file__).resolve().parent.parent / "examples"
        sys.path.insert(0, str(examples_dir))
        try:
            module = importlib.import_module("verified_evidence_demo")
        finally:
            sys.path.remove(str(examples_dir))

        rc = module.main()
        out = capsys.readouterr().out

        assert rc == 0
        # The mandated trajectory, computed by BOUND from real collected evidence.
        assert "candidate decision -> REPLAN" in out
        assert "candidate decision -> ACCEPT" in out
        assert out.index("REPLAN") < out.index("ACCEPT")
        # Evidence is independently VERIFIED (not the agent's CLAIMED pass).
        assert "pytest collector   -> 1/3 passed        VERIFIED" in out
        assert "pytest collector   -> 3/3 passed        VERIFIED" in out
        # The trace proves the config hash and that CLAIMED evidence was not used.
        assert "policy config hash:" in out
        assert "depended_on_claimed=False" in out
        assert "did NOT depend on CLAIMED evidence: True" in out


