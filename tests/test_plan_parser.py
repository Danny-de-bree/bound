"""Tests for the BOUND plan parser (``bound.plan_parser``).

Verifies plan.md discovery, parsing, acceptance criteria, and deterministic IDs.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from bound.plan_parser import load_plan


def _write_plan(tmpdir: Path, content: str, name: str = "plan.md") -> Path:
    """Write a plan.md file in a temp directory and return its path."""
    plan_path = tmpdir / name
    plan_path.write_text(content, encoding="utf-8")
    return plan_path


class TestPlanDiscovery:
    """Tests for plan.md file discovery order."""

    def test_no_plan_found_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = load_plan(td)
            assert result is None

    def test_finds_plan_md_in_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# Test\n\n## Step 1\n- [ ] Task A\n")
            result = load_plan(td)
            assert result is not None
            assert result.goal == "Test"
            assert len(result.steps) == 1  # phase only; checkbox is acceptance check
            assert len(result.steps[0].acceptance_checks) == 1

    def test_finds_plan_md_in_bound_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bound").mkdir()
            _write_plan(root / ".bound", "# Bound\n\n## S\n- [ ] T\n")
            _write_plan(root, "# Root\n\n## R\n- [ ] T\n")
            result = load_plan(td)
            assert result is not None
            assert result.goal == "Bound"

    def test_explicit_path_takes_priority(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            explicit = _write_plan(root, "# Explicit\n\n## E\n- [ ] T\n", "custom.md")
            _write_plan(root, "# Root\n\n## R\n- [ ] T\n")
            result = load_plan(root, explicit_path=explicit)
            assert result is not None
            assert result.goal == "Explicit"

    def test_case_insensitive_discovery(self) -> None:
        for name in ("PLAN.md", "Plan.md"):
            with tempfile.TemporaryDirectory() as td:
                _write_plan(Path(td), f"# {name}\n\n## S\n- [ ] T\n", name)
                result = load_plan(td)
                assert result is not None, f"Failed to find {name}"
                assert result.goal == name


class TestHeadingParsing:
    """Tests for heading-based plan structure."""

    def test_goal_from_h1(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# Ship\n\n## Write\n- [ ] Task\n")
            result = load_plan(td)
            assert result is not None
            assert result.goal == "Ship"

    def test_phases_from_h2(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# G\n\n## Imp\n## Ver\n## Doc\n")
            result = load_plan(td)
            assert result is not None
            assert len(result.steps) == 3
            assert all(s.depth == 0 for s in result.steps)

    def test_substeps_from_h3(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# G\n\n## Phase\n### Sub A\n### Sub B\n")
            result = load_plan(td)
            assert result is not None
            assert len(result.steps) == 3
            assert result.steps[0].depth == 0
            assert result.steps[1].depth == 1
            assert result.steps[2].depth == 1

    def test_no_h1_goal_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "## Phase\n- [ ] Task\n")
            result = load_plan(td)
            assert result is not None
            assert result.goal is None


class TestListParsing:
    """Tests for checkbox and numbered list parsing."""

    def test_checkbox_unchecked_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# T\n\n## Phase\n- [ ] Write tests\n- [ ] Lint\n")
            result = load_plan(td)
            assert result is not None
            assert len(result.steps) == 1  # only the phase heading
            assert len(result.steps[0].acceptance_checks) == 2

    def test_checkbox_checked_is_completed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# T\n\n## Phase\n- [x] Write tests\n- [ ] Lint\n")
            result = load_plan(td)
            assert result is not None
            assert len(result.steps) == 1
            checks = result.steps[0].acceptance_checks
            assert "✅ Write tests" in checks[0]
            assert "☐ Lint" in checks[1]

    def test_numbered_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# P\n\n## Phase\n1. Inspect\n2. Validate\n3. Test\n")
            result = load_plan(td)
            assert result is not None
            assert len(result.steps) == 1
            assert len(result.steps[0].acceptance_checks) == 3


class TestAcceptanceCriteria:
    """Tests for acceptance-criteria extraction."""

    def test_criteria_attached_to_last_step(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            content = (
                "# G\n\n## Imp\n- [ ] Add validator\n\n"
                "## Acceptance\n- All tests pass\n- Coverage 80%\n"
            )
            _write_plan(Path(td), content)
            result = load_plan(td)
            assert result is not None
            assert len(result.steps) == 1  # Imp phase
            checks = result.steps[0].acceptance_checks
            assert len(checks) == 3
            assert "☐ Add validator" in checks[0]
            assert "All tests pass" in checks[1] or any("All tests pass" in c for c in checks)

    def test_criteria_header_variants(self) -> None:
        for header in ("## Acceptance", "## Criteria", "## Checks", "## Acceptance Criteria"):
            with tempfile.TemporaryDirectory() as td:
                content = f"# G\n\n## Phase\n- [ ] T\n\n{header}\n- Check\n"
                _write_plan(Path(td), content)
                result = load_plan(td)
                assert result is not None
                assert len(result.steps) == 1
                checks = result.steps[0].acceptance_checks
                assert len(checks) >= 1


class TestDeterministicIds:
    """Tests for stable, deterministic step ID generation."""

    def test_same_content_same_ids(self) -> None:
        content = "# G\n\n## Phase\n- [ ] Task\n"
        with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
            _write_plan(Path(td1), content)
            _write_plan(Path(td2), content)
            r1 = load_plan(td1)
            r2 = load_plan(td2)
            assert r1 is not None and r2 is not None
            assert r1.hash == r2.hash
            assert [s.step_id for s in r1.steps] == [s.step_id for s in r2.steps]

    def test_hash_is_sha256_of_content(self) -> None:
        content = "# G\n\n## S\n- [ ] T\n"
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), content)
            expected = hashlib.sha256(content.encode()).hexdigest()
            result = load_plan(td)
            assert result is not None
            assert result.hash == expected

    def test_snapshot_has_plan_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# G\n\n## S\n- [ ] T\n")
            result = load_plan(td)
            assert result is not None
            assert result.plan_id.startswith("plan-")
            assert len(result.plan_id) > 5
