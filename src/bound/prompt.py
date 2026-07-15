"""BOUND steering-prompt rendering (Phase 5).

Produces a deterministic, mathematically-correct plain-text steering prompt
purely from an :class:`~bound.models.EvaluationResult`. No LLM is involved: the
prompt is assembled from the result's score, threshold, components and decision
so it is bit-for-bit reproducible from the inputs alone.

The prompt always contains the final score ``S``, the acceptance threshold
``T`` and the BOUND decision, makes the bounded-optimization philosophy
explicit for ``ACCEPT``, and is kept under :data:`MAX_WORDS` words.
"""

from __future__ import annotations

from bound.models import EvaluationResult

#: Maximum allowed word count for a steering prompt.
MAX_WORDS = 150

# Decision-specific assessment / suggested-next-step text for the non-ACCEPT
# branches. The text is fixed (not generated) so the prompt stays deterministic
# and reproducible.
_NON_ACCEPT_TEXT: dict[str, tuple[str, str]] = {
    "ROLLBACK": (
        "Risk outweighs resource cost, signalling excessive downside if the "
        "action proceeds.",
        "Roll back and select a lower-risk alternative that preserves goal "
        "satisfaction.",
    ),
    "RETRY": (
        "Resource cost outweighs risk, signalling the action is too expensive "
        "for its benefit.",
        "Retry with a leaner approach that lowers resource cost while "
        "preserving goal satisfaction.",
    ),
    "REPLAN": (
        "Risk and resource cost are balanced, but the score remains below the "
        "acceptance threshold.",
        "Choose an alternative approach that improves goal satisfaction or "
        "downstream impact while reducing risk or resource cost.",
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


def _influence_term(influence: float) -> str:
    """Render the influence term with a sign-aware operator.

    Influence may be negative, so the operator is chosen to keep the
    substituted formula readable (``"+ 0.20"`` vs ``"- 0.10"``) while remaining
    mathematically exact — ``+ (-x)`` and ``- x`` are arithmetically identical.

    Args:
        influence: The influence component ``I`` (may be negative).

    Returns:
        The operator-prefixed term, e.g. ``"+ 0.20"`` or ``"- 0.10"``.
    """
    if influence < 0:
        return f"- {abs(influence):.2f}"
    return f"+ {influence:.2f}"


def generate_prompt(result: EvaluationResult) -> str:
    """Render a deterministic steering prompt from an :class:`EvaluationResult`.

    The prompt is plain text, requires no LLM, and is fully determined by the
    result's score ``S``, threshold ``T`` and decision. It always contains the
    substituted formula ``S = (W × A) + I - R - C`` with its final value, the
    threshold ``T``, and the decision, and is kept under :data:`MAX_WORDS` words.

    For ``ACCEPT`` the bounded-optimization principle is made explicit: the
    action meets the threshold, further optimization is not required, and the
    agent should proceed. For the other decisions an assessment and a suggested
    next step are included.

    Args:
        result: The auditable :class:`EvaluationResult` to render.

    Returns:
        A deterministic, multi-line steering prompt as a string.
    """
    acceptance = result.scores.acceptance
    influence = result.scores.influence
    risk = result.scores.risk
    cost = result.scores.cost

    formula = (
        f"S = ({_fmt(result.weight)} × {_fmt(acceptance)}) "
        f"{_influence_term(influence)} - {_fmt(risk)} - {_fmt(cost)}"
    )

    lines: list[str] = [
        "[BOUND evaluation]",
        "",
        f"Decision: {result.decision}",
        "",
        "Bounded utility:",
        "S = (W × A) + I - R - C",
        formula,
        f"S = {_fmt(result.score)}",
        "",
        "Acceptance threshold:",
        f"T = {_fmt(result.threshold)}",
        "",
    ]

    if result.decision == "ACCEPT":
        lines.extend(
            [
                "The proposed action meets the required acceptance threshold.",
                "Further optimization is not required.",
                "Proceed with the action and continue toward the larger goal.",
            ],
        )
    else:
        assessment, next_step = _NON_ACCEPT_TEXT[result.decision]
        lines.extend(
            [
                "The proposed action does not yet meet the required acceptance threshold.",
                "",
                "Assessment:",
                assessment,
                "",
                "Suggested next step:",
                next_step,
            ],
        )

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

