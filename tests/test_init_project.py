"""Tests for the project initialisation module (``bound.init_project``).

Verifies that:

1. :func:`detect_tooling` correctly detects tooling configurations.
2. :func:`generate_policy` produces valid YAML matching the policy schema.
3. Confidence levels are correctly assigned (DETECTED, UNCERTAIN, NOT_FOUND).
4. Edge cases (empty directory, no config files, unknown tooling) are handled.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bound.init_project import (
    Confidence,
    ProjectDetections,
    ToolDetection,
    detect_tooling,
    generate_policy,
)

# =========================================================================
# ToolDetection unit tests
# =========================================================================


class TestToolDetection:
    """Unit tests for the :class:`ToolDetection` container."""

    def test_detected_is_truthy(self) -> None:
        """A detected tool is truthy."""
        td = ToolDetection("pytest", Confidence.DETECTED)
        assert td

    def test_uncertain_is_truthy(self) -> None:
        """An uncertain tool is truthy (caller may still act on it)."""
        td = ToolDetection("ruff", Confidence.UNCERTAIN, detail="weak signal")
        assert td

    def test_not_found_is_falsy(self) -> None:
        """A not-found tool is falsy."""
        td = ToolDetection("pytest", Confidence.NOT_FOUND)
        assert not td

    def test_comment_line_detected_empty(self) -> None:
        """A detected tool produces no comment line."""
        td = ToolDetection("pytest", Confidence.DETECTED)
        assert td.comment_line == ""

    def test_comment_line_uncertain(self) -> None:
        """An uncertain tool produces a comment line with detail."""
        td = ToolDetection("ruff", Confidence.UNCERTAIN, detail="weak signal")
        assert "UNCERTAIN" in td.comment_line
        assert "ruff" in td.comment_line

    def test_comment_line_not_found(self) -> None:
        """A not-found tool produces a comment line."""
        td = ToolDetection("pytest", Confidence.NOT_FOUND)
        assert "NOT FOUND" in td.comment_line
        assert "pytest" in td.comment_line


# =========================================================================
# detect_tooling tests
# =========================================================================


class TestDetectTooling:
    """Tests for :func:`detect_tooling`."""

    def test_returns_project_detections(self, tmp_path: Path) -> None:
        """detect_tooling returns a ProjectDetections instance."""
        detections = detect_tooling(tmp_path)
        assert isinstance(detections, ProjectDetections)
        assert detections.project_dir == tmp_path.resolve()

    def test_empty_dir_all_not_found(self, tmp_path: Path) -> None:
        """An empty directory should result in NOT_FOUND for all tooling."""
        detections = detect_tooling(tmp_path)
        for attr in ("test_framework", "linter", "type_checker", "coverage", "build_system"):
            detection: ToolDetection = getattr(detections, attr)
            assert detection.confidence == Confidence.NOT_FOUND, f"{attr} should be NOT_FOUND"

    def test_detects_pytest_from_pyproject_toml(self, tmp_path: Path) -> None:
        """Detect pytest when pyproject.toml has [tool.pytest.ini_options]."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
""")
        detections = detect_tooling(tmp_path)
        assert detections.test_framework.name == "pytest"
        assert detections.test_framework.confidence == Confidence.DETECTED

    def test_detects_pytest_from_pytest_ini(self, tmp_path: Path) -> None:
        """Detect pytest when pytest.ini exists."""
        (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
        detections = detect_tooling(tmp_path)
        assert detections.test_framework.name == "pytest"
        assert detections.test_framework.confidence == Confidence.DETECTED

    def test_detects_pytest_from_conftest(self, tmp_path: Path) -> None:
        """Detect pytest when tests/conftest.py exists."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "conftest.py").write_text("")
        detections = detect_tooling(tmp_path)
        assert detections.test_framework.name == "pytest"
        assert detections.test_framework.confidence == Confidence.DETECTED

    def test_detects_unittest_when_no_pytest(self, tmp_path: Path) -> None:
        """Detect unittest when test files import unittest and not pytest."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_foo.py").write_text(
            "import unittest\n\nclass TestFoo(unittest.TestCase):\n    pass\n"
        )
        detections = detect_tooling(tmp_path)
        assert detections.test_framework.name == "unittest"
        assert detections.test_framework.confidence == Confidence.DETECTED

    def test_detects_ruff_from_pyproject(self, tmp_path: Path) -> None:
        """Detect ruff when pyproject.toml has [tool.ruff]."""
        (tmp_path / "pyproject.toml").write_text("""[tool.ruff]
target-version = "py312"
""")
        detections = detect_tooling(tmp_path)
        assert detections.linter.name == "ruff"
        assert detections.linter.confidence == Confidence.DETECTED

    def test_detects_ruff_from_ruff_toml(self, tmp_path: Path) -> None:
        """Detect ruff when .ruff.toml exists."""
        (tmp_path / ".ruff.toml").write_text('target-version = "py312"\n')
        detections = detect_tooling(tmp_path)
        assert detections.linter.name == "ruff"
        assert detections.linter.confidence == Confidence.DETECTED

    def test_detects_flake8_from_setup_cfg(self, tmp_path: Path) -> None:
        """Detect flake8 when setup.cfg has [flake8]."""
        (tmp_path / "setup.cfg").write_text("[flake8]\nmax-line-length = 100\n")
        detections = detect_tooling(tmp_path)
        assert detections.linter.name == "flake8"
        assert detections.linter.confidence == Confidence.DETECTED

    def test_detects_mypy_from_pyproject(self, tmp_path: Path) -> None:
        """Detect mypy when pyproject.toml has [tool.mypy]."""
        (tmp_path / "pyproject.toml").write_text("""[tool.mypy]
strict = true
""")
        detections = detect_tooling(tmp_path)
        assert detections.type_checker.name == "mypy"
        assert detections.type_checker.confidence == Confidence.DETECTED

    def test_detects_mypy_from_mypy_ini(self, tmp_path: Path) -> None:
        """Detect mypy when mypy.ini exists."""
        (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = true\n")
        detections = detect_tooling(tmp_path)
        assert detections.type_checker.name == "mypy"
        assert detections.type_checker.confidence == Confidence.DETECTED

    def test_detects_pyright_from_config(self, tmp_path: Path) -> None:
        """Detect pyright when pyrightconfig.json exists."""
        (tmp_path / "pyrightconfig.json").write_text("{}")
        detections = detect_tooling(tmp_path)
        assert detections.type_checker.name == "pyright"
        assert detections.type_checker.confidence == Confidence.DETECTED

    def test_hatchling_build_system(self, tmp_path: Path) -> None:
        """Detect hatchling build system."""
        (tmp_path / "pyproject.toml").write_text("""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")
        detections = detect_tooling(tmp_path)
        assert detections.build_system.name == "hatchling"
        assert detections.build_system.confidence == Confidence.DETECTED

    def test_setuptools_from_setup_py(self, tmp_path: Path) -> None:
        """Detect setuptools when setup.py exists."""
        (tmp_path / "setup.py").write_text(
            "from setuptools import setup\nsetup(name='test')\n"
        )
        detections = detect_tooling(tmp_path)
        assert detections.build_system.name == "setuptools"
        assert detections.build_system.confidence == Confidence.DETECTED

    def test_poetry_detected(self, tmp_path: Path) -> None:
        """Detect poetry when pyproject.toml has [tool.poetry]."""
        (tmp_path / "pyproject.toml").write_text("""[tool.poetry]
name = "test"
""")
        detections = detect_tooling(tmp_path)
        assert detections.build_system.name == "poetry"
        assert detections.build_system.confidence == Confidence.DETECTED

    def test_uv_detected(self, tmp_path: Path) -> None:
        """Detect uv when uv.lock exists."""
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "pyproject.toml").write_text("""[project]
name = "test"
""")
        detections = detect_tooling(tmp_path)
        assert detections.build_system.name == "uv"
        assert detections.build_system.confidence == Confidence.DETECTED

    def test_git_branch_detected(self, tmp_path: Path) -> None:
        """Detect git branch from .git/HEAD."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        detections = detect_tooling(tmp_path)
        assert detections.git_branch == "main"

    def test_git_detached_head(self, tmp_path: Path) -> None:
        """Detect detached HEAD state."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("abc123def456\n")
        detections = detect_tooling(tmp_path)
        assert detections.git_branch == "detached HEAD"

    def test_git_remote_detected(self, tmp_path: Path) -> None:
        """Detect git remote from .git/config."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = git@github.com:user/repo.git\n'
        )
        detections = detect_tooling(tmp_path)
        assert detections.git_remote == "git@github.com:user/repo.git"
        assert detections.ci_provider.name == "github-actions"

    def test_no_git_directory(self, tmp_path: Path) -> None:
        """No .git directory means no git info."""
        detections = detect_tooling(tmp_path)
        assert detections.git_branch == ""
        assert detections.git_remote == ""
        assert detections.ci_provider.confidence == Confidence.NOT_FOUND

    def test_detects_coverage_from_coveragerc(self, tmp_path: Path) -> None:
        """Detect coverage.py from .coveragerc."""
        (tmp_path / ".coveragerc").write_text("[run]\nsource = src\n")
        detections = detect_tooling(tmp_path)
        assert detections.coverage.name == "coverage.py"
        assert detections.coverage.confidence == Confidence.DETECTED

    def test_detects_coverage_from_pyproject(self, tmp_path: Path) -> None:
        """Detect coverage.py from pyproject.toml [tool.coverage.run]."""
        (tmp_path / "pyproject.toml").write_text("""[tool.coverage.run]
source = ["src"]
""")
        detections = detect_tooling(tmp_path)
        assert detections.coverage.name == "coverage.py"
        assert detections.coverage.confidence == Confidence.DETECTED


# =========================================================================
# generate_policy tests
# =========================================================================


class TestGeneratePolicy:
    """Tests for :func:`generate_policy`."""

    def _generate(self, tmp_path: Path) -> str:
        """Generate a policy for the given path and return the YAML string."""
        detections = detect_tooling(tmp_path)
        return generate_policy(detections)

    def test_returns_valid_yaml(self, tmp_path: Path) -> None:
        """The generated policy is valid YAML."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert isinstance(parsed, dict)

    def test_has_schema_version(self, tmp_path: Path) -> None:
        """The generated policy includes schema_version."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert parsed.get("schema_version") == "1.0"

    def test_has_policy_section(self, tmp_path: Path) -> None:
        """The generated policy includes a policy section."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "policy" in parsed
        assert parsed["policy"]["id"] == "auto-generated"

    def test_has_collectors_section(self, tmp_path: Path) -> None:
        """The generated policy includes a collectors section."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "collectors" in parsed
        assert "test" in parsed["collectors"]

    def test_has_acceptance_checks(self, tmp_path: Path) -> None:
        """The generated policy includes acceptance_checks."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "acceptance_checks" in parsed

    def test_has_quality_checks(self, tmp_path: Path) -> None:
        """The generated policy includes quality_checks."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "quality_checks" in parsed

    def test_has_risk_checks(self, tmp_path: Path) -> None:
        """The generated policy includes risk_checks."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "risk_checks" in parsed
        risk_ids = [c["id"] for c in parsed["risk_checks"]]
        assert "no-secrets" in risk_ids
        assert "scope-respected" in risk_ids

    def test_has_budgets(self, tmp_path: Path) -> None:
        """The generated policy includes budgets."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "budgets" in parsed
        for dim in ("attempts", "tool_calls", "tokens", "runtime", "financial_cost"):
            assert dim in parsed["budgets"], f"budget dimension {dim} missing"

    def test_has_change_scope(self, tmp_path: Path) -> None:
        """The generated policy includes change_scope."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "change_scope" in parsed
        assert "allowed_paths" in parsed["change_scope"]
        assert "forbidden_paths" in parsed["change_scope"]

    def test_has_approvals(self, tmp_path: Path) -> None:
        """The generated policy includes approvals."""
        yaml_content = self._generate(tmp_path)
        parsed = yaml.safe_load(yaml_content)
        assert "approvals" in parsed

    def test_with_pytest_detected(self, tmp_path: Path) -> None:
        """When pytest is detected, the test collector and acceptance check are present."""
        (tmp_path / "pyproject.toml").write_text("""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
""")
        detections = detect_tooling(tmp_path)
        yaml_content = generate_policy(detections)
        parsed = yaml.safe_load(yaml_content)
        checks = parsed["acceptance_checks"]
        assert any(c["id"] == "tests-pass" for c in checks)
        assert "test" in parsed["collectors"]
        collector = parsed["collectors"]["test"]
        assert "pytest" in str(collector.get("command", ""))

    def test_with_ruff_detected(self, tmp_path: Path) -> None:
        """When ruff is detected, a lint collector and lint-clean check are present."""
        (tmp_path / "pyproject.toml").write_text("""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py312"
""")
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        detections = detect_tooling(tmp_path)
        yaml_content = generate_policy(detections)
        parsed = yaml.safe_load(yaml_content)
        assert "lint" in parsed["collectors"]
        quality_ids = [c["id"] for c in parsed["quality_checks"]]
        assert "lint-clean" in quality_ids

    def test_with_mypy_detected(self, tmp_path: Path) -> None:
        """When mypy is detected, a typecheck collector and check are present."""
        (tmp_path / "mypy.ini").write_text("[mypy]\n")
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        detections = detect_tooling(tmp_path)
        yaml_content = generate_policy(detections)
        parsed = yaml.safe_load(yaml_content)
        assert "typecheck" in parsed["collectors"]
        quality_ids = [c["id"] for c in parsed["quality_checks"]]
        assert "typecheck-clean" in quality_ids

    def test_can_be_parsed_by_policy_schema(self, tmp_path: Path) -> None:
        """The generated policy can be loaded by load_policy_yaml."""
        from bound.policy_schema import load_policy_yaml

        (tmp_path / "pyproject.toml").write_text("""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"

[tool.mypy]
strict = true
""")
        detections = detect_tooling(tmp_path)
        yaml_content = generate_policy(detections)

        policy_path = tmp_path / "bound-policy.yaml"
        policy_path.write_text(yaml_content, encoding="utf-8")

        config = load_policy_yaml(policy_path)
        assert config.schema_version == "1.0"
        assert config.policy.id == "auto-generated"


# =========================================================================
# Integration with CLI
# =========================================================================


class TestInitCLI:
    """Tests for the ``bound init`` CLI command."""

    def test_init_no_project_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """bound init with a non-existent directory returns an error."""
        from bound.cli import main

        rc = main(["init", "--project-dir", str(tmp_path / "nonexistent")])
        out, err = capsys.readouterr()
        assert rc != 0
        assert "error: directory not found" in err

    def test_init_stdout_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """bound init --stdout prints the generated policy to stdout."""
        from bound.cli import main

        (tmp_path / "pytest.ini").write_text("[pytest]\n")

        rc = main(["init", "--project-dir", str(tmp_path), "--stdout"])
        out, err = capsys.readouterr()
        assert rc == 0, f"init failed: {err}"
        assert "schema_version:" in out
        assert "policy:" in out
        assert "collectors:" in out
        assert "Detecting tooling" in err
        assert "Next steps" in err

    def test_init_writes_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """bound init writes bound-policy.yaml to the project dir."""
        from bound.cli import main

        (tmp_path / "pytest.ini").write_text("[pytest]\n")

        rc = main(["init", "--project-dir", str(tmp_path)])
        out, err = capsys.readouterr()
        assert rc == 0, f"init failed: {err}"
        assert "Wrote" in err
        policy_path = tmp_path / "bound-policy.yaml"
        assert policy_path.exists()
        content = policy_path.read_text(encoding="utf-8")
        assert "schema_version:" in content

    def test_init_refuses_overwrite(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """bound init refuses to overwrite an existing bound-policy.yaml."""
        from bound.cli import main

        policy_path = tmp_path / "bound-policy.yaml"
        policy_path.write_text("# existing policy\n", encoding="utf-8")

        rc = main(["init", "--project-dir", str(tmp_path)])
        out, err = capsys.readouterr()
        assert rc != 0
        assert "already exists" in err

    def test_init_with_project_dir_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bound init uses the current directory by default."""
        import os
        import tempfile
        from pathlib import Path

        from bound.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "pytest.ini").write_text("[pytest]\n")
            original_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                rc = main(["init"])
                out, err = capsys.readouterr()
                assert rc == 0, f"init failed: {err}"
                assert "Wrote" in err
                policy_path = tmp_path / "bound-policy.yaml"
                assert policy_path.exists()
            finally:
                os.chdir(original_cwd)

    def test_init_generated_policy_is_valid(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The policy generated by bound init is schema-valid."""
        from bound.cli import main
        from bound.policy_schema import load_policy_yaml

        (tmp_path / "pyproject.toml").write_text("""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"

[tool.mypy]
strict = true
""")
        (tmp_path / "setup.cfg").write_text("[coverage:run]\nsource = src\n")

        rc = main(["init", "--project-dir", str(tmp_path)])
        out, err = capsys.readouterr()
        assert rc == 0, f"init failed: {err}"

        policy_path = tmp_path / "bound-policy.yaml"
        config = load_policy_yaml(policy_path)
        assert config.schema_version == "1.0"
        assert config.policy.id == "auto-generated"

