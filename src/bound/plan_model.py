"""Plan model — immutable plan, version, and run-plan-link Pydantic models.

Provides the core plan identity, versioning, and REPLAN semantics for BOUND.
Uses Pydantic BaseModel for data validation and the existing
:func:`bound.hashing.sha256_hex_bare` for content addressing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bound.hashing import sha256_hex_bare

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Plan(BaseModel):
    """Immutable representation of a BOUND plan.

    A Plan is identified by a stable ``plan_id`` derived from either
    YAML front matter, a stable hash of project + source path, or a
    generated UUID for implicit (no source) plans.

    Attributes:
        plan_id: Stable plan identifier.
        project_id: The project this plan belongs to.
        source_path: Path to the plan.md file, or ``None`` for implicit plans.
        created_at: UTC instant the plan record was first created.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    project_id: str
    source_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanVersion(BaseModel):
    """An immutable snapshot of a plan at a point in time.

    Each version captures the full raw content, a SHA-256 content hash,
    and a monotonically increasing version number.  Versions form a chain
    via ``parent_version`` so the full evolution of a plan is auditable.

    Attributes:
        plan_id: The plan this version belongs to.
        version: Monotonically increasing, 1-based version number.
        content_hash: SHA-256 hex digest of the raw content.
        content: Raw markdown content of this version.
        parent_version: Previous version number, or ``None`` for initial.
        source: How this version was created (``"file"``,
            ``"agent_submitted"``, ``"implicit"``, ``"replan"``).
        reason: Why this version was created, or ``None``.
        triggering_decision_id: Decision that triggered this version, or
            ``None``.
        parsed_steps: Parsed step data from the plan parser, or ``None``.
        created_at: UTC instant this version was created.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    version: int
    content_hash: str
    content: str
    parent_version: int | None = None
    source: str = "file"
    reason: str | None = None
    triggering_decision_id: str | None = None
    parsed_steps: list[dict] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunPlanLink(BaseModel):
    """Links a lineage run to a specific plan version.

    Tracks which plan version a run started with and which version
    is current (may differ after replanning).

    Attributes:
        run_id: The lineage run identifier.
        plan_id: The plan identifier.
        initial_plan_version: Plan version when the run started.
        current_plan_version: Current plan version (updated after replan).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    plan_id: str
    initial_plan_version: int
    current_plan_version: int


# ---------------------------------------------------------------------------
# Plan identity resolution
# ---------------------------------------------------------------------------


def _extract_front_matter(content: str) -> dict:
    """Parse YAML front matter from plan content.

    Extracts the YAML block between the first pair of ``---`` markers.
    Returns an empty dict when no front matter is present or when the
    YAML cannot be parsed.

    Args:
        content: Raw markdown content, possibly with front matter.

    Returns:
        Parsed front matter as a dict, or an empty dict.
    """
    import yaml

    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return {}

    lines = stripped.splitlines()
    if len(lines) < 2:
        return {}

    # Skip the opening ---
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}

    yaml_block = "\n".join(lines[1:end_idx])
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return {}

    if isinstance(parsed, dict):
        return parsed
    return {}


def resolve_plan_id(
    content: str,
    project_path: str,
    source_path: str | None = None,
) -> str:
    """Resolve a stable plan identifier from content and context.

    Resolution order:

    1. Parse YAML front matter for ``bound.plan_id``.
    2. Compute a stable hash of ``project_path + source_path``.
    3. For implicit plans (no source): generate a UUID fragment.

    Args:
        content: Raw markdown content of the plan.
        project_path: Absolute path to the project root.
        source_path: Path to the plan source file, or ``None`` for implicit.

    Returns:
        A stable plan identifier string (e.g. ``"plan-a1b2c3d4e5f6"``).
    """
    # 1. Try front matter — look for bound.plan_id
    fm = _extract_front_matter(content)
    bound_section = fm.get("bound")
    if isinstance(bound_section, dict) and bound_section.get("plan_id"):
        return str(bound_section["plan_id"])

    # 2. Stable hash of project_path + source_path
    if source_path:
        stable_input = f"{project_path}:{source_path}"
        digest = sha256_hex_bare(stable_input)
        return f"plan-{digest[:12]}"

    # 3. Implicit plan — UUID fallback
    return f"plan-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Plan snapshot / version management
# ---------------------------------------------------------------------------


def compute_plan_hash(content: str) -> str:
    """Compute the SHA-256 hex digest of plan content.

    Uses :func:`bound.hashing.sha256_hex_bare` so the result is a bare
    64-character hex string (no ``sha256:`` prefix).

    Args:
        content: Raw markdown plan content.

    Returns:
        64-character lowercase hex digest.
    """
    return sha256_hex_bare(content)


def create_plan_version(
    plan: Plan,
    content: str,
    source: str,
    reason: str | None = None,
    parent: PlanVersion | None = None,
    triggering_decision_id: str | None = None,
) -> PlanVersion:
    """Create a new immutable :class:`PlanVersion` from content.

    Computes a content hash and increments the version number based on
    the parent version.  When *parent* is ``None`` the new version is
    version 1 (initial).

    Args:
        plan: The :class:`Plan` this version belongs to.
        content: Raw markdown content for this version.
        source: How this version was created (``"file"``,
            ``"agent_submitted"``, ``"implicit"``, ``"replan"``).
        reason: Optional human-readable reason for this version.
        parent: Optional parent :class:`PlanVersion` to chain from.
        triggering_decision_id: Optional decision that triggered this
            version.

    Returns:
        A new frozen :class:`PlanVersion` instance.
    """
    content_hash = compute_plan_hash(content)
    version = (parent.version + 1) if parent else 1
    parent_version = parent.version if parent else None

    return PlanVersion(
        plan_id=plan.plan_id,
        version=version,
        content_hash=content_hash,
        content=content,
        parent_version=parent_version,
        source=source,
        reason=reason,
        triggering_decision_id=triggering_decision_id,
        created_at=datetime.now(UTC),
    )


def find_or_create_plan(
    project_id: str,
    source_path: str | None = None,
    content: str | None = None,
) -> Plan:
    """Find an existing Plan or create a new one.

    Generates a stable ``plan_id`` from the context and returns a new
    :class:`Plan` instance.  The returned Plan is suitable for use with
    :func:`create_plan_version`.

    Args:
        project_id: Identifier for the project (typically the absolute
            path to the project root).
        source_path: Path to the plan source file, or ``None`` for
            implicit plans.
        content: Optional plan content for front-matter-based id
            resolution.

    Returns:
        A new :class:`Plan` instance with a resolved ``plan_id``.
    """
    plan_id = resolve_plan_id(
        content=content or "",
        project_path=project_id,
        source_path=source_path,
    )
    return Plan(
        plan_id=plan_id,
        project_id=project_id,
        source_path=source_path,
    )


# ---------------------------------------------------------------------------
# Plan auto-discovery
# ---------------------------------------------------------------------------

_IMPLICIT_PLAN_CONTENT: str = """\
# Implicit BOUND Plan

## Inspect
- Understand the codebase and current state
- Identify key components, dependencies, and constraints

## Implement
- Make changes to address the task
- Follow project standards and conventions

## Verify
- Run tests and lint to confirm correctness
- Validate the implementation meets requirements
"""

_KNOWN_PLAN_NAMES: tuple[str, ...] = ("plan.md", "PLAN.md", "Plan.md")


def discover_plan(
    project_dir: str | Path,
    *,
    explicit_path: str | Path | None = None,
    no_plan: bool = False,
) -> tuple[Plan, PlanVersion, bool]:
    """Discover or create a plan for a project.

    Discovery order:
    1. *explicit_path* (e.g. ``--plan plan.md``) — loads that file.
    2. Auto-discover ``plan.md``, ``PLAN.md``, or ``Plan.md`` in
       ``project_dir`` and in ``project_dir/.bound/``.
    3. Implicit fallback — when no plan file exists and *no_plan* is
       ``False``, returns a built-in three-step Inspect→Implement→Verify
       plan.

    Args:
        project_dir: Absolute or relative path to the project root.
        explicit_path: Optional explicit path to a plan file (supports
            ``--plan`` CLI flag semantics).
        no_plan: When ``True``, skip auto-discovery and implicit fallback.
            Returns ``(None, None, False)`` in that case.

    Returns:
        A tuple of ``(plan, version, auto_discovered)`` where:

        * *plan* is the resolved :class:`Plan` (or ``None`` when *no_plan*
          is ``True``).
        * *version* is the initial :class:`PlanVersion` snapshot (or
          ``None``).
        * *auto_discovered* is ``True`` when the plan was found via
          directory scan rather than *explicit_path*.

    """
    project = Path(project_dir).resolve()

    # --no-plan: skip everything
    if no_plan:
        return None, None, False  # type: ignore[return-value]

    # 1. explicit path
    if explicit_path is not None:
        ep = Path(explicit_path)
        if not ep.is_absolute():
            ep = project / ep
        if ep.is_file():
            content = ep.read_text(encoding="utf-8")
            plan = find_or_create_plan(
                project_id=str(project),
                source_path=str(ep),
                content=content,
            )
            version = create_plan_version(plan=plan, content=content, source="file")
            return plan, version, False

    # 2. auto-discover in .bound/ and project root
    for search_dir in (project / ".bound", project):
        for name in _KNOWN_PLAN_NAMES:
            candidate = search_dir / name
            if candidate.is_file():
                content = candidate.read_text(encoding="utf-8")
                plan = find_or_create_plan(
                    project_id=str(project),
                    source_path=str(candidate),
                    content=content,
                )
                version = create_plan_version(
                    plan=plan, content=content, source="file"
                )
                return plan, version, True

    # 3. implicit fallback
    plan = find_or_create_plan(
        project_id=str(project),
        source_path=None,
        content=_IMPLICIT_PLAN_CONTENT,
    )
    version = create_plan_version(
        plan=plan, content=_IMPLICIT_PLAN_CONTENT, source="implicit"
    )
    return plan, version, True


# ---------------------------------------------------------------------------
# REPLAN semantics
# ---------------------------------------------------------------------------


def require_replan(
    plan: Plan,
    current_version: PlanVersion,
    new_content: str,
    decision_id: str,
    reason: str | None = None,
) -> PlanVersion:
    """Create a new plan version representing a materially different approach.

    A REPLAN occurs when the agent decides the current plan approach is no
    longer viable and a substantially different plan is needed.  The new
    version chains from *current_version* with ``source="replan"``.

    Args:
        plan: The :class:`Plan` being replanned.
        current_version: The current (soon-to-be-parent)
            :class:`PlanVersion`.
        new_content: Raw markdown content of the replanned version.
        decision_id: Identifier of the decision that triggered the replan.
        reason: Optional human-readable reason for the replan.

    Returns:
        A new :class:`PlanVersion` with ``source="replan"`` chained from
        *current_version*.
    """
    return create_plan_version(
        plan=plan,
        content=new_content,
        source="replan",
        reason=reason,
        parent=current_version,
        triggering_decision_id=decision_id,
    )


__all__ = [
    "Plan",
    "PlanVersion",
    "RunPlanLink",
    "compute_plan_hash",
    "create_plan_version",
    "discover_plan",
    "find_or_create_plan",
    "require_replan",
    "resolve_plan_id",
]
