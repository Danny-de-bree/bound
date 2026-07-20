from __future__ import annotations

import pytest
from pydantic import ValidationError

from bound.contracts import EvidencePolicyAction
from bound.evidence import EvidenceProvenance
from bound.models import DecisionAssurance
from bound.policy_canon import (
    canonicalize_policy,
    compute_contract_hash,
    compute_policy_hash,
    policy_changed_since,
)
from bound.policy_schema import (
    BUDGET_DIMENSIONS,
    DEFAULT_WEIGHTS,
    POLICY_SCHEMA_VERSION,
    ApprovalsPolicy,
    BudgetDimension,
    ChangeScope,
    CollectorConfig,
    HardGate,
    PolicyIdentity,
    UnexpectedArtifactsPolicy,
    WeightedSignal,
    load_policy_yaml,
    parse_policy_yaml,
    policy_json_schema,
)
from tests.conftest import REPO_ROOT

DEFAULT_POLICY_PATH = REPO_ROOT / "src" / "bound" / "default_policy.yaml"


def _minimal_yaml() -> str:
    return """
schema_version: "1.0"
policy:
  id: coding-default
  version: "1.0"
"""


def test_minimal_policy_parses() -> None:
    """A minimal policy with only identity validates and uses safe defaults."""
    cfg = parse_policy_yaml(_minimal_yaml())
    assert cfg.schema_version == "1.0"
    assert cfg.policy == PolicyIdentity(id="coding-default", version="1.0")
    assert cfg.collectors == {}
    assert cfg.acceptance_checks == []
    assert cfg.quality_checks == []
    assert cfg.risk_checks == []
    assert cfg.budgets == {}
    assert isinstance(cfg.change_scope, ChangeScope)
    assert isinstance(cfg.approvals, ApprovalsPolicy)


def test_load_policy_yaml_from_disk() -> None:
    """The shipped default policy loads and validates from disk."""
    cfg = load_policy_yaml(DEFAULT_POLICY_PATH)
    assert cfg.policy.id == "coding-default"
    assert cfg.policy.version == "1.0"
    assert any(c.id == "tests-pass" for c in cfg.acceptance_checks)
    assert any(c.id == "lint-clean" for c in cfg.quality_checks)
    assert "tool_calls" in cfg.budgets
    assert cfg.change_scope.allowed_paths == ["src/**", "tests/**"]


def test_unknown_top_level_field_rejected() -> None:
    """``extra='forbid'`` rejects stray top-level keys."""
    with pytest.raises(ValidationError):
        parse_policy_yaml(_minimal_yaml() + "unknown_section: {}\n")


def test_wrong_schema_version_rejected() -> None:
    """Only ``schema_version: '1.0'`` is accepted."""
    bad = _minimal_yaml().replace('schema_version: "1.0"', 'schema_version: "2.0"')
    with pytest.raises(ValidationError):
        parse_policy_yaml(bad)


def test_unknown_budget_dimension_rejected() -> None:
    """Budget keys are constrained to the recognised dimensions."""
    bad = _minimal_yaml() + "budgets:\n  nonsense:\n    hard_limit: 1\n"
    with pytest.raises(ValidationError):
        parse_policy_yaml(bad)


def test_duplicate_check_id_rejected() -> None:
    """A check id reused across sections is a validation error."""
    bad = _minimal_yaml() + """
acceptance_checks:
  - id: dup
    description: "first"
quality_checks:
  - id: dup
    description: "second"
"""
    with pytest.raises(ValidationError) as exc:
        parse_policy_yaml(bad)
    assert "duplicate check id" in str(exc.value)


def test_duplicate_collector_id_rejected() -> None:
    """Duplicate YAML mapping keys for a collector raise at load time."""
    bad = (
        _minimal_yaml()
        + "collectors:\n  pytest:\n    type: pytest\n"
        + "  pytest:\n    type: command\n    command: ['x']\n"
    )
    with pytest.raises(ValueError):
        parse_policy_yaml(bad)


# ---------------------------------------------------------------------------
# Hard gate / weighted signal / collector / budget semantics
# ---------------------------------------------------------------------------


def test_hard_gate_importance_locked_to_blocker() -> None:
    """A HardGate's importance is always 'blocker'."""
    gate = HardGate(id="g", description="d")
    assert gate.importance == "blocker"
    assert gate.required is True
    with pytest.raises(ValidationError):
        HardGate(id="g", description="d", importance="medium")  # type: ignore[call-arg]


def test_hard_gate_rejects_empty_accepted_provenance() -> None:
    """An empty accepted_provenance allow-list rejects all evidence."""
    with pytest.raises(ValidationError):
        HardGate(id="g", description="d", accepted_provenance=[])


def test_weighted_signal_default_weights_resolved() -> None:
    """importance maps to DEFAULT_WEIGHTS; effective_weight is stored."""
    assert DEFAULT_WEIGHTS == {"high": 1.0, "medium": 0.5, "low": 0.25, "ignore": 0.0}
    assert WeightedSignal(id="s", description="d", importance="medium").effective_weight == 0.5
    assert WeightedSignal(id="s", description="d", importance="high").effective_weight == 1.0
    assert WeightedSignal(id="s", description="d", importance="ignore").effective_weight == 0.0


def test_weighted_signal_explicit_weight_overrides() -> None:
    """An explicit numeric weight overrides the importance-derived default."""
    sig = WeightedSignal(id="s", description="d", importance="low", weight=2.0)
    assert sig.effective_weight == 2.0


def test_weighted_signal_negative_weight_rejected() -> None:
    """Weights must be >= 0.0."""
    with pytest.raises(ValidationError):
        WeightedSignal(id="s", description="d", weight=-0.1)


def test_collector_command_requires_argv() -> None:
    """A command collector without a command argv is invalid."""
    with pytest.raises(ValidationError):
        CollectorConfig(type="command")
    assert CollectorConfig(type="command", command=["echo", "hi"]).command == ["echo", "hi"]


def test_budget_hard_not_below_soft() -> None:
    """hard_limit must not be below soft_limit when both are set."""
    with pytest.raises(ValidationError):
        BudgetDimension(soft_limit=5, hard_limit=2)
    assert BudgetDimension(soft_limit=2, hard_limit=2).hard_limit == 2


def test_budget_dimensions_constant() -> None:
    """BUDGET_DIMENSIONS lists the five recognised budget names."""
    assert set(BUDGET_DIMENSIONS) == {
        "attempts",
        "tool_calls",
        "tokens",
        "runtime",
        "financial_cost",
    }


def test_policy_schema_version_constant() -> None:
    assert POLICY_SCHEMA_VERSION == "1.0"


def test_policy_json_schema_is_dict_with_props() -> None:
    """policy_json_schema() returns a JSON Schema describing the policy."""
    schema = policy_json_schema()
    assert isinstance(schema, dict)
    assert "properties" in schema
    assert "policy" in schema["properties"]
    assert "acceptance_checks" in schema["properties"]


def test_unexpected_artifacts_default() -> None:
    assert UnexpectedArtifactsPolicy().enabled is True
    assert isinstance(ChangeScope().unexpected_artifacts, UnexpectedArtifactsPolicy)


_FULL_YAML = """
schema_version: "1.0"
policy:
  id: coding-default
  version: "1.0"
collectors:
  pytest:
    type: pytest
  mycmd:
    type: command
    command: ["ruff", "check", "."]
    success_exit_codes: [0, 2]
    timeout_seconds: 30
    cwd: "."
    env_allowlist: ["PATH"]
acceptance_checks:
  - id: tests-pass
    description: "All tests pass"
    importance: blocker
    on_failure: retry
    on_missing: retry
    on_claimed: replan
    minimum_assurance: verified
    accepted_provenance: [verified, observed]
    collector: pytest
quality_checks:
  - id: lint-clean
    description: "Lint is clean"
    importance: medium
  - id: coverage
    description: "Coverage does not regress"
    importance: high
    weight: 2.0
risk_checks:
  - id: no-secrets
    description: "No secrets introduced"
    importance: blocker
    on_failure: rollback
budgets:
  attempts:
    soft_limit: 2
    hard_limit: 3
    on_soft: retry
    on_hard: replan
  financial_cost:
    enabled: false
change_scope:
  allowed_paths: ["src/**", "tests/**"]
  forbidden_paths: [".git/**"]
  dependency_file_patterns: ["pyproject.toml"]
  unexpected_artifacts:
    enabled: true
    on_unexpected: replan
    allowed_patterns: ["*.md"]
approvals:
  commands_requiring_approval: ["rm"]
  destructive_actions: ["rm -rf"]
  require_rollback_availability: true
  on_missing_rollback: replan
"""


def test_full_policy_parses_and_round_trips_fields() -> None:
    """A fully-specified policy round-trips every section."""
    cfg = parse_policy_yaml(_FULL_YAML)
    assert cfg.policy.id == "coding-default"
    assert set(cfg.collectors) == {"pytest", "mycmd"}
    assert cfg.collectors["mycmd"].command == ["ruff", "check", "."]
    assert cfg.collectors["mycmd"].success_exit_codes == [0, 2]
    assert cfg.collectors["mycmd"].env_allowlist == ["PATH"]
    acc = cfg.acceptance_checks[0]
    assert acc.importance == "blocker"
    assert acc.on_failure is EvidencePolicyAction.RETRY
    assert acc.on_claimed is EvidencePolicyAction.REPLAN
    assert acc.minimum_assurance is DecisionAssurance.VERIFIED
    assert acc.accepted_provenance == [EvidenceProvenance.VERIFIED, EvidenceProvenance.OBSERVED]
    assert acc.collector == "pytest"
    risk = cfg.risk_checks[0]
    assert risk.importance == "blocker"
    assert risk.on_failure is EvidencePolicyAction.ROLLBACK
    assert cfg.budgets["attempts"].soft_limit == 2
    assert cfg.budgets["financial_cost"].enabled is False
    assert cfg.change_scope.allowed_paths == ["src/**", "tests/**"]
    assert cfg.change_scope.unexpected_artifacts.on_unexpected is EvidencePolicyAction.REPLAN
    assert cfg.approvals.require_rollback_availability is True


# ---------------------------------------------------------------------------
# Canonicalisation and hashing (todo 4.2)
# ---------------------------------------------------------------------------


def test_policy_hash_is_prefixed_sha256_and_stable() -> None:
    """compute_policy_hash returns 'sha256:<64hex>' and is deterministic."""
    cfg = load_policy_yaml(DEFAULT_POLICY_PATH)
    h = compute_policy_hash(cfg)
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64
    assert compute_policy_hash(cfg) == h


def test_canonical_form_independent_of_yaml_key_order() -> None:
    """Two policies differing only in YAML key order hash the same."""
    a = parse_policy_yaml(
        """
schema_version: "1.0"
policy: {id: p, version: "1.0"}
acceptance_checks:
  - id: x
    description: "x"
quality_checks:
  - id: y
    description: "y"
"""
    )
    b = parse_policy_yaml(
        """
schema_version: "1.0"
policy: {version: "1.0", id: p}
quality_checks:
  - id: y
    description: "y"
acceptance_checks:
  - id: x
    description: "x"
"""
    )
    assert canonicalize_policy(a) == canonicalize_policy(b)
    assert compute_policy_hash(a) == compute_policy_hash(b)


def test_policy_hash_changes_with_effective_weight() -> None:
    """A changed effective weight changes the policy hash."""
    base = parse_policy_yaml(
        """
schema_version: "1.0"
policy: {id: p, version: "1.0"}
quality_checks:
  - id: y
    description: "y"
    importance: medium
"""
    )
    changed = parse_policy_yaml(
        """
schema_version: "1.0"
policy: {id: p, version: "1.0"}
quality_checks:
  - id: y
    description: "y"
    importance: high
"""
    )
    assert compute_policy_hash(base) != compute_policy_hash(changed)
    assert policy_changed_since(base, changed) is True
    assert policy_changed_since(base, base) is False


def test_policy_changed_since_accepts_hash_strings() -> None:
    """policy_changed_since compares model snapshots or hash strings."""
    cfg = load_policy_yaml(DEFAULT_POLICY_PATH)
    h = compute_policy_hash(cfg)
    assert policy_changed_since(h, h) is False
    assert policy_changed_since(h, "sha256:deadbeef") is True


def test_compute_contract_hash_bare_hex_and_matches_lineage() -> None:
    """policy_canon contract hash is bare 64-hex and equals lineage's."""
    from bound.contracts import AcceptanceCheck, StepContract
    from bound.lineage import compute_contract_hash as lineage_contract_hash

    contract = StepContract(
        id="PHASE-001",
        description="d",
        goal="g",
        acceptance_checks=[AcceptanceCheck(id="t", description="t")],
    )
    h = compute_contract_hash(contract)
    assert len(h) == 64
    assert h == lineage_contract_hash(contract)
    assert compute_contract_hash({"a": 1, "b": 2}) == compute_contract_hash({"b": 2, "a": 1})


def test_default_policy_yaml_is_in_package() -> None:
    """The default policy file lives under src/bound and is readable."""
    assert DEFAULT_POLICY_PATH.is_file()


