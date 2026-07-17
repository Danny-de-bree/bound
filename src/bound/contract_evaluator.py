from __future__ import annotations

from bound.contracts import StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.models import EvaluationScores, ScoreEvidence

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

    def __init__(self, *, influence: float | None = None) -> None:
        """Store the optional influence override and clear provenance.

        Args:
            influence: Optional externally-supplied downstream influence
                ``I ∈ [-1, 1]``. When ``None`` (the default) influence is set to
                ``0.0`` with an explicit honesty note in the provenance. When
                supplied, it is used verbatim (Pydantic validates the range on
                :class:`~bound.models.EvaluationScores`).
        """
        self._influence_override = influence
        self._provenance: dict[str, list[ScoreEvidence]] = {}


    @property
    def influence(self) -> float | None:
        """The externally-supplied influence override, or ``None`` for default."""
        return self._influence_override

    @property
    def provenance(self) -> dict[str, list[ScoreEvidence]]:
        """Per-dimension evidence from the most recent :meth:`evaluate` call.

        Returns a dict keyed by ``"acceptance"``, ``"influence"``, ``"risk"``,
        ``"cost"``. Each value is the list of :class:`ScoreEvidence` that
        produced that dimension, so a consumer can answer "why is ``A`` what it
        is?". Returns an empty dict before :meth:`evaluate` has been called.
        """
        return self._provenance

    def evaluate(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
    ) -> EvaluationScores:
        """Derive :class:`EvaluationScores` from a contract and its evidence.

        The computation is pure and deterministic: the same ``contract`` and
        ``evidence`` always yield identical scores, with no network or model
        dependency. Per-dimension evidence is available via :attr:`provenance`.

        Args:
            contract: The :class:`~bound.contracts.StepContract` whose declared
                acceptance checks, risk checks, and budget scope the scoring.
            evidence: The :class:`~bound.evidence.ExecutionEvidence` observed
                after the step executed. Unknown ``check_id`` values are
                allowed; they are simply ignored during contract reconciliation.

        Returns:
            The :class:`EvaluationScores` (``A``, ``I``, ``R``, ``C``) plus a
            structured ``reasoning`` summary. Per-dimension evidence is available
            via :attr:`provenance`. This never produces a BOUND decision; that
            remains the policy's responsibility.
        """
        acceptance, acceptance_evidence = self._acceptance(contract, evidence)
        risk, risk_evidence = self._risk(contract, evidence)
        cost, cost_evidence = self._cost(contract, evidence)
        influence, influence_evidence = self._influence()

        self._provenance = {
            "acceptance": acceptance_evidence,
            "influence": influence_evidence,
            "risk": risk_evidence,
            "cost": cost_evidence,
        }

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

        ``C`` is the mean of the available dimensions only. ``retry_count`` and
        ``tool_call_count`` are always measured (ints ≥ 0); ``token_usage`` and
        ``runtime_seconds`` may be ``None`` (unmeasured). When a declared budget
        dimension's telemetry is unmeasured, it is conservatively saturated to
        :data:`_UNMEASURED_COST_SATURATION` because budget compliance cannot be
        confirmed (a declared constraint with no confirming evidence is never
        silently treated as cheap).

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

        # (source, raw_value, max, normalized, description_note)
        terms: list[tuple[str, float, float, float, str]] = []

        if budget.max_retries is not None:
            raw = float(evidence.retry_count)
            mx = float(budget.max_retries)
            norm = _normalize_capped(raw, mx)
            terms.append(
                ("retry_cost", raw, mx, norm, f"normalized=min({raw}/{mx}, 1.0)={norm:.4f}"),
            )

        if budget.max_tool_calls is not None:
            raw = float(evidence.tool_call_count)
            mx = float(budget.max_tool_calls)
            norm = _normalize_capped(raw, mx)
            terms.append(
                ("tool_cost", raw, mx, norm, f"normalized=min({raw}/{mx}, 1.0)={norm:.4f}"),
            )

        if budget.max_tokens is not None:
            mx = float(budget.max_tokens)
            if evidence.token_usage is not None:
                raw = float(evidence.token_usage)
                norm = _normalize_capped(raw, mx)
                note = f"normalized=min({raw}/{mx}, 1.0)={norm:.4f}"
            else:
                raw = 0.0
                norm = _UNMEASURED_COST_SATURATION
                note = (
                    "telemetry unmeasured (token_usage=None); conservatively "
                    f"saturated to {norm:.4f} because compliance with the token "
                    "budget cannot be confirmed"
                )
            terms.append(("token_cost", raw, mx, norm, note))

        if budget.max_runtime_seconds is not None:
            mx = float(budget.max_runtime_seconds)
            if evidence.runtime_seconds is not None:
                raw = float(evidence.runtime_seconds)
                norm = _normalize_capped(raw, mx)
                note = f"normalized=min({raw}/{mx}, 1.0)={norm:.4f}"
            else:
                raw = 0.0
                norm = _UNMEASURED_COST_SATURATION
                note = (
                    "telemetry unmeasured (runtime_seconds=None); conservatively "
                    f"saturated to {norm:.4f} because compliance with the runtime "
                    "budget cannot be confirmed"
                )
            terms.append(("runtime_cost", raw, mx, norm, note))

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
        cost = sum(norm for _, _, _, norm, _ in terms) / count
        records = [
            ScoreEvidence(
                source=source,
                value=raw,
                contribution=norm / count,
                description=(
                    f"{note}; contributes {norm / count:.4f} to the mean of "
                    f"{count} available cost dimension(s)."
                ),
            )
            for source, raw, _mx, norm, note in terms
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
        the default is ``0.0`` with an explicit honesty note. A caller may
        instead supply influence externally at construction.

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
            ),
        ]

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
            "influence from contract evidence; honesty over invented "
            "sophistication.\n"
            "Per-dimension ScoreEvidence provenance is available via "
            "ContractEvaluator.provenance."
        )

