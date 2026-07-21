"""Project initialisation — detect tooling and generate ``bound-policy.yaml``.

Provides two public functions:

* :func:`detect_tooling` — probes a project directory and returns a
  :class:`ToolingDetections` result describing which test frameworks, linters,
  type checkers, coverage tools, build systems, and Git CI providers are
  present.

* :func:`generate_policy` — converts the detections into a minimal but
  reviewable ``bound-policy.yaml`` string, with uncertain detections emitted as
  YAML comments so a human can make the final call.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =========================================================================
# Confidence levels
# =========================================================================


class Confidence(str, Enum):
    """Confidence level for a tooling detection.

    ``DETECTED`` — evidence was found (config file, import, or command).
    ``UNCERTAIN`` — weak/ambiguous evidence, or the detection heuristic may not
        be reliable.
    ``NOT_FOUND`` — no evidence was found.
    """

    DETECTED = "DETECTED"
    UNCERTAIN = "UNCERTAIN"
    NOT_FOUND = "NOT_FOUND"


# =========================================================================
# Typed detection results
# =========================================================================


class ToolDetection:
    """Base container for one tool detection.

    Attributes:
        name: Normalised tool name (e.g. ``"pytest"``, ``"ruff"``).
        confidence: :class:`Confidence` level.
        detail: Optional human-readable detail (e.g. version string).
    """

    def __init__(self, name: str, confidence: Confidence, detail: str | None = None) -> None:
        self.name = name
        self.confidence = confidence
        self.detail = detail

    def __bool__(self) -> bool:
        """A detection is truthy when confidence is at least ``UNCERTAIN``."""
        return self.confidence in (Confidence.DETECTED, Confidence.UNCERTAIN)

    def __repr__(self) -> str:
        return f"ToolDetection({self.name}, {self.confidence.value}{', ' + self.detail if self.detail else ''})"

    @property
    def comment_line(self) -> str:
        """Return a YAML-comment line for uncertain detections, else an empty string."""
        if self.confidence == Confidence.UNCERTAIN:
            return f"# UNCERTAIN: {self.name} ({self.detail or 'weak evidence'})"
        if self.confidence == Confidence.NOT_FOUND:
            return f"# NOT FOUND: {self.name}"
        return ""


class ProjectDetections:
    """Aggregated detection results for a project directory.

    Attributes:
        test_framework: Detection for the primary test framework.
        linter: Detection for the primary linter.
        type_checker: Detection for the primary type checker.
        coverage: Detection for the coverage tool.
        build_system: Detection for the build/package system.
        git_branch: Current Git branch (empty string when not detected).
        git_remote: Remote URL (empty string when not detected).
        ci_provider: Detection for a CI provider.
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.test_framework: ToolDetection = ToolDetection("unknown", Confidence.NOT_FOUND)
        self.linter: ToolDetection = ToolDetection("unknown", Confidence.NOT_FOUND)
        self.type_checker: ToolDetection = ToolDetection("unknown", Confidence.NOT_FOUND)
        self.coverage: ToolDetection = ToolDetection("unknown", Confidence.NOT_FOUND)
        self.build_system: ToolDetection = ToolDetection("unknown", Confidence.NOT_FOUND)
        self.git_branch: str = ""
        self.git_remote: str = ""
        self.ci_provider: ToolDetection = ToolDetection("unknown", Confidence.NOT_FOUND)

# =========================================================================
# Detection helpers
# =========================================================================


def _read_file_safe(path: Path) -> str | None:
    """Read a text file, returning ``None`` on any I/O error.

    Args:
        path: The file to read.

    Returns:
        The file content, or ``None`` if the file does not exist or cannot be
        read.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None


def _check_toml_key(content: str, table: str, key: str) -> bool:
    """Check whether a dotted ``[table]`` contains ``key = ...`` in TOML text.

    This is a simple line-oriented check — not a full TOML parser — suitable
    for fast heuristic detection.

    Args:
        content: Raw TOML file content.
        table: Table path, e.g. ``"tool.pytest.ini_options"``.
        key: The key to look for inside that table.

    Returns:
        ``True`` if the key was found inside the table section.
    """
    in_table = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_table = stripped.strip("[]").strip()
            in_table = current_table == table
        elif in_table and stripped.startswith(key) and ("=" in stripped or ":" in stripped):
            return True
    return False


def _check_cfg_section(content: str, section: str) -> bool:
    """Check whether a ``[section]`` exists in an INI-style config file.

    Args:
        content: Raw config file content.
        section: Section name, e.g. ``"tool:pytest"``.

    Returns:
        ``True`` if the section header was found.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped.strip("[]").strip()
            if current == section:
                return True
    return False
# =========================================================================
# Public API
# =========================================================================


def detect_tooling(project_dir: str | Path) -> ProjectDetections:
    """Detect existing project tooling configuration in *project_dir*.

    Probes the filesystem and (when available) the ``pyproject.toml``,
    ``setup.cfg``, ``.git`` metadata, and installed tools to classify each
    tool category. Results carry a :class:`Confidence` level so callers can
    decide how to handle ambiguous evidence.

    Args:
        project_dir: Path to the project root directory.

    Returns:
        A :class:`ProjectDetections` with per-category :class:`ToolDetection`
        results.
    """
    root = Path(project_dir).resolve()
    detections = ProjectDetections(root)

    # --- Read key config files ---
    pyproject_toml = _read_file_safe(root / "pyproject.toml")
    setup_cfg = _read_file_safe(root / "setup.cfg")
    setup_py = _read_file_safe(root / "setup.py")

    # ------------------------------------------------------------------
    # Build system
    # ------------------------------------------------------------------
    if pyproject_toml is not None:
        if "build-system" in pyproject_toml and 'hatchling' in pyproject_toml:
            detections.build_system = ToolDetection("hatchling", Confidence.DETECTED)
        elif "build-system" in pyproject_toml and 'setuptools' in pyproject_toml:
            detections.build_system = ToolDetection("setuptools", Confidence.DETECTED)
        elif "build-system" in pyproject_toml:
            detections.build_system = ToolDetection("hatchling", Confidence.UNCERTAIN,
                                                    detail="build-system found but backend unclear")
        else:
            detections.build_system = ToolDetection("hatchling", Confidence.UNCERTAIN,
                                                    detail="pyproject.toml present but no build-system")

    if detections.build_system.confidence == Confidence.NOT_FOUND:
        if setup_py is not None and "setup" in setup_py:
            detections.build_system = ToolDetection("setuptools", Confidence.DETECTED,
                                                    detail="setup.py present")
        if setup_cfg is not None and "metadata" in setup_cfg:
            detections.build_system = ToolDetection("setuptools", Confidence.DETECTED,
                                                    detail="setup.cfg present")

    # Check for poetry / uv independently
    if pyproject_toml is not None:
        if "[tool.poetry]" in pyproject_toml:
            detections.build_system = ToolDetection("poetry", Confidence.DETECTED,
                                                    detail="[tool.poetry] section")
        if "[tool.uv]" in pyproject_toml or (root / "uv.lock").exists():
            detections.build_system = ToolDetection("uv", Confidence.DETECTED,
                                                    detail="uv tool config or uv.lock")

    # Fallback — check for Makefile, requirements.txt, etc.
    if detections.build_system.confidence == Confidence.NOT_FOUND:
        if (root / "Makefile").exists():
            detections.build_system = ToolDetection("make", Confidence.UNCERTAIN,
                                                    detail="Makefile found")
        if (root / "requirements.txt").exists():
            detections.build_system = ToolDetection("pip", Confidence.UNCERTAIN,
                                                    detail="requirements.txt found")

    if detections.build_system.confidence == Confidence.NOT_FOUND:
        if (root / "Cargo.toml").exists() or (root / "package.json").exists():
            detections.build_system = ToolDetection("other", Confidence.UNCERTAIN,
                                                    detail="non-Python build file detected")
        else:
            detections.build_system = ToolDetection("unknown", Confidence.NOT_FOUND)
# ------------------------------------------------------------------
    # Test framework
    # ------------------------------------------------------------------
    detected_test = False

    if pyproject_toml is not None:
        if _check_toml_key(pyproject_toml, "tool.pytest.ini_options", "testpaths") or \
           "pytest" in pyproject_toml.lower():
            detections.test_framework = ToolDetection("pytest", Confidence.DETECTED,
                                                      detail="pyproject.toml pytest config")
            detected_test = True

    if not detected_test and setup_cfg is not None:
        if _check_cfg_section(setup_cfg, "tool:pytest"):
            detections.test_framework = ToolDetection("pytest", Confidence.DETECTED,
                                                      detail="setup.cfg pytest config")
            detected_test = True

    if not detected_test:
        cfg = _read_file_safe(root / "pytest.ini")
        if cfg is not None:
            detections.test_framework = ToolDetection("pytest", Confidence.DETECTED,
                                                      detail="pytest.ini")
            detected_test = True

    if not detected_test:
        tox_ini = _read_file_safe(root / "tox.ini")
        if tox_ini is not None and "pytest" in tox_ini.lower():
            detections.test_framework = ToolDetection("pytest", Confidence.UNCERTAIN,
                                                      detail="referenced in tox.ini")
            detected_test = True

    if not detected_test:
        conftest = root / "tests" / "conftest.py"
        if conftest.exists():
            detections.test_framework = ToolDetection("pytest", Confidence.DETECTED,
                                                      detail="tests/conftest.py found")
            detected_test = True

    if not detected_test:
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            test_files = list(tests_dir.rglob("test_*.py"))
            if test_files:
                content = _read_file_safe(test_files[0])
                if content and "unittest" in content and "pytest" not in content:
                    detections.test_framework = ToolDetection("unittest", Confidence.DETECTED,
                                                              detail=f"{test_files[0].name} imports unittest")
                    detected_test = True
                else:
                    detections.test_framework = ToolDetection("pytest", Confidence.UNCERTAIN,
                                                              detail="test_*.py files found")
                    detected_test = True

    if not detected_test:
        detections.test_framework = ToolDetection("unknown", Confidence.NOT_FOUND)
# ------------------------------------------------------------------
    # Linter
    # ------------------------------------------------------------------
    detected_lint = False

    if pyproject_toml is not None:
        if "[tool.ruff]" in pyproject_toml:
            detections.linter = ToolDetection("ruff", Confidence.DETECTED,
                                              detail="[tool.ruff] in pyproject.toml")
            detected_lint = True
        elif "ruff" in pyproject_toml.lower():
            detections.linter = ToolDetection("ruff", Confidence.UNCERTAIN,
                                              detail="ruff mentioned in pyproject.toml")
            detected_lint = True

    if not detected_lint:
        for cfg_name in (".ruff.toml", "ruff.toml"):
            if (root / cfg_name).exists():
                detections.linter = ToolDetection("ruff", Confidence.DETECTED,
                                                  detail=f"{cfg_name} found")
                detected_lint = True
                break

    if not detected_lint:
        if (root / ".flake8").exists() or (setup_cfg is not None and "[flake8]" in setup_cfg):
            detections.linter = ToolDetection("flake8", Confidence.DETECTED,
                                              detail="flake8 config found")
            detected_lint = True

    if not detected_lint:
        pylint_rc = root / ".pylintrc"
        if pylint_rc.exists() or (root / "pylintrc").exists():
            detections.linter = ToolDetection("pylint", Confidence.DETECTED,
                                              detail="pylintrc found")
            detected_lint = True

    if not detected_lint:
        if pyproject_toml is not None and "lint" in pyproject_toml.lower():
            detections.linter = ToolDetection("unknown", Confidence.UNCERTAIN,
                                              detail="lint mentioned in pyproject.toml")

    if not detected_lint:
        detections.linter = ToolDetection("unknown", Confidence.NOT_FOUND)
# ------------------------------------------------------------------
    # Type checker
    # ------------------------------------------------------------------
    detected_type = False

    if pyproject_toml is not None:
        if "[tool.mypy]" in pyproject_toml:
            detections.type_checker = ToolDetection("mypy", Confidence.DETECTED,
                                                    detail="[tool.mypy] in pyproject.toml")
            detected_type = True
        elif "mypy" in pyproject_toml.lower():
            detections.type_checker = ToolDetection("mypy", Confidence.UNCERTAIN,
                                                    detail="mypy mentioned in pyproject.toml")
            detected_type = True

    if not detected_type:
        mypy_ini = root / "mypy.ini"
        if mypy_ini.exists():
            detections.type_checker = ToolDetection("mypy", Confidence.DETECTED,
                                                    detail="mypy.ini found")
            detected_type = True

    if not detected_type:
        if setup_cfg is not None and "[mypy]" in setup_cfg:
            detections.type_checker = ToolDetection("mypy", Confidence.DETECTED,
                                                    detail="[mypy] in setup.cfg")
            detected_type = True

    if not detected_type:
        if pyproject_toml is not None and "[tool.pyright]" in pyproject_toml:
            detections.type_checker = ToolDetection("pyright", Confidence.DETECTED,
                                                    detail="[tool.pyright] in pyproject.toml")
            detected_type = True

    if not detected_type:
        pyright_json = root / "pyrightconfig.json"
        if pyright_json.exists():
            detections.type_checker = ToolDetection("pyright", Confidence.DETECTED,
                                                    detail="pyrightconfig.json found")
            detected_type = True

# ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------
    detected_cov = False

    if pyproject_toml is not None:
        if "[tool.coverage.run]" in pyproject_toml or "[tool.coverage.report]" in pyproject_toml:
            detections.coverage = ToolDetection("coverage.py", Confidence.DETECTED,
                                                detail="[tool.coverage.*] in pyproject.toml")
            detected_cov = True

    if not detected_cov:
        for cfg_name in (".coveragerc", ".coverage"):
            if (root / cfg_name).exists():
                detections.coverage = ToolDetection("coverage.py", Confidence.DETECTED,
                                                    detail=f"{cfg_name} found")
                detected_cov = True
                break

    if not detected_cov:
        if setup_cfg is not None and _check_cfg_section(setup_cfg, "coverage:run"):
            detections.coverage = ToolDetection("coverage.py", Confidence.DETECTED,
                                                detail="[coverage:run] in setup.cfg")
            detected_cov = True

    if not detected_cov:
        tox_ini = _read_file_safe(root / "tox.ini")
        if tox_ini is not None and "pytest-cov" in tox_ini:
            detections.coverage = ToolDetection("pytest-cov", Confidence.UNCERTAIN,
                                                detail="referenced in tox.ini")
            detected_cov = True

    if not detected_cov:
        if pyproject_toml is not None and (
            "pytest-cov" in pyproject_toml or "coverage" in pyproject_toml.lower()
        ):
            detections.coverage = ToolDetection("coverage.py", Confidence.UNCERTAIN,
                                                detail="coverage mentioned in pyproject.toml")
            detected_cov = True
# ------------------------------------------------------------------
    # Git configuration
    # ------------------------------------------------------------------
    git_dir = root / ".git"
    if git_dir.is_dir():
        head_content = _read_file_safe(git_dir / "HEAD")
        if head_content is not None:
            match = re.match(r"^ref:\s*refs/heads/(\S+)", head_content.strip())
            if match:
                detections.git_branch = match.group(1)
            else:
                detections.git_branch = "detached HEAD"

        git_config = _read_file_safe(git_dir / "config")
        if git_config is not None:
            remote_match = re.search(
                r'\[remote\s+"origin"\]\s*\n\s*url\s*=\s*(\S+)',
                git_config,
                re.MULTILINE,
            )
            if remote_match:
                detections.git_remote = remote_match.group(1)

        if detections.git_remote:
            remote_upper = detections.git_remote.lower()
            if "github.com" in remote_upper:
                gh_actions = root / ".github" / "workflows"
                if gh_actions.is_dir() and list(gh_actions.iterdir()):
                    detections.ci_provider = ToolDetection("github-actions", Confidence.DETECTED,
                                                           detail=".github/workflows found")
                else:
                    detections.ci_provider = ToolDetection("github-actions", Confidence.DETECTED,
                                                           detail="remote is github.com")
            elif "gitlab" in remote_upper:
                detections.ci_provider = ToolDetection("gitlab-ci", Confidence.DETECTED,
                                                       detail="remote is gitlab")
            else:
                detections.ci_provider = ToolDetection("unknown", Confidence.UNCERTAIN,
                                                       detail=f"remote: {detections.git_remote[:60]}")
        else:
            detections.ci_provider = ToolDetection("unknown", Confidence.NOT_FOUND,
                                                   detail="no remote configured")
    else:
        detections.ci_provider = ToolDetection("unknown", Confidence.NOT_FOUND,
                                               detail="no .git directory")

    logger.debug("detected tooling: %s", detections)
    return detections

    if not detected_cov:
        detections.coverage = ToolDetection("unknown", Confidence.NOT_FOUND)
    if not detected_type:
        detections.type_checker = ToolDetection("unknown", Confidence.NOT_FOUND)
def generate_policy(detections: ProjectDetections) -> str:
    """Generate a minimal reviewable ``bound-policy.yaml``.

    The generated policy includes:

    * Acceptance checks based on the detected test framework.
    * Quality checks based on the detected linter/type checker.
    * Risk checks (generic — tuned for most projects).
    * A :class:`ChangeScope` based on the detected project structure.
    * Budgets (generic defaults).
    * Uncertain detections are emitted as YAML comments so a human can resolve
      them before approving the policy.

    Args:
        detections: The tooling detections from :func:`detect_tooling`.

    Returns:
        The complete ``bound-policy.yaml`` content as a string.
    """
    lines: list[str] = []

    # --- Header ---
    lines.append("# BOUND policy configuration — auto-generated by ``bound init``.")
    lines.append("# Review and adjust before use.")
    lines.append("")

    # --- Policy identity ---
    lines.append("schema_version: \"1.0\"")
    lines.append("")
    lines.append("policy:")
    lines.append("  id: auto-generated")
    lines.append('  version: "0.1.0"')
    lines.append("")

    # --- Collectors ---
    lines.append("# --- Collectors ---")
    lines.append("collectors:")
    lines.append("  test:")
    lines.append("    type: command")
    test_cmd = _test_command(detections.test_framework)
    lines.append(f"    command: {test_cmd}")
    lines.append("    timeout_seconds: 120")
    lines.append("    success_exit_codes: [0]")
    lines.append("")

    if detections.linter:
        linter_cmd = _linter_command(detections.linter)
        if linter_cmd:
            lines.append("  lint:")
            lines.append("    type: command")
            lines.append(f"    command: {linter_cmd}")
            lines.append("    timeout_seconds: 60")
            lines.append("    success_exit_codes: [0]")
            lines.append("")

    if detections.type_checker:
        type_cmd = _type_checker_command(detections.type_checker)
        if type_cmd:
            lines.append("  typecheck:")
            lines.append("    type: command")
            lines.append(f"    command: {type_cmd}")
            lines.append("    timeout_seconds: 120")
            lines.append("    success_exit_codes: [0]")
            lines.append("")
# --- Acceptance checks ---
    lines.append("# --- Hard gates (blockers): can never be compensated by positive scores. ---")

    if detections.test_framework and detections.test_framework.confidence != Confidence.NOT_FOUND:
        lines.append("acceptance_checks:")
        lines.append("  - id: tests-pass")
        lines.append('    description: "All tests pass."')
        lines.append("    importance: blocker")
        lines.append("    required: true")
        lines.append("    on_failure: retry")
        lines.append("    on_missing: retry")
        lines.append("    on_claimed: replan")
        lines.append("    minimum_assurance: verified")
        lines.append("    accepted_provenance: [verified, observed]")
        lines.append("    collector: test")
        lines.append("")
    else:
        lines.append("acceptance_checks: []")

    # --- Quality checks ---
    lines.append("# --- Weighted signals (quality): soft contributions, never override a blocker. ---")

    added_quality = False

    # Collect quality-check items first
    quality_items: list[str] = []

    if detections.linter and detections.linter:
        if detections.linter.confidence == Confidence.NOT_FOUND:
            quality_items.append("  # NOTE: no linter detected; add a lint-clean check if needed.")
        else:
            quality_items.append("  - id: lint-clean")
            quality_items.append('    description: "Lint is clean."')
            if detections.linter.confidence == Confidence.UNCERTAIN:
                quality_items.append(f"    # UNCERTAIN: {detections.linter.detail or 'linter detection uncertain'}")
            quality_items.append("    importance: medium")
            quality_items.append("    collector: lint")
            quality_items.append("")
            added_quality = True

    if detections.type_checker:
        if detections.type_checker.confidence == Confidence.NOT_FOUND:
            quality_items.append("  # NOTE: no type checker detected; add a typecheck signal if needed.")
        else:
            quality_items.append("  - id: typecheck-clean")
            quality_items.append('    description: "Type checking passes."')
            if detections.type_checker.confidence == Confidence.UNCERTAIN:
                quality_items.append(f"    # UNCERTAIN: {detections.type_checker.detail or 'type checker detection uncertain'}")
            quality_items.append("    importance: medium")
            quality_items.append("    collector: typecheck")
            quality_items.append("")
            added_quality = True

    if detections.coverage:
        if detections.coverage.confidence == Confidence.NOT_FOUND:
            quality_items.append("  # NOTE: no coverage tool detected; add a coverage signal if needed.")
        else:
            quality_items.append("  - id: coverage")
            quality_items.append('    description: "Test coverage does not regress."')
            if detections.coverage.confidence == Confidence.UNCERTAIN:
                quality_items.append(f"    # UNCERTAIN: {detections.coverage.detail or 'coverage detection uncertain'}")
            quality_items.append("    importance: low")
            quality_items.append("")
            added_quality = True

    if added_quality:
        lines.append("quality_checks:")
        lines.extend(quality_items)
    else:
        # Empty list — schema requires a list, not null
        lines.append("quality_checks: []")
# --- Risk checks ---
    lines.append("# --- Risk hard gates (blockers): violations are unacceptable. ---")
    lines.append("risk_checks:")
    lines.append("  - id: no-secrets")
    lines.append('    description: "No plaintext secrets are introduced in the diff."')
    lines.append("    importance: blocker")
    lines.append("    required: true")
    lines.append("    on_failure: rollback")
    lines.append("    on_missing: replan")
    lines.append("    on_claimed: rollback")
    lines.append("    accepted_provenance: [verified, observed]")
    lines.append("")
    lines.append("  - id: scope-respected")
    lines.append('    description: "Only allowed paths were modified."')
    lines.append("    importance: blocker")
    lines.append("    required: true")
    lines.append("    on_failure: replan")
    lines.append("    on_missing: replan")
    lines.append("")

    # --- Budgets ---
    lines.append("# --- Budgets: soft/hard limits with configurable action at each limit. ---")
    lines.append("budgets:")
    lines.append("  attempts:")
    lines.append("    soft_limit: 2")
    lines.append("    hard_limit: 3")
    lines.append("    on_soft: retry")
    lines.append("    on_hard: replan")
    lines.append("  tool_calls:")
    lines.append("    soft_limit: 15")
    lines.append("    hard_limit: 20")
    lines.append("    on_soft: retry")
    lines.append("    on_hard: replan")
    lines.append("  tokens:")
    lines.append("    hard_limit: 200000")
    lines.append("    on_hard: replan")
    lines.append("  runtime:")
    lines.append("    soft_limit: 300")
    lines.append("    hard_limit: 600")
    lines.append("    on_soft: retry")
    lines.append("    on_hard: replan")
    lines.append("  financial_cost:")
    lines.append("    enabled: false  # disabled by default; enable when cost telemetry exists")
    lines.append("")

    # --- Change scope ---
    lines.append("# --- Scope and safety. ---")
    lines.append("change_scope:")

    src_dirs = _find_source_dirs(detections.project_dir)
    lines.append("  allowed_paths:")
    for d in src_dirs:
        lines.append(f'    - "{d}/**"')
    if not src_dirs:
        lines.append('    - "src/**"  # default: adjust as needed')
        lines.append('    - "tests/**"')

    lines.append("  forbidden_paths:")
    lines.append('    - ".git/**"')
    lines.append('    - "**/.env"')

    dep_patterns = _find_dependency_files(detections.project_dir)
    lines.append("  dependency_file_patterns:")
    for p in dep_patterns or ["pyproject.toml", "requirements*.txt"]:
        lines.append(f'    - "{p}"')

    lines.append("  unexpected_artifacts:")
    lines.append("    enabled: true")
    lines.append("    on_unexpected: replan")
    lines.append("    allowed_patterns:")
    lines.append('      - "*.md"')
    lines.append("")

    # --- Approvals ---
    lines.append("# --- Approvals and rollback. ---")
    lines.append("approvals:")
    lines.append("  commands_requiring_approval:")
    lines.append('    - "rm"')
    lines.append('    - "git push --force"')
    lines.append("  destructive_actions:")
    lines.append('    - "rm -rf"')
    lines.append('    - "git push --force"')
    lines.append("  require_rollback_availability: false")
    lines.append("  on_missing_rollback: replan")
    lines.append("")

    # --- Comments from uncertain detections ---
    uncertain_lines = _uncertain_comment_lines(detections)
    if uncertain_lines:
        lines.append("# --- Resolve these before approving ---")
        lines.extend(uncertain_lines)
        lines.append("")

    return "\n".join(lines)
# =========================================================================
# Helper utilities
# =========================================================================


def _test_command(detection: ToolDetection) -> str:
    """Return the YAML list representation of the test command based on detection.

    Args:
        detection: The test framework detection.

    Returns:
        A YAML inline list string (e.g. ``[python, -m, pytest]``).
    """
    if detection.name == "pytest" or detection.confidence == Confidence.DETECTED:
        return "[python, -m, pytest]"
    if detection.name == "unittest":
        return "[python, -m, unittest, discover]"
    return "[python, -m, pytest]  # default: adjust for your framework"


def _linter_command(detection: ToolDetection) -> str | None:
    """Return the YAML list for the linter command, or ``None`` if unknown.

    Args:
        detection: The linter detection.

    Returns:
        A YAML inline list string, or ``None``.
    """
    if detection.name == "ruff":
        return "[python, -m, ruff, check, .]"
    if detection.name == "flake8":
        return "[python, -m, flake8, .]"
    if detection.name == "pylint":
        return "[python, -m, pylint, src]"
    return None


def _type_checker_command(detection: ToolDetection) -> str | None:
    """Return the YAML list for the type-checker command, or ``None`` if unknown.

    Args:
        detection: The type checker detection.

    Returns:
        A YAML inline list string, or ``None``.
    """
    if detection.name == "mypy":
        return "[python, -m, mypy, src]"
    if detection.name == "pyright":
        return "[python, -m, pyright]"
    return None


def _find_source_dirs(project_dir: Path) -> list[str]:
    """Detect likely Python source directories under *project_dir*.

    Args:
        project_dir: The project root.

    Returns:
        A sorted list of relative directory names (e.g. ``[\"src\"]``).
    """
    dirs: list[str] = []
    for candidate in ("src", project_dir.name):
        path = project_dir / candidate
        if path.is_dir() and any(path.rglob("*.py")):
            dirs.append(candidate)
    if not dirs and any(project_dir.rglob("*.py")):
        py_files = list(project_dir.rglob("*.py"))
        if py_files:
            dirs.append(".")
    return sorted(set(dirs)) if dirs else ["src"]


def _find_dependency_files(project_dir: Path) -> list[str]:
    """Detect dependency manifest files in *project_dir*.

    Args:
        project_dir: The project root.

    Returns:
        A list of relative file names or glob patterns.
    """
    patterns: list[str] = []
    for fname in ("pyproject.toml", "uv.lock", "requirements.txt", "Pipfile", "Pipfile.lock",
                  "poetry.lock", "Cargo.toml", "Cargo.lock", "package.json", "yarn.lock"):
        if (project_dir / fname).exists():
            patterns.append(fname)
    return patterns or ["pyproject.toml", "requirements*.txt"]


def _uncertain_comment_lines(detections: ProjectDetections) -> list[str]:
    """Collect YAML comment lines for uncertain or missing detections.

    Args:
        detections: The tooling detections.

    Returns:
        A list of comment lines (empty if all confident).
    """
    lines: list[str] = []
    for attr in ("test_framework", "linter", "type_checker", "coverage", "build_system", "ci_provider"):
        detection: ToolDetection = getattr(detections, attr)
        comment = detection.comment_line
        if comment:
            lines.append(f"# {attr}: {comment.lstrip('# ')}")
    if detections.git_branch:
        lines.append(f"# git branch: {detections.git_branch}")
    if detections.git_remote:
        lines.append(f"# git remote: {detections.git_remote}")
    return lines
