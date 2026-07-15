"""Unit tests for the deterministic coding-workflow evaluator (Phases 6 & 7).

These tests pin the :class:`bound.workflow.CodingWorkflowEvaluator` contract
mandated by the v0.2 TODO ("Workflow signals" + "Evaluator" sections):

* valid signals map to the documented A / I / R / C values,
* invalid signal ranges are rejected by the model (Pydantic does the heavy
  lifting here),
* missing optional signals are *ignored* rather than defaulted to zero,
* the absence of any acceptance evidence raises a clear error,
* the same inputs always produce the same outputs (determinism),
* the evaluator pulls in no network or LLM SDK,
* and every score is backed by explicit :class:`ScoreEvidence` provenance so a
  consumer can answer "why is ``A = 0.85``?".

All mappings are v0.2 reference heuristics — these tests assert the *documented*
behaviour of those heuristics, not scientific calibration.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bound.evaluator import Evaluator
from bound.models import (
    Action,
    CodingWorkflowSignals,
    EvaluationScores,
    WorkflowNormalization,
)
from bound.workflow import CodingWorkflowEvaluator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACTION = Action(description="Implement feature X", goal="Complete issue #123")

#: A fully-populated signal set used to assert the end-to-end happy path.
_FULL_SIGNALS = CodingWorkflowSignals(
    test_pass_rate=1.0,
    required_checks_passed=0.75,
    lint_passed=True,
    type_check_passed=True,
    retry_count=2,
    tool_call_count=14,
    token_usage=40000,
    execution_time_seconds=180.0,
    files_changed=3,
    unexpected_files_changed=1,
    rollback_available=False,
)


# ---------------------------------------------------------------------------
# Protocol & happy path
# ---------------------------------------------------------------------------


def test_evaluator_satisfies_evaluator_protocol() -> None:
    """CodingWorkflowEvaluator structurally satisfies the Evaluator Protocol.

    The policy relies on runtime-checkable structural typing, so the workflow
    evaluator must slot in wherever an Evaluator is expected without subclassing
    — this is what keeps the deterministic core provider-agnostic.
    """
    evaluator = CodingWorkflowEvaluator(_FULL_SIGNALS)

    assert isinstance(evaluator, Evaluator)


def test_valid_signals_produce_correct_scores() -> None:
    """A fully-populated signal set maps to the documented A/I/R/C values.

    Pins the v0.2 reference heuristic end-to-end so a future refactor that
    silently changes a coefficient is caught here:

    * A = mean(1.0, 0.75, 1.0, 1.0) = 0.9375
    * R = mean(1.0 unexpected, 1.0 no-rollback, 0.3 surface, 0.0625 failed) ≈ 0.5906
    * C = mean(0.4, 0.28, 0.4, 0.05) = 0.2825
    * I = 0.0 (v0.2 honest default)
    """
    evaluator = CodingWorkflowEvaluator(_FULL_SIGNALS)

    scores = evaluator.evaluate(_ACTION)

    assert scores.acceptance == pytest.approx(0.9375)
    assert scores.influence == pytest.approx(0.0)
    assert scores.risk == pytest.approx(0.590625)
    assert scores.cost == pytest.approx(0.2825)


def test_evaluate_returns_evaluation_scores_with_reasoning() -> None:
    """evaluate() returns EvaluationScores carrying a structured reasoning note.

    Reasoning is the second provenance channel (the first being
    ``evaluator.provenance``), so the scores must explain themselves standalone.
    """
    evaluator = CodingWorkflowEvaluator(_FULL_SIGNALS)

    scores = evaluator.evaluate(_ACTION)

    assert isinstance(scores, EvaluationScores)
    assert scores.reasoning is not None
    assert "v0.2 reference heuristic" in scores.reasoning


# ---------------------------------------------------------------------------
# Acceptance mapping
# ---------------------------------------------------------------------------


def test_acceptance_is_mean_of_available_completion_signals() -> None:
    """Acceptance averages only the signals that are present, then floors by breadth.

    With test_pass_rate=0.5 and lint_passed=True the raw mean is
    (0.5 + 1.0) / 2 = 0.75, but only 2 of 4 gates are available, so the
    evidence-breadth floor is 2/4 = 0.5 and ``A = 0.75 × 0.5 = 0.375``. The
    absent gates (type-check, required checks) must not pull the mean down, but
    their absence *does* reduce confidence via the breadth floor.
    """
    signals = CodingWorkflowSignals(test_pass_rate=0.5, lint_passed=True)

    scores = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    assert scores.acceptance == pytest.approx(0.375)


def test_lint_false_counts_as_zero_not_ignored() -> None:
    """A False gate is *evidence of failure*, not missing evidence.

    This is the key edge case: ``lint_passed=False`` must contribute 0.0 to the
    mean (lowering acceptance) rather than being skipped like a ``None`` gate.
    With test_pass_rate=1.0 and lint_passed=False the raw mean is 0.5 across 2 of
    4 gates, so the breadth-floored acceptance is ``0.5 × (2/4) = 0.25``.
    """
    signals = CodingWorkflowSignals(test_pass_rate=1.0, lint_passed=False)

    scores = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    assert scores.acceptance == pytest.approx(0.25)

# ---------------------------------------------------------------------------
# Evidence-breadth confidence floor (blind-spot fix on A)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gates", "expected_a"),
    [
        ({"test_pass_rate": 1.0}, 0.25),
        ({"test_pass_rate": 1.0, "lint_passed": True}, 0.5),
        (
            {"test_pass_rate": 1.0, "lint_passed": True, "type_check_passed": True},
            0.75,
        ),
        (
            {
                "test_pass_rate": 1.0,
                "lint_passed": True,
                "type_check_passed": True,
                "required_checks_passed": 1.0,
            },
            1.0,
        ),
    ],
)
def test_acceptance_evidence_breadth_floor_scales_with_available_signals(
    gates: dict[str, object], expected_a: float
) -> None:
    """A = (mean of available signals) × (n_available / 4).

    With every available gate green the raw mean is 1.0, so acceptance equals the
    evidence-breadth floor itself: 1/4 → 0.25, 2/4 → 0.5, 3/4 → 0.75, 4/4 → 1.0.
    This is the core blind-spot fix: thin evidence can no longer masquerade as
    full confidence.
    """
    signals = CodingWorkflowSignals(**gates)  # type: ignore[arg-type]

    scores = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    assert scores.acceptance == pytest.approx(expected_a)


def test_single_lint_passed_no_longer_yields_max_acceptance() -> None:
    """A single lint_passed=True must NOT read as A=1.0 (blind-spot fix).

    Before the evidence-breadth floor, one green gate produced maximum
    confidence because missing signals were ignored rather than counted as
    absent. Now ``lint_passed=True`` alone gives ``A = 1.0 × (1/4) = 0.25``.
    """
    signals = CodingWorkflowSignals(lint_passed=True)

    scores = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    assert scores.acceptance == pytest.approx(0.25)
    assert scores.acceptance < 1.0


def test_acceptance_unchanged_when_all_four_signals_available() -> None:
    """All four completion signals available → breadth floor is 1.0 (backward compat).

    With the full gate set the floor is a no-op, so ``A`` equals the plain mean —
    the pre-mitigation behaviour is preserved for well-evidenced runs.
    """
    signals = CodingWorkflowSignals(
        test_pass_rate=1.0,
        required_checks_passed=0.75,
        lint_passed=True,
        type_check_passed=True,
    )

    scores = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    # mean(1.0, 0.75, 1.0, 1.0) = 0.9375; breadth = 4/4 = 1.0 -> unchanged.
    assert scores.acceptance == pytest.approx(0.9375)


def test_acceptance_provenance_records_evidence_breadth() -> None:
    """Acceptance provenance carries an ``evidence_breadth`` entry describing the floor.

    The breadth entry's value is ``n_available / 4`` and its description spells
    out ``A = mean × breadth``, so the blind-spot fix is auditable through
    provenance. With all four gates it equals 1.0 (no penalty).
    """
    thin = CodingWorkflowEvaluator(CodingWorkflowSignals(lint_passed=True))
    thin.evaluate(_ACTION)
    breadth = next(
        e for e in thin.provenance["acceptance"] if e.source == "evidence_breadth"
    )
    assert breadth.value == pytest.approx(0.25)
    assert breadth.contribution == pytest.approx(0.25)
    assert "evidence_breadth" in breadth.description

    full = CodingWorkflowEvaluator(
        CodingWorkflowSignals(
            test_pass_rate=1.0,
            required_checks_passed=1.0,
            lint_passed=True,
            type_check_passed=True,
        )
    )
    full.evaluate(_ACTION)
    full_breadth = next(
        e for e in full.provenance["acceptance"] if e.source == "evidence_breadth"
    )
    assert full_breadth.value == pytest.approx(1.0)



# ---------------------------------------------------------------------------
# Missing / absent evidence
# ---------------------------------------------------------------------------


def test_missing_optional_signals_are_ignored() -> None:
    """Optional signals that are None are skipped, never defaulted to zero.

    With only test_pass_rate=0.5 plus the always-present cost counters: the raw
    acceptance is 0.5 (single gate), but the evidence-breadth floor is 1/4 = 0.25,
    so ``A = 0.5 × 0.25 = 0.125``. Risk then reduces to the failed-checks gap
    (1 - 0.125 = 0.875), and cost averages retry_count and tool_call_count only
    (tokens/runtime absent). This proves "ignore unavailable" rather than "treat
    missing as failing" — a ``None`` gate is excluded from the *mean*, though its
    absence now lowers confidence via the breadth floor (blind-spot fix).
    """
    signals = CodingWorkflowSignals(
        test_pass_rate=0.5,
        retry_count=1,
        tool_call_count=10,
    )

    scores = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    assert scores.acceptance == pytest.approx(0.125)
    # No unexpected-file / rollback / change-surface evidence -> risk = 1 - A.
    assert scores.risk == pytest.approx(0.875)
    # cost = mean(1/5, 10/50) = mean(0.2, 0.2) = 0.2 (tokens/runtime absent).
    assert scores.cost == pytest.approx(0.2)


def test_no_acceptance_evidence_raises_clear_error() -> None:
    """With no completion gates at all, evaluate() raises a clear ValueError.

    Acceptance is the one dimension that genuinely cannot be defaulted; surfacing
    a loud error (rather than a misleading 0.0) keeps the pipeline honest.
    """
    signals = CodingWorkflowSignals(retry_count=2, tool_call_count=5)

    evaluator = CodingWorkflowEvaluator(signals)

    with pytest.raises(ValueError, match="acceptance"):
        evaluator.evaluate(_ACTION)


def test_provenance_empty_before_first_evaluate() -> None:
    """provenance is empty until evaluate() has actually run.

    Guards the contract that provenance reflects the *last* evaluation: callers
    must not read stale or fabricated evidence from a fresh evaluator.
    """
    evaluator = CodingWorkflowEvaluator(_FULL_SIGNALS)

    assert evaluator.provenance == {}


# ---------------------------------------------------------------------------
# Invalid ranges (Pydantic does the heavy lifting)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"test_pass_rate": 1.5},
        {"test_pass_rate": -0.1},
        {"required_checks_passed": 1.2},
        {"required_checks_passed": -0.5},
        {"retry_count": -1},
        {"tool_call_count": -3},
        {"token_usage": -5},
        {"execution_time_seconds": -1.0},
        {"files_changed": -1},
        {"unexpected_files_changed": -2},
    ],
)
def test_invalid_signal_ranges_raise_validation_error(kwargs: dict) -> None:
    """Out-of-range signal values are rejected at construction by Pydantic.

    Each case encodes a BOUND range contract (rates in [0, 1], counts
    non-negative). Construction must fail fast so an evaluator never scores from
    malformed evidence.
    """
    with pytest.raises(ValidationError):
        CodingWorkflowSignals(**kwargs)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_same_input_same_output_within_evaluator() -> None:
    """Repeated evaluate() calls on the same evaluator yield equal scores.

    Determinism is a core BOUND guarantee; the workflow evaluator must be a pure
    function of its signals so downstream calculations never vary between runs.
    """
    evaluator = CodingWorkflowEvaluator(_FULL_SIGNALS)

    first = evaluator.evaluate(_ACTION)
    second = evaluator.evaluate(_ACTION)

    assert first == second


def test_deterministic_two_evaluators_with_identical_signals() -> None:
    """Two evaluators built from equal signals produce identical scores.

    Belt-and-braces on top of the single-evaluator check: determinism must hold
    across independent constructions, not just repeated calls.
    """
    signals = CodingWorkflowSignals.model_validate(_FULL_SIGNALS.model_dump())

    a = CodingWorkflowEvaluator(_FULL_SIGNALS).evaluate(_ACTION)
    b = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    assert a == b


def test_deterministic_normalization_cost_value() -> None:
    """Cost is the documented mean of normalized terms (no hidden constants).

    With custom caps the expected cost is fully reconstructable from the signals,
    proving normalization is configuration-driven and deterministic.
    """
    norm = WorkflowNormalization(
        max_expected_retries=4,
        max_expected_tool_calls=100,
        max_expected_tokens=1000,
        max_expected_runtime_seconds=60.0,
    )
    signals = CodingWorkflowSignals(
        test_pass_rate=1.0,
        retry_count=2,          # 2/4 = 0.5
        tool_call_count=25,     # 25/100 = 0.25
        token_usage=300,        # 300/1000 = 0.3
        execution_time_seconds=30.0,  # 30/60 = 0.5
    )

    scores = CodingWorkflowEvaluator(signals, norm).evaluate(_ACTION)

    # mean(0.5, 0.25, 0.3, 0.5) = 1.55 / 4 = 0.3875
    assert scores.cost == pytest.approx(0.3875)


# ---------------------------------------------------------------------------
# Cost normalization (caps + cap-zero guard)
# ---------------------------------------------------------------------------


def test_cost_normalization_clamps_to_one() -> None:
    """Values above their cap saturate at 1.0 (never exceed the [0, 1] bound).

    EvaluationScores.cost is constrained to [0, 1]; the min(value/cap, 1.0)
    rule guarantees that even runaway usage cannot push cost out of range. Both
    always-present counters (retry_count, tool_call_count) are saturated here.
    """
    norm = WorkflowNormalization(max_expected_retries=5, max_expected_tool_calls=10)
    signals = CodingWorkflowSignals(
        test_pass_rate=1.0,
        retry_count=999,
        tool_call_count=999,
    )

    scores = CodingWorkflowEvaluator(signals, norm).evaluate(_ACTION)

    assert scores.cost == pytest.approx(1.0)


def test_cost_cap_zero_treats_nonzero_as_over_budget() -> None:
    """A cap of 0 means any nonzero usage is already over budget.

    Guards the division-by-zero branch in _normalize_capped: with retries capped
    at 0, three retries normalize to 1.0 (not a crash), while zero tool calls
    stay 0.0 — so cost = mean(1.0, 0.0) = 0.5.
    """
    norm = WorkflowNormalization(max_expected_retries=0)
    signals = CodingWorkflowSignals(test_pass_rate=1.0, retry_count=3, tool_call_count=0)
    evaluator = CodingWorkflowEvaluator(signals, norm)

    scores = evaluator.evaluate(_ACTION)

    assert scores.cost == pytest.approx(0.5)
    retry_term = next(
        e for e in evaluator.provenance["cost"] if e.source == "retry_count"
    )
    # Saturated normalized value of 1.0, split across the 2 available cost terms.
    assert retry_term.contribution == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Risk rule (each indicator documented and visible in code)
# ---------------------------------------------------------------------------


def _clean_risk_signals(**overrides: object) -> CodingWorkflowSignals:
    """Signals with a perfect acceptance gate and no risk evidence by default.

    Lets each risk test toggle exactly one indicator while keeping acceptance
    (and therefore the failed-checks indicator) fixed at zero, isolating the
    effect of a single rule.

    All four completion gates are populated and green so the evidence-breadth
    floor is ``1.0`` and ``A = 1.0`` (failed-checks = ``0.0``). Using a single
    gate here would now trigger the breadth floor (``A = 0.25``) and contaminate
    every risk value with a nonzero failed-checks term.
    """
    base: dict[str, object] = {
        "test_pass_rate": 1.0,
        "required_checks_passed": 1.0,
        "lint_passed": True,
        "type_check_passed": True,
    }
    base.update(overrides)
    return CodingWorkflowSignals(**base)  # type: ignore[arg-type]


def test_risk_unexpected_files_increase_risk() -> None:
    """Any unexpected file change raises risk (binary surprise indicator).

    With a perfect gate (A=1.0 → failed-checks=0.0) and no other risk evidence,
    zero unexpected files gives R=0.0 while ≥1 gives R=0.5 (mean of 1.0 and 0.0).
    """
    clean = CodingWorkflowEvaluator(_clean_risk_signals()).evaluate(_ACTION)
    surprised = CodingWorkflowEvaluator(
        _clean_risk_signals(unexpected_files_changed=5)
    ).evaluate(_ACTION)

    assert clean.risk == pytest.approx(0.0)
    assert surprised.risk == pytest.approx(0.5)
    assert surprised.risk > clean.risk


def test_risk_rollback_unavailable_increases_risk() -> None:
    """No clean rollback raises risk.

    rollback_available=True contributes 0.0; False contributes 1.0. With a
    perfect gate this is the only active indicator besides failed-checks=0.0.
    """
    safe = CodingWorkflowEvaluator(
        _clean_risk_signals(rollback_available=True)
    ).evaluate(_ACTION)
    unsafe = CodingWorkflowEvaluator(
        _clean_risk_signals(rollback_available=False)
    ).evaluate(_ACTION)

    assert safe.risk == pytest.approx(0.0)
    assert unsafe.risk == pytest.approx(0.5)
    assert unsafe.risk > safe.risk


def test_risk_large_change_surface_is_graded() -> None:
    """files_changed scales risk linearly up to the large-change-surface cap.

    0 → 0.0, 5 → 0.25, 10 (and above) saturates at 0.5 (mean with failed=0.0).
    This pins the documented _LARGE_CHANGE_SURFACE_FILES=10 saturation point.
    """
    r0 = CodingWorkflowEvaluator(_clean_risk_signals(files_changed=0)).evaluate(_ACTION)
    r5 = CodingWorkflowEvaluator(_clean_risk_signals(files_changed=5)).evaluate(_ACTION)
    r10 = CodingWorkflowEvaluator(_clean_risk_signals(files_changed=10)).evaluate(_ACTION)
    r20 = CodingWorkflowEvaluator(_clean_risk_signals(files_changed=20)).evaluate(_ACTION)

    assert r0.risk == pytest.approx(0.0)
    assert r5.risk == pytest.approx(0.25)
    assert r10.risk == pytest.approx(0.5)
    assert r20.risk == pytest.approx(0.5)  # saturated, no further increase
    assert r0.risk < r5.risk < r10.risk


def test_risk_failed_checks_are_coupled_to_acceptance() -> None:
    """The failed-checks indicator is 1 - A: worse completion → more risk.

    With only a test gate (no other risk evidence), risk equals the acceptance
    gap, proving the rule reuses acceptance rather than hiding a second scorer.
    Because a single gate now triggers the evidence-breadth floor, ``A`` is
    ``test_pass_rate × (1/4)`` and the failed-checks gap (hence risk) is the
    complement — so a single passing gate reads as ``A=0.25 / R=0.75`` rather
    than the old blind-spot ``A=1.0 / R=0.0``.
    """
    perfect = CodingWorkflowEvaluator(
        CodingWorkflowSignals(test_pass_rate=1.0)
    ).evaluate(_ACTION)
    half = CodingWorkflowEvaluator(
        CodingWorkflowSignals(test_pass_rate=0.5)
    ).evaluate(_ACTION)
    none = CodingWorkflowEvaluator(
        CodingWorkflowSignals(test_pass_rate=0.0)
    ).evaluate(_ACTION)

    assert perfect.risk == pytest.approx(0.75)
    assert half.risk == pytest.approx(0.875)
    assert none.risk == pytest.approx(1.0)
    # Risk is monotonic in the acceptance gap.
    assert perfect.risk < half.risk < none.risk

def test_risk_tests_removed_increases_risk() -> None:
    """Deleting tests raises risk (the canonical blind-spot signal).

    An agent that removes failing tests to force a green suite is "mechanically
    correct, semantically wrong". With a clean four-gate base (``A=1.0``,
    failed-checks=0.0) and no other risk evidence, no removals gives ``R=0.0``
    while any ``tests_removed > 0`` adds a full surprise indicator, so
    ``R = mean(1.0, 0.0) = 0.5``.
    """
    clean = CodingWorkflowEvaluator(_clean_risk_signals()).evaluate(_ACTION)
    mutating = CodingWorkflowEvaluator(
        _clean_risk_signals(tests_removed=2)
    ).evaluate(_ACTION)

    assert clean.risk == pytest.approx(0.0)
    assert mutating.risk == pytest.approx(0.5)
    assert mutating.risk > clean.risk
    # The deletion is visible in risk provenance.
    mutating_ev = CodingWorkflowEvaluator(_clean_risk_signals(tests_removed=2))
    mutating_ev.evaluate(_ACTION)
    assert "tests_removed" in {e.source for e in mutating_ev.provenance["risk"]}


def test_risk_tests_modified_is_milder_than_tests_removed() -> None:
    """Modifying tests is a milder, graded risk signal than deleting them.

    ``tests_modified`` scales with the count (capped below 1.0) so it never
    outweighs a deletion. With a clean four-gate base: one modification gives
    ``indicator = min(1/5, 1.0) × 0.5 = 0.1`` → ``R = mean(0.1, 0.0) = 0.05``;
    ten modifications saturate at ``1.0 × 0.5 = 0.5`` → ``R = 0.25`` — still
    milder than a single deletion (``R = 0.5``).
    """
    clean = CodingWorkflowEvaluator(_clean_risk_signals()).evaluate(_ACTION)
    one_mod = CodingWorkflowEvaluator(
        _clean_risk_signals(tests_modified=1)
    ).evaluate(_ACTION)
    many_mod = CodingWorkflowEvaluator(
        _clean_risk_signals(tests_modified=10)
    ).evaluate(_ACTION)
    removed = CodingWorkflowEvaluator(
        _clean_risk_signals(tests_removed=1)
    ).evaluate(_ACTION)

    assert one_mod.risk == pytest.approx(0.05)
    assert many_mod.risk == pytest.approx(0.25)
    assert many_mod.risk < removed.risk
    assert clean.risk < one_mod.risk < many_mod.risk < removed.risk


def test_risk_tests_added_is_not_a_risk_signal() -> None:
    """Adding tests is good evidence, never a risk signal.

    ``tests_added`` is recorded on the signals for auditability but must not
    appear in risk provenance or raise risk — only removals and modifications do.
    """
    clean = CodingWorkflowEvaluator(_clean_risk_signals()).evaluate(_ACTION)
    adding = CodingWorkflowEvaluator(
        _clean_risk_signals(tests_added=5)
    ).evaluate(_ACTION)

    assert adding.risk == pytest.approx(clean.risk)
    adding_ev = CodingWorkflowEvaluator(_clean_risk_signals(tests_added=5))
    adding_ev.evaluate(_ACTION)
    assert "tests_added" not in {e.source for e in adding_ev.provenance["risk"]}




# ---------------------------------------------------------------------------
# Influence (v0.2 honest default or external)
# ---------------------------------------------------------------------------


def test_influence_defaults_to_zero_with_explanation() -> None:
    """Without external influence, I=0.0 and the provenance says why.

    v0.2 prefers honesty over invented sophistication: no workflow signal can
    ground a downstream-influence claim, so the default is explicitly zero with a
    human-readable justification.
    """
    evaluator = CodingWorkflowEvaluator(CodingWorkflowSignals(test_pass_rate=1.0))

    scores = evaluator.evaluate(_ACTION)

    assert scores.influence == pytest.approx(0.0)
    influence_evidence = evaluator.provenance["influence"]
    assert len(influence_evidence) == 1
    assert influence_evidence[0].source == "default"
    assert influence_evidence[0].value == pytest.approx(0.0)


def test_influence_can_be_supplied_externally() -> None:
    """A caller may inject influence at construction (e.g. from another source).

    Lets an experiment or policy combine a workflow-derived A/R/C with an
    externally grounded influence value, recorded honestly as 'external'.
    """
    evaluator = CodingWorkflowEvaluator(
        CodingWorkflowSignals(test_pass_rate=1.0),
        influence=0.3,
    )

    scores = evaluator.evaluate(_ACTION)

    assert scores.influence == pytest.approx(0.3)
    influence_evidence = evaluator.provenance["influence"]
    assert len(influence_evidence) == 1
    assert influence_evidence[0].source == "external"
    assert influence_evidence[0].contribution == pytest.approx(0.3)


def test_influence_external_may_be_negative() -> None:
    """Influence ∈ [-1, 1] may be negative (a downstream penalty/bonus).

    Guards the negative side of the range — EvaluationScores allows negative
    influence, so an externally grounded penalty must flow through unchanged.
    """
    evaluator = CodingWorkflowEvaluator(
        CodingWorkflowSignals(test_pass_rate=1.0),
        influence=-0.2,
    )

    scores = evaluator.evaluate(_ACTION)

    assert scores.influence == pytest.approx(-0.2)


# ---------------------------------------------------------------------------
# Explicit provenance
# ---------------------------------------------------------------------------


def test_provenance_explains_every_score() -> None:
    """Each dimension's evidence reconstructs its score.

    This is the core Phase 7 guarantee: a consumer can answer "why is A=0.9375?"
    by reading ``provenance["acceptance"]``. For influence, risk and cost the
    contributions still *sum* to the score. For acceptance the blind-spot fix
    makes the score a *product* — the per-signal contributions sum to the raw
    mean and the ``evidence_breadth`` entry is the multiplicative floor — so a
    consumer reconstructs ``A = (sum of per-signal contributions) ×
    evidence_breadth`` rather than a plain sum.
    """
    evaluator = CodingWorkflowEvaluator(_FULL_SIGNALS)

    scores = evaluator.evaluate(_ACTION)

    assert set(evaluator.provenance) == {"acceptance", "influence", "risk", "cost"}

    dimensions = [
        ("acceptance", scores.acceptance),
        ("influence", scores.influence),
        ("risk", scores.risk),
        ("cost", scores.cost),
    ]
    for dim, score in dimensions:
        evidence = evaluator.provenance[dim]
        assert evidence, f"dimension {dim!r} has no evidence"
        if dim == "acceptance":
            # Acceptance is mean × evidence_breadth (blind-spot fix): the
            # breadth entry is a multiplier, so reconstruct as a product.
            breadth = next(e for e in evidence if e.source == "evidence_breadth")
            signal_total = sum(
                e.contribution for e in evidence if e.source != "evidence_breadth"
            )
            reconstructed = signal_total * breadth.contribution
        else:
            reconstructed = sum(e.contribution for e in evidence)
        assert reconstructed == pytest.approx(score), (
            f"{dim}: reconstructed {reconstructed} does not equal score {score}"
        )
        for piece in evidence:
            assert piece.source
            assert isinstance(piece.value, float)
            assert piece.description


# ---------------------------------------------------------------------------
# No network / no model dependency
# ---------------------------------------------------------------------------


def test_no_network_or_model_dependency() -> None:
    """The evaluator module imports no networking or LLM SDK.

    BOUND's deterministic core must stay provider-agnostic; importing this module
    and scoring must require no network keys or model clients. We assert the
    module exposes none of the usual offender names as top-level attributes.
    """
    import bound.workflow as workflow_mod

    evaluator = CodingWorkflowEvaluator(_FULL_SIGNALS)
    scores = evaluator.evaluate(_ACTION)  # succeeds with no env / network

    assert scores is not None
    for forbidden in ("requests", "openai", "anthropic", "httpx", "aiohttp", "socket"):
        assert not hasattr(workflow_mod, forbidden), (
            f"bound.workflow must not expose '{forbidden}'"
        )


def test_scores_stay_within_bound_ranges() -> None:
    """A/R/C stay in [0, 1] and I in [-1, 1] for a high-usage signal set.

    Even with saturated cost and maximal risk the evaluator must never produce
    values outside EvaluationScores' validated ranges (the policy relies on this).
    """
    signals = CodingWorkflowSignals(
        test_pass_rate=0.0,
        lint_passed=False,
        type_check_passed=False,
        retry_count=999,
        tool_call_count=999,
        token_usage=9_999_999,
        execution_time_seconds=99_999.0,
        files_changed=999,
        unexpected_files_changed=999,
        rollback_available=False,
    )

    scores = CodingWorkflowEvaluator(signals).evaluate(_ACTION)

    assert 0.0 <= scores.acceptance <= 1.0
    assert -1.0 <= scores.influence <= 1.0
    assert 0.0 <= scores.risk <= 1.0
    assert 0.0 <= scores.cost <= 1.0
    # Worst-case inputs saturate cost and risk at 1.0.
    assert scores.cost == pytest.approx(1.0)
    assert scores.risk == pytest.approx(1.0)

