from __future__ import annotations

from bound.contract_evaluator import ContractEvaluator
from bound.contracts import BoundPlan, ContractGenerator, StepContract
from bound.evidence import ExecutionEvidence
from bound.models import BoundCriteria, EvaluationResult
from bound.policy import BoundPolicy


class BoundWorkflow:
    """High-level orchestration of the v0.3 BOUND contract pipeline.

    The workflow wires three deterministic components — a
    :class:`~bound.contracts.ContractGenerator`, a
    :class:`~bound.contract_evaluator.ContractEvaluator`, and a
    :class:`~bound.policy.BoundPolicy` — behind two operations:
    :meth:`prepare` (goal + plan -> validated
    :class:`~bound.contracts.BoundPlan`) and :meth:`evaluate_step` (one executed
    step -> :class:`~bound.models.EvaluationResult` whose decision comes from
    the :class:`~bound.policy.BoundPolicy`). It is intentionally thin: no agent
    loop, no execution control, no decision rule. The consuming agent decides
    when to prepare, when to evaluate, and how to react to
    ACCEPT / RETRY / REPLAN / ROLLBACK.

    Attributes:
        contract_generator: The :class:`~bound.contracts.ContractGenerator`
            used by :meth:`prepare`, or ``None`` when the workflow was
            constructed without one (in which case :meth:`prepare` raises).
        evaluator: The :class:`~bound.contract_evaluator.ContractEvaluator`
            used to score an executed step against its contract.
        policy: The :class:`~bound.policy.BoundPolicy` that owns the final
            ACCEPT / RETRY / REPLAN / ROLLBACK decision.
    """

    def __init__(
        self,
        contract_generator: ContractGenerator | None = None,
        evaluator: ContractEvaluator | None = None,
        policy: BoundPolicy | None = None,
    ) -> None:
        """Bind the workflow to its deterministic components.

        Every component has a sensible default, so the minimal contract
        pipeline is a placeholder-free ``BoundWorkflow()`` followed by
        :meth:`evaluate_step`. Only :meth:`prepare` requires a
        ``contract_generator`` (it compiles a goal + plan into a
        :class:`~bound.contracts.BoundPlan`), so a workflow used purely for
        ``evaluate_step`` need not supply one.

        Args:
            contract_generator: Any object satisfying the
                :class:`~bound.contracts.ContractGenerator` Protocol, or
                ``None`` (the default) when :meth:`prepare` will not be used.
                It turns a natural-language goal + plan into a validated
                :class:`~bound.contracts.BoundPlan` and must never produce a
                BOUND decision or A/I/R/C scores.
            evaluator: The :class:`~bound.contract_evaluator.ContractEvaluator`
                that scores an executed step into
                :class:`~bound.models.EvaluationScores`, or ``None`` to use a
                fresh default evaluator. It must never produce a BOUND
                decision.
            policy: The :class:`~bound.policy.BoundPolicy` that owns the final
                decision, or ``None`` to construct a default ``BoundPolicy()``.
                No injected :class:`~bound.evaluator.Evaluator` is required:
                :meth:`evaluate_step` scores via the contract ``evaluator`` and
                feeds those scores straight through
                :meth:`~bound.policy.BoundPolicy.decide`.
        """
        self._contract_generator = contract_generator
        self._evaluator = evaluator if evaluator is not None else ContractEvaluator()
        self._policy = policy if policy is not None else BoundPolicy()

    @property
    def contract_generator(self) -> ContractGenerator | None:
        """The :class:`~bound.contracts.ContractGenerator` bound to this workflow.

        ``None`` when the workflow was constructed without one, in which case
        :meth:`prepare` raises.
        """
        return self._contract_generator

    @property
    def evaluator(self) -> ContractEvaluator:
        """The :class:`~bound.contract_evaluator.ContractEvaluator` bound here."""
        return self._evaluator

    @property
    def policy(self) -> BoundPolicy:
        """The :class:`~bound.policy.BoundPolicy` that owns the decision."""
        return self._policy

    def prepare(
        self,
        *,
        goal: str,
        plan: str,
        context: str | None = None,
    ) -> BoundPlan:
        """Compile ``goal`` and ``plan`` into a validated :class:`BoundPlan`.

        Delegates to the bound :class:`~bound.contracts.ContractGenerator`,
        which turns the natural-language goal + plan (plus optional context)
        into a Pydantic-validated :class:`~bound.contracts.BoundPlan`. The
        workflow performs no decision and no scoring here; it merely forwards
        the arguments. Execution remains controlled by the consuming agent.

        Args:
            goal: The natural-language top-level goal of the plan.
            plan: The natural-language plan text (e.g. a sequence of steps).
            context: Optional additional context influencing contract
                generation. Defaults to ``None``.

        Returns:
            A :class:`~bound.contracts.BoundPlan` that has passed Pydantic
            validation.

        Raises:
            RuntimeError: If the workflow was constructed without a
                ``contract_generator``.
        """
        if self._contract_generator is None:
            raise RuntimeError(
                "BoundWorkflow.prepare requires a contract_generator; construct "
                "one with BoundWorkflow(contract_generator=...), or call "
                "evaluate_step directly when you already hold a StepContract."
            )
        return self._contract_generator.generate(
            goal=goal,
            plan=plan,
            context=context,
        )

    def evaluate_step(
        self,
        *,
        contract: StepContract,
        evidence: ExecutionEvidence,
        criteria: BoundCriteria,
    ) -> EvaluationResult:
        """Score one executed step into a deterministic :class:`EvaluationResult`.

        The pipeline is ``StepContract + ExecutionEvidence -> ContractEvaluator
        -> EvaluationScores (A / I / R / C) -> BoundPolicy.decide ->
        EvaluationResult (ACCEPT / RETRY / REPLAN / ROLLBACK)``. The contract
        scores come from the bound
        :class:`~bound.contract_evaluator.ContractEvaluator` (the single
        deterministic source, which also exposes per-dimension ``provenance``);
        the decision comes from the bound
        :class:`~bound.policy.BoundPolicy` — never from the workflow. The scores
        are fed straight into :meth:`~bound.policy.BoundPolicy.decide`, the
        single place where ``calculate_components`` and the decision rule run,
        so the contract is never re-scored and no placeholder evaluator is
        involved. See the module docstring for the full rationale.

        Args:
            contract: The :class:`~bound.contracts.StepContract` whose declared
                acceptance checks, risk checks, and budget scope the scoring.
            evidence: The :class:`~bound.evidence.ExecutionEvidence` observed
                after the step executed.
            criteria: The :class:`~bound.models.BoundCriteria` (threshold,
                weights, retry margin, rollback risk boundary) the policy
                evaluates against.

        Returns:
            An :class:`~bound.models.EvaluationResult` carrying the contract
            scores, weighted components, final score, threshold metadata, the
            deterministic decision, and the
            :class:`~bound.contract_evaluator.ContractEvaluator`'s provenance.
        """
        # 1. Contract scores (single deterministic source) + provenance. The
        #    ContractEvaluator populates its `provenance` on every evaluate.
        scores = self._evaluator.evaluate(contract, evidence)
        contract_provenance = self._evaluator.provenance or None

        # 2. Decision in one place: feed the contract scores straight into the
        #    policy's decide() (calculate_components + decision rule), passing
        #    the contract provenance so the result explains "why A/I/R/C?".
        #    No StaticEvaluator bridge and no rebinding of the policy's
        #    evaluator — the contract workflow never needs an Action-based
        #    evaluator at all.
        return self._policy.decide(scores, criteria, provenance=contract_provenance)
