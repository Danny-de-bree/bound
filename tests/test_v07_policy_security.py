"""BOUND v0.7.0 — Policy-configuration security tests (todo Phase 10).

These tests pin the *policy-security* invariants added on top of the v0.7
verified-evidence model: an approved ``bound-policy.yaml`` is the single source
of decision authority, blockers can never be bought off by positive weighted
signals, the schema rejects drift (unknown fields, duplicate IDs, unviable
blockers), the canonical policy hash is formatting-independent and detects
mid-run weakening, and every material weakening (budget increase, scope
expansion, provenance narrowing, weight reduction, blocker removal) changes the
hash and therefore demands renewed approval.

The prior ``tests/test_v07_verified_evidence.py`` covers the evidence /
assurance / lineage / privacy honesty invariants; this file covers the NEW
policy-configuration security surface and complements it with focused tests for
weighted-signal scoring, budget enforcement and the ``EvidenceStatus.STALE``
blocker path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWeights,
    BoundWorkflow,
    CheckEvidence,
    EvidencePolicyAction,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
    LineageStore,
    RiskCheck,
    StepContract,
    start_run,
)
from bound.cli import _policy_warnings
from bound.contract_evaluator import PolicyGateOutcome
from bound.evidence import EvidenceMetric
from bound.lineage import build_run_config
from bound.policy_canon import (
    canonicalize_policy,
    compute_policy_hash,
    policy_changed_since,
)
from bound.policy_schema import (
    DEFAULT_WEIGHTS,
    ApprovalsPolicy,
    BoundPolicyConfig,
    BudgetDimension,
    ChangeScope,
    CollectorConfig,
    HardGate,
    PolicyIdentity,
    WeightedSignal,
    parse_policy_yaml,
)

#: Provenance that counts as independently verified for a blocker gate.
_VERIFIED: list[EvidenceProvenance] = [
    EvidenceProvenance.OBSERVED,
    EvidenceProvenance.VERIFIED,
    EvidenceProvenance.ATTESTED,
]

#: Threshold 0.7 / retry margin 0.1, default unit weights.
_CRITERIA = BoundCriteria(weights=BoundWeights(), threshold=0.7, retry_margin=0.1)


def _gate(
    gid: str,
    *,
    on_failure: EvidencePolicyAction = EvidencePolicyAction.RETRY,
    on_missing: EvidencePolicyAction = EvidencePolicyAction.RETRY,
    on_claimed: EvidencePolicyAction = EvidencePolicyAction.RETRY,
    accepted_provenance: list[EvidenceProvenance] | None = _VERIFIED,
    collector: str | None = "bound.command",
    required: bool = True,
) -> HardGate:
    return HardGate(
        id=gid,
        description=gid,
        on_failure=on_failure,
        on_missing=on_missing,
        on_claimed=on_claimed,
        accepted_provenance=accepted_provenance,
        collector=collector,
        required=required,
    )


def _signal(
    sid: str,
    *,
    importance: str = "high",
    weight: float | None = None,
    accepted_provenance: list[EvidenceProvenance] | None = _VERIFIED,
    collector: str | None = "bound.command",
) -> WeightedSignal:
    return WeightedSignal(
        id=sid,
        description=sid,
        importance=importance,  # type: ignore[arg-type]
        weight=weight,
        accepted_provenance=accepted_provenance,
        collector=collector,
    )


def _policy(
    *,
    pid: str = "security-test",
    version: str = "1.0",
    acceptance: list[HardGate] | None = None,
    quality: list[WeightedSignal] | None = None,
    risk: list[HardGate] | None = None,
    budgets: dict[str, BudgetDimension] | None = None,
    change_scope: ChangeScope | None = None,
    approvals: ApprovalsPolicy | None = None,
    collectors: dict[str, CollectorConfig] | None = None,
) -> BoundPolicyConfig:
    return BoundPolicyConfig(
        policy=PolicyIdentity(id=pid, version=version),
        collectors=collectors or {"bound.command": CollectorConfig(type="pytest")},
        acceptance_checks=acceptance or [],
        quality_checks=quality or [],
        risk_checks=risk or [],
        budgets=budgets or {},
        change_scope=change_scope or ChangeScope(),
        approvals=approvals or ApprovalsPolicy(),
    )


def _ev(
    check_id: str,
    *,
    passed: bool | None,
    provenance: EvidenceProvenance = EvidenceProvenance.VERIFIED,
    status: EvidenceStatus | None = None,
    collector: str = "bound.command",
) -> CheckEvidence:
    return CheckEvidence(
        check_id=check_id,
        passed=passed,
        provenance=provenance,
        status=status,
        collector=collector,
    )


def _contract(
    *,
    acceptance: list[AcceptanceCheck] | None = None,
    risks: list[RiskCheck] | None = None,
) -> StepContract:
    return StepContract(
        id="PHASE-001",
        description="security-test contract",
        goal="secure the policy",
        acceptance_checks=acceptance or [],
        risk_checks=risks or [],
    )


def _acceptance_check(
    cid: str,
    *,
    accepted_provenance: list[EvidenceProvenance] = _VERIFIED,
    on_missing: EvidencePolicyAction = EvidencePolicyAction.REPLAN,
    on_claimed: EvidencePolicyAction = EvidencePolicyAction.RETRY,
    required: bool = True,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        id=cid,
        description=cid,
        accepted_provenance=accepted_provenance,
        on_missing=on_missing,
        on_claimed=on_claimed,
        required=required,
    )


def _minimal_yaml() -> str:
    return '''
schema_version: "1.0"
policy:
  id: security-test
  version: "1.0"
'''


class TestBlockerUncompensable:
    """A failed blocker forces a non-ACCEPT decision regardless of score."""

    def test_failed_blocker_downgrades_a_score_implied_accept(self) -> None:
        """A high acceptance score (driven by passing weighted signals) is
        downgraded when a single blocker fails — the blocker cannot be
        compensated by positive weighted signals (todo 2.2 / 10)."""
        policy = _policy(
            acceptance=[_gate("tests-pass", on_failure=EvidencePolicyAction.RETRY)],
            quality=[
                _signal("sig-a"),
                _signal("sig-b"),
                _signal("sig-c"),
                _signal("sig-d"),
            ],
        )
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[
                _ev("tests-pass", passed=False),
                _ev("sig-a", passed=True),
                _ev("sig-b", passed=True),
                _ev("sig-c", passed=True),
                _ev("sig-d", passed=True),
            ],
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.candidate_decision == "ACCEPT"
        assert result.final_decision == "RETRY"
        assert result.final_decision != "ACCEPT"
        assert result.active_policy_id == "security-test"
        assert result.active_policy_hash == compute_policy_hash(policy)
        assert result.active_policy_hash.startswith("sha256:")

    def test_missing_blocker_evidence_cannot_be_compensated(self) -> None:
        """A blocker with NO evidence fails regardless of passing signals."""
        policy = _policy(
            acceptance=[_gate("tests-pass", on_missing=EvidencePolicyAction.REPLAN)],
            quality=[_signal("lint", importance="high")],
        )
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[_ev("lint", passed=True)],
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.final_decision == "REPLAN"
        assert result.final_decision != "ACCEPT"

    def test_stale_blocker_evidence_cannot_be_compensated(self) -> None:
        """INVALID/STALE blocker evidence fails the gate (todo 1.2 / 10)."""
        policy = _policy(
            acceptance=[_gate("junit", on_missing=EvidencePolicyAction.REPLAN)],
            quality=[_signal("lint", importance="high")],
        )
        contract = _contract(acceptance=[_acceptance_check("junit")])
        evidence = ExecutionEvidence(
            acceptance=[
                _ev("junit", passed=None, status=EvidenceStatus.STALE),
                _ev("lint", passed=True),
            ],
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.final_decision == "REPLAN"
        assert result.final_decision != "ACCEPT"

    def test_passing_blocker_does_not_force_a_downgrade(self) -> None:
        """When the blocker holds, the gate imposes no forced action."""
        policy = _policy(
            acceptance=[_gate("tests-pass", on_failure=EvidencePolicyAction.RETRY)],
            quality=[_signal("lint", importance="high")],
        )
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[_ev("tests-pass", passed=True), _ev("lint", passed=True)],
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.candidate_decision == "ACCEPT"
        assert result.final_decision == "ACCEPT"
        assert result.assurance.value == "verified"


class TestSchemaDriftRejected:
    def test_unknown_top_level_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_policy_yaml(_minimal_yaml() + "unknown_section: {}\n")

    def test_unknown_nested_field_in_gate_rejected(self) -> None:
        bad = _minimal_yaml() + """
acceptance_checks:
  - id: tests-pass
    description: "tests"
    bogus_field: true
"""
        with pytest.raises(ValidationError):
            parse_policy_yaml(bad)

    def test_unknown_field_in_budget_dimension_rejected(self) -> None:
        bad = _minimal_yaml() + """
budgets:
  tool_calls:
    hard_limit: 20
    on_hard: replan
    stray_key: 1
"""
        with pytest.raises(ValidationError):
            parse_policy_yaml(bad)

    def test_unknown_field_in_collector_rejected(self) -> None:
        bad = _minimal_yaml() + """
collectors:
  pytest:
    type: pytest
    not_a_field: true
"""
        with pytest.raises(ValidationError):
            parse_policy_yaml(bad)


class TestDuplicateIdsRejected:
    def test_duplicate_check_id_across_lists_rejected(self) -> None:
        bad = _minimal_yaml() + """
acceptance_checks:
  - id: dup
    description: "first"
quality_checks:
  - id: dup
    description: "second"
"""
        with pytest.raises(ValidationError) as exc:
            parse_policy_yaml(bad)
        assert "duplicate check id" in str(exc.value)

    def test_duplicate_check_id_within_same_list_rejected(self) -> None:
        bad = _minimal_yaml() + """
acceptance_checks:
  - id: dup
    description: "first"
  - id: dup
    description: "second"
"""
        with pytest.raises(ValidationError) as exc:
            parse_policy_yaml(bad)
        assert "duplicate check id" in str(exc.value)

    def test_duplicate_risk_check_id_rejected(self) -> None:
        bad = _minimal_yaml() + """
risk_checks:
  - id: dup
    description: "first"
acceptance_checks:
  - id: dup
    description: "second"
"""
        with pytest.raises(ValidationError) as exc:
            parse_policy_yaml(bad)
        assert "duplicate check id" in str(exc.value)

    def test_duplicate_collector_id_rejected(self) -> None:
        """Duplicate YAML mapping keys for a collector raise at load time."""
        bad = _minimal_yaml() + """
collectors:
  pytest:
    type: pytest
  pytest:
    type: command
    command: ["echo", "hi"]
"""
        with pytest.raises(ValueError):
            parse_policy_yaml(bad)


class TestBlockerWithoutViableCollector:
    """A blocker that binds no collector (or an unknown one) cannot be
    independently verified; BOUND surfaces this as a validation warning so the
    approver never silently approves an unmeasurable gate (todo 4.1 / 10)."""

    def test_blocker_with_no_collector_warns(self) -> None:
        policy = _policy(
            acceptance=[_gate("tests-pass", collector=None)],
            collectors={},
        )
        warnings = _policy_warnings(policy)
        assert any("tests-pass" in w and "binds no collector" in w for w in warnings)

    def test_blocker_referencing_unknown_collector_warns(self) -> None:
        policy = _policy(
            acceptance=[_gate("tests-pass", collector="no-such-collector")],
            collectors={"pytest": CollectorConfig(type="pytest")},
        )
        warnings = _policy_warnings(policy)
        assert any("unknown collector 'no-such-collector'" in w for w in warnings)

    def test_cli_validate_surfaces_blocker_without_collector_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from bound.cli import main

        bad = tmp_path / "bound-policy.yaml"
        bad.write_text(
            _minimal_yaml().replace("security-test", "no-collector-policy")
            + """
acceptance_checks:
  - id: orphan-blocker
    description: "a blocker with no collector"
""",
            encoding="utf-8",
        )
        rc = main(["policy", "validate", str(bad)])
        out, _ = capsys.readouterr()
        assert rc == 0
        assert "no-collector-policy@1.0: valid" in out
        assert "orphan-blocker" in out
        assert "binds no collector" in out


class TestPolicyHashStability:
    def test_hash_stable_across_key_order(self) -> None:
        a = parse_policy_yaml(
            """
schema_version: "1.0"
policy: {id: p, version: "1.0"}
acceptance_checks:
  - id: x
    description: "x"
quality_checks:
  - id: y
    description: "y"
"""
        )
        b = parse_policy_yaml(
            """
schema_version: "1.0"
policy: {version: "1.0", id: p}
quality_checks:
  - id: y
    description: "y"
acceptance_checks:
  - id: x
    description: "x"
"""
        )
        assert canonicalize_policy(a) == canonicalize_policy(b)
        assert compute_policy_hash(a) == compute_policy_hash(b)

    def test_hash_stable_across_comments_and_whitespace(self) -> None:
        plain = """
schema_version: "1.0"
policy:
  id: p
  version: "1.0"
budgets:
  tool_calls:
    hard_limit: 20
    on_hard: replan
"""
        commented = """
# A leading comment.
schema_version: "1.0"   # inline comment

policy:
  # identity
  id: p
  version: "1.0"

budgets:
  tool_calls:
    hard_limit: 20   # twenty tool calls
    on_hard: replan
"""
        assert compute_policy_hash(parse_policy_yaml(plain)) == compute_policy_hash(
            parse_policy_yaml(commented)
        )

    def test_hash_changes_with_material_content(self) -> None:
        base = parse_policy_yaml(
            """
schema_version: "1.0"
policy: {id: p, version: "1.0"}
budgets:
  tool_calls:
    hard_limit: 20
    on_hard: replan
"""
        )
        changed = parse_policy_yaml(
            """
schema_version: "1.0"
policy: {id: p, version: "1.0"}
budgets:
  tool_calls:
    hard_limit: 21
    on_hard: replan
"""
        )
        assert compute_policy_hash(base) != compute_policy_hash(changed)


class TestPolicyChangeDetection:
    def test_policy_changed_since_true_on_material_change(self) -> None:
        base = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=20)})
        changed = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=21)})
        assert policy_changed_since(base, changed) is True

    def test_policy_changed_since_false_on_identical(self) -> None:
        base = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=20)})
        assert policy_changed_since(base, base) is False

    def test_policy_changed_since_accepts_hash_strings(self) -> None:
        base = _policy()
        h = compute_policy_hash(base)
        assert policy_changed_since(h, h) is False
        assert policy_changed_since(h, "sha256:deadbeef") is True

    def test_run_config_snapshot_records_policy_hash(self, tmp_path: Path) -> None:
        """A run records the active policy hash on its config snapshot, so a
        later checkpoint can detect a mid-run change (todo 4.2 / 7.2)."""
        policy = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=20)})
        config = build_run_config(policy=policy, threshold=0.7, retry_margin=0.1)
        store = LineageStore(base_dir=tmp_path / "runs")
        with start_run("task", store=store, config=config) as run:
            run_id = run.run_id
        log = store.read_run(run_id)
        assert log.run.config is not None
        assert log.run.config.policy_hash == compute_policy_hash(policy)
        assert log.run.config.policy_id == "security-test"
        changed = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=21)})
        assert policy_changed_since(log.run.config.policy_hash, changed) is True


class TestWeakeningRequiresApproval:
    """Every material weakening changes the canonical policy hash, so
    ``policy_changed_since`` flags it and the policy must be re-approved before
    it may control decisions again (todo 3.3 / 10)."""

    def test_blocker_removed_changes_hash(self) -> None:
        base = _policy(acceptance=[_gate("tests-pass")])
        weakened = _policy(acceptance=[])
        assert policy_changed_since(base, weakened) is True

    def test_weight_reduced_changes_hash(self) -> None:
        base = _policy(quality=[_signal("lint", importance="high")])
        weakened = _policy(quality=[_signal("lint", importance="low")])
        assert policy_changed_since(base, weakened) is True
        assert base.quality_checks[0].effective_weight == DEFAULT_WEIGHTS["high"]
        assert weakened.quality_checks[0].effective_weight == DEFAULT_WEIGHTS["low"]

    def test_budget_increased_changes_hash(self) -> None:
        base = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=20)})
        weakened = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=999)})
        assert policy_changed_since(base, weakened) is True

    def test_expanded_scope_changes_hash(self) -> None:
        base = _policy(
            change_scope=ChangeScope(allowed_paths=["src/**", "tests/**"])
        )
        weakened = _policy(
            change_scope=ChangeScope(allowed_paths=["src/**", "tests/**", "**"])
        )
        assert policy_changed_since(base, weakened) is True

    def test_weakened_provenance_changes_hash(self) -> None:
        """Narrowing accepted_provenance (e.g. dropping VERIFIED) weakens the
        requirement and changes the hash."""
        strong = _policy(
            acceptance=[
                _gate(
                    "tests-pass",
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                )
            ]
        )
        weak = _policy(
            acceptance=[
                _gate(
                    "tests-pass",
                    accepted_provenance=[EvidenceProvenance.CLAIMED],
                )
            ]
        )
        assert policy_changed_since(strong, weak) is True

    def test_collector_replaced_changes_hash(self) -> None:
        base = _policy(
            acceptance=[_gate("tests-pass", collector="pytest")],
            collectors={"pytest": CollectorConfig(type="pytest")},
        )
        weakened = _policy(
            acceptance=[_gate("tests-pass", collector="shell")],
            collectors={"shell": CollectorConfig(type="command", command=["true"])},
        )
        assert policy_changed_since(base, weakened) is True


class TestActivePolicyCannotBeWeakened:
    """Only an APPROVED -> ACTIVATED policy controls decisions, and the active
    policy hash is immutable for the run. An agent cannot replace or weaken it
    mid-run: any change is detected by ``policy_changed_since`` and demands a
    fresh approval lifecycle (todo 3.3 / 10)."""

    def test_policy_lifecycle_events_recorded(self, tmp_path: Path) -> None:
        store = LineageStore(base_dir=tmp_path / "runs")
        policy = _policy(budgets={"tool_calls": BudgetDimension(hard_limit=20)})
        phash = compute_policy_hash(policy)
        with start_run("task", store=store) as run:
            run.record_policy_proposed(
                policy_id="security-test", policy_version="1.0", policy_hash=phash
            )
            run.record_policy_validated(
                policy_id="security-test", policy_version="1.0", policy_hash=phash
            )
            run.record_policy_approved(
                policy_id="security-test",
                policy_version="1.0",
                policy_hash=phash,
                approver="alice",
            )
            activated = run.record_policy_activated(
                policy_id="security-test", policy_version="1.0", policy_hash=phash
            )
            run_id = run.run_id
        log = store.read_run(run_id)
        events = [e.event for e in log.events]
        assert events[:4] == [
            "run_started",
            "policy.proposed",
            "policy.validated",
            "policy.approved",
        ]
        assert "policy.activated" in events
        assert activated.policy_hash == phash

    def test_decision_records_active_policy_hash(self) -> None:
        """Every decision records the active policy hash (release blocker)."""
        policy = _policy(acceptance=[_gate("tests-pass")])
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[_ev("tests-pass", passed=True)], rollback_available=True
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.final_decision == "ACCEPT"
        assert result.active_policy_hash == compute_policy_hash(policy)

    def test_mid_run_weakening_detected(self) -> None:
        """An agent attempting to weaken the active policy mid-run produces a
        different hash, which ``policy_changed_since`` detects — so the
        weakened policy cannot silently take over."""
        active = _policy(
            acceptance=[_gate("tests-pass")],
            budgets={"tool_calls": BudgetDimension(hard_limit=20)},
        )
        active_hash = compute_policy_hash(active)
        weakened = _policy(
            acceptance=[],
            budgets={"tool_calls": BudgetDimension(hard_limit=999)},
        )
        assert policy_changed_since(active_hash, weakened) is True
        assert compute_policy_hash(weakened) != active_hash

    def test_policy_gate_forced_action_picks_most_severe(self) -> None:
        """The gate never weakens a conservative candidate: the most severe of
        the blocker/budget actions wins (todo 6.2)."""
        gate = PolicyGateOutcome(
            blocker_failed=True,
            blocker_action=EvidencePolicyAction.RETRY,
            budget_breached=True,
            budget_action=EvidencePolicyAction.ROLLBACK,
        )
        assert gate.forced_action is EvidencePolicyAction.ROLLBACK
        gate2 = PolicyGateOutcome(
            blocker_failed=True, blocker_action=EvidencePolicyAction.REPLAN
        )
        assert gate2.forced_action is EvidencePolicyAction.REPLAN
        assert PolicyGateOutcome().forced_action is None


class TestBudgetEnforcement:
    def test_hard_budget_breach_forces_downgrade(self) -> None:
        """A breached hard budget forces a non-ACCEPT action even when the
        acceptance score implies ACCEPT (todo 2.2 / 10)."""
        policy = _policy(
            acceptance=[_gate("tests-pass")],
            quality=[_signal("lint", importance="high")],
            budgets={
                "tool_calls": BudgetDimension(
                    hard_limit=20, on_hard=EvidencePolicyAction.REPLAN
                )
            },
        )
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[_ev("tests-pass", passed=True), _ev("lint", passed=True)],
            tool_call_count=EvidenceMetric(
                value=25, provenance=EvidenceProvenance.OBSERVED, source="harness"
            ),
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.candidate_decision == "ACCEPT"
        assert result.final_decision == "REPLAN"

    def test_missing_budget_telemetry_cannot_silently_satisfy(self) -> None:
        """A declared budget with unmeasured telemetry is treated as
        not-within-budget (missing is never a silent zero) (todo 2.2 / 10)."""
        policy = _policy(
            acceptance=[_gate("tests-pass")],
            quality=[_signal("lint", importance="high")],
            budgets={
                "tool_calls": BudgetDimension(
                    hard_limit=20, on_hard=EvidencePolicyAction.REPLAN
                )
            },
        )
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[_ev("tests-pass", passed=True), _ev("lint", passed=True)],
            tool_call_count=None,
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.candidate_decision == "ACCEPT"
        assert result.final_decision == "REPLAN"

    def test_within_budget_does_not_force(self) -> None:
        """A measured, within-budget tool-call count does not force a downgrade."""
        policy = _policy(
            acceptance=[_gate("tests-pass")],
            quality=[_signal("lint", importance="high")],
            budgets={
                "tool_calls": BudgetDimension(
                    hard_limit=20, on_hard=EvidencePolicyAction.REPLAN
                )
            },
        )
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[_ev("tests-pass", passed=True), _ev("lint", passed=True)],
            tool_call_count=EvidenceMetric(
                value=18, provenance=EvidenceProvenance.OBSERVED, source="harness"
            ),
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.final_decision == "ACCEPT"


class TestWeightedSignalScoring:
    def test_effective_weights_recorded_on_result(self) -> None:
        """The resolved effective weights are stored on the result so the trace
        can reconstruct the weighted-signal contribution (todo 2.2)."""
        policy = _policy(
            acceptance=[_gate("tests-pass")],
            quality=[
                _signal("lint", importance="medium"),
                _signal("coverage", importance="low"),
            ],
        )
        contract = _contract(acceptance=[_acceptance_check("tests-pass")])
        evidence = ExecutionEvidence(
            acceptance=[
                _ev("tests-pass", passed=True),
                _ev("lint", passed=True),
                _ev("coverage", passed=False),
            ],
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=_CRITERIA, policy=policy
        )
        assert result.effective_weights == {
            "tests-pass": 1.0,
            "lint": DEFAULT_WEIGHTS["medium"],
            "coverage": DEFAULT_WEIGHTS["low"],
        }

    def test_explicit_weight_override_resolves(self) -> None:
        """An explicit ``weight`` overrides the importance-derived default and
        is part of the canonical form/hash."""
        sig = _signal("lint", importance="medium", weight=0.9)
        assert sig.effective_weight == 0.9
        base = _policy(quality=[_signal("lint", importance="medium")])
        override = _policy(quality=[_signal("lint", importance="medium", weight=0.9)])
        assert compute_policy_hash(base) != compute_policy_hash(override)


class TestGoldenDemo:
    """The Phase 12 golden demo runs end-to-end and ends in ACCEPT (todo 12)."""

    def test_golden_demo_ends_in_accept(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import importlib
        import sys

        examples_dir = Path(__file__).resolve().parent.parent / "examples"
        sys.path.insert(0, str(examples_dir))
        try:
            module = importlib.import_module("golden_demo")
        finally:
            sys.path.remove(str(examples_dir))

        rc = module.main()
        out = capsys.readouterr().out
        assert rc == 0
        # The two-attempt REPLAN -> ACCEPT path is proven by real collectors.
        assert "attempt 1" in out
        assert "candidate=REPLAN  final=REPLAN  assurance=verified" in out
        assert "candidate=ACCEPT  final=ACCEPT  assurance=verified" in out
        # The policy lifecycle and hash are recorded.
        assert "golden-demo@1.0" in out
        assert "canonical policy hash: sha256:" in out
        assert "policy lifecycle: proposed -> validated -> approved -> activated" in out
        # Independently collected, non-CLAIMED evidence.
        assert "VERIFIED" in out
        assert "the final decision did NOT depend on CLAIMED evidence: True" in out
        # Real artifacts are written.
        assert "wrote" in out and "INTEGRATION_REPORT.md" in out
        assert "reproduction command:" in out
