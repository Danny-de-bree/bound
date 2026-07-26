"""Decision mappings — single source of truth for all decision/action/reason translations.

Every mapping from a BOUND decision to a control action, evaluation reason code,
or outcome reason code lives here and only here. Consumers import these mappings
rather than re-defining them, so a change to the decision vocabulary propagates
to every consumer through one file.

Mappings:
    :data:`DECISION_TO_ACTION` — BOUND decision → agent control action
        (``"continue"`` / ``"retry"`` / ``"replan"`` / ``"rollback"``).
    :data:`DECISION_TO_EVAL_REASON` — BOUND decision → lineage evaluation
        reason code string (matching :class:`bound.lineage.ReasonCode` values).
    :data:`ACTION_TO_OUTCOME_REASON` — control action → lineage outcome
        reason code string (matching :class:`bound.lineage.ReasonCode` values).
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ACTION_TO_OUTCOME_REASON",
    "DECISION_TO_ACTION",
    "DECISION_TO_CONTROL",
    "DECISION_TO_EVAL_REASON",
]

#: The deterministic BOUND decision → agent control action mapping.
#: This is the **single runtime source** of that translation.
DECISION_TO_ACTION: Final[dict[str, str]] = {
    "ACCEPT": "continue",
    "RETRY": "retry",
    "REPLAN": "replan",
    "ROLLBACK": "rollback",
}

#: Alias of :data:`DECISION_TO_ACTION` published for integration specs.
#: The same mapping, under the name consumers expect.
DECISION_TO_CONTROL: Final[dict[str, str]] = DECISION_TO_ACTION

#: BOUND decision → evaluation reason code string.  The evaluation event
#: mirrors the deterministic decision rather than re-deriving free-text
#: evidence, so the recorded lineage is reproducible from the decision alone.
#: Values match :class:`~bound.lineage.ReasonCode` member values.
DECISION_TO_EVAL_REASON: Final[dict[str, str]] = {
    "ACCEPT": "ACCEPT",
    "RETRY": "RETRY",
    "REPLAN": "REPLAN",
    "ROLLBACK": "ROLLBACK",
}

#: Mapped control action → outcome reason code string.
#: Values match :class:`~bound.lineage.ReasonCode` member values.
ACTION_TO_OUTCOME_REASON: Final[dict[str, str]] = {
    "continue": "CONTINUED",
    "retry": "RETRIED",
    "replan": "REPLANNED",
    "rollback": "ROLLED_BACK",
}
