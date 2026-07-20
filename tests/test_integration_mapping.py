from __future__ import annotations

import ast
import importlib
import pathlib
import sys
from typing import Any

import pytest

from bound.contracts import AcceptanceCheck, RiskCheck, StepContract, is_valid_phase_id
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.integration import _DECISION_TO_ACTION, evaluate_agent_step
from bound.integration_spec import integration_spec
from bound.models import BoundCriteria, BoundWeights

#: The canonical BOUND decision -> control-action mapping. Lives ONLY in tests,
#: used purely to assert the public API (``bound.integration`` and
#: ``bound.integration_spec``) agrees with it. It is never consulted at runtime
#: to translate a decision independently of BOUND.
from tests.conftest import DECISION_TO_CONTROL

EXPECTED_DECISION_TO_CONTROL = DECISION_TO_CONTROL

_REQUIRED = [
    AcceptanceCheck(id="a", description="check a"),
    AcceptanceCheck(id="b", description="check b"),
]


def _contract(*, risk_checks: list[RiskCheck] | None = None) -> StepContract:
    """Build a contract with two required acceptance checks and optional risk checks.

    Args:
        risk_checks: Optional risk checks to attach.

    Returns:
        A :class:`StepContract`.
    """
    return StepContract(
        id="PHASE-001",
        description="do the step",
        goal="advance the goal",
        acceptance_checks=list(_REQUIRED),
        risk_checks=risk_checks or [],
    )


def _passed(check_id: str) -> CheckEvidence:
    """Build a passing :class:`CheckEvidence` for ``check_id``."""
    return CheckEvidence(check_id=check_id, passed=True, source="pytest")


def _failed(check_id: str) -> CheckEvidence:
    """Build a failing :class:`CheckEvidence` for ``check_id``."""
    return CheckEvidence(check_id=check_id, passed=False, source="pytest")


# ---------------------------------------------------------------------------
# Phase 7: the public mapping is the single runtime source
# ---------------------------------------------------------------------------


def test_expected_mapping_matches_integration_runtime_source() -> None:
    """The runtime ``_DECISION_TO_ACTION`` equals the expected mapping."""
    assert _DECISION_TO_ACTION == EXPECTED_DECISION_TO_CONTROL


def test_expected_mapping_matches_integration_spec() -> None:
    """The published ``decision_to_control`` spec equals the expected mapping."""
    assert integration_spec()["decision_to_control"] == EXPECTED_DECISION_TO_CONTROL


def test_runtime_source_and_spec_agree() -> None:
    """The runtime source and the published spec carry the same mapping."""
    assert integration_spec()["decision_to_control"] == _DECISION_TO_ACTION


@pytest.mark.parametrize(
    ("decision", "contract", "evidence", "criteria"),
    [
        pytest.param(
            "ACCEPT",
            _contract(),
            ExecutionEvidence(
                acceptance=[_passed("a"), _passed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6),
            id="accept",
        ),
        pytest.param(
            "RETRY",
            _contract(),
            ExecutionEvidence(
                acceptance=[_passed("a"), _failed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6, retry_margin=0.2),
            id="retry",
        ),
        pytest.param(
            "REPLAN",
            _contract(),
            ExecutionEvidence(
                acceptance=[_failed("a"), _failed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6, retry_margin=0.1),
            id="replan",
        ),
        pytest.param(
            "ROLLBACK",
            _contract(
                risk_checks=[RiskCheck(id="r", description="boundary", severity=0.9)]
            ),
            ExecutionEvidence(
                acceptance=[_passed("a"), _passed("b")],
                risks=[_failed("r")],
                rollback_available=True,
            ),
            BoundCriteria(
                threshold=0.6,
                rollback_risk_threshold=0.8,
                weights=BoundWeights(acceptance=2.0),
            ),
            id="rollback",
        ),
    ],
)
def test_evaluate_agent_step_maps_each_decision_to_expected_action(
    decision: str,
    contract: StepContract,
    evidence: ExecutionEvidence,
    criteria: BoundCriteria,
) -> None:
    """``evaluate_agent_step`` maps the BOUND decision to the expected control action.

    Each parametrized case is constructed so the deterministic pipeline yields a
    different decision (ACCEPT / RETRY / REPLAN / ROLLBACK); the resulting
    ``next_action`` must match the expected mapping and the decision the public
    API reports. This proves the mapping is exercised end to end through the
    public surface, not just inspected as a constant.
    """
    result = evaluate_agent_step(contract=contract, evidence=evidence, criteria=criteria)
    assert result.evaluation.decision == decision
    assert result.next_action == EXPECTED_DECISION_TO_CONTROL[decision]


# ---------------------------------------------------------------------------
# Phase 7: the example must use the public API and not duplicate the mapping
# ---------------------------------------------------------------------------


def _example_source() -> str:
    """Return the source text of ``examples/agent_control_loop.py``."""
    path = (
        pathlib.Path(__file__).resolve().parent.parent / "examples" / "agent_control_loop.py"
    )
    assert path.exists(), f"example not found at {path}"
    return path.read_text(encoding="utf-8")


def test_example_imports_evaluate_agent_step_from_public_api() -> None:
    """The example consumes the public ``evaluate_agent_step`` API."""
    source = _example_source()
    assert "from bound.integration import" in source
    assert "evaluate_agent_step" in source


def test_example_does_not_define_runtime_decision_mapping() -> None:
    """The example must not re-implement the BOUND decision->action mapping.

    Audits the example AST for an ``Assign`` whose value is a ``dict`` literal
    keyed by the BOUND decision strings (ACCEPT/RETRY/REPLAN/ROLLBACK) — the
    structural signature of a duplicate runtime mapping. The example must
    obtain ``next_action`` only from the public :func:`evaluate_agent_step` API.
    """
    tree = ast.parse(_example_source(), filename="agent_control_loop.py")
    decision_keys = set(EXPECTED_DECISION_TO_CONTROL)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            if isinstance(value, ast.Dict):
                keys = {
                    k.value
                    for k in value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
                assert not (keys & decision_keys), (
                    "examples/agent_control_loop.py must not define its own "
                    f"decision->action mapping; found keys: {keys & decision_keys}"
                )


def test_example_does_not_shadow_mapping_via_module_attribute() -> None:
    """The imported example module exposes no decision->action mapping attribute.

    Imports the example in-process and asserts it has no module-level attribute
    that is a ``dict`` mapping the BOUND decisions to control actions — guarding
    against a mapping constructed dynamically rather than as a literal. It also
    confirms the example's ``evaluate_agent_step`` is the public API function.
    """
    examples_dir = pathlib.Path(__file__).resolve().parent.parent / "examples"
    sys.path.insert(0, str(examples_dir))
    try:
        module = importlib.import_module("agent_control_loop")
    finally:
        sys.path.remove(str(examples_dir))
        sys.modules.pop("agent_control_loop", None)

    for name in dir(module):
        if name.startswith("__"):
            continue
        value: Any = getattr(module, name)
        if isinstance(value, dict):
            assert not (set(value) & set(EXPECTED_DECISION_TO_CONTROL)), (
                "example module exposes a decision->action mapping attribute "
                f"'{name}' with decision keys: "
                f"{set(value) & set(EXPECTED_DECISION_TO_CONTROL)}"
            )
    assert hasattr(module, "evaluate_agent_step")
    assert module.evaluate_agent_step is evaluate_agent_step


# ---------------------------------------------------------------------------
# Phase 6: stable plan IDs survive into StepContract
# ---------------------------------------------------------------------------


def test_step_contract_preserves_phase_id() -> None:
    """A ``PHASE-001`` id is preserved verbatim on the constructed contract."""
    contract = StepContract(
        id="PHASE-001",
        description="Add input validation",
        goal="Add input validation to the registration endpoint.",
        acceptance_checks=[AcceptanceCheck(id="valid_input_passes", description="ok")],
    )
    assert contract.id == "PHASE-001"


def test_plan_token_maps_to_step_contract_id() -> None:
    """A PLAN.md-style ``PHASE-001`` token maps to ``StepContract(id="PHASE-001")``.

    Simulates the plan->contract lineage: the plan identifier is carried
    unchanged into the contract id (never replaced with an unrelated slug), and
    the helper recognises it as a valid phase id.
    """
    plan_token = "PHASE-001"
    assert is_valid_phase_id(plan_token)
    contract = StepContract(
        id=plan_token,
        description="the planned phase",
        goal="advance the plan",
        acceptance_checks=[AcceptanceCheck(id="a", description="check a")],
    )
    assert contract.id == plan_token
    assert contract.id == "PHASE-001"


@pytest.mark.parametrize(
    "phase_id",
    [
        "PHASE-001",
        "PHASE-002",
        "PHASE-003",
        "PHASE-002-A",
        "PHASE-002-B",
        "PHASE-001-R1",
        "PHASE-001-R2",
        "PHASE-001-R2-A",
        "PHASE-002-A-R1",
    ],
)
def test_is_valid_phase_id_accepts_documented_forms(phase_id: str) -> None:
    """Every documented stable-ID form validates as a phase id."""
    assert is_valid_phase_id(phase_id)


@pytest.mark.parametrize(
    "phase_id",
    [
        "phase-001",        # lowercase
        "PHASE-ABC",        # non-numeric
        "PHASE001",         # missing dash
        "PHASE-001-",       # trailing dash
        "PHASE-001-A-B",    # two letter suffixes
        "PHASE-001-R1-R2",  # two replan suffixes
        "write-tests",      # unrelated slug
        "",                 # empty
    ],
)
def test_is_valid_phase_id_rejects_undocumented_forms(phase_id: str) -> None:
    """Undocumented / malformed identifiers are rejected by the helper."""
    assert not is_valid_phase_id(phase_id)


def test_step_contract_id_field_remains_free_form() -> None:
    """Non-PHASE ids remain legal: the helper is advisory, not enforced.

    The contract ``id`` must stay a free-form ``str`` so existing identifiers
    (e.g. ``write-tests``) keep validating; the stable-ID convention is opt-in.
    """
    contract = StepContract(
        id="write-tests",
        description="Add unit tests",
        goal="Cover the parser",
        acceptance_checks=[AcceptanceCheck(id="tests_pass", description="tests pass")],
    )
    assert contract.id == "write-tests"
    assert not is_valid_phase_id(contract.id)


def test_replan_id_preserves_root_identity() -> None:
    """A replan id keeps the root ``PHASE-001`` identity (history preserved).

    PHASE-001 -> PHASE-001-R1 -> PHASE-001-R2 keeps the root identity; the
    builder must not silently substitute an unrelated id (v0.6 Phase 6 DoD).
    """
    for replan in ("PHASE-001-R1", "PHASE-001-R2"):
        assert is_valid_phase_id(replan)
        assert replan.startswith("PHASE-001")
    contract = StepContract(
        id="PHASE-001-R1",
        description="replan of phase 1",
        goal="advance the plan",
        acceptance_checks=[AcceptanceCheck(id="a", description="check a")],
    )
    assert contract.id == "PHASE-001-R1"
    assert contract.id.startswith("PHASE-001")
