"""Plan parser for BOUND (v0.9.1).

Loads ``plan.md`` from the project root or ``.bound/`` directory and parses it
into a structured :class:`PlanSnapshot`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bound.ui_models import PlanStep, PlanStepStatus

logger = logging.getLogger("bound.plan_parser")

_KNOWN_PLAN_PATHS: tuple[str, ...] = ("plan.md", "PLAN.md", "Plan.md")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[([ xX])\]\s+(.+)$")
_NUMBERED_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$")
_LIST_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_ACCEPTANCE_SECTION_NAMES: frozenset[str] = frozenset(
    {"acceptance", "criteria", "acceptance criteria", "checks"}
)


class PlanSnapshot(BaseModel):
    """An immutable snapshot of a parsed plan.md file."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    version: int = Field(default=1, ge=1)
    hash: str
    source_path: str
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    goal: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)


def load_plan(
    project_dir: str | Path,
    *,
    explicit_path: str | Path | None = None,
) -> PlanSnapshot | None:
    """Load and parse a ``plan.md`` file from a project directory.

    Discovery order: explicit_path, .bound/plan.md, root candidates.
    """
    project = Path(project_dir)

    if explicit_path is not None:
        ep = Path(explicit_path)
        if not ep.is_absolute():
            ep = project / ep
        if ep.exists():
            return _parse_file(ep)

    bound_plan = project / ".bound" / "plan.md"
    if bound_plan.exists():
        return _parse_file(bound_plan)

    for name in _KNOWN_PLAN_PATHS:
        candidate = project / name
        if candidate.exists():
            return _parse_file(candidate)

    return None


def _parse_file(path: Path) -> PlanSnapshot:
    """Parse a plan file into a PlanSnapshot."""
    raw = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    plan_id = f"plan-{content_hash[:12]}"
    steps = _parse_steps(raw)
    goal = _extract_goal(raw)
    return PlanSnapshot(
        plan_id=plan_id,
        hash=content_hash,
        source_path=str(path),
        goal=goal,
        steps=steps,
    )


def _extract_goal(raw: str) -> str | None:
    """Extract the top-level goal from the first ``#`` heading."""
    for line in raw.splitlines():
        m = _HEADING_RE.match(line)
        if m and m.group(1) == "#":
            return m.group(2).strip()
    return None


def _derive_step_id(title: str, ordinal: int) -> str:
    """Derive a stable step id from a title and ordinal."""
    normalized = " ".join(title.lower().split())
    digest = hashlib.sha256(f"{ordinal}:{normalized}".encode()).hexdigest()
    return f"step-{digest[:8]}"


def _parse_steps(raw: str) -> list[PlanStep]:
    """Parse plan.md content into an ordered list of PlanStep."""
    steps: list[PlanStep] = []
    ordinal = 0
    current_phase_id: str | None = None
    in_acceptance_section = False
    lines = raw.splitlines()

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        heading_match = _HEADING_RE.match(stripped)

        if heading_match:
            heading_text = heading_match.group(2).strip().lower()
            in_acceptance_section = heading_text in _ACCEPTANCE_SECTION_NAMES
            # Skip this heading entirely if it starts/stays an acceptance section
            if in_acceptance_section:
                continue

        if not stripped:
            continue

        # ## heading = phase
        if heading_match and heading_match.group(1) == "##":
            title = heading_match.group(2).strip()
            if not in_acceptance_section:
                ordinal += 1
                step_id = _derive_step_id(title, ordinal)
                current_phase_id = step_id
                steps.append(PlanStep(
                    step_id=step_id, title=title, ordinal=ordinal,
                    depth=0, source_line=idx,
                ))
            continue

        # ### heading = sub-step
        if heading_match and heading_match.group(1) == "###":
            title = heading_match.group(2).strip()
            if not in_acceptance_section:
                ordinal += 1
                step_id = _derive_step_id(title, ordinal)
                steps.append(PlanStep(
                    step_id=step_id, title=title, ordinal=ordinal,
                    depth=1, source_line=idx,
                    parent_step_id=current_phase_id,
                ))
            continue

        # # heading = goal, skip
        if heading_match and heading_match.group(1) == "#":
            continue

        # Checkbox item: - [ ] / - [x]  → acceptance check for current phase
        checkbox_match = _CHECKBOX_RE.match(stripped)
        if checkbox_match and not in_acceptance_section and steps and current_phase_id:
            checked = checkbox_match.group(1).lower()
            title = checkbox_match.group(2).strip()
            status_mark = "✅" if checked == "x" else "☐"
            steps[-1].acceptance_checks.append(f"{status_mark} {title}")
            # Update phase status: completed only if all checks are done
            if checked != "x":
                pass  # keep phase as pending if any unchecked
            continue

        # Numbered item: 1. ...  → acceptance check
        numbered_match = _NUMBERED_ITEM_RE.match(stripped)
        if numbered_match and not in_acceptance_section and steps and current_phase_id:
            title = numbered_match.group(2).strip()
            steps[-1].acceptance_checks.append(f"☐ {title}")
            continue

        # Plain list item → acceptance check
        list_match = _LIST_ITEM_RE.match(stripped)
        if list_match and not in_acceptance_section and not checkbox_match and steps and current_phase_id:
            title = list_match.group(1).strip()
            steps[-1].acceptance_checks.append(f"☐ {title}")
            continue

        # Acceptance criteria collection
        if in_acceptance_section and stripped and not heading_match and steps:
            steps[-1].acceptance_checks.append(stripped)

    return steps


def extract_front_matter(content: str) -> dict:
    """Parse YAML front matter from plan content.

    Extracts the YAML block between the first pair of ``---`` markers
    and returns the parsed dict.  Handles the common ``bound: {plan_id: ...,
    title: ...}`` pattern used by BOUND plans.

    Args:
        content: Raw markdown content, possibly with front matter.

    Returns:
        Parsed front matter as a dict.  Returns an empty dict when no
        front matter is present or when the YAML cannot be parsed.

    Examples:
        >>> extract_front_matter('---\\nbound:\\n  plan_id: abc\\n---\\n# Goal')
        {'bound': {'plan_id': 'abc'}}
    """
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return {}

    lines = stripped.splitlines()
    if len(lines) < 2:
        return {}

    # Find the closing ---
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


def parse_plan_steps(content: str) -> list[dict]:
    """Parse plan content into a list of step dicts with stable step_ids.

    Parses ``##`` headings as phases and checklist items (``- [ ]`` /
    ``- [x]``) as checkable steps.  Each step dict receives a stable
    ``step_id`` derived from a content hash of its title and ordinal position.

    Args:
        content: Raw markdown plan content.

    Returns:
        A list of step dicts, each containing at minimum:
        ``step_id``, ``title``, ``ordinal``, ``depth``, ``status``,
        ``phase``, and ``source_line``.

    Examples:
        >>> steps = parse_plan_steps('## Inspect\\n- [ ] Read code\\n- [x] Run tests')
        >>> steps[0]['title']
        'Inspect'
        >>> steps[1]['status']
        'pending'
    """
    steps: list[dict] = []
    ordinal = 0
    current_phase: str | None = None
    lines = content.splitlines()

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        heading_match = _HEADING_RE.match(stripped)

        # Skip # headings (goal)
        if heading_match and heading_match.group(1) == "#":
            continue

        # ## heading = phase
        if heading_match and heading_match.group(1) == "##":
            title = heading_match.group(2).strip()
            ordinal += 1
            step_id = _derive_step_id(title, ordinal)
            current_phase = title
            steps.append({
                "step_id": step_id,
                "title": title,
                "ordinal": ordinal,
                "depth": 0,
                "status": "pending",
                "phase": None,
                "source_line": idx,
            })
            continue

        # ### heading = sub-step
        if heading_match and heading_match.group(1) == "###":
            title = heading_match.group(2).strip()
            ordinal += 1
            step_id = _derive_step_id(title, ordinal)
            steps.append({
                "step_id": step_id,
                "title": title,
                "ordinal": ordinal,
                "depth": 1,
                "status": "pending",
                "phase": current_phase,
                "source_line": idx,
            })
            continue

        # Checkbox items: - [ ] / - [x]  → acceptance check
        checkbox_match = _CHECKBOX_RE.match(stripped)
        if checkbox_match and steps:
            checked = checkbox_match.group(1).lower()
            title = checkbox_match.group(2).strip()
            mark = "✅" if checked == "x" else "☐"
            steps[-1].setdefault("acceptance_checks", []).append(f"{mark} {title}")
            continue

        # Numbered items: 1. ...  → acceptance check
        numbered_match = _NUMBERED_ITEM_RE.match(stripped)
        if numbered_match and steps:
            title = numbered_match.group(2).strip()
            steps[-1].setdefault("acceptance_checks", []).append(f"☐ {title}")
            continue

    return steps


__all__ = [
    "PlanSnapshot",
    "extract_front_matter",
    "load_plan",
    "parse_plan_steps",
]
