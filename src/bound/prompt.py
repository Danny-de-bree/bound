from __future__ import annotations

from bound.models import EvaluationResult

#: Maximum allowed word count for a steering prompt.
MAX_WORDS = 150

# Decision-specific steering text. The text is fixed (not generated) so the
# prompt stays deterministic and reproducible, and matches the v0.2 decision
# semantics specified in Phase 8 of the TODO.
_DECISION_TEXT: dict[str, str] = {
    "ACCEPT": (
        "The current result meets the required acceptance threshold and does "
        "not exceed the configured risk boundary. Further optimization of "
        "this step is not required. Continue toward the next goal."
    ),
    "RETRY": (
        "The current result is close to the required acceptance threshold. "
        "Stay with the same general approach and make one focused attempt to "
        "close the remaining gap."
    ),
    "REPLAN": (
        "The current result is materially below the required acceptance "
        "threshold. Do not keep iterating on the same approach. Choose a "
        "different strategy that better addresses the goal."
    ),
    "ROLLBACK": (
        "The action exceeds the configured acceptable risk boundary. Avoid or "
        "revert the action where possible before continuing."
    ),
}


def _fmt(value: float) -> str:
    """Format a numeric component to two decimal places.

    Args:
        value: The component value to format.

    Returns:
        The value rendered with exactly two decimals (e.g. ``"0.80"``).
    """
    return f"{value:.2f}"


def _term(weight: float, value: float) -> str:
    """Render a weighted score term as ``(weight × value)``.

    The value is substituted verbatim — including a negative sign for negative
    influence — so the rendered formula is always arithmetically exact.

    Args:
        weight: The weight applied to the term.
        value: The score dimension value (may be negative for influence).

    Returns:
        The substituted term, e.g. ``"(1.00 × 0.90)"`` or ``"(1.00 × -0.10)"``.
    """
    return f"({_fmt(weight)} × {_fmt(value)})"


def generate_prompt(result: EvaluationResult) -> str:
    """Render a deterministic steering prompt from an :class:`EvaluationResult`.

    The prompt is plain text, requires no LLM, and is fully determined by the
    result. It always contains the four-weight substituted formula
    ``S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)`` with its final value,
    the threshold ``T``, the signed distance to the threshold, the risk, the
    rollback risk threshold, and the decision. It is kept under
    :data:`MAX_WORDS` words.

    The per-decision wording follows the v0.2 semantics: ``ACCEPT`` is a
    boundary-inclusive satisficing signal (further optimization not required),
    ``RETRY`` asks for one focused attempt within the same approach,
    ``REPLAN`` calls for a different strategy, and ``ROLLBACK`` warns that the
    risk boundary has been exceeded.

    Args:
        result: The auditable :class:`EvaluationResult` to render.

    Returns:
        A deterministic, multi-line steering prompt as a string.
    """
    acceptance = result.scores.acceptance
    influence = result.scores.influence
    risk = result.scores.risk
    cost = result.scores.cost
    wa = result.weights.acceptance
    wi = result.weights.influence
    wr = result.weights.risk
    wc = result.weights.cost

    symbolic = "S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)"
    substituted = (
        f"S = {_term(wa, acceptance)} + {_term(wi, influence)} "
        f"- {_term(wr, risk)} - {_term(wc, cost)}"
    )

    lines: list[str] = [
        "[BOUND evaluation]",
        "",
        f"Decision: {result.decision}",
        "",
        "Bounded utility:",
        symbolic,
        substituted,
        f"S = {_fmt(result.score)}",
        "",
        f"Threshold: T = {_fmt(result.threshold)}",
        f"Distance to threshold: {_fmt(result.distance_to_threshold)}",
        f"Risk: {_fmt(risk)}",
        f"Rollback threshold: {_fmt(result.rollback_risk_threshold)}",
        "",
        _DECISION_TEXT[result.decision],
    ]

    return "\n".join(lines)


def word_count(text: str) -> int:
    """Count whitespace-separated tokens in ``text``.

    Used by tests to assert the steering prompt stays under :data:`MAX_WORDS`.
    Token counting (rather than natural-language word counting) is deliberately
    conservative: it over-counts, so a passing check guarantees the real word
    count is also within the limit.

    Args:
        text: The prompt text to count.

    Returns:
        The number of whitespace-separated tokens in ``text``.
    """
    return len(text.split())

