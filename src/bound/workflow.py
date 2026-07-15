"""Deterministic coding-workflow evaluator (Phases 6 & 7).

The :class:`CodingWorkflowEvaluator` is the first BOUND evaluator that derives
the four score dimensions from *real, deterministic* evidence instead of asking
an LLM. It consumes provider-agnostic
:class:`~bound.models.CodingWorkflowSignals` captured from a coding-agent run
(test pass rate, lint/type-check status, retry counts, tool calls, token usage,
file changes, ...) and maps them to :class:`~bound.models.EvaluationScores` using
transparent, fully-documented rules.

Every mapping is marked as a **v0.2 reference heuristic**: the constants are
deliberate, visible policy knobs, *not* scientifically calibrated weights. The
point of v0.2 is to prove BOUND inputs can be derived without an LLM and to make
the derivation auditable through :class:`~bound.models.ScoreEvidence` provenance
(Phase 7), so a consumer can answer "why is ``A = 0.85``?".

The evaluator implements the :class:`~bound.evaluator.Evaluator` Protocol
(structural: it exposes ``evaluate(action) -> EvaluationScores``). It stores the
signals at construction time and performs no network access and imports no LLM
SDK; once the signals are supplied the scores are fully deterministic.

Provenance contract
-------------------

:class:`~bound.models.EvaluationScores` has no provenance field, so this
evaluator exposes the per-dimension evidence two complementary ways:

1. ``CodingWorkflowEvaluator.provenance`` — a ``dict[str, list[ScoreEvidence]]``
   keyed by ``"acceptance"``, ``"influence"``, ``"risk"``, ``"cost"``, populated
   on every :meth:`evaluate` call. A policy or experiment harness can read this
   and attach it to :attr:`EvaluationResult.provenance`.
2. ``EvaluationScores.reasoning`` — a short, structured, human-readable summary of
   every rule and the resulting values, so the scores are self-explaining even
   without the result object.
"""

from __future__ import annotations

from bound.models import (
    Action,
    CodingWorkflowSignals,
    EvaluationScores,
    ScoreEvidence,
    WorkflowNormalization,
)

# ---------------------------------------------------------------------------
# v0.2 reference-heuristic constants (NOT scientifically calibrated)
#
# These are deliberate, visible policy knobs documented so the risk rule can be
# audited and challenged. They are NOT tuned weights.
# ---------------------------------------------------------------------------

#: Any unexpected file change counts as a full surprise-risk indicator. An
#: "unexpected" change is, by definition, not anticipated, so a single
#: occurrence is treated as a maximum surprise signal rather than a graded one.
_UNEXPECTED_CHANGE_INDICATOR = 1.0

#: Number of changed files treated as a "large change surface". At or above this
#: value the blast-radius indicator saturates at 1.0; below it the indicator
#: scales linearly as ``files_changed / _LARGE_CHANGE_SURFACE_FILES``.
_LARGE_CHANGE_SURFACE_FILES = 10


def _normalize_capped(value: float, cap: float) -> float:
    """Normalize ``value`` against ``cap`` into ``[0.0, 1.0]``.

    A cap of ``0`` means "any nonzero value is already over budget": the result
    is ``1.0`` when ``value > 0`` and ``0.0`` when ``value == 0``. This keeps the
    normalization fully configuration-driven while avoiding a division by zero.

    Args:
        value: The raw observed value (non-negative).
        cap: The configured :class:`WorkflowNormalization` ceiling.

    Returns:
        ``min(value / cap, 1.0)`` (or the cap-zero rule described above).
    """
    if cap <= 0:
        return 1.0 if value > 0 else 0.0
    return min(value / cap, 1.0)


class CodingWorkflowEvaluator:
    """Deterministic evaluator mapping workflow signals to BOUND scores.

    Implements the :class:`~bound.evaluator.Evaluator` Protocol. It holds a
    :class:`~bound.models.CodingWorkflowSignals` instance (plus a
    :class:`~bound.models.WorkflowNormalization`) and, on each
    :meth:`evaluate` call, derives :class:`~bound.models.EvaluationScores` with
    fully documented rules.

    All mappings are **v0.2 reference heuristics** — not scientifically
    calibrated:

    * **Acceptance (A)** ``∈ [0, 1]``: the mean of the *available* completion
      signals (``test_pass_rate``, ``required_checks_passed``,
      ``lint_passed`` → ``1.0``/``0.0``, ``type_check_passed`` →
      ``1.0``/``0.0``). Unavailable (``None``) signals are ignored rather than
      defaulted to zero. Raises :class:`ValueError` when no acceptance evidence
      is available at all.
    * **Risk (R)** ``∈ [0, 1]``: the mean of the *available* risk indicators —
      any ``unexpected_files_changed > 0``, ``rollback_available is False``, a
      large change surface via ``files_changed`` (graded against
      :data:`_LARGE_CHANGE_SURFACE_FILES`), and failed checks (``1.0 - A``,
      reusing the acceptance gap). Every constant is documented above.
    * **Cost (C)** ``∈ [0, 1]``: the mean of the *available* normalized terms
      (``retry_count``, ``tool_call_count``, ``token_usage``,
      ``execution_time_seconds``), each ``min(value / cap, 1.0)`` against the
      configured :class:`WorkflowNormalization` caps. No hidden constants.
    * **Influence (I)** ``∈ [-1, 1]``: for v0.2, ``0.0`` by default with an
      explicit explanation, or a value supplied externally at construction.
      Honesty is preferred over invented sophistication.

    Example:
        >>> from bound.models import Action, CodingWorkflowSignals
        >>> from bound.workflow import CodingWorkflowEvaluator
        >>> signals = CodingWorkflowSignals(test_pass_rate=1.0, lint_passed=True)
        >>> evaluator = CodingWorkflowEvaluator(signals)
        >>> scores = evaluator.evaluate(Action(description="Ship", goal="Release"))
        >>> scores.acceptance
        1.0
        >>> "acceptance" in evaluator.provenance
        True

    Attributes:
        signals: The :class:`CodingWorkflowSignals` being scored.
        normalization: The :class:`WorkflowNormalization` caps used for cost.
    """

    def __init__(
        self,
        signals: CodingWorkflowSignals,
        normalization: WorkflowNormalization | None = None,
        *,
        influence: float | None = None,
    ) -> None:
        """Store the signals, normalization caps, and optional influence.

        Args:
            signals: The provider-agnostic workflow signals to score. Held by
                reference; the same object is reused on every evaluation so
                results are reproducible.
            normalization: The caps used to normalize cost terms. Defaults to a
                fresh :class:`WorkflowNormalization` (v0.2 defaults) when
                ``None``.
            influence: Optional externally-supplied downstream influence
                ``I ∈ [-1, 1]``. When ``None`` (the default) influence is set
                to ``0.0`` with an explicit honesty note in the provenance.
        """
        self._signals = signals
        self._normalization = normalization or WorkflowNormalization()
        self._influence_override = influence
        self._provenance: dict[str, list[ScoreEvidence]] = {}

    @property
    def signals(self) -> CodingWorkflowSignals:
        """The :class:`CodingWorkflowSignals` this evaluator scores."""
        return self._signals

    @property
    def normalization(self) -> WorkflowNormalization:
        """The :class:`WorkflowNormalization` caps used to normalize cost."""
        return self._normalization

    @property
    def provenance(self) -> dict[str, list[ScoreEvidence]]:
        """Per-dimension evidence from the most recent :meth:`evaluate` call.

        Returns a dict keyed by ``"acceptance"``, ``"influence"``, ``"risk"``,
        ``"cost"``. Each value is the list of :class:`ScoreEvidence` that
        produced that dimension, so a consumer can answer "why is ``A`` what it
        is?". Returns an empty dict before :meth:`evaluate` has been called.
        """
        return self._provenance

    def evaluate(self, action: Action) -> EvaluationScores:  # noqa: ARG002
        """Derive :class:`EvaluationScores` from the stored workflow signals.

        The ``action`` is accepted to satisfy the :class:`Evaluator` Protocol but
        is not used for scoring — all evidence comes from the signals supplied at
        construction. The computation is pure: same signals → same scores, with no
        network or model dependency.

        Args:
            action: The proposed :class:`Action`. Unused for scoring but required
                by the :class:`Evaluator` Protocol.

        Returns:
            The :class:`EvaluationScores` (``A``, ``I``, ``R``, ``C``) plus a
            structured ``reasoning`` summary. Per-dimension evidence is available
            via :attr:`provenance`.

        Raises:
            ValueError: If no acceptance evidence is available (none of
                ``test_pass_rate``, ``required_checks_passed``, ``lint_passed``,
                ``type_check_passed`` are set on the signals).
        """
        acceptance, acceptance_evidence = self._acceptance()
        risk, risk_evidence = self._risk(acceptance)
        cost, cost_evidence = self._cost()
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
    # Dimension rules (v0.2 reference heuristic — NOT scientifically calibrated)
    # ------------------------------------------------------------------

    def _acceptance(self) -> tuple[float, list[ScoreEvidence]]:
        """Mean of the available completion signals.

        Booleans map ``True → 1.0`` / ``False → 0.0``. Missing (``None``)
        signals are ignored rather than treated as zero, so an unknown gate
        never drags acceptance down. Raises when no gate is available at all.

        Returns:
            A ``(acceptance, evidence)`` tuple.
        """
        s = self._signals
        entries: list[tuple[str, float]] = []

        if s.test_pass_rate is not None:
            entries.append(("test_pass_rate", float(s.test_pass_rate)))
        if s.required_checks_passed is not None:
            entries.append(("required_checks_passed", float(s.required_checks_passed)))
        if s.lint_passed is not None:
            entries.append(("lint_passed", 1.0 if s.lint_passed else 0.0))
        if s.type_check_passed is not None:
            entries.append(("type_check_passed", 1.0 if s.type_check_passed else 0.0))

        if not entries:
            raise ValueError(
                "no acceptance evidence available: provide at least one of "
                "test_pass_rate, required_checks_passed, lint_passed, "
                "type_check_passed in CodingWorkflowSignals."
            )

        count = len(entries)
        total = sum(value for _, value in entries)
        acceptance = total / count
        evidence = [
            ScoreEvidence(
                source=name,
                value=value,
                contribution=value / count,
                description=(
                    f"{name}={value}; contributes {value / count:.4f} to the "
                    f"mean of {count} available acceptance signal(s)."
                ),
            )
            for name, value in entries
        ]
        return acceptance, evidence


    def _risk(self, acceptance: float) -> tuple[float, list[ScoreEvidence]]:
        """Mean of the available risk indicators.

        Each indicator is normalized to ``[0.0, 1.0]``:

        * ``unexpected_files_changed``: ``1.0`` when ``> 0`` else ``0.0``
          (any unexpected change is a full surprise signal).
        * ``rollback_available``: ``1.0`` when ``False`` (no clean rollback)
          else ``0.0``.
        * ``files_changed``: ``min(value / _LARGE_CHANGE_SURFACE_FILES, 1.0)``
          (graded blast radius).
        * failed checks: ``1.0 - acceptance`` — reuses the acceptance gap so the
          same quality gates are not scored a second time with hidden constants.

        Acceptance is computed first (and raises if absent), so the failed-checks
        indicator is always available, keeping ``R ≥ 1`` term and thus within
        ``[0, 1]`` without a hidden floor.

        Args:
            acceptance: The already-computed acceptance ``A`` (used for the
                failed-checks indicator).

        Returns:
            A ``(risk, evidence)`` tuple.
        """
        s = self._signals
        indicators: list[tuple[str, float, float]] = []  # (source, raw_value, indicator)

        if s.unexpected_files_changed is not None:
            raw = float(s.unexpected_files_changed)
            indicator = _UNEXPECTED_CHANGE_INDICATOR if raw > 0 else 0.0
            indicators.append(("unexpected_files_changed", raw, indicator))

        if s.rollback_available is not None:
            # Raw value encodes availability (1.0 = available, 0.0 = unavailable);
            # the risk indicator rises when rollback is NOT available.
            raw = 1.0 if s.rollback_available else 0.0
            indicator = 0.0 if s.rollback_available else 1.0
            indicators.append(("rollback_available", raw, indicator))

        if s.files_changed is not None:
            raw = float(s.files_changed)
            indicator = min(raw / _LARGE_CHANGE_SURFACE_FILES, 1.0)
            indicators.append(("large_change_surface(files_changed)", raw, indicator))

        # Failed-checks indicator always available (acceptance computed first).
        failed_checks = 1.0 - acceptance
        indicators.append(("failed_checks(1 - A)", failed_checks, failed_checks))

        count = len(indicators)
        risk = sum(indicator for _, _, indicator in indicators) / count
        evidence = [
            ScoreEvidence(
                source=source,
                value=raw,
                contribution=indicator / count,
                description=(
                    f"indicator={indicator:.4f}; contributes "
                    f"{indicator / count:.4f} to the mean of {count} available "
                    f"risk indicator(s)."
                ),
            )
            for source, raw, indicator in indicators
        ]
        return risk, evidence


    def _cost(self) -> tuple[float, list[ScoreEvidence]]:
        """Mean of the available normalized resource terms.

        Each term is ``min(value / cap, 1.0)`` against the matching
        :class:`WorkflowNormalization` cap (with the cap-zero rule from
        :func:`_normalize_capped`). ``retry_count`` and ``tool_call_count`` are
        always present (they default to ``0``); ``token_usage`` and
        ``execution_time_seconds`` are skipped when ``None``.

        Returns:
            A ``(cost, evidence)`` tuple.
        """
        s = self._signals
        n = self._normalization
        # (source, raw_value, cap, normalized)
        terms: list[tuple[str, float, float, float]] = []

        terms.append(
            (
                "retry_count",
                float(s.retry_count),
                float(n.max_expected_retries),
                _normalize_capped(s.retry_count, n.max_expected_retries),
            )
        )
        terms.append(
            (
                "tool_call_count",
                float(s.tool_call_count),
                float(n.max_expected_tool_calls),
                _normalize_capped(s.tool_call_count, n.max_expected_tool_calls),
            )
        )
        if s.token_usage is not None:
            terms.append(
                (
                    "token_usage",
                    float(s.token_usage),
                    float(n.max_expected_tokens),
                    _normalize_capped(s.token_usage, n.max_expected_tokens),
                )
            )
        if s.execution_time_seconds is not None:
            terms.append(
                (
                    "execution_time_seconds",
                    float(s.execution_time_seconds),
                    float(n.max_expected_runtime_seconds),
                    _normalize_capped(
                        s.execution_time_seconds, n.max_expected_runtime_seconds
                    ),
                )
            )

        count = len(terms)
        cost = sum(normalized for _, _, _, normalized in terms) / count
        evidence = [
            ScoreEvidence(
                source=source,
                value=raw,
                contribution=normalized / count,
                description=(
                    f"normalized={normalized:.4f} (value={raw}, cap={cap}); "
                    f"contributes {normalized / count:.4f} to the mean of {count} "
                    f"available cost term(s)."
                ),
            )
            for source, raw, cap, normalized in terms
        ]
        return cost, evidence

    def _influence(self) -> tuple[float, list[ScoreEvidence]]:
        """Resolve downstream influence (v0.2: honest default or external).

        For v0.2 no downstream-influence evidence is derivable from workflow
        signals, so the default is ``0.0`` with an explicit honesty note. A caller
        may instead supply influence externally at construction.

        Returns:
            A ``(influence, evidence)`` tuple.
        """
        if self._influence_override is not None:
            influence = float(self._influence_override)
            evidence = [
                ScoreEvidence(
                    source="external",
                    value=influence,
                    contribution=influence,
                    description=(
                        "influence supplied externally at construction; v0.2 does "
                        "not derive downstream influence from workflow signals."
                    ),
                )
            ]
            return influence, evidence

        evidence = [
            ScoreEvidence(
                source="default",
                value=0.0,
                contribution=0.0,
                description=(
                    "v0.2 sets influence=0.0 by default: no downstream-influence "
                    "evidence is derivable from workflow signals. Honesty over "
                    "invented sophistication."
                ),
            )
        ]
        return 0.0, evidence

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
            "v0.2 reference heuristic (NOT scientifically calibrated).\n"
            f"Acceptance A={acceptance:.4f}: mean of available completion signals "
            "(test_pass_rate, required_checks_passed, lint_passed, "
            "type_check_passed); unavailable signals ignored.\n"
            f"Risk R={risk:.4f}: mean of available risk indicators "
            "(unexpected_files_changed>0, rollback_available is False, large "
            "change surface via files_changed, failed checks = 1 - A).\n"
            f"Cost C={cost:.4f}: mean of available normalized terms "
            "(retry_count, tool_call_count, token_usage, execution_time_seconds) "
            "each min(value/cap, 1.0) against WorkflowNormalization caps.\n"
            f"Influence I={influence:.4f}: v0.2 does not derive downstream "
            "influence from workflow signals; honesty over invented "
            "sophistication.\n"
            "Per-signal ScoreEvidence provenance is available via "
            "CodingWorkflowEvaluator.provenance."
        )

