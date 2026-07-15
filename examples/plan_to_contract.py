"""BOUND plan-to-contract example (Phase 12).

Turns a natural-language goal + plan into an explicit, validated
:class:`~bound.contracts.BoundPlan` **without an LLM**, using the deterministic
:class:`~bound.contracts.StaticContractGenerator`.

The scenario (from ``todo.md`` Phase 12)::

    Goal:
        Add JWT authentication.

    Plan:
        1. Add token creation.
        2. Add authentication middleware.
        3. Protect private endpoints.
        4. Add tests.

For each step we declare up front what success looks like
(:class:`~bound.contracts.AcceptanceCheck`), what risks matter
(:class:`~bound.contracts.RiskCheck` with a severity), what artifacts are
expected (``expected_artifacts``), and the execution budget
(:class:`~bound.contracts.StepBudget` with ``max_tool_calls`` / ``max_retries``).
A :class:`~bound.contracts.BoundPlan` bundles the goal and the ordered
:class:`~bound.contracts.StepContract` entries; :class:`StaticContractGenerator`
returns that plan verbatim on every ``generate`` call, so the full contract
pipeline is reproducible with no network access, no API key, and no LLM SDK.

This script prints the compiled plan — every step's id, description, goal,
acceptance checks, risk checks, expected artifacts, and budget — and verifies
that the plan round-trips through the :class:`~bound.contracts.ContractGenerator`
seam (i.e. ``StaticContractGenerator.generate(...)`` returns the exact plan).

Optional LLM adapter (Phase 4 boundary)
---------------------------------------
:class:`~bound.contracts.StaticContractGenerator` is one concrete implementation
of the :class:`~bound.contracts.ContractGenerator` Protocol. An *optional*
LLM-backed adapter (e.g. ``OpenAIContractGenerator``,
``AnthropicContractGenerator``) could generate the *same* contract automatically
from the natural-language goal + plan. The seam is unchanged: the adapter's
``generate(goal=..., plan=..., context=...)`` must return a
:class:`~bound.contracts.BoundPlan` only — never a BOUND decision, never
``A / I / R / C`` scores. Whatever an LLM emits is round-tripped through Pydantic
validation (the same validators that reject empty plans, steps with no
acceptance checks, invalid risk severities, and invalid budgets) before BOUND
can use it, so a malformed or hallucinated contract is rejected rather than
silently trusted. The deterministic evaluator and policy then own the scores and
the final ``ACCEPT / RETRY / REPLAN / ROLLBACK``. In short: the LLM defines
*what to measure*; BOUND remains the deterministic arbiter of *how it scores and
decides*. That adapter lives outside the deterministic core (an optional
dependency group), so this script — and the whole package — runs without it.

Run with::

    uv run python examples/plan_to_contract.py
"""

from __future__ import annotations

import sys

from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    RiskCheck,
    StaticContractGenerator,
    StepBudget,
    StepContract,
)

#: The top-level goal of the plan, exactly as stated in ``todo.md`` Phase 12.
GOAL = "Add JWT authentication."

#: The natural-language plan text. The :class:`StaticContractGenerator` ignores
#: this (it returns a pre-built plan), but it is the input a real LLM adapter
#: would compile into the :class:`BoundPlan` below.
PLAN_TEXT = (
    "1. Add token creation.\n"
    "2. Add authentication middleware.\n"
    "3. Protect private endpoints.\n"
    "4. Add tests."
)

#: Shared execution budget for every step in this plan (Phase 12 spec).
_BUDGET = StepBudget(max_tool_calls=20, max_retries=3)


def _build_plan() -> BoundPlan:
    """Construct the JWT-authentication :class:`BoundPlan`.

    Each :class:`StepContract` declares measurable acceptance checks, meaningful
    risk checks, the artifacts it should produce, and the shared budget. The
    contract carries no executable code — only identifiers and descriptions — so
    nothing here can smuggle arbitrary Python into the deterministic core.

    Returns:
        A validated :class:`BoundPlan` for the four-step JWT plan.
    """
    # ------------------------------------------------------------------ #
    # Step 1 — Token creation.                                           #
    # ------------------------------------------------------------------ #
    token_creation = StepContract(
        id="token-creation",
        description="Add JWT token creation for valid credentials.",
        goal="Authenticated callers can obtain a verifiable JWT.",
        acceptance_checks=[
            AcceptanceCheck(
                id="token_creation_returns_valid_jwt",
                description="Valid credentials produce a signed, parseable JWT.",
            ),
            AcceptanceCheck(
                id="generated_token_can_be_verified",
                description=(
                    "A generated token verifies successfully against the "
                    "signing key."
                ),
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_plaintext_secret_committed",
                description="No plaintext signing secret is committed to the "
                "repository.",
                severity=1.0,
            ),
            RiskCheck(
                id="token_expiry_configured",
                description="Token expiry is configured (tokens are not "
                "long-lived by default).",
                severity=0.8,
            ),
        ],
        expected_artifacts=["src/auth/token.py"],
        budget=_BUDGET,
    )

    # ------------------------------------------------------------------ #
    # Step 2 — Authentication middleware.                                  #
    # ------------------------------------------------------------------ #
    auth_middleware = StepContract(
        id="auth-middleware",
        description="Add middleware that authenticates requests from a JWT.",
        goal="Incoming requests carry a validated identity or are rejected.",
        acceptance_checks=[
            AcceptanceCheck(
                id="auth_middleware_rejects_missing_token",
                description="Requests with no Authorization header are rejected.",
            ),
            AcceptanceCheck(
                id="auth_middleware_rejects_invalid_token",
                description=(
                    "Requests with a malformed or bad-signature token "
                    "are rejected."
                ),
            ),
            AcceptanceCheck(
                id="auth_middleware_attaches_identity",
                description=(
                    "Requests with a valid token have the caller identity "
                    "attached to the request context."
                ),
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_plaintext_secret_committed",
                description="No plaintext signing secret is committed to the "
                "repository.",
                severity=1.0,
            ),
            RiskCheck(
                id="token_expiry_configured",
                description="Token expiry is enforced by the middleware.",
                severity=0.8,
            ),
        ],
        expected_artifacts=["src/auth/middleware.py"],
        budget=_BUDGET,
    )

    # ------------------------------------------------------------------ #
    # Step 3 — Protect private endpoints.                                #
    # ------------------------------------------------------------------ #
    protect_endpoints = StepContract(
        id="protect-private-endpoints",
        description="Require authentication on private endpoints.",
        goal="Private endpoints cannot be reached without a valid token.",
        acceptance_checks=[
            AcceptanceCheck(
                id="private_endpoints_require_auth",
                description=(
                    "Every private endpoint rejects unauthenticated "
                    "requests."
                ),
            ),
            AcceptanceCheck(
                id="public_endpoints_remain_open",
                description=(
                    "Public endpoints (login, health) remain reachable "
                    "without a token."
                ),
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_plaintext_secret_committed",
                description="No plaintext signing secret is committed to the "
                "repository.",
                severity=1.0,
            ),
        ],
        expected_artifacts=["src/routes/private.py"],
        budget=_BUDGET,
    )

    # ------------------------------------------------------------------ #
    # Step 4 — Tests.                                                    #
    # ------------------------------------------------------------------ #
    auth_tests = StepContract(
        id="auth-tests",
        description=(
            "Add tests covering token creation, middleware, and route "
            "protection."
        ),
        goal="The authentication surface is covered by a green test suite.",
        acceptance_checks=[
            AcceptanceCheck(
                id="tests_pass",
                description="The full authentication test suite passes.",
            ),
            AcceptanceCheck(
                id="tests_cover_token_and_middleware",
                description=(
                    "Tests assert token creation, middleware rejection, "
                    "and route protection (not just '200 OK')."
                ),
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_tests_removed",
                description="No existing tests are deleted to force a green "
                "suite.",
                severity=1.0,
            ),
            RiskCheck(
                id="no_plaintext_secret_committed",
                description="No plaintext signing secret is committed to the "
                "repository.",
                severity=1.0,
            ),
        ],
        expected_artifacts=["tests/test_auth.py"],
        budget=_BUDGET,
    )

    return BoundPlan(
        goal=GOAL,
        steps=[token_creation, auth_middleware, protect_endpoints, auth_tests],
    )


def _print_budget(budget: StepBudget | None) -> None:
    """Print a one-line summary of a step's budget.

    Args:
        budget: The :class:`StepBudget` to summarise, or ``None``.
    """
    if budget is None:
        print("    budget:            (none)")
        return
    print(
        f"    budget:            max_tool_calls={budget.max_tool_calls}, "
        f"max_retries={budget.max_retries}, max_tokens={budget.max_tokens}, "
        f"max_runtime_seconds={budget.max_runtime_seconds}"
    )


def _print_plan(plan: BoundPlan) -> None:
    """Print every step of a :class:`BoundPlan` as a labelled contract block.

    Args:
        plan: The :class:`BoundPlan` to print.
    """
    print(f"Goal: {plan.goal}")
    print(f"Steps: {len(plan.steps)}\n")
    for index, step in enumerate(plan.steps, start=1):
        print(f"--- Step {index}/{len(plan.steps)} ---")
        print(f"  id:                {step.id}")
        print(f"  description:       {step.description}")
        print(f"  goal:              {step.goal}")
        print("  acceptance checks:")
        for check in step.acceptance_checks:
            tag = "required" if check.required else "optional"
            print(f"    - [{check.id}] ({tag}) {check.description}")
        print("  risk checks:")
        for check in step.risk_checks:
            print(
                f"    - [{check.id}] severity={check.severity:.1f}  "
                f"{check.description}"
            )
        print("  expected artifacts:")
        for artifact in step.expected_artifacts:
            print(f"    - {artifact}")
        _print_budget(step.budget)
        print()


def main() -> int:
    """Build the JWT plan, generate it via the seam, and print it.

    Returns:
        ``0`` on success (the example is illustrative and never fails the
        process), so the script can be chained in demos and CI smoke runs.
    """
    print("BOUND plan-to-contract example (no LLM)\n")
    print("=" * 72)
    print("Natural-language input")
    print("=" * 72)
    print(f"goal: {GOAL}")
    print("plan:")
    print(PLAN_TEXT)
    print()

    # 1. Build the explicit, validated BoundPlan by hand (deterministic).
    plan = _build_plan()

    # 2. Drive it through the ContractGenerator seam. StaticContractGenerator
    #    returns the exact plan by identity — the same seam an optional LLM
    #    adapter would implement (see the module docstring).
    generator = StaticContractGenerator(plan)
    generated = generator.generate(goal=GOAL, plan=PLAN_TEXT)

    # The seam is a pure pass-through here: the generated plan is the plan.
    assert generated is plan, (
        "StaticContractGenerator must return the plan by identity"
    )

    print("=" * 72)
    print("Compiled BoundPlan (from StaticContractGenerator)")
    print("=" * 72)
    _print_plan(generated)

    # Verify the structural guarantees enforced by Pydantic, so the example
    # doubles as a smoke test of the contract validators.
    assert len(generated.steps) == 4, "expected 4 steps in the JWT plan"
    assert all(step.budget is not None for step in generated.steps), (
        "every step must carry the shared budget"
    )
    for step in generated.steps:
        assert step.acceptance_checks, (
            f"step {step.id!r} must define acceptance checks"
        )

    print("=" * 72)
    print("Optional LLM adapter note")
    print("=" * 72)
    print(
        "An optional LLM-backed ContractGenerator (e.g. an adapter living "
        "behind `pip install bound[llm]`)\n"
        "could emit the SAME BoundPlan from the natural-language goal + plan "
        "above.\n"
        "The seam is unchanged: generate(goal=..., plan=..., context=...) -> "
        "BoundPlan only —\n"
        "never a BOUND decision, never A/I/R/C scores. Whatever the LLM emits "
        "is round-tripped\n"
        "through Pydantic validation (rejecting empty plans, steps with no "
        "acceptance checks,\n"
        "invalid risk severities, and invalid budgets) before BOUND can use "
        "it. The deterministic\n"
        "ContractEvaluator and BoundPolicy then own the scores and the final "
        "ACCEPT/RETRY/REPLAN/ROLLBACK.\n"
        "In other words: the LLM defines WHAT to measure; BOUND remains the "
        "deterministic arbiter\n"
        "of HOW it scores and decides. This script — and the whole package — "
        "runs without that adapter."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
