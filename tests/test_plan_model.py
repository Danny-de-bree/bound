"""Tests for BOUND plan model — Plan, PlanVersion, RunPlanLink, and helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bound.hashing import sha256_hex_bare
from bound.plan_model import (
    Plan,
    PlanVersion,
    RunPlanLink,
    compute_plan_hash,
    create_plan_version,
    find_or_create_plan,
    require_replan,
    resolve_plan_id,
)
from bound.plan_parser import extract_front_matter, parse_plan_steps

# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------


class TestPlan:
    """Tests for the frozen Plan dataclass."""

    def test_plan_creation_basic(self) -> None:
        """Plan can be created with required fields."""
        plan = Plan(plan_id="plan-abc123", project_id="/tmp/test")
        assert plan.plan_id == "plan-abc123"
        assert plan.project_id == "/tmp/test"
        assert plan.source_path is None

    def test_plan_creation_with_source(self) -> None:
        """Plan with explicit source_path."""
        plan = Plan(
            plan_id="plan-xyz", project_id="/proj", source_path="plan.md"
        )
        assert plan.source_path == "plan.md"

    def test_plan_auto_datetime(self) -> None:
        """Plan.created_at is auto-populated with a UTC datetime."""
        plan = Plan(plan_id="p1", project_id="/p")
        assert isinstance(plan.created_at, datetime)
        assert plan.created_at.tzinfo == UTC

    def test_plan_is_frozen(self) -> None:
        """Plan dataclass is immutable."""
        plan = Plan(plan_id="p1", project_id="/p")
        with pytest.raises(ValidationError):
            plan.plan_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PlanVersion dataclass
# ---------------------------------------------------------------------------


class TestPlanVersion:
    """Tests for the frozen PlanVersion dataclass."""

    def test_version_creation_basic(self) -> None:
        """PlanVersion created with required fields."""
        pv = PlanVersion(
            plan_id="plan-1",
            version=1,
            content_hash="abc123",
            content="# Title\n\n## Step 1\n",
        )
        assert pv.plan_id == "plan-1"
        assert pv.version == 1
        assert pv.content_hash == "abc123"
        assert pv.parent_version is None
        assert pv.source == "file"
        assert pv.reason is None

    def test_version_with_parent(self) -> None:
        """PlanVersion with parent_version establishes a chain."""
        pv = PlanVersion(
            plan_id="plan-1",
            version=2,
            content_hash="def456",
            content="# Updated\n",
            parent_version=1,
            source="replan",
            reason="Materially different approach",
        )
        assert pv.parent_version == 1
        assert pv.source == "replan"
        assert pv.reason == "Materially different approach"

    def test_version_is_frozen(self) -> None:
        """PlanVersion dataclass is immutable."""
        pv = PlanVersion(
            plan_id="p1", version=1, content_hash="abc", content="# P\n"
        )
        with pytest.raises(ValidationError):
            pv.version = 2  # type: ignore[misc]

    def test_version_auto_datetime(self) -> None:
        """PlanVersion.created_at is auto-populated."""
        pv = PlanVersion(
            plan_id="p1", version=1, content_hash="abc", content="# P\n"
        )
        assert isinstance(pv.created_at, datetime)
        assert pv.created_at.tzinfo == UTC

    def test_version_parsed_steps_default(self) -> None:
        """parsed_steps defaults to None."""
        pv = PlanVersion(
            plan_id="p1", version=1, content_hash="abc", content="# P\n"
        )
        assert pv.parsed_steps is None

    def test_version_triggering_decision_id(self) -> None:
        """triggering_decision_id can be set for replan versions."""
        pv = PlanVersion(
            plan_id="p1",
            version=2,
            content_hash="abc",
            content="# P\n",
            triggering_decision_id="dec-001",
        )
        assert pv.triggering_decision_id == "dec-001"


# ---------------------------------------------------------------------------
# RunPlanLink dataclass
# ---------------------------------------------------------------------------


class TestRunPlanLink:
    """Tests for the frozen RunPlanLink dataclass."""

    def test_link_basic(self) -> None:
        """RunPlanLink connects a run to a plan version."""
        link = RunPlanLink(
            run_id="run-1",
            plan_id="plan-1",
            initial_plan_version=1,
            current_plan_version=2,
        )
        assert link.run_id == "run-1"
        assert link.plan_id == "plan-1"
        assert link.initial_plan_version == 1
        assert link.current_plan_version == 2

    def test_link_is_frozen(self) -> None:
        """RunPlanLink is immutable."""
        link = RunPlanLink(
            run_id="r1", plan_id="p1", initial_plan_version=1, current_plan_version=1
        )
        with pytest.raises(ValidationError):
            link.current_plan_version = 3  # type: ignore[misc]

    def test_link_same_initial_and_current(self) -> None:
        """When no replan occurs, versions stay the same."""
        link = RunPlanLink(
            run_id="r1", plan_id="p1", initial_plan_version=1, current_plan_version=1
        )
        assert link.initial_plan_version == link.current_plan_version


# ---------------------------------------------------------------------------
# resolve_plan_id
# ---------------------------------------------------------------------------


class TestResolvePlanId:
    """Tests for plan identity resolution."""

    def test_from_front_matter_bound_plan_id(self) -> None:
        """Resolves plan_id from YAML front matter bound.plan_id."""
        content = "---\nbound:\n  plan_id: my-custom-id\n---\n# Plan"
        result = resolve_plan_id(content, "/project", "plan.md")
        assert result == "my-custom-id"

    def test_from_front_matter_no_bound_key(self) -> None:
        """Falls back when front matter has no bound key."""
        content = "---\ntitle: Just a title\n---\n# Plan"
        result = resolve_plan_id(content, "/project", "plan.md")
        assert result.startswith("plan-")

    def test_from_front_matter_bound_no_plan_id(self) -> None:
        """Falls back when bound key exists but has no plan_id."""
        content = "---\nbound:\n  title: My Plan\n---\n# Plan"
        result = resolve_plan_id(content, "/project", "plan.md")
        assert result.startswith("plan-")

    def test_stable_hash_fallback(self) -> None:
        """Without front matter, uses stable hash of project + source."""
        content = "# Simple plan"
        result_a = resolve_plan_id(content, "/project", "plan.md")
        result_b = resolve_plan_id(content, "/project", "plan.md")
        assert result_a == result_b
        assert result_a.startswith("plan-")

    def test_stable_hash_different_projects(self) -> None:
        """Different project paths produce different plan IDs."""
        content = "# Plan"
        id1 = resolve_plan_id(content, "/proj/a", "plan.md")
        id2 = resolve_plan_id(content, "/proj/b", "plan.md")
        assert id1 != id2

    def test_stable_hash_different_sources(self) -> None:
        """Different source paths produce different plan IDs."""
        content = "# Plan"
        id1 = resolve_plan_id(content, "/proj", "plan.md")
        id2 = resolve_plan_id(content, "/proj", "other.md")
        assert id1 != id2

    def test_implicit_plan_uuid_fallback(self) -> None:
        """When source_path is None, generates a UUID-based ID."""
        content = "# Implicit plan"
        result = resolve_plan_id(content, "/project", source_path=None)
        assert result.startswith("plan-")
        result2 = resolve_plan_id(content, "/project", source_path=None)
        assert result != result2  # UUIDs are unique per call

    def test_empty_content_still_resolves(self) -> None:
        """Empty content with source still resolves via stable hash."""
        result = resolve_plan_id("", "/proj", "plan.md")
        assert result.startswith("plan-")

    def test_no_source_no_content_uuid(self) -> None:
        """Zero-info case produces a UUID plan ID."""
        result = resolve_plan_id("", "/proj")
        assert result.startswith("plan-")
        assert len(result) > 10


# ---------------------------------------------------------------------------
# compute_plan_hash
# ---------------------------------------------------------------------------


class TestComputePlanHash:
    """Tests for content hashing."""

    def test_hash_consistency(self) -> None:
        """Same content always produces the same hash."""
        content = "# Plan\n\n## Step 1\n- [ ] Task\n"
        h1 = compute_plan_hash(content)
        h2 = compute_plan_hash(content)
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_different_content(self) -> None:
        """Different content produces different hashes."""
        h1 = compute_plan_hash("# Plan A")
        h2 = compute_plan_hash("# Plan B")
        assert h1 != h2

    def test_hash_matches_sha256_hex_bare(self) -> None:
        """compute_plan_hash delegates to sha256_hex_bare."""
        content = "# Test"
        expected = sha256_hex_bare(content)
        assert compute_plan_hash(content) == expected


# ---------------------------------------------------------------------------
# create_plan_version
# ---------------------------------------------------------------------------


class TestCreatePlanVersion:
    """Tests for version creation."""

    def test_v1_no_parent(self) -> None:
        """First version has version=1 and no parent."""
        plan = Plan(plan_id="plan-1", project_id="/proj")
        pv = create_plan_version(plan=plan, content="# V1", source="file")
        assert pv.version == 1
        assert pv.parent_version is None
        assert pv.source == "file"
        assert pv.content == "# V1"

    def test_v2_with_parent(self) -> None:
        """Second version increments from parent."""
        plan = Plan(plan_id="plan-1", project_id="/proj")
        parent = create_plan_version(plan=plan, content="# V1", source="file")
        child = create_plan_version(
            plan=plan, content="# V2", source="replan", parent=parent
        )
        assert child.version == 2
        assert child.parent_version == 1
        assert child.source == "replan"

    def test_v3_chain(self) -> None:
        """Version chain v1 -> v2 -> v3."""
        plan = Plan(plan_id="p1", project_id="/p")
        v1 = create_plan_version(plan, "# V1", "file")
        v2 = create_plan_version(plan, "# V2", "agent_submitted", parent=v1)
        v3 = create_plan_version(plan, "# V3", "replan", parent=v2)
        assert v1.version == 1
        assert v2.version == 2
        assert v3.version == 3
        assert v2.parent_version == 1
        assert v3.parent_version == 2

    def test_content_hash_included(self) -> None:
        """Version carries correct content hash."""
        plan = Plan(plan_id="p1", project_id="/p")
        content = "# A plan"
        pv = create_plan_version(plan, content, "file")
        expected_hash = compute_plan_hash(content)
        assert pv.content_hash == expected_hash

    def test_reason_propagated(self) -> None:
        """Reason is stored on the version."""
        plan = Plan(plan_id="p1", project_id="/p")
        pv = create_plan_version(
            plan, "# P", "replan", reason="New strategy needed"
        )
        assert pv.reason == "New strategy needed"

    def test_triggering_decision_propagated(self) -> None:
        """triggering_decision_id is stored."""
        plan = Plan(plan_id="p1", project_id="/p")
        pv = create_plan_version(
            plan, "# P", "replan", triggering_decision_id="dec-42"
        )
        assert pv.triggering_decision_id == "dec-42"

    def test_plan_id_matches(self) -> None:
        """Version references the correct plan_id."""
        plan = Plan(plan_id="my-plan", project_id="/p")
        pv = create_plan_version(plan, "# P", "file")
        assert pv.plan_id == "my-plan"


# ---------------------------------------------------------------------------
# find_or_create_plan
# ---------------------------------------------------------------------------


class TestFindOrCreatePlan:
    """Tests for plan discovery/creation."""

    def test_creates_plan_with_source(self) -> None:
        """Creates a Plan with source_path."""
        plan = find_or_create_plan(
            project_id="/proj", source_path="plan.md", content="# Plan"
        )
        assert plan.project_id == "/proj"
        assert plan.source_path == "plan.md"
        assert plan.plan_id.startswith("plan-")

    def test_creates_implicit_plan(self) -> None:
        """Creates a Plan without source_path (implicit)."""
        plan = find_or_create_plan(project_id="/proj", content="# Implicit")
        assert plan.source_path is None
        assert plan.plan_id.startswith("plan-")

    def test_same_source_same_id(self) -> None:
        """Same project+source produces same plan_id."""
        p1 = find_or_create_plan("/proj", "plan.md", "# Plan")
        p2 = find_or_create_plan("/proj", "plan.md", "# Plan")
        assert p1.plan_id == p2.plan_id

    def test_front_matter_overrides_hash(self) -> None:
        """Front matter plan_id takes priority over hash."""
        content = "---\nbound:\n  plan_id: explicit-id\n---\n# Plan"
        plan = find_or_create_plan("/proj", "plan.md", content)
        assert plan.plan_id == "explicit-id"


# ---------------------------------------------------------------------------
# require_replan (REPLAN semantics)
# ---------------------------------------------------------------------------


class TestRequireReplan:
    """Tests for REPLAN semantics."""

    def test_replan_creates_new_version(self) -> None:
        """require_replan creates a new version chained from current."""
        plan = Plan(plan_id="plan-1", project_id="/proj")
        v1 = create_plan_version(plan, "# Original", "file")
        v2 = require_replan(
            plan=plan,
            current_version=v1,
            new_content="# Replanned",
            decision_id="dec-5",
            reason="Blocked by upstream",
        )
        assert v2.version == 2
        assert v2.parent_version == 1
        assert v2.source == "replan"
        assert v2.content == "# Replanned"
        assert v2.reason == "Blocked by upstream"
        assert v2.triggering_decision_id == "dec-5"

    def test_replan_content_differs_from_parent(self) -> None:
        """Replan content hash differs from parent."""
        plan = Plan(plan_id="p1", project_id="/p")
        v1 = create_plan_version(plan, "# V1", "file")
        v2 = require_replan(plan, v1, "# V2 revised", "dec-1")
        assert v1.content_hash != v2.content_hash

    def test_replan_preserves_plan_id(self) -> None:
        """Replan version belongs to the same plan."""
        plan = Plan(plan_id="my-plan", project_id="/p")
        v1 = create_plan_version(plan, "# V1", "file")
        v2 = require_replan(plan, v1, "# V2", "dec-1")
        assert v2.plan_id == "my-plan"

    def test_replan_can_chain_multiple(self) -> None:
        """Multiple replans create a version chain."""
        plan = Plan(plan_id="p1", project_id="/p")
        v1 = create_plan_version(plan, "# V1", "file")
        v2 = require_replan(plan, v1, "# V2", "dec-1")
        v3 = require_replan(plan, v2, "# V3", "dec-2")
        assert v3.version == 3
        assert v3.parent_version == 2


# ---------------------------------------------------------------------------
# extract_front_matter (from plan_parser)
# ---------------------------------------------------------------------------


class TestExtractFrontMatter:
    """Tests for YAML front matter extraction."""

    def test_extracts_bound_block(self) -> None:
        """Extracts the YAML block between --- markers."""
        content = "---\nbound:\n  plan_id: abc\n  title: My Plan\n---\n# Goal"
        fm = extract_front_matter(content)
        assert fm == {"bound": {"plan_id": "abc", "title": "My Plan"}}

    def test_no_front_matter_returns_empty(self) -> None:
        """Returns empty dict when no --- markers."""
        assert extract_front_matter("# Just a heading") == {}

    def test_unclosed_front_matter_returns_empty(self) -> None:
        """Returns empty dict when opening --- has no closing ---."""
        assert (
            extract_front_matter("---\nbound:\n  plan_id: x\n# No close") == {}
        )

    def test_invalid_yaml_returns_empty(self) -> None:
        """Returns empty dict for unparseable YAML."""
        assert (
            extract_front_matter("---\n:invalid: yaml: !!bad\n---\n# Plan") == {}
        )

    def test_front_matter_with_leading_whitespace(self) -> None:
        """Content with leading whitespace before --- still parses."""
        content = "  \n---\nkey: value\n---\n# Plan"
        fm = extract_front_matter(content)
        assert fm == {"key": "value"}

    def test_front_matter_non_dict_returns_empty(self) -> None:
        """When YAML parses to a non-dict (e.g. list), returns empty."""
        content = "---\n- item1\n- item2\n---\n# Plan"
        assert extract_front_matter(content) == {}


# ---------------------------------------------------------------------------
# parse_plan_steps (from plan_parser)
# ---------------------------------------------------------------------------


class TestParsePlanSteps:
    """Tests for plan step parsing into dicts."""

    def test_parses_phase_headings(self) -> None:
        """## headings become phase steps with depth=0."""
        content = "## Inspect\n## Implement\n## Verify"
        steps = parse_plan_steps(content)
        assert len(steps) == 3
        assert all(s["depth"] == 0 for s in steps)
        assert steps[0]["title"] == "Inspect"
        assert steps[1]["title"] == "Implement"
        assert steps[2]["title"] == "Verify"

    def test_parses_checkbox_items_as_acceptance_checks(self) -> None:
        """Checkbox items become acceptance checks on the preceding step."""
        content = "## Build\n- [ ] Pending task\n- [x] Done task"
        steps = parse_plan_steps(content)
        assert len(steps) == 1  # only the phase heading
        assert steps[0]["title"] == "Build"
        checks = steps[0].get("acceptance_checks", [])
        assert len(checks) == 2
        assert "☐ Pending task" in checks[0]
        assert "✅ Done task" in checks[1]

    def test_steps_have_stable_ids(self) -> None:
        """Step IDs are deterministic based on content."""
        content = "## Phase A\n## Phase B"
        steps1 = parse_plan_steps(content)
        steps2 = parse_plan_steps(content)
        assert steps1[0]["step_id"] == steps2[0]["step_id"]
        assert steps1[1]["step_id"] == steps2[1]["step_id"]

    def test_steps_have_ordinals(self) -> None:
        """Steps are numbered sequentially (only headings count)."""
        content = "## A\n- [ ] Task\n## B\n- [x] Done"
        steps = parse_plan_steps(content)
        assert len(steps) == 2
        assert [s["ordinal"] for s in steps] == [1, 2]

    def test_checkbox_under_phase_becomes_check(self) -> None:
        """Checkbox items under a ## heading become acceptance checks."""
        content = "## Build\n- [ ] Write code\n- [ ] Test"
        steps = parse_plan_steps(content)
        assert len(steps) == 1
        assert steps[0]["title"] == "Build"
        assert len(steps[0].get("acceptance_checks", [])) == 2

    def test_numbered_items_become_checks(self) -> None:
        """Numbered items become acceptance checks."""
        content = "## Phase\n1. First\n2. Second"
        steps = parse_plan_steps(content)
        assert len(steps) == 1
        checks = steps[0].get("acceptance_checks", [])
        assert len(checks) == 2

    def test_h1_skipped(self) -> None:
        """# headings (goal) are not treated as steps."""
        content = "# Goal\n## Phase\n- [ ] Task"
        steps = parse_plan_steps(content)
        assert len(steps) == 1
        assert steps[0]["title"] == "Phase"
        assert len(steps[0].get("acceptance_checks", [])) == 1

    def test_h3_substeps(self) -> None:
        """### headings become sub-steps with depth=1."""
        content = "## Phase\n### Sub A\n### Sub B"
        steps = parse_plan_steps(content)
        assert len(steps) == 3
        assert steps[0]["depth"] == 0  # phase
        assert steps[1]["depth"] == 1
        assert steps[1]["phase"] == "Phase"

    def test_step_id_format(self) -> None:
        """Step IDs follow the step-XXXXXXXX format."""
        content = "## Inspect"
        steps = parse_plan_steps(content)
        assert re.match(r"^step-[a-f0-9]{8}$", steps[0]["step_id"])

    def test_empty_content_returns_empty_list(self) -> None:
        """Empty content produces no steps."""
        assert parse_plan_steps("") == []
        assert parse_plan_steps("\n\n") == []

    def test_source_line_tracked(self) -> None:
        """Each step records its source line number."""
        content = "## Phase One\n- [ ] Task"
        steps = parse_plan_steps(content)
        assert steps[0]["source_line"] == 1
