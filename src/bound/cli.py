"""BOUND command-line interface (Phase 6).

Exposes the ``bound evaluate`` subcommand, which accepts the four BOUND
evaluation dimensions (plus the goal weight and acceptance threshold) directly
from the command line, validates them through the same Pydantic models used by
the core, and writes an auditable JSON result to STDOUT and a readable steering
prompt to STDERR.

Only the standard library and the BOUND core are used: no LLM SDK and no
network access. CLI output (the JSON result and the steering prompt) is written
intentionally to STDOUT/STDERR; internal diagnostics use the ``logging`` module.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from bound.evaluator import StaticEvaluator
from bound.models import Action, BoundCriteria, EvaluationResult, EvaluationScores
from bound.policy import BoundPolicy
from bound.prompt import generate_prompt

logger = logging.getLogger("bound.cli")

#: Exit code returned when user-supplied inputs fail Pydantic validation.
EXIT_VALIDATION_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with the ``evaluate`` subcommand.

    Returns:
        An ``ArgumentParser`` with the ``bound`` program metadata, a global
        ``-v/--verbose`` flag, and the ``evaluate`` subcommand bound to
        :func:`_run_evaluate` via ``set_defaults(func=...)``.
    """
    parser = argparse.ArgumentParser(
        prog="bound",
        description="BOUND — a deterministic bounded-utility policy for agentic systems.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (repeatable).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate a proposed action and emit the BOUND decision.",
        description=(
            "Evaluate a proposed action against BOUND bounded-utility criteria. "
            "Prints an auditable JSON result to STDOUT and a steering prompt to STDERR."
        ),
    )
    evaluate.add_argument("--action", required=True, help="Description of the proposed action.")
    evaluate.add_argument("--goal", required=True, help="The larger goal the action advances.")
    evaluate.add_argument("--context", default=None, help="Optional additional context.")
    evaluate.add_argument(
        "--acceptance", type=float, required=True, help="Acceptance score A in [0, 1]."
    )
    evaluate.add_argument(
        "--influence", type=float, required=True, help="Downstream influence I in [-1, 1]."
    )
    evaluate.add_argument("--risk", type=float, required=True, help="Risk penalty R in [0, 1].")
    evaluate.add_argument("--cost", type=float, required=True, help="Resource penalty C in [0, 1].")
    evaluate.add_argument(
        "--weight", type=float, default=1.0, help="Goal weight W (>= 0). Defaults to 1.0."
    )
    evaluate.add_argument(
        "--threshold", type=float, required=True, help="Acceptance threshold T (>= 0)."
    )
    evaluate.set_defaults(func=_run_evaluate)

    return parser


def _configure_logging(verbosity: int) -> None:
    """Configure root logging based on the requested verbosity level.

    Args:
        verbosity: Number of times ``-v`` was supplied (0 = warning, 1 = info,
            2+ = debug).
    """
    level = logging.DEBUG if verbosity >= 2 else logging.INFO if verbosity == 1 else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s:%(name)s:%(message)s")


def _result_to_payload(result: EvaluationResult) -> dict[str, object]:
    """Build the auditable JSON payload from an :class:`EvaluationResult`.

    The payload exposes every term of ``S = (W × A) + I - R - C`` so a consumer
    can reconstruct the score from the JSON alone. Scores are emitted without
    their optional ``reasoning`` field to keep the output minimal and stable.

    Args:
        result: The :class:`EvaluationResult` to serialize.

    Returns:
        A JSON-serializable dict with ``scores`` (the four dimensions),
        ``weight``, ``threshold``, the four components, ``score`` and
        ``decision``.
    """
    return {
        "scores": {
            "acceptance": result.scores.acceptance,
            "influence": result.scores.influence,
            "risk": result.scores.risk,
            "cost": result.scores.cost,
        },
        "weight": result.weight,
        "threshold": result.threshold,
        "acceptance_component": result.acceptance_component,
        "influence_component": result.influence_component,
        "risk_component": result.risk_component,
        "cost_component": result.cost_component,
        "score": result.score,
        "decision": result.decision,
    }


def _run_evaluate(args: argparse.Namespace) -> int:
    """Execute the ``bound evaluate`` subcommand.

    Builds the :class:`Action`, :class:`EvaluationScores` and
    :class:`BoundCriteria` from the parsed arguments — all validated through
    Pydantic — runs the deterministic :class:`BoundPolicy`, writes the JSON
    result to STDOUT and the steering prompt to STDERR.

    Args:
        args: The parsed namespace carrying ``--action``/``--goal``/``--context``
            and the score/weight/threshold values.

    Returns:
        ``0`` on success, or :data:`EXIT_VALIDATION_ERROR` when the supplied
        inputs fail Pydantic validation (e.g. an out-of-range score).
    """
    try:
        action = Action(description=args.action, goal=args.goal, context=args.context)
        scores = EvaluationScores(
            acceptance=args.acceptance,
            influence=args.influence,
            risk=args.risk,
            cost=args.cost,
        )
        criteria = BoundCriteria(weight=args.weight, threshold=args.threshold)
    except ValidationError as exc:
        print(f"error: invalid BOUND inputs: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR

    policy = BoundPolicy(StaticEvaluator(scores))
    result = policy.evaluate(action, criteria)

    logger.debug("BOUND evaluation complete: decision=%s score=%s", result.decision, result.score)

    payload = _result_to_payload(result)
    print(json.dumps(payload, indent=2))
    print(generate_prompt(result), file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the requested subcommand.

    Args:
        argv: Optional argument vector. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code. With no subcommand the CLI exits ``0``. The
        ``evaluate`` subcommand returns ``0`` on success or
        :data:`EXIT_VALIDATION_ERROR` on invalid inputs. ``--help`` and missing
        required arguments are handled by ``argparse`` (which exits ``0`` /
        ``2`` respectively).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", 0))

    func = getattr(args, "func", None)
    if func is None:
        return 0
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
