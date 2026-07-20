"""Canonical YAML policy configuration schema (BOUND v0.7.0).

This module defines the *policy configuration* — what a human reviews and
approves (``bound-policy.yaml``) — as strict Pydantic v2 models. It is
deliberately distinct from :mod:`bound.policy`, which is the *decision policy*
that turns a numeric score into ACCEPT/RETRY/REPLAN/ROLLBACK. Here we model the
declarative configuration a user approves before a run: which collectors to
trust, which checks are hard gates (blockers), which are weighted signals,
which budgets to enforce, and the scope/safety guardrails.

Three mechanisms are encoded (todo 2.2):

* **Hard gates** (:class:`HardGate`) — ``importance: blocker`` checks that can
  never be compensated by positive scores. Carries ``required``,
  ``on_failure``/``on_missing``/``on_claimed`` actions, ``minimum_assurance``
  and ``accepted_provenance``.
* **Weighted signals** (:class:`WeightedSignal`) — ``importance`` of
  ``high``/``medium``/``low``/``ignore`` mapped through
  :data:`DEFAULT_WEIGHTS`, with an optional explicit numeric ``weight``
  override. The resolved :attr:`~WeightedSignal.effective_weight` is stored on
  the model and is part of the canonical form.
* **Budgets** (:class:`BudgetDimension`) — soft/hard limits per dimension with
  a configurable :class:`~bound.contracts.EvidencePolicyAction` at each limit
  and an explicit ``enabled`` flag. Missing telemetry can never silently
  satisfy a declared budget (enforced downstream; the structure is encoded
  here).

Every model uses ``ConfigDict(extra="forbid")`` so a stray key is a clear
validation error rather than silent schema drift. Duplicate check IDs (across
all check lists) and duplicate collector IDs (YAML mapping keys) are rejected.
A JSON Schema is exported via :func:`policy_json_schema`, and
:func:`load_policy_yaml` / :func:`parse_policy_yaml` are the canonical loaders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bound.contracts import EvidencePolicyAction
from bound.evidence import EvidenceProvenance
from bound.models import DecisionAssurance

__all__ = [
    "DEFAULT_WEIGHTS",
    "POLICY_SCHEMA_VERSION",
    "BUDGET_DIMENSIONS",
    "ApprovalsPolicy",
    "BoundPolicyConfig",
    "BudgetDimension",
    "ChangeScope",
    "CollectorConfig",
    "HardGate",
    "PolicyIdentity",
    "UnexpectedArtifactsPolicy",
    "WeightedSignal",
    "load_policy_yaml",
    "parse_policy_yaml",
    "policy_json_schema",
]

#: Schema version of the policy configuration format. Bumped only on a
#: backwards-incompatible change to the ``bound-policy.yaml`` shape.
POLICY_SCHEMA_VERSION: str = "1.0"

#: The set of recognised budget dimension names. ``BoundPolicyConfig.budgets``
#: is keyed by these literals so an unknown dimension is a clear validation
#: error rather than a silently-ignored budget.
BUDGET_DIMENSIONS: tuple[str, ...] = (
    "attempts",
    "tool_calls",
    "tokens",
    "runtime",
    "financial_cost",
)

#: Default numeric weights for weighted-signal importance tiers (todo 2.2).
#: ``ignore`` always contributes ``0.0``. An explicit ``weight`` override on a
#: :class:`WeightedSignal` takes precedence over this map.
DEFAULT_WEIGHTS: dict[str, float] = {
    "high": 1.0,
    "medium": 0.5,
    "low": 0.25,
    "ignore": 0.0,
}

#: Importance tiers accepted on a weighted signal.
WeightImportance = Literal["high", "medium", "low", "ignore"]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class PolicyIdentity(BaseModel):
    """Identity of the approved policy.

    Attributes:
        id: Stable, human-readable policy identifier (e.g. ``coding-default``).
        version: Policy version label (e.g. ``"1.0"``). Bumped when the policy
            content changes; the canonical hash (see
            :mod:`bound.policy_canon`) is the exact-content identifier.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


class CollectorConfig(BaseModel):
    """Configuration for one named collector (``collectors: {<name>: {...}}``).

    A collector is the seam that turns execution into evidence. This model
    captures the declarative configuration a user approves — never executable
    code injected by the agent. All fields are optional except ``type`` so a
    purely built-in collector (e.g. ``bound.pytest``) can be referenced with
    only a type, while a generic ``command`` collector supplies its argv.

    Attributes:
        type: Collector implementation identifier (e.g. ``"command"``,
            ``"pytest"``, ``"junit"``, ``"git"``, ``"process_runtime"``,
            ``"budget"``). Free-form so new collectors can be added without a
            schema change; the policy-collectors layer resolves it.
        command: Argv for a ``command`` collector, as a list of strings (e.g.
            ``["pytest", "-q"]``). ``None`` for built-in collectors.
        success_exit_codes: Exit codes treated as success. Defaults to ``[0]``.
        timeout_seconds: Hard timeout in seconds (``>= 0``), or ``None`` for no
            explicit timeout.
        cwd: Working directory the collector runs in, or ``None`` to inherit.
        env_allowlist: Names of environment variables the collector is allowed
            to read/pass through. An empty/``None`` allowlist means no env is
            forwarded (the safe default).
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    command: list[str] | None = None
    success_exit_codes: list[int] = Field(default_factory=lambda: [0])
    timeout_seconds: float | None = Field(default=None, ge=0.0)
    cwd: str | None = None
    env_allowlist: list[str] | None = None

    @model_validator(mode="after")
    def _command_required_for_command_type(self) -> CollectorConfig:
        """A ``command`` collector must declare its argv.

        Returns:
            The validated collector config.

        Raises:
            ValueError: If ``type`` is ``"command"`` but ``command`` is unset.
        """
        if self.type == "command" and not self.command:
            raise ValueError(
                "collector of type 'command' requires a non-empty 'command' argv"
            )
        return self


# ---------------------------------------------------------------------------
# Hard gates (acceptance + risk)
# ---------------------------------------------------------------------------


class HardGate(BaseModel):
    """A hard gate: an uncompensable blocker check (todo 2.2).

    Hard gates are used for both ``acceptance_checks`` and ``risk_checks``. A
    blocker can never be compensated by positive weighted scores — the
    policy-gating layer enforces this; this model only encodes the flag
    (:attr:`importance` is fixed to ``"blocker"``) and the per-outcome actions.

    Attributes:
        id: Stable identifier correlating collected evidence with this gate.
        description: Human-readable statement of the expected outcome / risk.
        importance: Always ``"blocker"`` for a hard gate. (Kept as a field so
            the canonical form records the mechanism explicitly.)
        required: Whether this gate must hold. Defaults to ``True``.
        on_failure: Policy action when the gate is observed to fail. Defaults
            to :attr:`RETRY <EvidencePolicyAction.RETRY>`.
        on_missing: Policy action when no evidence is collected. Defaults to
            :attr:`RETRY <EvidencePolicyAction.RETRY>`.
        on_claimed: Policy action when the only evidence is CLAIMED. Defaults to
            :attr:`RETRY <EvidencePolicyAction.RETRY>`.
        minimum_assurance: Optional minimum
            :class:`~bound.models.DecisionAssurance` the gate's evidence must
            meet for a clean ``ACCEPT``; ``None`` means no per-gate floor.
        accepted_provenance: Optional allow-list of
            :class:`~bound.evidence.EvidenceProvenance` values this gate
            accepts. ``None`` accepts any provenance; a non-empty list rejects
            unlisted provenance (e.g. CLAIMED / MISSING).
        collector: Optional name of the collector that produces this gate's
            evidence, binding the gate to a configured or built-in collector.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    importance: Literal["blocker"] = "blocker"
    required: bool = True
    on_failure: EvidencePolicyAction = EvidencePolicyAction.RETRY
    on_missing: EvidencePolicyAction = EvidencePolicyAction.RETRY
    on_claimed: EvidencePolicyAction = EvidencePolicyAction.RETRY
    minimum_assurance: DecisionAssurance | None = None
    accepted_provenance: list[EvidenceProvenance] | None = None
    collector: str | None = None

    @model_validator(mode="after")
    def _accepted_provenance_non_empty_if_set(self) -> HardGate:
        """Reject an empty ``accepted_provenance`` list.

        An empty allow-list rejects all evidence — almost certainly a mistake.
        Use ``None`` to express "accept any provenance".

        Returns:
            The validated gate (unchanged on success).

        Raises:
            ValueError: If ``accepted_provenance`` is an empty list.
        """
        if self.accepted_provenance is not None and len(self.accepted_provenance) == 0:
            raise ValueError(
                "accepted_provenance must be None (accept any) or a non-empty "
                "list of EvidenceProvenance values."
            )
        return self


# ---------------------------------------------------------------------------
# Weighted signals (quality)
# ---------------------------------------------------------------------------


class WeightedSignal(BaseModel):
    """A weighted signal: a soft quality contribution (todo 2.2).

    Weighted signals populate ``quality_checks`` and are deliberately kept
    separate from hard gates: they add a numeric contribution to the score and
    can never override a blocker. The :attr:`importance` tier maps through
    :data:`DEFAULT_WEIGHTS` to an :attr:`effective_weight`; an explicit numeric
    :attr:`weight` override takes precedence. The resolved
    :attr:`effective_weight` is stored on the model and is part of the canonical
    form (see :mod:`bound.policy_canon`).

    Attributes:
        id: Stable identifier correlating collected evidence with this signal.
        description: Human-readable statement of the quality signal.
        importance: Weight tier — ``"high"``/``"medium"``/``"low"``/``"ignore"``.
            Defaults to ``"medium"``. ``"ignore"`` contributes ``0.0``.
        weight: Optional explicit numeric weight override (``>= 0.0``). When
            ``None`` the effective weight is derived from :attr:`importance`.
        effective_weight: The resolved weight actually used in scoring. Set
            automatically during validation: ``weight`` if provided, else
            ``DEFAULT_WEIGHTS[importance]``. Serialised as part of the canonical
            form.
        accepted_provenance: Optional allow-list of
            :class:`~bound.evidence.EvidenceProvenance` values this signal
            accepts. ``None`` accepts any provenance.
        collector: Optional name of the collector that produces this signal's
            evidence.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    importance: WeightImportance = "medium"
    weight: float | None = Field(default=None, ge=0.0)
    effective_weight: float = Field(default=0.0, ge=0.0)
    accepted_provenance: list[EvidenceProvenance] | None = None
    collector: str | None = None

    @model_validator(mode="after")
    def _resolve_effective_weight(self) -> WeightedSignal:
        """Resolve and store :attr:`effective_weight` (todo 2.2).

        ``weight`` (when provided) overrides the importance-derived default;
        otherwise :data:`DEFAULT_WEIGHTS` supplies the default for the
        :attr:`importance` tier. An explicit user-supplied ``effective_weight``
        is always overwritten by resolution so the field is never inconsistent
        with ``weight`` / ``importance``.

        Returns:
            The validated signal with ``effective_weight`` populated.

        Raises:
            ValueError: If ``importance`` is not in :data:`DEFAULT_WEIGHTS`
                (should be impossible via the ``Literal`` but guarded anyway).
        """
        if self.weight is not None:
            self.effective_weight = float(self.weight)
        else:
            default = DEFAULT_WEIGHTS.get(self.importance)
            if default is None:
                raise ValueError(
                    f"importance {self.importance!r} has no default weight"
                )
            self.effective_weight = float(default)
        return self

    @model_validator(mode="after")
    def _accepted_provenance_non_empty_if_set(self) -> WeightedSignal:
        """Reject an empty ``accepted_provenance`` list (see :class:`HardGate`).

        Returns:
            The validated signal (unchanged on success).

        Raises:
            ValueError: If ``accepted_provenance`` is an empty list.
        """
        if self.accepted_provenance is not None and len(self.accepted_provenance) == 0:
            raise ValueError(
                "accepted_provenance must be None (accept any) or a non-empty "
                "list of EvidenceProvenance values."
            )
        return self


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


class BudgetDimension(BaseModel):
    """One budget dimension with soft/hard limits and per-limit actions.

    A budget (todo 2.2) has a ``soft_limit`` and a ``hard_limit`` (each
    optional — ``None`` means "not enforced for this limit") plus the
    :class:`~bound.contracts.EvidencePolicyAction` to take at each limit and an
    ``enabled`` flag so a budget can be explicitly disabled. Missing telemetry
    can never silently satisfy a declared budget: the policy-gating layer
    treats a missing measurement as *not within budget*; this model only encodes
    the structure and the ``enabled`` flag.

    Attributes:
        soft_limit: Soft limit value (``>= 0``), or ``None`` for no soft limit.
        hard_limit: Hard limit value (``>= 0``), or ``None`` for no hard limit.
        on_soft: Action when the soft limit is reached/exceeded. Defaults to
            :attr:`RETRY <EvidencePolicyAction.RETRY>`.
        on_hard: Action when the hard limit is reached/exceeded. Defaults to
            :attr:`REPLAN <EvidencePolicyAction.REPLAN>`.
        enabled: When ``False`` the dimension is explicitly disabled (not
            enforced). Defaults to ``True``.
    """

    model_config = ConfigDict(extra="forbid")

    soft_limit: float | None = Field(default=None, ge=0.0)
    hard_limit: float | None = Field(default=None, ge=0.0)
    on_soft: EvidencePolicyAction = EvidencePolicyAction.RETRY
    on_hard: EvidencePolicyAction = EvidencePolicyAction.REPLAN
    enabled: bool = True

    @model_validator(mode="after")
    def _hard_not_below_soft(self) -> BudgetDimension:
        """When both limits are set, ``hard_limit`` must not be below ``soft_limit``.

        Returns:
            The validated budget dimension.

        Raises:
            ValueError: If both limits are set and ``hard_limit < soft_limit``.
        """
        if (
            self.soft_limit is not None
            and self.hard_limit is not None
            and self.hard_limit < self.soft_limit
        ):
            raise ValueError("budget hard_limit must not be below soft_limit")
        return self


#: Type alias for the budgets mapping key — one of :data:`BUDGET_DIMENSIONS`.
BudgetName = Literal["attempts", "tool_calls", "tokens", "runtime", "financial_cost"]


# ---------------------------------------------------------------------------
# Scope and safety
# ---------------------------------------------------------------------------


class UnexpectedArtifactsPolicy(BaseModel):
    """Policy for artefacts the step did not declare (todo 2.3).

    Attributes:
        enabled: When ``True`` (default) unexpected artefacts are detected and
            acted on; ``False`` disables detection.
        on_unexpected: Action when an unexpected artefact is found. Defaults to
            :attr:`REPLAN <EvidencePolicyAction.REPLAN>`.
        allowed_patterns: Additional path/glob patterns permitted beyond the
            contract's ``expected_artifacts``. Matches are not "unexpected".
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    on_unexpected: EvidencePolicyAction = EvidencePolicyAction.REPLAN
    allowed_patterns: list[str] = Field(default_factory=list)


class ChangeScope(BaseModel):
    """Scope and safety guardrails (todo 2.3).

    Attributes:
        allowed_paths: Path/glob patterns the agent is allowed to modify. An
            empty list means "no explicit allow-list" (the agent is not
            constrained by an allow-list, but ``forbidden_paths`` still apply).
        forbidden_paths: Path/glob patterns the agent must never modify.
        dependency_file_patterns: Patterns matching dependency-manifest files
            (e.g. ``pyproject.toml``, ``package.json``, ``Cargo.toml``); a
            change to one is flagged so a dependency change is never silent.
        unexpected_artifacts: Policy for undeclared artefacts.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    dependency_file_patterns: list[str] = Field(default_factory=list)
    unexpected_artifacts: UnexpectedArtifactsPolicy = Field(
        default_factory=UnexpectedArtifactsPolicy
    )


class ApprovalsPolicy(BaseModel):
    """Human-approval and rollback guardrails (todo 2.3).

    Attributes:
        commands_requiring_approval: Command names/patterns that require
            explicit human approval before BOUND lets them proceed.
        destructive_actions: High-risk actions (e.g. ``rm -rf``, force-push)
            that are flagged or blocked.
        require_rollback_availability: When ``True``, a clean ``ACCEPT`` requires
            rollback to be available (verified independently); otherwise the
            :attr:`on_missing_rollback` action applies.
        on_missing_rollback: Action when rollback is required but not
            available. Defaults to :attr:`REPLAN <EvidencePolicyAction.REPLAN>`.
    """

    model_config = ConfigDict(extra="forbid")

    commands_requiring_approval: list[str] = Field(default_factory=list)
    destructive_actions: list[str] = Field(default_factory=list)
    require_rollback_availability: bool = False
    on_missing_rollback: EvidencePolicyAction = EvidencePolicyAction.REPLAN


# ---------------------------------------------------------------------------
# Top-level policy configuration
# ---------------------------------------------------------------------------


class BoundPolicyConfig(BaseModel):
    """The approved policy configuration (``bound-policy.yaml``).

    This is the top-level model a human reviews and approves. Once approved it
    is canonicalised and hashed (see :mod:`bound.policy_canon`) so every
    decision can record *which* exact policy governed it.

    Attributes:
        schema_version: Policy schema version; currently ``"1.0"``.
        policy: :class:`PolicyIdentity` (id + version).
        collectors: Mapping of collector name → :class:`CollectorConfig`.
        acceptance_checks: Hard gates (blockers) for acceptance.
        quality_checks: Weighted signals (:class:`WeightedSignal`).
        risk_checks: Hard gates (blockers) for risk.
        budgets: Mapping of budget dimension name → :class:`BudgetDimension`.
        change_scope: :class:`ChangeScope` scope/safety guardrails.
        approvals: :class:`ApprovalsPolicy` approval/rollback guardrails.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = POLICY_SCHEMA_VERSION
    policy: PolicyIdentity
    collectors: dict[str, CollectorConfig] = Field(default_factory=dict)
    acceptance_checks: list[HardGate] = Field(default_factory=list)
    quality_checks: list[WeightedSignal] = Field(default_factory=list)
    risk_checks: list[HardGate] = Field(default_factory=list)
    budgets: dict[BudgetName, BudgetDimension] = Field(default_factory=dict)
    change_scope: ChangeScope = Field(default_factory=ChangeScope)
    approvals: ApprovalsPolicy = Field(default_factory=ApprovalsPolicy)

    @model_validator(mode="after")
    def _reject_duplicate_check_ids(self) -> BoundPolicyConfig:
        """Reject duplicate check IDs across all check lists (todo 2.1).

        Check IDs correlate collected evidence with their declaration; a
        duplicate would make evidence attribution ambiguous, so it is a
        validation error rather than a silently-merged list.

        Returns:
            The validated policy config.

        Raises:
            ValueError: If any check id appears more than once across
                ``acceptance_checks`` / ``quality_checks`` / ``risk_checks``.
        """
        seen: dict[str, str] = {}
        for section_name, checks in (
            ("acceptance_checks", self.acceptance_checks),
            ("quality_checks", self.quality_checks),
            ("risk_checks", self.risk_checks),
        ):
            for check in checks:
                if check.id in seen:
                    raise ValueError(
                        f"duplicate check id {check.id!r}: already declared in "
                        f"{seen[check.id]!r} and re-declared in {section_name!r}"
                    )
                seen[check.id] = section_name
        return self


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------


def policy_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for :class:`BoundPolicyConfig`.

    The schema is generated by Pydantic v2 and is suitable for editor
    validation/generation of ``bound-policy.yaml``-equivalent JSON. It is a
    pure function (no side effects) and stable for a given schema version.

    Returns:
        A JSON Schema (dict) describing the canonical policy configuration.
    """
    return BoundPolicyConfig.model_json_schema()


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class _NoDuplicateKeysLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    PyYAML's default loaders silently let a later mapping key overwrite an
    earlier one, which would let a duplicate collector ID silently win. This
    loader raises on the first duplicate so a malformed policy is a clear error.
    """


def _construct_mapping_no_duplicates(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """Construct a mapping, raising on the first duplicate key.

    Args:
        loader: The YAML loader instance.
        node: The mapping node being constructed.
        deep: Whether to deeply construct values.

    Returns:
        The constructed mapping (no duplicate keys).

    Raises:
        ValueError: If the mapping contains a duplicate key.
    """
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(f"duplicate YAML key: {key!r}")
        seen.add(key)
    # Delegate to the original constructor for the real build.
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_NoDuplicateKeysLoader.construct_mapping = _construct_mapping_no_duplicates  # type: ignore[assignment]


def parse_policy_yaml(text: str) -> BoundPolicyConfig:
    """Parse a ``bound-policy.yaml`` document into a validated config.

    Loads the YAML with duplicate-key detection (so duplicate collector IDs
    are a clear error), then validates it strictly through
    :class:`BoundPolicyConfig` (``extra="forbid"``).

    Args:
        text: The raw YAML document text.

    Returns:
        The validated :class:`BoundPolicyConfig`.

    Raises:
        pydantic.ValidationError: If the document does not match the schema.
        ValueError: If the YAML contains duplicate mapping keys or is not a
            top-level mapping.
        yaml.YAMLError: If the document is not valid YAML.
    """
    raw = yaml.load(text, Loader=_NoDuplicateKeysLoader)  # noqa: S506 (trusted policy text)
    if not isinstance(raw, dict):
        raise ValueError(
            "policy YAML must be a mapping at the top level, got "
            f"{type(raw).__name__}"
        )
    return BoundPolicyConfig.model_validate(raw)


def load_policy_yaml(path: str | Path) -> BoundPolicyConfig:
    """Load and validate a ``bound-policy.yaml`` file from disk.

    Args:
        path: Path to the policy YAML file.

    Returns:
        The validated :class:`BoundPolicyConfig`.

    Raises:
        pydantic.ValidationError: If the file does not match the schema.
        ValueError: If the YAML contains duplicate mapping keys or is not a
            top-level mapping.
        yaml.YAMLError: If the file is not valid YAML.
        FileNotFoundError: If ``path`` does not exist.
    """
    text = Path(path).read_text(encoding="utf-8")
    return parse_policy_yaml(text)


