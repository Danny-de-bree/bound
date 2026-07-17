from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from bound.evaluator import StaticEvaluator
from bound.models import (
    Action,
    BoundCriteria,
    BoundWeights,
    CodingWorkflowSignals,
    EvaluationResult,
    EvaluationScores,
)
from bound.policy import BoundPolicy
from bound.prompt import generate_prompt
from bound.workflow import CodingWorkflowEvaluator

logger = logging.getLogger("bound.cli")

#: Exit code returned when user-supplied inputs fail Pydantic validation.
EXIT_VALIDATION_ERROR = 2


def _add_weight_and_threshold_args(sub: argparse.ArgumentParser) -> None:
    """Register the shared v0.2 weight/threshold arguments on ``sub``.

    Both ``evaluate`` and ``evaluate-workflow`` accept the same knobs so the
    policy configuration is consistent across direct-score and workflow modes.

    Args:
        sub: The subparser to attach the arguments to.
    """
    sub.add_argument(
        "--acceptance-weight",
        type=float,
        default=1.0,
        help="Weight W_A applied to acceptance (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--influence-weight",
        type=float,
        default=1.0,
        help="Weight W_I applied to downstream influence (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--risk-weight",
        type=float,
        default=1.0,
        help="Weight W_R applied to the risk penalty (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--cost-weight",
        type=float,
        default=1.0,
        help="Weight W_C applied to the resource cost (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--weight",
        type=float,
        default=None,
        help=(
            "Deprecated alias for --acceptance-weight. Supplying it together "
            "with a non-default --*-weight is rejected."
        ),
    )
    sub.add_argument(
        "--threshold", type=float, required=True, help="Acceptance threshold T (>= 0)."
    )
    sub.add_argument(
        "--retry-margin",
        type=float,
        default=0.1,
        help="How far below T a score may fall while still RETRY (>= 0). Defaults to 0.1.",
    )
    sub.add_argument(
        "--rollback-risk-threshold",
        type=float,
        default=0.8,
        help="Hard risk boundary in [0, 1] above which the action rolls back. Defaults to 0.8.",
    )


def _build_criteria(args: argparse.Namespace) -> BoundCriteria:
    """Build :class:`BoundCriteria` from the shared weight/threshold args.

    The symmetric weights are always constructed from the ``--*-weight`` flags.
    The deprecated scalar ``--weight`` is forwarded only when explicitly set;
    :class:`BoundCriteria` rejects the combination of ``weight`` with a
    non-default ``weights`` so the two weight systems can never silently
    compete.

    Args:
        args: The parsed namespace carrying the weight/threshold values.

    Returns:
        The validated :class:`BoundCriteria`.

    Raises:
        ValidationError: If any value is out of range or the two weight
            systems conflict.
    """
    weights = BoundWeights(
        acceptance=args.acceptance_weight,
        influence=args.influence_weight,
        risk=args.risk_weight,
        cost=args.cost_weight,
    )
    kwargs: dict[str, object] = {
        "threshold": args.threshold,
        "retry_margin": args.retry_margin,
        "rollback_risk_threshold": args.rollback_risk_threshold,
        "weights": weights,
    }
    if getattr(args, "weight", None) is not None:
        kwargs["weight"] = args.weight
    return BoundCriteria(**kwargs)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with the BOUND subcommands.

    Returns:
        An ``ArgumentParser`` with the ``bound`` program metadata, a global
        ``-v/--verbose`` flag, and the ``evaluate`` and ``evaluate-workflow``
        subcommands bound to their runners via ``set_defaults(func=...)``.
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
    _add_weight_and_threshold_args(evaluate)
    evaluate.set_defaults(func=_run_evaluate)

    workflow = subparsers.add_parser(
        "evaluate-workflow",
        help="Derive BOUND scores from coding-workflow signals and decide.",
        description=(
            "Derive the four BOUND score dimensions from provider-agnostic "
            "coding-workflow signals (test pass rate, lint/type-check status, "
            "retry/tool-call counts, ...) via CodingWorkflowEvaluator, then run "
            "the deterministic BOUND policy. No LLM, no network."
        ),
    )
    workflow.add_argument("--action", required=True, help="Description of the proposed action.")
    workflow.add_argument("--goal", required=True, help="The larger goal the action advances.")
    workflow.add_argument("--context", default=None, help="Optional additional context.")
    workflow.add_argument(
        "--test-pass-rate", type=float, default=None, help="Fraction of tests passing in [0, 1]."
    )
    workflow.add_argument(
        "--lint-passed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether the linter is clean (--lint-passed / --no-lint-passed).",
    )
    workflow.add_argument(
        "--type-check-passed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether type-checking is clean (--type-check-passed / --no-type-check-passed).",
    )
    workflow.add_argument(
        "--required-checks-passed",
        type=float,
        default=None,
        help="Fraction of required checks passing in [0, 1].",
    )
    workflow.add_argument(
        "--retry-count", type=int, default=0, help="Number of retries performed so far."
    )
    workflow.add_argument(
        "--tool-call-count", type=int, default=0, help="Number of tool calls performed so far."
    )
    workflow.add_argument("--token-usage", type=int, default=None, help="Total tokens consumed.")
    workflow.add_argument(
        "--execution-time-seconds",
        type=float,
        default=None,
        help="Wall-clock execution time in seconds.",
    )
    workflow.add_argument(
        "--files-changed", type=int, default=None, help="Number of files changed."
    )
    workflow.add_argument(
        "--unexpected-files-changed",
        type=int,
        default=None,
        help="Number of unexpected files changed.",
    )
    workflow.add_argument(
        "--rollback-available",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Whether a clean rollback is available "
            "(--rollback-available / --no-rollback-available)."
        ),
    )
    workflow.add_argument(
        "--influence",
        type=float,
        default=None,
        help="Optional externally-supplied downstream influence I in [-1, 1].",
    )
    _add_weight_and_threshold_args(workflow)
    workflow.set_defaults(func=_run_evaluate_workflow)

    spec = subparsers.add_parser(
        "integration-spec",
        help="Emit the framework-neutral BOUND integration specification as JSON.",
        description=(
            "Emit the framework-neutral BOUND integration specification as "
            "structured JSON to STDOUT. Defines when to call BOUND, when not to, "
            "the required flow, and the evidence rule. Deterministic: no LLM, "
            "no network."
        ),
    )
    spec.set_defaults(func=_run_integration_spec)

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

    The payload exposes every term of ``S = (W_A × A) + (W_I × I) - (W_R × R) -
    (W_C × C)`` so a consumer can reconstruct the score from the JSON alone. The
    symmetric ``weights``, the deprecated ``weight`` alias, the threshold
    metadata (``retry_margin``, ``rollback_risk_threshold``) and the signed
    ``distance_to_threshold`` are all carried through for auditability. Scores
    are emitted without their optional ``reasoning`` field to keep the output
    minimal and stable. Provenance is only included when present (workflow
    mode), so direct-score output stays minimal.

    Args:
        result: The :class:`EvaluationResult` to serialize.

    Returns:
        A JSON-serializable dict with ``scores`` (the four dimensions),
        ``weights``, ``weight``, ``threshold``, the threshold metadata, the
        four components, ``score``, ``distance_to_threshold``, ``decision``,
        and (when available) ``provenance``.
    """
    payload: dict[str, object] = {
        "scores": {
            "acceptance": result.scores.acceptance,
            "influence": result.scores.influence,
            "risk": result.scores.risk,
            "cost": result.scores.cost,
        },
        "weights": {
            "acceptance": result.weights.acceptance,
            "influence": result.weights.influence,
            "risk": result.weights.risk,
            "cost": result.weights.cost,
        },
        "weight": result.weight,
        "threshold": result.threshold,
        "retry_margin": result.retry_margin,
        "rollback_risk_threshold": result.rollback_risk_threshold,
        "acceptance_component": result.acceptance_component,
        "influence_component": result.influence_component,
        "risk_component": result.risk_component,
        "cost_component": result.cost_component,
        "score": result.score,
        "distance_to_threshold": result.distance_to_threshold,
        "decision": result.decision,
    }
    if result.provenance is not None:
        payload["provenance"] = {
            dimension: [evidence.model_dump() for evidence in evidence_list]
            for dimension, evidence_list in result.provenance.items()
        }
    return payload


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
        inputs fail Pydantic validation (e.g. an out-of-range score or a
        conflict between the deprecated ``--weight`` and the symmetric
        ``--*-weight`` flags).
    """
    try:
        action = Action(description=args.action, goal=args.goal, context=args.context)
        scores = EvaluationScores(
            acceptance=args.acceptance,
            influence=args.influence,
            risk=args.risk,
            cost=args.cost,
        )
        criteria = _build_criteria(args)
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


def _build_workflow_signals(args: argparse.Namespace) -> CodingWorkflowSignals:
    """Build :class:`CodingWorkflowSignals` from the workflow subcommand args.

    Optional signals default to ``None`` (treated as unobserved by the
    evaluator) rather than zero, preserving the workflow evaluator's
    "ignore missing signals" contract.

    Args:
        args: The parsed namespace carrying the workflow signal values.

    Returns:
        The validated :class:`CodingWorkflowSignals`.
    """
    return CodingWorkflowSignals(
        test_pass_rate=args.test_pass_rate,
        lint_passed=args.lint_passed,
        type_check_passed=args.type_check_passed,
        required_checks_passed=args.required_checks_passed,
        retry_count=args.retry_count,
        tool_call_count=args.tool_call_count,
        token_usage=args.token_usage,
        execution_time_seconds=args.execution_time_seconds,
        files_changed=args.files_changed,
        unexpected_files_changed=args.unexpected_files_changed,
        rollback_available=args.rollback_available,
    )


def _run_evaluate_workflow(args: argparse.Namespace) -> int:
    """Execute the ``bound evaluate-workflow`` subcommand.

    Builds :class:`CodingWorkflowSignals` from the parsed arguments, feeds them
    to a :class:`CodingWorkflowEvaluator` (deriving ``A``/``I``/``R``/``C``
    deterministically — no LLM, no network), runs the deterministic
    :class:`BoundPolicy`, and writes the JSON result (including the input
    ``signals`` and the evaluator ``provenance``) to STDOUT and the steering
    prompt to STDERR.

    Args:
        args: The parsed namespace carrying the workflow signals plus the
            shared weight/threshold values.

    Returns:
        ``0`` on success, or :data:`EXIT_VALIDATION_ERROR` when the supplied
        inputs fail validation (e.g. an out-of-range signal or no acceptance
        evidence at all).
    """
    try:
        action = Action(description=args.action, goal=args.goal, context=args.context)
        signals = _build_workflow_signals(args)
        evaluator = CodingWorkflowEvaluator(signals, influence=args.influence)
        criteria = _build_criteria(args)
    except ValidationError as exc:
        print(f"error: invalid BOUND inputs: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR

    policy = BoundPolicy(evaluator)
    try:
        result = policy.evaluate(action, criteria)
    except ValueError as exc:
        # CodingWorkflowEvaluator raises ValueError when no acceptance evidence
        # is available at all (none of the completion signals were supplied).
        print(f"error: could not evaluate workflow: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR

    logger.debug(
        "BOUND workflow evaluation complete: decision=%s score=%s",
        result.decision,
        result.score,
    )

    payload = _result_to_payload(result)
    payload["signals"] = signals.model_dump()
    print(json.dumps(payload, indent=2))
    print(generate_prompt(result), file=sys.stderr)
    return 0


def _run_integration_spec(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Execute the ``bound integration-spec`` subcommand.

    Emits the framework-neutral BOUND integration specification as structured
    JSON to STDOUT. The spec is produced deterministically (no LLM, no network)
    by :func:`bound.integration_spec.integration_spec` and is intended to be
    consumable by any agent integration.

    Args:
        args: The parsed namespace. Unused — the subcommand takes no arguments.

    Returns:
        ``0`` on success.
    """
    from bound.integration_spec import integration_spec

    print(json.dumps(integration_spec(), indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the requested subcommand.

    Args:
        argv: Optional argument vector. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code. With no subcommand the CLI exits ``0``. The
        ``evaluate`` and ``evaluate-workflow`` subcommands return ``0`` on
        success or :data:`EXIT_VALIDATION_ERROR` on invalid inputs. ``--help``
        and missing required arguments are handled by ``argparse`` (which exits
        ``0`` / ``2`` respectively).
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
