"""Tests for the BOUND skills.sh layout and ZIP determinism.

Verifies that the skills/ directory is valid for ``npx skills add``,
that the ZIP is buildable deterministically, and that the installed
skill layout passes CI smoke checks.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "skills" / "bound"
SKILL_ZIP = REPO_ROOT / "release" / "skills" / "BOUND-agent-skill.zip"

# Required files that MUST be present in the skills directory
REQUIRED_SKILL_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/integration-report.md",
}


def _rel_skill(path: Path) -> str:
    """Return path relative to skills/bound/."""
    return str(path.relative_to(SKILLS_DIR))


def test_skills_directory_exists() -> None:
    assert SKILLS_DIR.is_dir(), f"skills/bound/ directory not found at {SKILLS_DIR}"


def test_skill_has_required_files() -> None:
    """Verify all required files are present in the skills directory."""
    existing = {_rel_skill(p) for p in SKILLS_DIR.rglob("*") if p.is_file()}
    missing = REQUIRED_SKILL_FILES - existing
    assert not missing, f"Missing required skill files: {', '.join(sorted(missing))}"


def test_skill_md_has_frontmatter() -> None:
    """SKILL.md must have valid YAML frontmatter with name and description."""
    text = (SKILLS_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must start with --- frontmatter"
    assert "name:" in text.split("---")[1], "SKILL.md frontmatter must include name:"
    assert "description:" in text.split("---")[
        1
    ], "SKILL.md frontmatter must include description:"


def test_skill_has_no_pycache() -> None:
    """Skills directory must not contain __pycache__ or .pyc files."""
    for p in SKILLS_DIR.rglob("*"):
        if p.is_file():
            rel = _rel_skill(p)
            assert "__pycache__" not in rel, f"Found __pycache__ in skill: {rel}"
            assert not rel.endswith(".pyc"), f"Found .pyc file in skill: {rel}"


def test_openai_agent_yaml_valid() -> None:
    """agents/openai.yaml must be valid YAML."""
    import yaml

    path = SKILLS_DIR / "agents" / "openai.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "agents/openai.yaml must be a dict"
    assert "interface" in data, "agents/openai.yaml must contain 'interface' key"
    assert "display_name" in data["interface"], "interface must have display_name"
    assert "short_description" in data["interface"], (
        "interface must have short_description"
    )


def test_zip_builds_deterministically(tmp_path: Path) -> None:
    """Build the ZIP twice and verify they are byte-for-byte identical."""
    from scripts.build_skill_zip import build_skill_zip

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    zip1 = build_skill_zip(out_dir=out1)
    zip2 = build_skill_zip(out_dir=out2)

    assert zip1.read_bytes() == zip2.read_bytes(), (
        "ZIP built twice from the same source is not byte-for-byte identical"
    )


@pytest.mark.skipif(
    not SKILL_ZIP.is_file(),
    reason="ZIP not pre-built (run 'uv run python scripts/build_skill_zip.py' first)",
)
def test_prebuilt_zip_contains_required_files() -> None:
    """Verify a pre-built ZIP has the correct structure."""
    with zipfile.ZipFile(SKILL_ZIP) as zf:
        names = set(zf.namelist())
        required = {"bound/SKILL.md", "bound/agents/openai.yaml"}
        missing = required - names
        assert not missing, f"Pre-built ZIP missing: {', '.join(sorted(missing))}"


@pytest.mark.skipif(
    not SKILL_ZIP.is_file(),
    reason="ZIP not pre-built",
)
def test_prebuilt_zip_no_pycache() -> None:
    """Pre-built ZIP must not contain __pycache__ or .pyc entries."""
    with zipfile.ZipFile(SKILL_ZIP) as zf:
        for name in zf.namelist():
            assert "__pycache__" not in name, f"ZIP contains __pycache__: {name}"
            assert not name.endswith(".pyc"), f"ZIP contains .pyc: {name}"


def test_zip_has_deterministic_timestamps(tmp_path: Path) -> None:
    """All ZIP entries must have the canonical deterministic timestamp."""
    from scripts.build_skill_zip import DETERMINISTIC_TIMESTAMP, build_skill_zip

    zip_path = build_skill_zip(out_dir=tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            info = zf.getinfo(name)
            assert info.date_time == DETERMINISTIC_TIMESTAMP, (
                f"Entry {name} has timestamp {info.date_time}, "
                f"expected {DETERMINISTIC_TIMESTAMP}"
            )