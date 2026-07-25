"""Diagnostic checks for ``bound doctor`` (v0.8.1).

Provides the core logic for :command:`bound doctor` — a read-only project
health check that reports on BOUND version, Python runtime, policy presence
and validity, configured collectors, Git state, checkpoint support,
integration installation, writable lineage directories, and stale or
incompatible configuration.

All checks are implemented as pure functions in :func:`run_doctor` and
return a :class:`DoctorReport` containing zero or more :class:`DoctorCheck`
items.  The module **never** mutates the project and never calls
``sys.exit`` or ``print`` — the CLI adapter handles I/O and exit codes.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bound.policy_schema import BoundPolicyConfig, load_policy_yaml
from bound.services import PolicyService, PolicyValidateRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------

CheckStatus = Literal["PASS", "WARNING", "ERROR"]


class DoctorCheck(BaseModel):
    """A single diagnostic check result.

    Attributes:
        status: ``PASS``, ``WARNING``, or ``ERROR``.
        name: Short, machine-readable check id (e.g. ``"bound_version"``).
        message: One-line human-readable summary.
        detail: Optional additional detail (multi-line allowed).
    """

    model_config = ConfigDict(frozen=True)

    status: CheckStatus
    name: str = Field(min_length=1)
    message: str = Field(min_length=1)
    detail: str | None = None


class DoctorReport(BaseModel):
    """Aggregated diagnostic report returned by :func:`run_doctor`.

    Attributes:
        checks: Ordered list of :class:`DoctorCheck` items, one per check.
        project_dir: The absolute path that was scanned.
    """

    model_config = ConfigDict(frozen=True)

    checks: list[DoctorCheck] = Field(default_factory=list)
    project_dir: Path

    # ------------------------------------------------------------------
    # Derived convenience properties
    # ------------------------------------------------------------------

    @property
    def has_errors(self) -> bool:
        """``True`` when at least one check has status ``ERROR``."""
        return any(c.status == "ERROR" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        """``True`` when at least one check has status ``WARNING``."""
        return any(c.status == "WARNING" for c in self.checks)

    @property
    def error_count(self) -> int:
        """Number of checks with status ``ERROR``."""
        return sum(1 for c in self.checks if c.status == "ERROR")

    @property
    def warning_count(self) -> int:
        """Number of checks with status ``WARNING``."""
        return sum(1 for c in self.checks if c.status == "WARNING")

    @property
    def pass_count(self) -> int:
        """Number of checks with status ``PASS``."""
        return sum(1 for c in self.checks if c.status == "PASS")

    @property
    def recommended_exit_code(self) -> int:
        """CLI exit code derived from the report: 0=clean, 1=warnings, 2=errors."""
        if self.has_errors:
            return 2
        if self.has_warnings:
            return 1
        # ---------------------------------------------------------------------------


# Private helpers
# ---------------------------------------------------------------------------


def _bound_version() -> str:
    """Return the BOUND package version string.

    Tries ``bound.__version__`` first, then falls back to
    ``importlib.metadata.version("bound-policy")``.
    """
    try:
        from bound import __version__  # type: ignore[attr-defined]

        return __version__
    except ImportError:
        pass
    try:
        from importlib.metadata import version

        return version("bound-policy")
    except Exception:
        return "unknown"


def _python_version() -> str:
    """Return the Python runtime version string."""
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _git_branch(cwd: Path) -> str | None:
    """Return the current Git branch name, or ``None``."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _git_is_clean(cwd: Path) -> bool | None:
    """Return ``True`` for a clean working tree, ``False`` for dirty, ``None`` if unknown."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() == ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _git_available() -> bool:
    """Return ``True`` when ``git`` is on ``$PATH``."""
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _collector_names(policy: BoundPolicyConfig) -> list[str]:
    """Return the sorted list of collector names declared in the policy."""
    return sorted(policy.collectors)


def _collector_commands(policy: BoundPolicyConfig) -> dict[str, list[str]]:
    """Return ``{collector_name: argv}`` for every collector that has a command."""
    result: dict[str, list[str]] = {}
    for name, cfg in policy.collectors.items():
        if cfg.command:
            result[name] = list(cfg.command)
    return result


def _command_exists(cmd: str) -> bool:
    """Check whether an executable name is on ``$PATH`` (via ``which``)."""
    try:
        proc = subprocess.run(
            ["which", cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _integrations_dir() -> Path | None:
    """Return the ``integrations/`` directory path if it exists, else ``None``.

    We locate it relative to *this module's* file, so the check works
    regardless of where ``bound`` is installed.
    """
    candidate = Path(__file__).resolve().parent.parent / "integrations"
    if candidate.is_dir():
        return candidate
    return None


def _integration_names(integrations_dir: Path) -> list[str]:
    """Return sorted names of installed integrations (subdirectory names).

    Directories that begin with ``__`` or ``.`` are skipped.
    """
    names: list[str] = []
    for entry in sorted(integrations_dir.iterdir()):
        if (
            entry.is_dir()
            and not entry.name.startswith(".")
            and not entry.name.startswith("__")
            and (entry / "INSTALL_BOUND.md").exists()
        ):
            names.append(entry.name)
    return names


# ---------------------------------------------------------------------------
# Core diagnostic function
# ---------------------------------------------------------------------------


def run_doctor(project_dir: Path | None = None) -> DoctorReport:
    """Run all diagnostic checks against *project_dir* and return a report.

    This is the single public entry-point for the diagnostic engine. Every
    check is a pure read-only observation — **nothing is mutated**.

    Args:
        project_dir: The project directory to scan. Defaults to ``Path.cwd()``.

    Returns:
        A :class:`DoctorReport` containing every check result.
    """
    cwd = Path(project_dir).resolve() if project_dir is not None else Path.cwd().resolve()
    checks: list[DoctorCheck] = []

    # -- 1. BOUND version ----------------------------------------------------
    bv = _bound_version()
    if bv == "unknown":
        checks.append(
            DoctorCheck(
                status="WARNING",
                name="bound_version",
                message="Cannot determine BOUND version.",
                detail="The bound package may not be correctly installed.",
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                status="PASS",
                name="bound_version",
                message=f"BOUND version {bv}",
            ),
        )

    # -- 2. Python version ---------------------------------------------------
    pv = _python_version()
    major, minor, *_ = sys.version_info
    if (major, minor) < (3, 12):
        checks.append(
            DoctorCheck(
                status="ERROR",
                name="python_version",
                message=f"Python {pv} — requires >= 3.12",
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                status="PASS",
                name="python_version",
                message=f"Python {pv}",
            ),
        )

    # -- 3. Policy presence --------------------------------------------------
    policy_path = cwd / "bound-policy.yaml"
    if not policy_path.exists():
        checks.append(
            DoctorCheck(
                status="ERROR",
                name="policy_presence",
                message="bound-policy.yaml not found",
                detail=f"Expected at {policy_path}. Run `bound init` to generate one.",
            ),
        )
        # Skip policy-dependent checks when the file is missing.
        return DoctorReport(checks=checks, project_dir=cwd)

    # -- 4. Policy validity --------------------------------------------------
    policy: BoundPolicyConfig | None = None
    try:
        policy = load_policy_yaml(str(policy_path))
    except Exception as exc:
        checks.append(
            DoctorCheck(
                status="ERROR",
                name="policy_validity",
                message="bound-policy.yaml failed to load",
                detail=str(exc),
            ),
        )
        return DoctorReport(checks=checks, project_dir=cwd)

    # Re-validate through PolicyService to get warnings
    response = PolicyService.validate(PolicyValidateRequest(path=str(policy_path)))
    if response.valid:
        detail_parts: list[str] = []
        if response.policy:
            detail_parts.append(f"id={response.policy.id}")
            detail_parts.append(f"version={response.policy.version}")
            detail_parts.append(f"hash={response.policy.hash}")
        if response.warnings:
            checks.append(
                DoctorCheck(
                    status="WARNING",
                    name="policy_validity",
                    message="Policy is valid but has semantic warnings",
                    detail="\n".join(response.warnings),
                ),
            )
        else:
            checks.append(
                DoctorCheck(
                    status="PASS",
                    name="policy_validity",
                    message="Policy is valid",
                    detail="\n".join(detail_parts) if detail_parts else None,
                ),
            )
    else:
        checks.append(
            DoctorCheck(
                status="ERROR",
                name="policy_validity",
                message="Policy validation failed",
                detail="\n".join(response.errors),
            ),
        )
        return DoctorReport(checks=checks, project_dir=cwd)
    # -- 5. Configured collectors -------------------------------------------
    collector_ids = _collector_names(policy)
    if not collector_ids:
        checks.append(
            DoctorCheck(
                status="WARNING",
                name="configured_collectors",
                message="No collectors configured",
                detail="Without collectors, BOUND cannot independently verify evidence.",
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                status="PASS",
                name="configured_collectors",
                message=f"{len(collector_ids)} collector(s) configured",
                detail=", ".join(collector_ids),
            ),
        )

    # -- 6. Collector command availability ----------------------------------
    commands = _collector_commands(policy)
    if commands:
        unavailable: list[str] = []
        for name, argv in commands.items():
            exe = argv[0]
            if not _command_exists(exe):
                unavailable.append(f"{name}: {exe!r} not found on $PATH")
        if unavailable:
            checks.append(
                DoctorCheck(
                    status="WARNING",
                    name="collector_command_availability",
                    message=f"{len(unavailable)} collector command(s) unavailable",
                    detail="\n".join(unavailable),
                ),
            )
        else:
            checks.append(
                DoctorCheck(
                    status="PASS",
                    name="collector_command_availability",
                    message="All collector commands available",
                ),
            )
    else:
        checks.append(
            DoctorCheck(
                status="PASS",
                name="collector_command_availability",
                message="No command-based collectors to verify",
            ),
        )

    # -- 7. Git repository state --------------------------------------------
    if not _git_available():
        checks.append(
            DoctorCheck(
                status="WARNING",
                name="git_state",
                message="git is not available on $PATH",
                detail="Checkpoint and rollback features require git.",
            ),
        )
    else:
        branch = _git_branch(cwd)
        clean = _git_is_clean(cwd)
        if branch is None:
            checks.append(
                DoctorCheck(
                    status="WARNING",
                    name="git_state",
                    message="Not a git repository (or git command failed)",
                    detail="Run `git init` if you want checkpoint support.",
                ),
            )
        else:
            status_line = f"branch={branch}"
            if clean is True:
                status_line += ", clean"
                checks.append(
                    DoctorCheck(
                        status="PASS",
                        name="git_state",
                        message=status_line,
                    ),
                )
            elif clean is False:
                status_line += ", dirty"
                checks.append(
                    DoctorCheck(
                        status="WARNING",
                        name="git_state",
                        message=status_line,
                        detail="Uncommitted changes may prevent clean checkpoints.",
                    ),
                )
            else:
                checks.append(
                    DoctorCheck(
                        status="WARNING",
                        name="git_state",
                        message=f"branch={branch}, unknown clean status",
                    ),
                )

    # -- 8. Checkpoint support ----------------------------------------------
    try:
        import bound.checkpoint  # noqa: F401

        checks.append(
            DoctorCheck(
                status="PASS",
                name="checkpoint_support",
                message="Checkpoint module available",
            ),
        )
    except ImportError as exc:
        checks.append(
            DoctorCheck(
                status="ERROR",
                name="checkpoint_support",
                message="Checkpoint module unavailable",
                detail=str(exc),
            ),
        )

    # -- 9. Integration installation status ---------------------------------
    integrations_root = _integrations_dir()
    if integrations_root is None:
        checks.append(
            DoctorCheck(
                status="WARNING",
                name="integration_status",
                message="Integrations directory not found",
                detail="Integration prompts may not be available.",
            ),
        )
    else:
        names = _integration_names(integrations_root)
        if not names:
            checks.append(
                DoctorCheck(
                    status="WARNING",
                    name="integration_status",
                    message="No integration prompts found",
                    detail=f"Checked {integrations_root}",
                ),
            )
        else:
            checks.append(
                DoctorCheck(
                    status="PASS",
                    name="integration_status",
                    message=f"{len(names)} integration(s) available",
                    detail=", ".join(names),
                ),
            )

    # -- 10. Writable lineage directory -------------------------------------
    lineage_dir = cwd / ".bound" / "lineage"
    if lineage_dir.exists():
        if os.access(str(lineage_dir), os.W_OK):
            checks.append(
                DoctorCheck(
                    status="PASS",
                    name="lineage_directory",
                    message=f"Lineage directory writable: {lineage_dir}",
                ),
            )
        else:
            checks.append(
                DoctorCheck(
                    status="ERROR",
                    name="lineage_directory",
                    message=f"Lineage directory not writable: {lineage_dir}",
                    detail="Check file permissions.",
                ),
            )
    else:
        parent = lineage_dir.parent
        if parent.exists() and os.access(str(parent), os.W_OK):
            checks.append(
                DoctorCheck(
                    status="PASS",
                    name="lineage_directory",
                    message=(
                        "Lineage parent writable "
                        f"(directory will be created on first run): {parent}"
                    ),
                ),
            )
        else:
            checks.append(
                DoctorCheck(
                    status="WARNING",
                    name="lineage_directory",
                    message=f"Lineage directory does not exist: {lineage_dir}",
                    detail="Will be created on first `bound run start`.",
                ),
            )

    # -- 11. Stale or incompatible configuration ----------------------------
    stale_details: list[str] = []

    expected_schema = "1.0"
    if policy.schema_version != expected_schema:
        stale_details.append(
            f"Policy schema_version is {policy.schema_version!r}; "
            f"expected {expected_schema!r}. "
            "Your policy file may need updating.",
        )

    if response.warnings:
        stale_details.extend(response.warnings)

    if stale_details:
        checks.append(
            DoctorCheck(
                status="WARNING",
                name="stale_configuration",
                message="Potential configuration issues detected",
                detail="\n".join(stale_details),
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                status="PASS",
                name="stale_configuration",
                message="No stale or incompatible configuration detected",
            ),
        )

    return DoctorReport(checks=checks, project_dir=cwd)
