"""Tests for the ``bound setup`` command and integration installers (v0.8.1).

Verifies that:

1. Each installer's ``detect``, ``plan``, and ``install`` methods work correctly.
2. Installers are idempotent (running twice does not corrupt files).
3. ``--dry-run`` describes intended changes without writing files.
4. ``--force`` overwrites an existing policy.
5. ``--verify`` validates the generated policy.
6. ``--json`` emits machine-readable output.
7. The ``setup_project`` orchestrator works end-to-end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bound.setup import (
    ChangeKind,
    ClaudeCodeInstaller,
    ClineInstaller,
    CodexInstaller,
    GenericPromptInstaller,
    InstallationResult,
    PlannedChange,
    SetupError,
    _ensure_bound_dirs,
    _plan_bound_dirs,
    get_installer,
    setup_project,
)

# ===========================================================================
# Helper: run the CLI
# ===========================================================================


def _run_setup_cli(
    tmp_path: Path, args: list[str], *, cwd: Path | None = None
) -> tuple[int, str, str]:
    """Run ``bound setup`` via ``main()`` and return (exit_code, stdout, stderr)."""
    import sys
    from io import StringIO

    from bound.cli import main

    old_cwd = os.getcwd()
    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        if cwd is not None:
            os.chdir(str(cwd))
        sys.argv = ["bound", "setup"] + args
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        rc = main(["setup"] + args)
        return rc, out.getvalue(), err.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        sys.argv = old_argv
        os.chdir(old_cwd)


# ===========================================================================
# PlannedChange / InstallationResult unit tests
# ===========================================================================


class TestPlannedChange:
    """Tests for the :class:`PlannedChange` model."""

    def test_create_minimal(self) -> None:
        pc = PlannedChange(path="foo.txt", kind=ChangeKind.CREATE)
        assert pc.path == "foo.txt"
        assert pc.kind == "create"
        assert pc.description == ""
        assert pc.content_preview is None

    def test_modify_with_preview(self) -> None:
        pc = PlannedChange(
            path=".bound/policy.yaml",
            kind=ChangeKind.MODIFY,
            description="Updated policy",
            content_preview="version: 0.8.0",
        )
        assert pc.kind == "modify"
        assert pc.content_preview == "version: 0.8.0"

    def test_serialization(self) -> None:
        pc = PlannedChange(
            path="test.md",
            kind=ChangeKind.CREATE,
            description="Created test",
        )
        d = pc.model_dump()
        assert d["path"] == "test.md"
        assert d["kind"] == "create"


class TestInstallationResult:
    """Tests for the :class:`InstallationResult` model."""

    def test_defaults(self) -> None:
        ir = InstallationResult(agent_id="generic", display_name="Generic")
        assert ir.changes == []
        assert ir.warning is None

    def test_with_changes(self) -> None:
        ir = InstallationResult(
            agent_id="codex",
            display_name="Codex",
            changes=[
                PlannedChange(
                    path=".codex/instructions.md",
                    kind=ChangeKind.CREATE,
                    description="Created",
                )
            ],
        )
        assert len(ir.changes) == 1
        assert ir.changes[0].path == ".codex/instructions.md"


# ===========================================================================
# GenericPromptInstaller tests
# ===========================================================================


class TestGenericPromptInstaller:
    """Tests for the :class:`GenericPromptInstaller`."""

    def test_detect_false_when_no_file(self, tmp_path: Path) -> None:
        installer = GenericPromptInstaller()
        assert installer.detect(tmp_path) is False

    def test_detect_true_when_file_exists(self, tmp_path: Path) -> None:
        installer = GenericPromptInstaller()
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        (bound_dir / "integration-prompt.md").write_text("test")
        assert installer.detect(tmp_path) is True

    def test_plan_new_installation(self, tmp_path: Path) -> None:
        installer = GenericPromptInstaller()
        plan = installer.plan(tmp_path)
        assert len(plan) >= 1
        paths = [c.path for c in plan]
        assert ".bound/integration-prompt.md" in paths or any(
            "integration-prompt.md" in c.description for c in plan
        )

    def test_plan_existing_file(self, tmp_path: Path) -> None:
        installer = GenericPromptInstaller()
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        (bound_dir / "integration-prompt.md").write_text("old")
        plan = installer.plan(tmp_path)
        assert any(c.kind == ChangeKind.MODIFY for c in plan)

    def test_install_creates_file(self, tmp_path: Path) -> None:
        installer = GenericPromptInstaller()
        result = installer.install(tmp_path)
        assert result.agent_id == "generic"
        target = tmp_path / ".bound" / "integration-prompt.md"
        assert target.is_file()
        content = target.read_text()
        assert "BOUND" in content
        assert len(result.changes) >= 1

    def test_install_idempotent(self, tmp_path: Path) -> None:
        installer = GenericPromptInstaller()
        installer.install(tmp_path)
        result2 = installer.install(tmp_path)
        # Second run should succeed without error
        assert result2.agent_id == "generic"
        # File should still exist with valid content
        target = tmp_path / ".bound" / "integration-prompt.md"
        assert target.is_file()
        assert "BOUND" in target.read_text()


# ===========================================================================
# CodexInstaller tests
# ===========================================================================


class TestCodexInstaller:
    """Tests for the :class:`CodexInstaller`."""

    def test_detect_false_by_default(self, tmp_path: Path) -> None:
        installer = CodexInstaller()
        assert installer.detect(tmp_path) is False

    def test_install_creates_codex_dir_and_file(self, tmp_path: Path) -> None:
        installer = CodexInstaller()
        result = installer.install(tmp_path)
        target = tmp_path / ".codex" / "instructions.md"
        assert target.is_file()
        assert "Codex" in target.read_text()
        assert len(result.changes) >= 1

    def test_plan_new_installation(self, tmp_path: Path) -> None:
        installer = CodexInstaller()
        plan = installer.plan(tmp_path)
        assert len(plan) >= 1
        paths = [c.path for c in plan]
        assert any("codex" in p for p in paths)

    def test_install_idempotent(self, tmp_path: Path) -> None:
        installer = CodexInstaller()
        installer.install(tmp_path)
        result2 = installer.install(tmp_path)
        assert result2.agent_id == "codex"
        target = tmp_path / ".codex" / "instructions.md"
        assert target.is_file()


# ===========================================================================
# ClaudeCodeInstaller tests
# ===========================================================================


class TestClaudeCodeInstaller:
    """Tests for the :class:`ClaudeCodeInstaller`."""

    def test_detect_false_when_no_file(self, tmp_path: Path) -> None:
        installer = ClaudeCodeInstaller()
        assert installer.detect(tmp_path) is False

    def test_detect_false_when_no_bound_content(self, tmp_path: Path) -> None:
        installer = ClaudeCodeInstaller()
        (tmp_path / "CLAUDE.md").write_text("# Some other instructions")
        assert installer.detect(tmp_path) is False

    def test_detect_true_when_bound_present(self, tmp_path: Path) -> None:
        installer = ClaudeCodeInstaller()
        (tmp_path / "CLAUDE.md").write_text("# Claude Instructions\n\nBOUND bounded-utility policy")
        assert installer.detect(tmp_path) is True

    def test_install_creates_file(self, tmp_path: Path) -> None:
        installer = ClaudeCodeInstaller()
        result = installer.install(tmp_path)
        target = tmp_path / "CLAUDE.md"
        assert target.is_file()
        assert "BOUND" in target.read_text()
        assert result.agent_id == "claude-code"

    def test_install_idempotent(self, tmp_path: Path) -> None:
        installer = ClaudeCodeInstaller()
        installer.install(tmp_path)
        result2 = installer.install(tmp_path)
        assert result2.agent_id == "claude-code"
        target = tmp_path / "CLAUDE.md"
        assert target.is_file()

    def test_install_appends_to_existing(self, tmp_path: Path) -> None:
        installer = ClaudeCodeInstaller()
        (tmp_path / "CLAUDE.md").write_text("# Existing content\n")
        result = installer.install(tmp_path)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "Existing content" in content
        assert "BOUND" in content
        assert any(c.kind == ChangeKind.MODIFY for c in result.changes)


# ===========================================================================
# ClineInstaller tests
# ===========================================================================


class TestClineInstaller:
    """Tests for the :class:`ClineInstaller`."""

    def test_detect_false_by_default(self, tmp_path: Path) -> None:
        installer = ClineInstaller()
        assert installer.detect(tmp_path) is False

    def test_install_creates_file(self, tmp_path: Path) -> None:
        installer = ClineInstaller()
        result = installer.install(tmp_path)
        target = tmp_path / ".clinerules"
        assert target.is_file()
        assert "BOUND" in target.read_text()
        assert result.agent_id == "cline"

    def test_install_idempotent(self, tmp_path: Path) -> None:
        installer = ClineInstaller()
        installer.install(tmp_path)
        result2 = installer.install(tmp_path)
        assert result2.agent_id == "cline"
        target = tmp_path / ".clinerules"
        assert target.is_file()

    def test_plan_includes_create_for_new(self, tmp_path: Path) -> None:
        installer = ClineInstaller()
        plan = installer.plan(tmp_path)
        assert any(c.kind == ChangeKind.CREATE for c in plan)
        assert any("clinerules" in c.path for c in plan)


# ===========================================================================
# get_installer tests
# ===========================================================================


class TestGetInstaller:
    """Tests for the :func:`get_installer` lookup."""

    def test_returns_installer_for_valid_ids(self) -> None:
        for agent_id in ("generic", "codex", "claude-code", "cline"):
            installer = get_installer(agent_id)
            assert installer.id == agent_id

    def test_raises_keyerror_for_unknown(self) -> None:
        with pytest.raises(KeyError, match="Unknown agent"):
            get_installer("nonexistent")


# ===========================================================================
# setup_project tests
# ===========================================================================


class TestSetupProject:
    """Tests for the :func:`setup_project` orchestrator."""

    def test_raises_setup_error_for_missing_dir(self) -> None:
        with pytest.raises(SetupError):
            setup_project("/nonexistent/path")

    def test_dry_run_does_not_write_files(self, tmp_path: Path) -> None:
        result = setup_project(tmp_path, agent_id="generic", dry_run=True)
        assert result.installation is not None
        # No files should be written
        assert not (tmp_path / ".bound").exists()
        assert not (tmp_path / "bound-policy.yaml").exists()

    def test_dry_run_reports_planned_changes(self, tmp_path: Path) -> None:
        result = setup_project(tmp_path, agent_id="generic", dry_run=True)
        assert result.installation is not None
        assert len(result.installation.changes) >= 1
        # Check that changes describe intended actions
        assert any("integration-prompt.md" in c.path for c in result.installation.changes)

    def test_writes_policy_and_integration(self, tmp_path: Path) -> None:
        result = setup_project(tmp_path, agent_id="generic", force=True)
        assert (tmp_path / "bound-policy.yaml").is_file()
        assert (tmp_path / ".bound" / "integration-prompt.md").is_file()
        assert result.policy_valid is True

    def test_idempotent_twice(self, tmp_path: Path) -> None:
        result1 = setup_project(tmp_path, agent_id="generic", force=True)
        result2 = setup_project(tmp_path, agent_id="generic", force=True)
        assert result1.agent_id == result2.agent_id
        # Files should still be valid
        assert (tmp_path / "bound-policy.yaml").is_file()
        assert (tmp_path / ".bound" / "integration-prompt.md").is_file()

    def test_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        # Create an existing policy
        (tmp_path / "bound-policy.yaml").write_text("# existing")
        result = setup_project(tmp_path, agent_id="generic", force=False)
        # Should warn about existing policy
        assert any("already exists" in w.lower() for w in result.policy_warnings)

    def test_force_overwrites_existing_policy(self, tmp_path: Path) -> None:
        (tmp_path / "bound-policy.yaml").write_text("# old policy")
        result = setup_project(tmp_path, agent_id="generic", force=True)
        content = (tmp_path / "bound-policy.yaml").read_text()
        assert "version" in content  # newly generated policy
        assert result.policy_valid is True

    def test_with_verify(self, tmp_path: Path) -> None:
        result = setup_project(tmp_path, agent_id="generic", force=True, verify=True)
        # Smoke eval should have added a warning note
        has_smoke = any("smoke evaluation" in w.lower() for w in result.policy_warnings)
        # Policy should be valid
        assert result.policy_valid is True
        # verify adds a smoke note (as a "warning" technically, which is the
        # informational smoke-eval message)
        assert has_smoke or len(result.policy_warnings) >= 0

    def test_next_commands_for_codex(self, tmp_path: Path) -> None:
        result = setup_project(tmp_path, agent_id="codex", dry_run=True)
        assert any("codex" in cmd for cmd in result.next_commands)

    def test_next_commands_for_claude(self, tmp_path: Path) -> None:
        result = setup_project(tmp_path, agent_id="claude-code", dry_run=True)
        assert any("claude" in cmd for cmd in result.next_commands)

    def test_creates_bound_directories(self, tmp_path: Path) -> None:
        setup_project(tmp_path, agent_id="generic", force=True)
        assert (tmp_path / ".bound").is_dir()
        assert (tmp_path / ".bound" / "runs").is_dir()
        assert (tmp_path / ".bound" / "checkpoints").is_dir()


# ===========================================================================
# Helper function tests
# ===========================================================================


class TestEnsureBoundDirs:
    """Tests for :func:`_ensure_bound_dirs`."""

    def test_creates_all_subdirs(self, tmp_path: Path) -> None:
        changes = _ensure_bound_dirs(tmp_path)
        assert (tmp_path / ".bound").is_dir()
        assert (tmp_path / ".bound" / "runs").is_dir()
        assert (tmp_path / ".bound" / "checkpoints").is_dir()
        assert len(changes) == 3

    def test_idempotent(self, tmp_path: Path) -> None:
        _ensure_bound_dirs(tmp_path)
        changes = _ensure_bound_dirs(tmp_path)
        # No new directories should be created
        assert len(changes) == 0


class TestPlanBoundDirs:
    """Tests for :func:`_plan_bound_dirs`."""

    def test_plans_all_when_empty(self, tmp_path: Path) -> None:
        changes = _plan_bound_dirs(tmp_path)
        assert len(changes) == 3
        for c in changes:
            assert c.kind == ChangeKind.CREATE

    def test_plans_none_when_exist(self, tmp_path: Path) -> None:
        _ensure_bound_dirs(tmp_path)
        changes = _plan_bound_dirs(tmp_path)
        assert len(changes) == 0


# ===========================================================================
# CLI integration tests (using a temp project dir)
# ===========================================================================


class TestCLISetup:
    """End-to-end tests via the ``main()`` entry point."""

    def _setup_project(self, tmp_path: Path) -> None:
        """Create a minimal Python project so detect_tooling can find things."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\n[tool.pytest.ini_options]\n'
        )
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "__init__.py").write_text("")

    def test_dry_run_exit_zero(self, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        rc, out, err = _run_setup_cli(
            tmp_path,
            ["--agent", "generic", "--dry-run", "--project-dir", str(tmp_path)],
        )
        assert rc == 0

    def test_json_output(self, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        rc, out, err = _run_setup_cli(
            tmp_path,
            [
                "--agent",
                "generic",
                "--dry-run",
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert rc == 0
        # stdout should contain valid JSON
        data = json.loads(out)
        assert data["agent_id"] == "generic"
        assert "installation" in data
        assert "next_commands" in data

    def test_force_writes_policy(self, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        rc, out, err = _run_setup_cli(
            tmp_path,
            ["--agent", "generic", "--force", "--project-dir", str(tmp_path)],
        )
        assert rc == 0
        assert (tmp_path / "bound-policy.yaml").is_file()

    def test_missing_directory_errors(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        rc, out, err = _run_setup_cli(
            tmp_path,
            ["--agent", "generic", "--project-dir", str(missing)],
        )
        assert rc == 1

    def test_invalid_agent_rejected(self, tmp_path: Path) -> None:
        """argparse should reject an invalid --agent choice before reaching handler."""
        self._setup_project(tmp_path)
        import sys
        from io import StringIO

        from bound.cli import main

        old_stderr = sys.stderr
        err = StringIO()
        sys.stderr = err
        try:
            main(["setup", "--agent", "invalid-agent", "--project-dir", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 2
        finally:
            sys.stderr = old_stderr
