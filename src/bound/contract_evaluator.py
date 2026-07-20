from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from bound.contracts import (
    AcceptanceCheck,
    EvidencePolicyAction,
    RiskCheck,
    StepContract,
)
from bound.evidence import (
    CheckEvidence,
    EvidenceMetric,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
)
from bound.models import DecisionAssurance, EvaluationScores, ScoreEvidence
from bound.policy_canon import compute_policy_hash
from bound.policy_schema import (
    BUDGET_DIMENSIONS,
    BoundPolicyConfig,
    HardGate,
)

if TYPE_CHECKING:
    from bound.lineage_api import RunContext

# ---------------------------------------------------------------------------
# v0.3 reference-heuristic constants (NOT scientifically calibrated)
#
# These are deliberate, visible policy knobs documented so the risk rule can be
# audited and challenged. They are NOT tuned weights.
# ---------------------------------------------------------------------------

#: Any unexpected artifact is a full surprise-risk indicator. An "unexpected"
#: artifact is, by definition, not anticipated, so a single occurrence is
#: treated as a maximum surprise signal rather than a graded one — mirroring the
#: :data:`bound.workflow._UNEXPECTED_CHANGE_INDICATOR` convention.
_UNEXPECTED_ARTIFACT_INDICATOR = 1.0

#: A confirmed-unavailable rollback is a full recovery-risk indicator. With no
#: clean rollback the step cannot be undone, which is the maximum recovery risk.
#: Matches the :data:`bound.workflow` rollback-indicator convention. Visible and
#: challengeable. v0.3 reference heuristic (NOT scientifically calibrated).
_ROLLBACK_UNAVAILABLE_INDICATOR = 1.0

#: Conservative saturation applied to a *declared* budget dimension whose
#: telemetry was left unmeasured (``token_usage`` / ``runtime_seconds`` is
#: ``None``). When a budget exists BOUND cannot confirm the step stayed within
#: it, so the dimension is scored as if the budget were fully consumed rather
#: than silently as zero cost. v0.3 reference heuristic (NOT scientifically
#: calibrated).
_UNMEASURED_COST_SATURATION = 1.0


def _normalize_capped(value: float, cap: float) -> float:
    """Normalize ``value`` against ``cap`` into ``[0.0, 1.0]``.

    A cap of ``0`` means "any nonzero value is already over budget": the result
    is ``1.0`` when ``value > 0`` and ``0.0`` when ``value == 0``. This keeps the
    normalization fully configuration-driven while avoiding a division by zero,
    and mirrors :func:`bound.workflow._normalize_capped`.

    Args:
        value: The raw observed value (non-negative).
        cap: The configured budget ceiling for this dimension.

    Returns:
        ``min(value / cap, 1.0)`` (or the cap-zero rule described above).
    """
    if cap <= 0:
        return 1.0 if value > 0 else 0.0
    return min(value / cap, 1.0)


def _budget_dimension(
    metric: EvidenceMetric | None,
    limit: float | None,
    *,
    field_name: str,
    budget_label: str,
    cost_source: str,
) -> tuple[str, float, float, float, str, EvidenceProvenance] | None:
    """Compute one cost-dimension contribution, or return ``None`` when not declared.

    When the *budget* declares this dimension (``limit`` is not ``None``), the
    observed ``metric`` is normalized against the cap and the result is returned
    as a ``(source, raw, mx, norm, note, provenance)`` tuple that the caller
    appends to the cost terms list. When the metric is absent (``None``) the
    dimension is saturated to :data:`_UNMEASURED_COST_SATURATION` because budget
    compliance cannot be confirmed.

    Args:
        metric: The observed telemetry metric (or ``None`` when unmeasured).
        limit: The configured budget ceiling for this dimension (or ``None``
            when the dimension is not declared).
        field_name: The evidence-field name used in description text, e.g.
            ``"retry_count"``.
        budget_label: Short human-readable label for the budget, e.g.
            ``"retry"``, ``"tool-call"``, ``"token"``, ``"runtime"``.
        cost_source: The source name used in the :class:`ScoreEvidence`, e.g.
            ``"retry_cost"``.

    Returns:
        A ``(source, raw, mx, norm, note, provenance)`` tuple when the
        dimension is declared, or ``None`` when ``limit`` is ``None``.
    """
    if limit is None:
        return None
    mx = float(limit)
    raw_value = _metric_value(metric)
    if raw_value is not None:
        raw = float(raw_value)
        norm = _normalize_capped(raw, mx)
        note = f"normalized=min({raw}/{mx}, 1.0)={norm:.4f}"
    else:
        raw = 0.0
        norm = _UNMEASURED_COST_SATURATION
        note = (
            f"telemetry unmeasured ({field_name}=None); conservatively "
            f"saturated to {norm:.4f} because compliance with the {budget_label} "
            "budget cannot be confirmed"
        )
    prov = (
        metric.provenance
        if raw_value is not None
        else EvidenceProvenance.MISSING
    )
    return (cost_source, raw, mx, norm, note, prov)


def _metric_value(metric: EvidenceMetric | None) -> int | float | bool | None:
    """Extract the raw numeric value from a telemetry :class:`EvidenceMetric`.

    v0.7 models execution telemetry (``retry_count``, ``tool_call_count``,
    ``token_usage``, ``runtime_seconds``) as :class:`EvidenceMetric | None` so
    a *measured* value is distinguishable from a *missing* one. This helper
    unwraps the metric to its scalar: ``None`` when the metric itself is absent
    or when its ``value`` is ``None`` (an explicitly-missing signal), otherwise
    the observed scalar. Callers (notably :meth:`ContractEvaluator._cost`)
    treat ``None`` as "unmeasured" and apply conservative saturation — never a
    silent zero — preserving the missing-vs-zero honesty rule.

    Args:
        metric: A telemetry metric, or ``None`` when the signal was not measured.

    Returns:
        The metric's raw scalar value, or ``None`` when the signal is missing.
    """
    if metric is None:
        return None
    return metric.value


def _metric_provenance(metric: EvidenceMetric | None) -> EvidenceProvenance:
    """Extract the trust provenance of a telemetry :class:`EvidenceMetric`.

    Companion to :func:`_metric_value`: returns the metric's
    :class:`EvidenceProvenance` so a cost dimension can record *how* its
    telemetry was sourced alongside the scalar value. When the metric itself is
    absent the signal was never measured, so the provenance is
    :attr:`EvidenceProvenance.MISSING` (never silently ``OBSERVED``). When the
    metric exists but its ``value`` is ``None`` the metric already carries an
    explicit provenance (a collector recording an explicitly-missing signal),
    which is returned verbatim.

    Args:
        metric: A telemetry metric, or ``None`` when the signal was not measured.

    Returns:
        The metric's provenance, or :attr:`MISSING <EvidenceProvenance.MISSING>`
        when no metric exists.
    """
    if metric is None:
        return EvidenceProvenance.MISSING
    return metric.provenance


#: Provenance values that count as *independently verified* for assurance.
#: OBSERVED (direct collector measurement), VERIFIED (BOUND re-ran the check),
#: and ATTESTED (trusted third-party attestation) are the only provenances that
#: can support a VERIFIED :class:`DecisionAssurance`. Everything weaker
#: (EVALUATED, CLAIMED, DEFAULTED, MISSING) degrades it.
_VERIFIED_PROVENANCE: frozenset[EvidenceProvenance] = frozenset(
    {
        EvidenceProvenance.OBSERVED,
        EvidenceProvenance.VERIFIED,
        EvidenceProvenance.ATTESTED,
    }
)


def _strongest_provenance(records: list[CheckEvidence]) -> EvidenceProvenance | None:
    """Return the strongest trust provenance among ``records``, or ``None``.

    When several :class:`CheckEvidence` records share a ``check_id`` the
    evaluator deduplicates conservatively (all must pass). For *trust*
    provenance the opposite holds: an independently observed/verified record
    outranks a bare agent self-report, so the strongest provenance present
    wins (``observed`` beats ``claimed`` — the "observed wins" honesty rule).
    Returns ``None`` when there are no records at all.

    Args:
        records: The evidence records for a single check id.

    Returns:
        The strongest provenance among the records, or ``None`` when empty.
    """
    if not records:
        return None
    rank: dict[EvidenceProvenance, int] = {
        EvidenceProvenance.OBSERVED: 4,
        EvidenceProvenance.VERIFIED: 4,
        EvidenceProvenance.ATTESTED: 4,
        EvidenceProvenance.EVALUATED: 3,
        EvidenceProvenance.CLAIMED: 2,
        EvidenceProvenance.DEFAULTED: 1,
        EvidenceProvenance.MISSING: 0,
    }
    return max(records, key=lambda e: rank.get(e.provenance, 0)).provenance


#: Severity ordering of :class:`EvidencePolicyAction` for selecting the most
#: conservative block action when several restricted checks fail at once.
#: ``ROLLBACK`` is the most severe (undo the step), ``ACCEPT`` the least
#: (accept despite weak evidence). When a candidate ACCEPT is blocked the most
#: severe action among the failing checks wins, so a single
#: ``on_missing=rollback`` critical check drags the whole decision to
#: ``ROLLBACK`` rather than letting a softer check dilute it.
_ACTION_SEVERITY: dict[EvidencePolicyAction, int] = {
    EvidencePolicyAction.ACCEPT: 0,
    EvidencePolicyAction.RETRY: 1,
    EvidencePolicyAction.REPLAN: 2,
    EvidencePolicyAction.ROLLBACK: 3,
}


@dataclass
class AssuranceAssessment:
    """The assurance assessment computed from decision-critical evidence.

    :class:`ContractEvaluator.assess_assurance` produces this from a contract
    and its evidence. The :class:`~bound.policy.BoundPolicy` consumes it to
    gate a candidate ACCEPT: when the assurance is :attr:`CLAIMED` or
    :attr:`INSUFFICIENT` the candidate decision is downgraded to
    :attr:`accept_block_action` (mapped to a BOUND decision) and
    :attr:`accept_block_reasons` explains why.

    Attributes:
        assurance: The computed :class:`DecisionAssurance` level.
        reasons: Human-readable reasons for the assurance level (which check
            had which provenance, which critical evidence was missing, ...).
        accept_block_action: When not ``None``, the candidate ACCEPT must be
            downgraded to this :class:`EvidencePolicyAction` (the most severe
            among the failing restricted checks' ``on_missing``/``on_claimed``).
            ``None`` means ACCEPT is permitted (assurance is VERIFIED or MIXED,
            or no restricted checks exist).
        accept_block_reasons: Reasons explaining the block (appended to
            :attr:`reasons` on the result when a block fires). Empty when no
            block fires.
    """

    assurance: DecisionAssurance
    reasons: list[str] = field(default_factory=list)
    accept_block_action: EvidencePolicyAction | None = None
    accept_block_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# v0.7 active-policy gating
#
# When an approved :class:`~bound.policy_schema.BoundPolicyConfig` governs a
# step, three mechanisms layer on top of the contract-only candidate decision:
# hard gates (blockers) that can never be compensated, weighted signals that
# feed the acceptance score, and budgets with soft/hard limits. The structures
# below are the trace-ready outcome of that layering; the computation lives on
# :class:`ContractEvaluator` and the decision override on
# :class:`~bound.policy.BoundPolicy`.
# ---------------------------------------------------------------------------

#: Strength ordering of :class:`DecisionAssurance` for the
#: ``minimum_assurance`` comparison (todo 6.1). Higher is stronger: a gate
#: whose evidence only reaches ``CLAIMED`` does not satisfy a
#: ``minimum_assurance`` of ``VERIFIED``. ``INSUFFICIENT`` (missing/invalid
#: evidence) is the weakest.
_ASSURANCE_RANK: dict[DecisionAssurance, int] = {
    DecisionAssurance.VERIFIED: 4,
    DecisionAssurance.MIXED: 3,
    DecisionAssurance.CLAIMED: 2,
    DecisionAssurance.INSUFFICIENT: 1,
}

#: Maps an assurance *category* (returned by :meth:`ContractEvaluator._classify_check`)
#: to the :class:`DecisionAssurance` it represents, so a gate's evidence can be
#: compared against its ``minimum_assurance`` floor.
_CATEGORY_ASSURANCE: dict[str, DecisionAssurance] = {
    "verified": DecisionAssurance.VERIFIED,
    "evaluated": DecisionAssurance.MIXED,
    "claimed": DecisionAssurance.CLAIMED,
    "insufficient": DecisionAssurance.INSUFFICIENT,
}


@dataclass
class BudgetStatus:
    """The evaluated state of one declared budget dimension (todo 2.2).

    Attributes:
        dimension: The budget dimension name (one of
            :data:`~bound.policy_schema.BUDGET_DIMENSIONS`).
        measured_value: The observed telemetry value, or ``None`` when the
            telemetry was not measured. ``None`` is *missing*, never a zero.
        soft_limit: The configured soft limit, or ``None`` when unset.
        hard_limit: The configured hard limit, or ``None`` when unset.
        state: How the dimension fared: ``"none"`` (within budget / no limit
            declared), ``"soft"`` (soft limit reached/exceeded),
            ``"hard"`` (hard limit reached/exceeded), or ``"missing"``
            (telemetry missing for a dimension with a declared limit — treated
            conservatively as *not within budget*).
        action: The :class:`EvidencePolicyAction` the policy prescribes for the
            reached limit (``on_soft``/``on_hard``), or ``None`` when within
            budget or the dimension is disabled.
        reason: Human-readable explanation of the state.
    """

    dimension: str
    measured_value: float | None
    soft_limit: float | None
    hard_limit: float | None
    state: Literal["none", "soft", "hard", "missing"]
    action: EvidencePolicyAction | None
    reason: str


@dataclass
class PolicyGateOutcome:
    """The active-policy gate outcome layered on the candidate decision.

    :class:`ContractEvaluator.assess_policy_gate` produces this from an active
    :class:`~bound.policy_schema.BoundPolicyConfig` and the collected evidence.
    :class:`~bound.policy.BoundPolicy` consumes it to *force* a decision when a
    blocker failed or a budget was breached — these cannot be compensated by a
    high score or by positive weighted signals.

    Attributes:
        blocker_failed: ``True`` when any hard gate (acceptance or risk
            blocker) failed, had missing/invalid evidence, or fell below its
            ``minimum_assurance`` floor.
        blocker_action: The most-severe :class:`EvidencePolicyAction` among
            failed blockers (the most conservative wins), or ``None`` when no
            blocker failed.
        blocker_reasons: Human-readable reasons for each failed blocker.
        budget_breached: ``True`` when any enabled budget dimension
            reached/exceeded a declared limit (or its telemetry was missing for
            a declared limit).
        budget_action: The most-severe :class:`EvidencePolicyAction` among
            breached budgets, or ``None`` when no budget was breached.
        budget_reasons: Human-readable reasons for each breached budget.
        budget_status: The per-dimension :class:`BudgetStatus` list (for the
            trace), including dimensions that stayed within budget.
        effective_weights: Resolved per-signal weights actually used in the
            weighted acceptance aggregation, keyed by check id (todo 2.2).
        policy_id: The active policy's id (for the trace).
        policy_version: The active policy's version (for the trace).
        policy_hash: The canonical ``"sha256:<hex>"`` policy hash (for the
            trace), so a decision is reproducible from its policy hash.
    """

    blocker_failed: bool = False
    blocker_action: EvidencePolicyAction | None = None
    blocker_reasons: list[str] = field(default_factory=list)
    budget_breached: bool = False
    budget_action: EvidencePolicyAction | None = None
    budget_reasons: list[str] = field(default_factory=list)
    budget_status: list[BudgetStatus] = field(default_factory=list)
    effective_weights: dict[str, float] = field(default_factory=dict)
    policy_id: str | None = None
    policy_version: str | None = None
    policy_hash: str | None = None

    @property
    def forced_action(self) -> EvidencePolicyAction | None:
        """Most-severe action forced by blockers and budgets, or ``None``.

        A blocker failure or a budget breach forces a non-``ACCEPT`` action
        that cannot be compensated by a positive score. When both fire the
        most conservative (``ROLLBACK`` > ``REPLAN`` > ``RETRY`` > ``ACCEPT``)
        wins. ``None`` means the gate imposes no forced action.
        """
        candidates: list[EvidencePolicyAction] = [
            a for a in (self.blocker_action, self.budget_action) if a is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda a: _ACTION_SEVERITY.get(a, 0))

    @property
    def forced_reasons(self) -> list[str]:
        """Human-readable reasons for every forced gate (blockers + budgets)."""
        return [*self.blocker_reasons, *self.budget_reasons]


class ContractEvaluator:
    """Deterministic evaluator mapping a contract + evidence to BOUND scores.

    Unlike the :class:`~bound.evaluator.Evaluator` Protocol (which scores a
    proposed :class:`~bound.models.Action`), this evaluator scores an *executed*
    step by reconciling collected :class:`~bound.evidence.ExecutionEvidence`
    against the declared :class:`~bound.contracts.StepContract`. The computation
    is pure: same contract + evidence → same scores, with no network or model
    dependency.

    All mappings are **v0.3 reference heuristics** — not scientifically
    calibrated:

    * **Acceptance (A)** ``∈ [0, 1]``: ``A = passed_required /
      total_required``, where each required :class:`~bound.contracts.AcceptanceCheck`
      is reconciled against :class:`~bound.evidence.CheckEvidence` by ``id``. A
      required check with **no** matching evidence counts as **FAILED** (never
      silently passing). Duplicate evidence for one ``id`` is deduplicated
      conservatively: the check passes only when *every* matching record has
      ``passed=True``. Optional (``required=False``) checks are recorded as
      advisory provenance only and do **not** affect ``A``. When the contract
      defines no required checks at all, ``A = 0.0`` (acceptance cannot be
      established from advisory checks alone).
    * **Cost (C)** ``∈ [0, 1]``: the mean of the *available* budget dimensions.
      Each declared dimension is ``min(actual / max, 1.0)`` (with the cap-zero
      rule from :func:`_normalize_capped`): ``retry_cost``, ``tool_cost``,
      ``token_cost``, ``runtime_cost``. A dimension is available when its budget
      maximum is defined. When a declared budget dimension's telemetry is
      unmeasured (``token_usage`` / ``runtime_seconds`` is ``None``) it is
      conservatively saturated to :data:`_UNMEASURED_COST_SATURATION` because
      budget compliance cannot be confirmed. When ``contract.budget`` is
      ``None``, ``C = 0.0`` and the provenance records "no cost budget was
      defined." When a budget exists but no dimension is defined, ``C = 0.0``.
    * **Risk (R)** ``∈ [0, 1]``: ``R = min(1.0, Σ contributions)`` — additive
      and capped, so a single failed check makes risk "rise by its severity".
      Each declared :class:`~bound.contracts.RiskCheck` contributes its
      ``severity`` when violated (evidence shows ``passed=False``, or —
      conservatively — when there is **no** matching evidence, because BOUND
      cannot confirm the risk was avoided), and ``0.0`` when confirmed passed.
      Two observable safety signals are added on top: ``unexpected_artifacts``
      non-empty contributes :data:`_UNEXPECTED_ARTIFACT_INDICATOR`;
      ``rollback_available is False`` contributes
      :data:`_ROLLBACK_UNAVAILABLE_INDICATOR`. ``rollback_available is None`` (a
      pure observable the contract does not declare) is *skipped* rather than
      invented.
    * **Influence (I)** ``∈ [-1, 1]``: ``0.0`` by default with an explicit
      honesty note, because no downstream-influence evidence is derivable from
      contract evidence. A caller may instead supply influence externally at
      construction. Honesty is preferred over invented sophistication.

    Attributes:
        influence: The externally-supplied downstream influence override, or
            ``None`` to use the honest ``0.0`` default.
    """

    def __init__(
        self,
        *,
        influence: float | None = None,
        run: RunContext | None = None,
    ) -> None:
        """Store the optional influence override and clear provenance.

        Args:
            influence: Optional externally-supplied downstream influence
                ``I ∈ [-1, 1]``. When ``None`` (the default) influence is set to
                ``0.0`` with an explicit honesty note in the provenance. When
                supplied, it is used verbatim (Pydantic validates the range on
                :class:`~bound.models.EvaluationScores`).
            run: Optional :class:`~bound.lineage_api.RunContext` configured once
                on the evaluator so that
                :meth:`bound.bound_workflow.BoundWorkflow.evaluate_step`
                auto-records lineage without a per-call ``run`` argument. Defaults
                to ``None`` (no lineage). This never affects the computed scores;
                it is purely a convenience default for the workflow's
                auto-instrumentation.
        """
        self._influence_override = influence
        self._provenance: dict[str, list[ScoreEvidence]] = {}
        self._lineage_run: RunContext | None = run
        self._assurance: AssuranceAssessment | None = None
        self._policy_gate: PolicyGateOutcome | None = None
        self._effective_weights: dict[str, float] = {}


    @property
    def influence(self) -> float | None:
        """The externally-supplied influence override, or ``None`` for default."""
        return self._influence_override

    @property
    def lineage_run(self) -> RunContext | None:
        """Optional lineage run configured on this evaluator, or ``None``.

        When set, :meth:`bound.bound_workflow.BoundWorkflow.evaluate_step` uses
        it as the default run context for automatic lineage recording (an
        explicitly passed ``run`` argument takes precedence). Never affects the
        computed scores.
        """
        return self._lineage_run

    @property
    def provenance(self) -> dict[str, list[ScoreEvidence]]:
        """Per-dimension evidence from the most recent :meth:`evaluate` call.

        Returns a dict keyed by ``"acceptance"``, ``"influence"``, ``"risk"``,
        ``"cost"``. Each value is the list of :class:`ScoreEvidence` that
        produced that dimension, so a consumer can answer "why is ``A`` what it
        is?". Returns an empty dict before :meth:`evaluate` has been called.
        """
        return self._provenance

    @property
    def assurance_assessment(self) -> AssuranceAssessment | None:
        """The :class:`AssuranceAssessment` from the most recent :meth:`evaluate`.

        After :meth:`evaluate` runs, this holds the decision-assurance
        assessment computed by :meth:`assess_assurance` from the contract's
        decision-critical / accepted-provenance-restricted checks and their
        collected evidence. :class:`~bound.policy.BoundPolicy` consumes it to
        gate a candidate ACCEPT. Returns ``None`` before :meth:`evaluate` has
        been called (no assurance has been computed yet).
        """
        return self._assurance

    @property
    def policy_gate(self) -> PolicyGateOutcome | None:
        """The active-policy gate outcome from the most recent :meth:`evaluate`.

        Populated only when :meth:`evaluate` was called with an active
        :class:`~bound.policy_schema.BoundPolicyConfig`. It carries the
        blocker/budget gate state and the resolved effective weights so
        :class:`~bound.policy.BoundPolicy` can force an uncompensable decision
        and the trace can reconstruct the weighted-signal contribution.
        Returns ``None`` before :meth:`evaluate` has been called or when no
        active policy was bound (the contract-only path).
        """
        return self._policy_gate

    @property
    def effective_weights(self) -> dict[str, float]:
        """Resolved per-signal weights from the most recent :meth:`evaluate`.

        Empty for the contract-only path; populated (keyed by check id) when an
        active policy's weighted signals fed the acceptance aggregation.
        """
        return self._effective_weights

    def evaluate(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
        *,
        policy: BoundPolicyConfig | None = None,
    ) -> EvaluationScores:
        """Derive :class:`EvaluationScores` from a contract and its evidence.

        The computation is pure and deterministic: the same ``contract`` and
        ``evidence`` always yield identical scores, with no network or model
        dependency. Per-dimension evidence is available via :attr:`provenance`.

        When an active ``policy`` is supplied it governs the
        evaluation: weighted quality signals feed the acceptance dimension, the
        policy's hard gates and budgets are assessed into a
        :class:`PolicyGateOutcome` (available via :attr:`policy_gate`), and the
        resolved effective weights are stored on :attr:`effective_weights`. When
        ``policy`` is ``None`` the behaviour is identical to the contract-only
        path (backwards compatible): no gate is computed and
        :attr:`effective_weights` is empty.

        Args:
            contract: The :class:`~bound.contracts.StepContract` whose declared
                acceptance checks, risk checks, and budget scope the scoring.
            evidence: The :class:`~bound.evidence.ExecutionEvidence` observed
                after the step executed. Unknown ``check_id`` values are
                allowed; they are simply ignored during contract reconciliation.
            policy: Optional active :class:`~bound.policy_schema.BoundPolicyConfig`
                governing gates/weights/budgets. ``None`` (the default) selects
                the contract-only path.

        Returns:
            The :class:`EvaluationScores` (``A``, ``I``, ``R``, ``C``) plus a
            structured ``reasoning`` summary. Per-dimension evidence is available
            via :attr:`provenance`. This never produces a BOUND decision; that
            remains the policy's responsibility.
        """
        if policy is None:
            self._policy_gate = None
            self._effective_weights = {}
            acceptance, acceptance_evidence = self._acceptance(contract, evidence)
        else:
            acceptance, acceptance_evidence = self._acceptance_with_policy(
                contract, evidence, policy
            )

        risk, risk_evidence = self._risk(contract, evidence)
        cost, cost_evidence = self._cost(contract, evidence)
        influence, influence_evidence = self._influence()

        self._provenance = {
            "acceptance": acceptance_evidence,
            "influence": influence_evidence,
            "risk": risk_evidence,
            "cost": cost_evidence,
        }

        self._assurance = self.assess_assurance(contract, evidence)
        self._policy_gate = (
            self.assess_policy_gate(contract, evidence, policy)
            if policy is not None
            else None
        )
        self._effective_weights = (
            self._policy_gate.effective_weights if self._policy_gate is not None else {}
        )

        reasoning = self._render_reasoning(acceptance, influence, risk, cost)
        return EvaluationScores(
            acceptance=acceptance,
            influence=influence,
            risk=risk,
            cost=cost,
            reasoning=reasoning,
        )

    # ------------------------------------------------------------------
    # Dimension rules (v0.3 reference heuristic — NOT scientifically calibrated)
    # ------------------------------------------------------------------

    def _acceptance(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
    ) -> tuple[float, list[ScoreEvidence]]:
        """Score acceptance as the required-check pass rate.

        ``A = passed_required / total_required``. Each required
        :class:`~bound.contracts.AcceptanceCheck` is reconciled against the
        evidence by ``id``:

        * If matching evidence exists and *every* record has ``passed=True`` the
          check passes (conservative all-must-pass dedup of duplicates).
        * If matching evidence exists but any record has ``passed=False`` the
          check fails.
        * If **no** matching evidence exists the check **fails** — missing
          required evidence is never silently treated as passing.

        Optional (``required=False``) checks are recorded as advisory provenance
        and do not affect ``A``. When the contract defines no required checks,
        ``A = 0.0`` because acceptance cannot be established from advisory
        checks alone.

        Args:
            contract: The step contract declaring the acceptance checks.
            evidence: The collected execution evidence.

        Returns:
            A ``(acceptance, evidence)`` tuple. The evidence lists one
            :class:`ScoreEvidence` per required check (each contributing
            ``1 / total_required`` to ``A`` when passed), advisory entries for
            optional checks, and a final ``summary`` entry reconstructing ``A``.
        """
        by_id: dict[str, list[CheckEvidence]] = {}
        for ce in evidence.acceptance:
            by_id.setdefault(ce.check_id, []).append(ce)

        required = [c for c in contract.acceptance_checks if c.required]
        optional = [c for c in contract.acceptance_checks if not c.required]
        total = len(required)

        records: list[ScoreEvidence] = []

        if total == 0:
            records.append(
                ScoreEvidence(
                    source="summary",
                    value=0.0,
                    contribution=0.0,
                    description=(
                        "no required acceptance checks defined on the contract; "
                        "A=0.0 because acceptance cannot be established from "
                        "optional (advisory) checks alone."
                    ),
                ),
            )
            self._record_optional_acceptance(optional, by_id, records)
            return 0.0, records

        passed_ids: list[str] = []
        failed_ids: list[str] = []
        for check in required:
            evs = by_id.get(check.id, [])
            if evs and all(e.passed for e in evs):
                passed_ids.append(check.id)
                records.append(
                    ScoreEvidence(
                        source=check.id,
                        value=1.0,
                        contribution=1.0 / total,
                        description=(
                            f"✓ passed: {len(evs)} evidence record(s), all "
                            f"passed; contributes {1.0 / total:.4f} to A."
                        ),
                    ),
                )
            elif evs:
                failed_ids.append(check.id)
                n_fail = sum(1 for e in evs if not e.passed)
                records.append(
                    ScoreEvidence(
                        source=check.id,
                        value=0.0,
                        contribution=0.0,
                        description=(
                            f"✗ failed: {n_fail} of {len(evs)} evidence "
                            f"record(s) report passed=False; contributes 0.0 to A."
                        ),
                    ),
                )
            else:
                failed_ids.append(check.id)
                records.append(
                    ScoreEvidence(
                        source=check.id,
                        value=0.0,
                        contribution=0.0,
                        description=(
                            "✗ failed: no matching CheckEvidence for required "
                            "check — missing required evidence counts as failed "
                            "(never silently passing); contributes 0.0 to A."
                        ),
                    ),
                )

        passed_count = len(passed_ids)
        acceptance = passed_count / total
        records.append(
            ScoreEvidence(
                source="summary",
                value=acceptance,
                contribution=acceptance,
                description=(
                    f"✓{passed_count} of {total} required check(s) passed: "
                    f"{', '.join(passed_ids) if passed_ids else 'none'}; "
                    f"✗ {', '.join(failed_ids) if failed_ids else 'none'}. "
                    f"A = {passed_count}/{total} = {acceptance:.4f}."
                ),
            ),
        )
        self._record_optional_acceptance(optional, by_id, records)
        return acceptance, records


    @staticmethod
    def _record_optional_acceptance(
        optional: list,
        by_id: dict[str, list[CheckEvidence]],
        records: list[ScoreEvidence],
    ) -> None:
        """Append advisory provenance for optional acceptance checks.

        Optional (``required=False``) checks do not affect ``A``. They are
        recorded here so a consumer can still see whether advisory gates were
        confirmed, but their contribution is always ``0.0``.

        Args:
            optional: The optional acceptance checks from the contract.
            by_id: Evidence grouped by ``check_id``.
            records: The provenance list to append advisory entries to.
        """
        for check in optional:
            evs = by_id.get(check.id, [])
            confirmed = bool(evs) and all(e.passed for e in evs)
            records.append(
                ScoreEvidence(
                    source=check.id,
                    value=1.0 if confirmed else 0.0,
                    contribution=0.0,
                    description=(
                        f"optional check (advisory, does not affect A); "
                        f"{'✓ confirmed passed' if confirmed else '✗ not confirmed'}."
                    ),
                ),
            )


    # ------------------------------------------------------------------
    # v0.7 active-policy weighted acceptance (todo 2.2 / 6.1)
    # ------------------------------------------------------------------

    @staticmethod
    def _signal_passed(evs: list[CheckEvidence]) -> bool:
        """Return whether a weighted signal's evidence confirms a pass.

        A weighted quality signal contributes its :attr:`effective_weight` to
        the acceptance aggregate only when its evidence confirms a pass. The
        conservative all-must-pass dedup mirrors :meth:`_acceptance`: a signal
        with no evidence, or with any ``passed=False`` record, does **not**
        confirm a pass. A signal whose evidence is MISSING/INVALID/STALE also
        does not confirm a pass.

        Args:
            evs: The matching :class:`CheckEvidence` records (may be empty).

        Returns:
            ``True`` only when there is at least one record and every record
            confirms a pass.
        """
        if not evs:
            return False
        if any(
            e.status
            in (EvidenceStatus.MISSING, EvidenceStatus.INVALID, EvidenceStatus.STALE)
            for e in evs
        ):
            return False
        return all(e.passed for e in evs)

    def _acceptance_with_policy(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
        policy: BoundPolicyConfig,
    ) -> tuple[float, list[ScoreEvidence]]:
        """Score acceptance blending contract checks with weighted signals.

        When an active ``policy`` governs the step (todo 2.2) the acceptance
        dimension aggregates *both* the contract's required acceptance checks
        (each a binary gate of weight ``1.0``) and the policy's weighted
        quality signals (each weighted by its resolved ``effective_weight``).
        The result is the weighted pass fraction::

            A = Σ(effective_weight_i × pass_i) / Σ(effective_weight_i)

        over all acceptance-relevant signals, where ``pass_i`` is ``1`` when the
        signal's evidence confirms a pass and ``0`` otherwise (missing/failed
        evidence never confirms a pass). Hard-gate blockers that fail still
        contribute ``0`` here *and* force a gate downgrade via
        :meth:`assess_policy_gate` — so a failed blocker cannot be compensated
        by high-scoring weighted signals.

        When the policy declares no acceptance checks and no quality signals the
        contract-only acceptance (``passed_required / total_required``) is used,
        so a policy that only adds budgets/risk does not erase contract
        acceptance.

        Args:
            contract: The step contract declaring the required acceptance checks.
            evidence: The collected execution evidence.
            policy: The active policy whose acceptance/quality checks govern.

        Returns:
            A ``(acceptance, evidence)`` tuple. The evidence lists one
            :class:`ScoreEvidence` per acceptance signal plus a ``summary``.
        """
        by_id: dict[str, list[CheckEvidence]] = {}
        for ce in evidence.acceptance:
            by_id.setdefault(ce.check_id, []).append(ce)

        required = [c for c in contract.acceptance_checks if c.required]
        records: list[ScoreEvidence] = []
        effective_weights: dict[str, float] = {}

        # Contract required checks (weight 1.0) + policy acceptance blockers
        # (weight 1.0) + quality signals (effective_weight). A check id is
        # counted once: a policy blocker that duplicates a contract required
        # check is the same gate, so it is not double-counted in the weighted
        # average (the gate still fires independently via assess_policy_gate).
        terms: list[tuple[str, float, bool]] = []
        seen: set[str] = set()
        for check in required:
            evs = by_id.get(check.id, [])
            passed = bool(evs) and all(e.passed for e in evs)
            effective_weights[check.id] = 1.0
            terms.append((check.id, 1.0, passed))
            seen.add(check.id)
        for gate in policy.acceptance_checks:
            if gate.id in seen:
                continue
            evs = by_id.get(gate.id, [])
            passed = self._signal_passed(evs)
            effective_weights[gate.id] = 1.0
            terms.append((gate.id, 1.0, passed))
            seen.add(gate.id)
        for signal in policy.quality_checks:
            if signal.id in seen:
                continue
            evs = by_id.get(signal.id, [])
            passed = self._signal_passed(evs)
            effective_weights[signal.id] = signal.effective_weight
            terms.append((signal.id, signal.effective_weight, passed))
            seen.add(signal.id)

        total_weight = sum(w for _, w, _ in terms)
        if total_weight <= 0.0:
            # No acceptance-relevant signals: fall back to contract-only
            # acceptance so a budget/risk-only policy does not zero out A.
            return self._acceptance(contract, evidence)

        weighted_sum = sum(w * (1.0 if passed else 0.0) for _, w, passed in terms)
        acceptance = weighted_sum / total_weight

        for cid, w, passed in terms:
            records.append(
                ScoreEvidence(
                    source=cid,
                    value=1.0 if passed else 0.0,
                    contribution=(w * (1.0 if passed else 0.0)) / total_weight,
                    description=(
                        f"weighted signal (effective_weight={w:.4f}); "
                        f"{'✓ passed' if passed else '✗ not passed'}; "
                        f"contributes {(w * (1.0 if passed else 0.0)) / total_weight:.4f} to A."
                    ),
                ),
            )
        records.append(
            ScoreEvidence(
                source="summary",
                value=acceptance,
                contribution=acceptance,
                description=(
                    f"A = Σ(w_i × pass_i) / Σ(w_i) = {weighted_sum:.4f} / "
                    f"{total_weight:.4f} = {acceptance:.4f} "
                    f"(weighted aggregation of {len(terms)} acceptance signal(s))."
                ),
            ),
        )
        return acceptance, records

    def _risk(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
    ) -> tuple[float, list[ScoreEvidence]]:
        """Score risk as a capped sum of violated-check severities and signals.

        ``R = min(1.0, Σ contributions)`` — additive and capped at ``1.0`` so a
        single failed check makes risk "rise by its severity" rather than being
        diluted by a mean. Contributions are:

        * Each declared :class:`~bound.contracts.RiskCheck`: its ``severity``
          when violated, ``0.0`` when confirmed passed. A risk check with **no**
          matching evidence is treated conservatively as **violated**
          (contributes its full severity) because BOUND cannot confirm the risk
          was avoided — mirroring the acceptance "missing required evidence =
          failed" principle for declared contract items.
        * ``unexpected_artifacts`` non-empty: :data:`_UNEXPECTED_ARTIFACT_INDICATOR`.
        * ``rollback_available is False``: :data:`_ROLLBACK_UNAVAILABLE_INDICATOR`.
          ``rollback_available is None`` (a pure observable the contract does
          not declare) is *skipped* rather than invented, so an unmeasured
          rollback does not inflate baseline risk.

        Duplicate evidence for one ``id`` is deduplicated conservatively: the
        check is considered passed only when *every* matching record has
        ``passed=True``.

        Args:
            contract: The step contract declaring the risk checks.
            evidence: The collected execution evidence.

        Returns:
            A ``(risk, evidence)`` tuple. The evidence lists one
            :class:`ScoreEvidence` per risk check and safety signal (with its
            pre-cap contribution), plus a final ``summary`` entry applying the
            ``min(1.0, Σ)`` cap.
        """
        by_id: dict[str, list[CheckEvidence]] = {}
        for ce in evidence.risks:
            by_id.setdefault(ce.check_id, []).append(ce)

        # (source, raw_value, contribution, description)
        entries: list[tuple[str, float, float, str]] = []

        for check in contract.risk_checks:
            evs = by_id.get(check.id, [])
            if evs and all(e.passed for e in evs):
                entries.append(
                    (
                        check.id,
                        check.severity,
                        0.0,
                        f"✓ risk check passed ({len(evs)} record(s)); "
                        f"severity={check.severity:.4f} contributes 0.0.",
                    ),
                )
            elif evs:
                n_fail = sum(1 for e in evs if not e.passed)
                entries.append(
                    (
                        check.id,
                        check.severity,
                        check.severity,
                        f"✗ risk check violated: {n_fail} of {len(evs)} "
                        f"record(s) report passed=False; severity="
                        f"{check.severity:.4f} contributes {check.severity:.4f}.",
                    ),
                )
            else:
                entries.append(
                    (
                        check.id,
                        check.severity,
                        check.severity,
                        "✗ risk check violated (no matching CheckEvidence): "
                        "cannot confirm the risk was avoided, treated "
                        f"conservatively as violated; severity="
                        f"{check.severity:.4f} contributes {check.severity:.4f}.",
                    ),
                )

        # Observable safety signal: unexpected artifacts (always "measured" —
        # a list that is either empty or non-empty).
        if evidence.unexpected_artifacts:
            count = len(evidence.unexpected_artifacts)
            entries.append(
                (
                    "unexpected_artifacts",
                    float(count),
                    _UNEXPECTED_ARTIFACT_INDICATOR,
                    f"✗ {count} unexpected artifact(s) observed: "
                    f"{', '.join(evidence.unexpected_artifacts)}; contributes "
                    f"{_UNEXPECTED_ARTIFACT_INDICATOR:.4f}.",
                ),
            )
        else:
            entries.append(
                (
                    "unexpected_artifacts",
                    0.0,
                    0.0,
                    "✓ no unexpected artifacts observed; contributes 0.0.",
                ),
            )

        # Observable safety signal: rollback availability. None is a *pure*
        # observable the contract does not declare, so it is skipped (not
        # invented) when unmeasured; only a measured False raises risk.
        if evidence.rollback_available is False:
            entries.append(
                (
                    "rollback_available",
                    1.0,
                    _ROLLBACK_UNAVAILABLE_INDICATOR,
                    f"✗ rollback unavailable (rollback_available=False); "
                    f"contributes {_ROLLBACK_UNAVAILABLE_INDICATOR:.4f}.",
                ),
            )
        elif evidence.rollback_available is True:
            entries.append(
                (
                    "rollback_available",
                    0.0,
                    0.0,
                    "✓ rollback available (rollback_available=True); "
                    "contributes 0.0.",
                ),
            )
        else:
            entries.append(
                (
                    "rollback_available",
                    0.0,
                    0.0,
                    "rollback availability unmeasured (None); not scored — "
                    "pure observable, unmeasured telemetry is not invented.",
                ),
            )

        raw_sum = sum(contribution for _, _, contribution, _ in entries)
        risk = min(1.0, raw_sum)
        records = [
            ScoreEvidence(
                source=source,
                value=raw,
                contribution=contribution,
                description=description,
            )
            for source, raw, contribution, description in entries
        ]
        records.append(
            ScoreEvidence(
                source="summary",
                value=risk,
                contribution=risk,
                description=(
                    f"R = min(1.0, Σ contributions) = min(1.0, {raw_sum:.4f}) "
                    f"= {risk:.4f} (sum of {len(entries)} indicator "
                    "contribution(s), capped at 1.0)."
                ),
            ),
        )
        return risk, records


    def _cost(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
    ) -> tuple[float, list[ScoreEvidence]]:
        """Score cost as the mean of the available normalized budget dimensions.

        For each *available* budget dimension (one whose maximum is defined),
        ``normalized = min(actual / max, 1.0)`` using the cap-zero rule from
        :func:`_normalize_capped`:

        * ``retry_cost`` = ``retry_count / max_retries``
        * ``tool_cost`` = ``tool_call_count / max_tool_calls``
        * ``token_cost`` = ``token_usage / max_tokens``
        * ``runtime_cost`` = ``runtime_seconds / max_runtime_seconds``

        ``C`` is the mean of the available dimensions only. In v0.7 each
        telemetry value is an :class:`~bound.evidence.EvidenceMetric` (or
        ``None`` when unmeasured); a measured value carries its trust provenance
        while ``None`` means MISSING, never a silent zero. When a declared
        budget dimension's telemetry is unmeasured, it is conservatively
        saturated to :data:`_UNMEASURED_COST_SATURATION` because budget
        compliance cannot be confirmed (a declared constraint with no confirming
        evidence is never silently treated as cheap).

        When ``contract.budget`` is ``None``, ``C = 0.0`` and the provenance
        records "no cost budget was defined." When a budget exists but no
        dimension is defined, ``C = 0.0`` (no available dimensions).

        Args:
            contract: The step contract declaring the optional budget.
            evidence: The collected execution evidence.

        Returns:
            A ``(cost, evidence)`` tuple. The evidence lists one
            :class:`ScoreEvidence` per available dimension (each contributing
            ``normalized / count`` to ``C``) plus a ``summary`` entry, or a
            single entry explaining the absence of a budget.
        """
        budget = contract.budget
        if budget is None:
            return 0.0, [
                ScoreEvidence(
                    source="budget",
                    value=0.0,
                    contribution=0.0,
                    description=(
                        "no cost budget was defined on the contract; C=0.0 "
                        "(cost cannot be assessed without declared budgets)."
                    ),
                ),
            ]

        # (source, raw_value, max, normalized, description_note, provenance)
        terms: list[tuple[str, float, float, float, str, EvidenceProvenance]] = []

        dim = _budget_dimension(
            evidence.retry_count, budget.max_retries,
            field_name="retry_count", budget_label="retry", cost_source="retry_cost",
        )
        if dim is not None:
            terms.append(dim)

        dim = _budget_dimension(
            evidence.tool_call_count, budget.max_tool_calls,
            field_name="tool_call_count", budget_label="tool-call",
            cost_source="tool_cost",
        )
        if dim is not None:
            terms.append(dim)

        dim = _budget_dimension(
            evidence.token_usage, budget.max_tokens,
            field_name="token_usage", budget_label="token",
            cost_source="token_cost",
        )
        if dim is not None:
            terms.append(dim)

        dim = _budget_dimension(
            evidence.runtime_seconds, budget.max_runtime_seconds,
            field_name="runtime_seconds", budget_label="runtime",
            cost_source="runtime_cost",
        )
        if dim is not None:
            terms.append(dim)

        if not terms:
            return 0.0, [
                ScoreEvidence(
                    source="budget",
                    value=0.0,
                    contribution=0.0,
                    description=(
                        "a budget is defined but no cost dimensions "
                        "(max_retries/max_tool_calls/max_tokens/"
                        "max_runtime_seconds) are set; C=0.0 (no available "
                        "dimensions)."
                    ),
                ),
            ]

        count = len(terms)
        cost = sum(norm for _, _, _, norm, _, _ in terms) / count
        records = [
            ScoreEvidence(
                source=source,
                value=raw,
                contribution=norm / count,
                description=(
                    f"{note}; contributes {norm / count:.4f} to the mean of "
                    f"{count} available cost dimension(s)."
                ),
                provenance=prov,
            )
            for source, raw, _mx, norm, note, prov in terms
        ]
        records.append(
            ScoreEvidence(
                source="summary",
                value=cost,
                contribution=cost,
                description=f"C = mean of {count} available normalized dimension(s) = {cost:.4f}.",
            ),
        )
        return cost, records


    def _influence(self) -> tuple[float, list[ScoreEvidence]]:
        """Resolve downstream influence (v0.3: honest default or external).

        No downstream-influence evidence is derivable from contract evidence, so
        the default is ``0.0`` recorded as an explicit DEFAULTED value (not a
        measurement): ``raw_value=None``, ``effective_value=0.0``, and a
        ``reason`` explaining the policy-neutral substitution. DEFAULTED is
        never presented as VERIFIED. A caller may instead supply influence
        externally at construction; that value is recorded as EVALUATED
        (derived/supplied, not independently observed).

        Returns:
            A ``(influence, evidence)`` tuple.
        """
        if self._influence_override is not None:
            influence = float(self._influence_override)
            return influence, [
                ScoreEvidence(
                    source="external",
                    value=influence,
                    contribution=influence,
                    description=(
                        "influence supplied externally at construction; v0.3 "
                        "does not derive downstream influence from contract "
                        "evidence."
                    ),
                    provenance=EvidenceProvenance.EVALUATED,
                    raw_value=influence,
                    effective_value=influence,
                ),
            ]

        return 0.0, [
            ScoreEvidence(
                source="default",
                value=0.0,
                contribution=0.0,
                description=(
                    "v0.3 sets influence=0.0 by default: no downstream-influence "
                    "evidence is derivable from contract evidence. Honesty over "
                    "invented sophistication."
                ),
                provenance=EvidenceProvenance.DEFAULTED,
                raw_value=None,
                effective_value=0.0,
                reason="policy neutral value; no evidence source",
            ),
        ]

    # ------------------------------------------------------------------
    # Decision assurance (v0.7)
    # ------------------------------------------------------------------

    @staticmethod
    def _restricted_acceptance_checks(
        contract: StepContract,
    ) -> list[AcceptanceCheck]:
        """Return acceptance checks with an explicit provenance restriction.

        Only checks with a non-``None`` :attr:`accepted_provenance` influence
        the decision assurance — unrestricted checks accept any provenance and
        therefore cannot degrade the trust assessment.

        Args:
            contract: The step contract declaring the acceptance checks.

        Returns:
            The subset of acceptance checks with ``accepted_provenance`` set.
        """
        return [c for c in contract.acceptance_checks if c.accepted_provenance is not None]

    @staticmethod
    def _restricted_risk_checks(contract: StepContract) -> list[RiskCheck]:
        """Return risk checks that are decision-critical or provenance-restricted.

        A risk check influences assurance when it is
        :attr:`~bound.contracts.RiskCheck.decision_critical` **or** declares an
        :attr:`accepted_provenance` allow-list.

        Args:
            contract: The step contract declaring the risk checks.

        Returns:
            The subset of risk checks subject to assurance gating.
        """
        return [
            c
            for c in contract.risk_checks
            if c.decision_critical or c.accepted_provenance is not None
        ]

    @staticmethod
    def _classify_check(
        check_id: str,
        is_critical: bool,
        accepted_provenance: list[EvidenceProvenance] | None,
        on_missing: EvidencePolicyAction,
        on_claimed: EvidencePolicyAction,
        evs: list[CheckEvidence],
    ) -> tuple[str, EvidencePolicyAction | None, str]:
        """Classify a single check's evidence for the assurance computation.

        Returns a ``(category, block_action, reason)`` triple. ``category`` is
        one of ``"verified"``, ``"evaluated"``, ``"claimed"``, or
        ``"insufficient"``; ``block_action`` is the
        :class:`EvidencePolicyAction` to apply when ACCEPT should be blocked
        (``None`` when the check passes the trust bar); ``reason`` is
        human-readable.

        Classification (in order): no evidence → insufficient; any INVALID
        status → insufficient; effective provenance (strongest among records)
        not in an explicit ``accepted_provenance`` → CLAIMED→claimed else
        insufficient; otherwise verified-tier→verified, EVALUATED→evaluated,
        CLAIMED→claimed, MISSING/DEFAULTED→insufficient.

        Args:
            check_id: The check identifier (for reason messages).
            is_critical: Whether the check is decision-critical.
            accepted_provenance: The check's provenance allow-list, or ``None``.
            on_missing: Action when evidence is missing/unacceptable.
            on_claimed: Action when evidence is only CLAIMED.
            evs: The matching :class:`CheckEvidence` records (may be empty).

        Returns:
            A ``(category, block_action, reason)`` triple.
        """
        critical_note = " (decision-critical)" if is_critical else ""

        if not evs:
            return (
                "insufficient",
                on_missing,
                f"check '{check_id}'{critical_note} has no matching evidence "
                f"(provenance: MISSING)",
            )

        if any(e.status is EvidenceStatus.INVALID for e in evs):
            return (
                "insufficient",
                on_missing,
                f"check '{check_id}'{critical_note} has INVALID evidence "
                f"(unusable artefact or collector failure)",
            )

        eff = _strongest_provenance(evs)
        assert eff is not None  # evs is non-empty here

        if accepted_provenance is not None and eff not in accepted_provenance:
            if eff is EvidenceProvenance.CLAIMED:
                return (
                    "claimed",
                    on_claimed,
                    f"check '{check_id}'{critical_note} relies on CLAIMED "
                    f"evidence (agent self-report) not in its "
                    f"accepted_provenance",
                )
            return (
                "insufficient",
                on_missing,
                f"check '{check_id}'{critical_note} evidence provenance "
                f"'{eff.value}' is not in its accepted_provenance",
            )

        if eff in _VERIFIED_PROVENANCE:
            return (
                "verified",
                None,
                f"check '{check_id}'{critical_note} backed by verified-tier "
                f"evidence (provenance: {eff.value})",
            )
        if eff is EvidenceProvenance.EVALUATED:
            return (
                "evaluated",
                None,
                f"check '{check_id}'{critical_note} backed by EVALUATED "
                f"evidence (derived, not independently verified)",
            )
        if eff is EvidenceProvenance.CLAIMED:
            return (
                "claimed",
                on_claimed,
                f"check '{check_id}'{critical_note} relies on CLAIMED "
                f"evidence (agent self-report)",
            )
        return (
            "insufficient",
            on_missing,
            f"check '{check_id}'{critical_note} evidence provenance "
            f"'{eff.value}' is not independently verified",
        )

    def assess_assurance(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
    ) -> AssuranceAssessment:
        """Compute the :class:`DecisionAssurance` from decision-critical evidence.

        The assurance is determined deterministically from the trust provenance
        of evidence backing the contract's *restricted* checks — acceptance
        checks with an :attr:`accepted_provenance` and risk checks that are
        :attr:`decision_critical <bound.contracts.RiskCheck.decision_critical>`
        or provenance-restricted. Unrestricted checks never degrade assurance.

        Assurance level (worst contributor wins, priority order):

        * **INSUFFICIENT** — a restricted check has no evidence, INVALID
          evidence, or a provenance outside its ``accepted_provenance``.
        * **CLAIMED** — a restricted check leans on CLAIMED (agent self-report).
        * **MIXED** — every restricted check has acceptable evidence but some is
          EVALUATED (derived, not independently observed).
        * **VERIFIED** — every restricted check's evidence is verified-tier
          (OBSERVED/VERIFIED/ATTETSTED). With no restricted checks, VERIFIED.

        When assurance is CLAIMED or INSUFFICIENT, a candidate ACCEPT is blocked:
        :attr:`AssuranceAssessment.accept_block_action` is the most severe
        ``on_missing``/``on_claimed`` among failing checks (mapped to a BOUND
        decision by the policy). MIXED and VERIFIED never block ACCEPT.

        Args:
            contract: The step contract declaring the (restricted) checks.
            evidence: The collected execution evidence.

        Returns:
            An :class:`AssuranceAssessment` with the level, reasons, and block
            action/reasons (when applicable).
        """
        acc_by_id: dict[str, list[CheckEvidence]] = {}
        for ce in evidence.acceptance:
            acc_by_id.setdefault(ce.check_id, []).append(ce)
        risk_by_id: dict[str, list[CheckEvidence]] = {}
        for ce in evidence.risks:
            risk_by_id.setdefault(ce.check_id, []).append(ce)

        reasons: list[str] = []
        has_insufficient = False
        has_claimed = False
        has_evaluated = False
        block_action: EvidencePolicyAction | None = None
        block_action_rank = -1
        block_reasons: list[str] = []

        def _consider(
            category: str,
            action: EvidencePolicyAction | None,
            reason: str,
        ) -> None:
            nonlocal has_insufficient, has_claimed, has_evaluated
            nonlocal block_action, block_action_rank, block_reasons
            reasons.append(reason)
            if category == "verified":
                return
            if category == "evaluated":
                has_evaluated = True
                return
            if category == "claimed":
                has_claimed = True
            else:  # insufficient
                has_insufficient = True
            if action is not None and _ACTION_SEVERITY[action] > block_action_rank:
                block_action = action
                block_action_rank = _ACTION_SEVERITY[action]
                block_reasons = [reason]

        for check in self._restricted_acceptance_checks(contract):
            evs = acc_by_id.get(check.id, [])
            category, action, reason = self._classify_check(
                check.id,
                is_critical=False,
                accepted_provenance=check.accepted_provenance,
                on_missing=check.on_missing,
                on_claimed=check.on_claimed,
                evs=evs,
            )
            _consider(category, action, reason)

        for check in self._restricted_risk_checks(contract):
            evs = risk_by_id.get(check.id, [])
            category, action, reason = self._classify_check(
                check.id,
                is_critical=check.decision_critical,
                accepted_provenance=check.accepted_provenance,
                on_missing=check.on_missing,
                on_claimed=check.on_claimed,
                evs=evs,
            )
            _consider(category, action, reason)

        if has_insufficient:
            assurance = DecisionAssurance.INSUFFICIENT
        elif has_claimed:
            assurance = DecisionAssurance.CLAIMED
        elif has_evaluated:
            assurance = DecisionAssurance.MIXED
        else:
            assurance = DecisionAssurance.VERIFIED

        if block_action is not None:
            block_reasons.append(
                "ACCEPT requires VERIFIED acceptance evidence; gated to the "
                f"contract's {block_action.value} action."
            )

        return AssuranceAssessment(
            assurance=assurance,
            reasons=reasons,
            accept_block_action=block_action,
            accept_block_reasons=block_reasons,
        )

    # ------------------------------------------------------------------
    # v0.7 active-policy gate assessment
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_effective_weights(
        contract: StepContract,
        policy: BoundPolicyConfig,
    ) -> dict[str, float]:
        """Resolve the per-signal effective weights used in weighted acceptance.

        Contract required acceptance checks and policy acceptance blockers
        carry an implicit weight of ``1.0``; policy quality signals carry their
        resolved :attr:`~WeightedSignal.effective_weight`. The result is stored
        on the :class:`PolicyGateOutcome` so the trace can reconstruct the
        weighted-signal contribution (todo 2.2).

        Args:
            contract: The step contract declaring required acceptance checks.
            policy: The active policy declaring acceptance/quality checks.

        Returns:
            A ``{check_id: effective_weight}`` mapping.
        """
        weights: dict[str, float] = {}
        for check in contract.acceptance_checks:
            if check.required:
                weights[check.id] = 1.0
        for gate in policy.acceptance_checks:
            weights[gate.id] = 1.0
        for signal in policy.quality_checks:
            weights[signal.id] = signal.effective_weight
        return weights

    def _classify_gate(
        self,
        gate: HardGate,
        evs: list[CheckEvidence],
    ) -> tuple[bool, EvidencePolicyAction | None, str]:
        """Classify a hard gate for the blocker gate computation.

        Returns a ``(failed, action, reason)`` triple. ``failed`` is ``True``
        when the gate did not hold and therefore cannot be compensated by a
        positive score; ``action`` is the :class:`EvidencePolicyAction` to
        force (``on_failure``/``on_missing``/``on_claimed``) when the gate
        failed, or ``None`` when it held; ``reason`` is human-readable.

        Classification (in order): a non-``required`` gate is advisory and never
        fails; no evidence → failed (``on_missing``); INVALID/STALE evidence →
        failed (``on_missing``); any ``passed=False`` → failed
        (``on_failure``); provenance outside ``accepted_provenance`` → failed
        (``on_claimed`` if CLAIMED else ``on_missing``); below
        ``minimum_assurance`` → failed (``on_claimed`` if CLAIMED else
        ``on_missing``); otherwise the gate held.

        Args:
            gate: The hard gate to classify.
            evs: The matching :class:`CheckEvidence` records (may be empty).

        Returns:
            A ``(failed, action, reason)`` triple.
        """
        if not gate.required:
            return False, None, f"blocker '{gate.id}' is advisory (required=False)."

        if not evs:
            return (
                True,
                gate.on_missing,
                f"blocker '{gate.id}' has no matching evidence "
                f"(provenance: MISSING); cannot be compensated.",
            )
        if any(
            e.status in (EvidenceStatus.INVALID, EvidenceStatus.STALE) for e in evs
        ):
            return (
                True,
                gate.on_missing,
                f"blocker '{gate.id}' has INVALID/STALE evidence; the gate "
                f"cannot be confirmed and cannot be compensated.",
            )
        if any(e.passed is False for e in evs):
            return (
                True,
                gate.on_failure,
                f"blocker '{gate.id}' was observed to fail (passed=False); "
                f"on_failure applies and cannot be compensated.",
            )

        eff = _strongest_provenance(evs)
        assert eff is not None  # evs is non-empty here

        if gate.accepted_provenance is not None and eff not in gate.accepted_provenance:
            action = (
                gate.on_claimed
                if eff is EvidenceProvenance.CLAIMED
                else gate.on_missing
            )
            return (
                True,
                action,
                f"blocker '{gate.id}' evidence provenance '{eff.value}' is not "
                f"in its accepted_provenance; cannot be compensated.",
            )

        if gate.minimum_assurance is not None:
            category, _, _ = self._classify_check(
                check_id=gate.id,
                is_critical=True,
                accepted_provenance=gate.accepted_provenance,
                on_missing=gate.on_missing,
                on_claimed=gate.on_claimed,
                evs=evs,
            )
            evidence_assurance = _CATEGORY_ASSURANCE.get(
                category, DecisionAssurance.INSUFFICIENT
            )
            if (
                _ASSURANCE_RANK[evidence_assurance]
                < _ASSURANCE_RANK[gate.minimum_assurance]
            ):
                action = (
                    gate.on_claimed
                    if evidence_assurance is DecisionAssurance.CLAIMED
                    else gate.on_missing
                )
                return (
                    True,
                    action,
                    f"blocker '{gate.id}' evidence assurance "
                    f"'{evidence_assurance.value}' is below its "
                    f"minimum_assurance '{gate.minimum_assurance.value}'; "
                    f"claimed/insufficient evidence cannot satisfy the gate.",
                )

        return False, None, f"blocker '{gate.id}' held (provenance: {eff.value})."

    @staticmethod
    def _budget_telemetry(
        dimension: str,
        evidence: ExecutionEvidence,
    ) -> float | None:
        """Return the observed telemetry value for a budget dimension.

        ``None`` means the telemetry was not measured (it is *missing*, never a
        zero) — which a declared budget treats conservatively as
        over-budget. ``financial_cost`` has no telemetry field on
        :class:`ExecutionEvidence`, so it is always ``None`` (missing) when
        declared and enabled.

        Args:
            dimension: One of :data:`~bound.policy_schema.BUDGET_DIMENSIONS`.
            evidence: The collected execution evidence.

        Returns:
            The observed scalar, or ``None`` when unmeasured.
        """
        metric: EvidenceMetric | None
        if dimension == "attempts":
            metric = evidence.retry_count
        elif dimension == "tool_calls":
            metric = evidence.tool_call_count
        elif dimension == "tokens":
            metric = evidence.token_usage
        elif dimension == "runtime":
            metric = evidence.runtime_seconds
        else:  # financial_cost — no telemetry field exists
            return None
        return _metric_value(metric)

    def _assess_budgets(
        self,
        policy: BoundPolicyConfig,
        evidence: ExecutionEvidence,
    ) -> tuple[list[BudgetStatus], EvidencePolicyAction | None, list[str]]:
        """Evaluate every declared budget dimension (todo 2.2).

        For each *enabled* :class:`~bound.policy_schema.BudgetDimension` with a
        declared limit, the observed telemetry is compared against the soft and
        hard limits. **Missing telemetry can never silently satisfy a declared
        budget**: a dimension with a declared limit and unmeasured telemetry is
        treated conservatively as *not within budget* (it breaches with the
        most severe declared action). An explicitly ``enabled=False`` dimension
        is skipped entirely.

        Args:
            policy: The active policy whose budgets govern.
            evidence: The collected execution evidence.

        Returns:
            A ``(budget_status, action, reasons)`` triple: the per-dimension
            :class:`BudgetStatus` list (for the trace), the most-severe breached
            action (or ``None``), and the human-readable breach reasons.
        """
        statuses: list[BudgetStatus] = []
        breached_actions: list[EvidencePolicyAction] = []
        reasons: list[str] = []

        for dimension in BUDGET_DIMENSIONS:
            budget = policy.budgets.get(dimension)  # type: ignore[arg-type]
            if budget is None or not budget.enabled:
                continue
            value = self._budget_telemetry(dimension, evidence)

            state: Literal["none", "soft", "hard", "missing"]
            action: EvidencePolicyAction | None
            if value is None:
                if budget.hard_limit is not None:
                    state = "missing"
                    action = budget.on_hard
                elif budget.soft_limit is not None:
                    state = "missing"
                    action = budget.on_soft
                else:
                    state = "none"
                    action = None
                reason = (
                    f"budget '{dimension}' telemetry is missing for a declared "
                    f"limit; treated conservatively as not-within-budget "
                    f"(missing telemetry cannot silently satisfy a budget)."
                )
            elif budget.hard_limit is not None and value >= budget.hard_limit:
                state = "hard"
                action = budget.on_hard
                reason = (
                    f"budget '{dimension}' value {value} >= hard_limit "
                    f"{budget.hard_limit}; on_hard applies."
                )
            elif budget.soft_limit is not None and value >= budget.soft_limit:
                state = "soft"
                action = budget.on_soft
                reason = (
                    f"budget '{dimension}' value {value} >= soft_limit "
                    f"{budget.soft_limit}; on_soft applies."
                )
            else:
                state = "none"
                action = None
                reason = (
                    f"budget '{dimension}' value {value} within declared limit(s)."
                )

            statuses.append(
                BudgetStatus(
                    dimension=dimension,
                    measured_value=value,
                    soft_limit=budget.soft_limit,
                    hard_limit=budget.hard_limit,
                    state=state,
                    action=action,
                    reason=reason,
                )
            )
            if state in ("soft", "hard", "missing") and action is not None:
                breached_actions.append(action)
                reasons.append(reason)

        action = (
            max(breached_actions, key=lambda a: _ACTION_SEVERITY.get(a, 0))
            if breached_actions
            else None
        )
        return statuses, action, reasons

    def assess_policy_gate(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
        policy: BoundPolicyConfig,
    ) -> PolicyGateOutcome:
        """Assess the active-policy gate outcome.

        Computes the uncompensable blocker/budget gate from the active policy
        and the collected evidence. A failed hard gate (acceptance or risk
        blocker) or a breached budget forces a non-``ACCEPT`` action that
        :class:`~bound.policy.BoundPolicy` applies on top of the candidate
        decision — these cannot be offset by a high score or positive weighted
        signals. Resolved effective weights and the policy identity/hash are
        carried for the trace.

        Args:
            contract: The step contract (its required acceptance checks feed
                the effective-weights trace).
            evidence: The collected execution evidence.
            policy: The active policy governing the step.

        Returns:
            The :class:`PolicyGateOutcome` for this step.
        """
        acceptance_by_id: dict[str, list[CheckEvidence]] = {}
        for ce in evidence.acceptance:
            acceptance_by_id.setdefault(ce.check_id, []).append(ce)
        risk_by_id: dict[str, list[CheckEvidence]] = {}
        for ce in evidence.risks:
            risk_by_id.setdefault(ce.check_id, []).append(ce)

        blocker_actions: list[EvidencePolicyAction] = []
        blocker_reasons: list[str] = []
        blocker_failed = False

        for gate in policy.acceptance_checks:
            evs = acceptance_by_id.get(gate.id, [])
            failed, action, reason = self._classify_gate(gate, evs)
            if failed:
                blocker_failed = True
                blocker_reasons.append(reason)
                if action is not None:
                    blocker_actions.append(action)

        for gate in policy.risk_checks:
            evs = risk_by_id.get(gate.id, [])
            failed, action, reason = self._classify_gate(gate, evs)
            if failed:
                blocker_failed = True
                blocker_reasons.append(reason)
                if action is not None:
                    blocker_actions.append(action)

        blocker_action = (
            max(blocker_actions, key=lambda a: _ACTION_SEVERITY.get(a, 0))
            if blocker_actions
            else None
        )

        budget_status, budget_action, budget_reasons = self._assess_budgets(
            policy, evidence
        )
        budget_breached = budget_action is not None

        return PolicyGateOutcome(
            blocker_failed=blocker_failed,
            blocker_action=blocker_action,
            blocker_reasons=blocker_reasons,
            budget_breached=budget_breached,
            budget_action=budget_action,
            budget_reasons=budget_reasons,
            budget_status=budget_status,
            effective_weights=self._collect_effective_weights(contract, policy),
            policy_id=policy.policy.id,
            policy_version=policy.policy.version,
            policy_hash=compute_policy_hash(policy),
        )

    # ------------------------------------------------------------------
    # Reasoning
    # ------------------------------------------------------------------

    @staticmethod
    def _render_reasoning(
        acceptance: float,
        influence: float,
        risk: float,
        cost: float,
    ) -> str:
        """Build a structured, human-readable summary of every rule and value.

        This lets an :class:`EvaluationScores` instance explain itself even
        without the surrounding result, complementing :attr:`provenance`.

        Args:
            acceptance: The computed acceptance ``A``.
            influence: The computed influence ``I``.
            risk: The computed risk ``R``.
            cost: The computed cost ``C``.

        Returns:
            A multi-line string documenting each dimension's rule and value.
        """
        return (
            "v0.3 reference heuristic (NOT scientifically calibrated).\n"
            f"Acceptance A={acceptance:.4f}: passed_required / total_required. "
            "Each required AcceptanceCheck is reconciled against CheckEvidence "
            "by id; a required check with no matching evidence counts as FAILED "
            "(never silently passing). Optional checks are advisory only.\n"
            f"Risk R={risk:.4f}: min(1.0, Σ contributions) — each violated "
            "RiskCheck contributes its severity (a check with no evidence is "
            "treated conservatively as violated), plus unexpected_artifacts "
            "(indicator 1.0 when non-empty) and rollback_available is False "
            "(indicator 1.0). rollback_available None is skipped.\n"
            f"Cost C={cost:.4f}: mean of available budget dimensions, each "
            "min(actual/max, 1.0) with the cap-zero rule. Unmeasured telemetry "
            "for a declared dimension is conservatively saturated to 1.0. No "
            "budget → C=0.0.\n"
            f"Influence I={influence:.4f}: v0.3 does not derive downstream "
            "influence from contract evidence; honest DEFAULTED value when no "
            "evidence source exists (never presented as VERIFIED).\n"
            "Per-dimension ScoreEvidence provenance is available via "
            "ContractEvaluator.provenance."
        )

