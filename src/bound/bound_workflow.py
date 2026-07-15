"""BOUND high-level orchestration workflow (v0.3 Phases 9 & 10).

This module provides :class:`BoundWorkflow`, the thin orchestration seam that
wires the v0.3 contract pipeline end-to-end *without* becoming an agent
framework:

.. code-block:: text

    goal + plan -> ContractGenerator -> BoundPlan
    StepContract + ExecutionEvidence -> ContractEvaluator -> A / I / R / C
        -> BoundPolicy -> EvaluationResult (ACCEPT / RETRY / REPLAN / ROLLBACK)

BOUND deliberately stays out of the execution loop. The consuming agent owns
*when* to call :meth:`prepare` and :meth:`evaluate_step`, and decides — using
the deterministic :class:`~bound.models.EvaluationResult` — whether to accept,
retry, replan, or roll back. The workflow only (a) prepares a
:class:`~bound.contracts.BoundPlan` from a goal + plan via a
:class:`~bound.contracts.ContractGenerator`, and (b) evaluates one executed
step into an :class:`~bound.models.EvaluationResult`. The final decision
remains the exclusive responsibility of the deterministic
:class:`~bound.policy.BoundPolicy`.

Score / decision boundary
-------------------------
The :class:`~bound.contract_evaluator.ContractEvaluator` scores an *executed*
step (``StepContract + ExecutionEvidence -> EvaluationScores``) and is **not**
an :class:`~bound.evaluator.Evaluator` (which scores a proposed
:class:`~bound.models.Action`). :meth:`~bound.policy.BoundPolicy.evaluate`, by
contrast, is built around the :class:`~bound.evaluator.Evaluator` Protocol
(``Action -> EvaluationScores``) and owns the decision rule (``_decide``) plus
the :class:`~bound.models.EvaluationResult` assembly.

To keep the decision in **one place** (the policy) and avoid recomputing the
contract scores, :meth:`evaluate_step` bridges the two seams with a throwaway
:class:`~bound.evaluator.StaticEvaluator` wrapping the contract scores: it
temporarily rebinds the policy's evaluator to that
:class:`~bound.evaluator.StaticEvaluator`, calls the policy's *unchanged*
:meth:`~bound.policy.BoundPolicy.evaluate` (so ``calculate_components`` and the
decision rule run exactly once, inside the policy), then restores the original
evaluator. Because the :class:`~bound.evaluator.StaticEvaluator` simply returns
the already-computed scores, no contract is re-scored — there is no
double-computation. The :class:`~bound.contract_evaluator.ContractEvaluator`'s
per-dimension ``provenance`` is then wired onto the result, since the
:class:`~bound.evaluator.StaticEvaluator` bridge carries none.

The policy's own injected :class:`~bound.evaluator.Evaluator` is therefore a
vestigial placeholder in the contract workflow: contract scores always come
from the bound :class:`~bound.contract_evaluator.ContractEvaluator`. It is
retained (and restored) so a caller that injected a specific
:class:`~bound.policy.BoundPolicy` instance still sees its decision logic and
its evaluator unchanged after every evaluation.

The module performs no network access and imports no LLM SDK; once a
:class:`~bound.contracts.BoundPlan` and
:class:`~bound.evidence.ExecutionEvidence` are supplied, every downstream
calculation is fully deterministic.
"""

from __future__ import annotations

from bound.contract_evaluator import ContractEvaluator
from bound.contracts import BoundPlan, ContractGenerator, StepContract
from bound.evaluator import StaticEvaluator
from bound.evidence import ExecutionEvidence
from bound.models import Action, BoundCriteria, EvaluationResult
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
            used by :meth:`prepare`.
        evaluator: The :class:`~bound.contract_evaluator.ContractEvaluator`
            used to score an executed step against its contract.
        policy: The :class:`~bound.policy.BoundPolicy` that owns the final
            ACCEPT / RETRY / REPLAN / ROLLBACK decision.
    """

    def __init__(
        self,
        contract_generator: ContractGenerator,
        evaluator: ContractEvaluator,
        policy: BoundPolicy,
    ) -> None:
        """Bind the workflow to its three deterministic components.

        Args:
            contract_generator: Any object satisfying the
                :class:`~bound.contracts.ContractGenerator` Protocol. It turns a
                natural-language goal + plan into a validated
                :class:`~bound.contracts.BoundPlan` and must never produce a
                BOUND decision or A/I/R/C scores.
            evaluator: The :class:`~bound.contract_evaluator.ContractEvaluator`
                that scores an executed step into
                :class:`~bound.models.EvaluationScores`. It must never produce a
                BOUND decision.
            policy: The :class:`~bound.policy.BoundPolicy` that owns the final
                decision. Its injected :class:`~bound.evaluator.Evaluator` is
                not used for contract scoring — :meth:`evaluate_step` scores via
                the ``evaluator`` and feeds those scores through the policy's
                decision pipeline (see the module docstring).
        """
        self._contract_generator = contract_generator
        self._evaluator = evaluator
        self._policy = policy

    @property
    def contract_generator(self) -> ContractGenerator:
        """The :class:`~bound.contracts.ContractGenerator` bound to this workflow."""
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
        """
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
        -> EvaluationScores (A / I / R / C) -> BoundPolicy ->
        EvaluationResult (ACCEPT / RETRY / REPLAN / ROLLBACK)``. The contract
        scores come from the bound
        :class:`~bound.contract_evaluator.ContractEvaluator` (the single
        deterministic source, which also exposes per-dimension ``provenance``);
        the decision comes from the bound
        :class:`~bound.policy.BoundPolicy` — never from the workflow. The
        contract scores are fed through the policy's *unchanged*
        :meth:`~bound.policy.BoundPolicy.evaluate` via a throwaway
        :class:`~bound.evaluator.StaticEvaluator`, so the policy's
        ``calculate_components`` and decision rule run exactly once and the
        contract is never re-scored. See the module docstring for the full
        rationale.

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

        # 2. Bridge the contract scores into BoundPolicy's Action-based
        #    pipeline. BoundPolicy.evaluate(action, criteria) scores via its
        #    own Evaluator (Action -> EvaluationScores); the ContractEvaluator
        #    is NOT an Evaluator (it scores StepContract + ExecutionEvidence).
        #    A StaticEvaluator wrapping the already-computed scores lets the
        #    policy's calculate_components + decision rule run unchanged, in
        #    one place, without re-scoring the contract. The policy's evaluator
        #    is restored afterwards so the injected policy is left unchanged.
        action = Action(description=contract.description, goal=contract.goal)
        bridge = StaticEvaluator(scores)
        previous_evaluator = self._policy.evaluator
        self._policy._evaluator = bridge  # rebinding the Action-based seam
        try:
            result = self._policy.evaluate(action, criteria)
        finally:
            self._policy._evaluator = previous_evaluator

        # 3. The StaticEvaluator bridge carries no provenance, so the policy
        #    sets result.provenance = None. Forward the ContractEvaluator's
        #    per-dimension evidence so a consumer can answer "why A/I/R/C?".
        result.provenance = contract_provenance
        return result
