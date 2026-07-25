"""Integration installer abstraction and ``bound setup`` orchestration.

Provides the :class:`IntegrationInstaller` Protocol, concrete installers for
each supported agent, typed request/response models, and the top-level
:func:`setup_project` function that orchestrates the full setup flow:

* detect project tooling (reuses :func:`bound.init_project.detect_tooling`)
* generate/update ``bound-policy.yaml``
* install the selected agent integration
* create ``.bound/`` directories
* validate the generated policy
* perform a deterministic local smoke evaluation
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from bound.init_project import detect_tooling, generate_policy
from bound.services import PolicyService, PolicyValidateRequest

logger = logging.getLogger(__name__)

#: Path to the policy file within a project.
POLICY_FILENAME = "bound-policy.yaml"

#: Directory where BOUND stores lineage, checkpoints, and integration artefacts.
BOUND_DIR = ".bound"

#: Integration prompt filename stored inside .bound/ (used by ``generic``).
INTEGRATION_PROMPT_FILENAME = "integration-prompt.md"

#: Source directory for agent-specific INSTALL_BOUND.md prompts.
INTEGRATIONS_SRC = Path(__file__).resolve().parent.parent.parent / "integrations"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChangeKind:
    """Kind of planned filesystem change."""

    CREATE: str = "create"
    MODIFY: str = "modify"
    SKIP: str = "skip"


class PlannedChange(BaseModel):
    """One planned (or executed) filesystem mutation.

    Attributes:
        path: Relative path from the project root.
        kind: What action will be/was performed.
        description: Human-readable summary of what changed.
        content_preview: Optional first ~120 chars of content for created files.
    """

    path: str
    kind: str
    description: str = ""
    content_preview: str | None = None


class InstallationResult(BaseModel):
    """Result returned by an installer after executing (or dry-running) its plan.

    Attributes:
        agent_id: The installer id (e.g. ``"codex"``).
        display_name: Human-readable agent name.
        changes: Ordered list of changes made or planned.
        warning: Optional non-fatal warning message.
    """

    agent_id: str
    display_name: str
    changes: list[PlannedChange] = Field(default_factory=list)
    warning: str | None = None


class SetupResult(BaseModel):
    """Top-level result from :func:`setup_project`.

    Attributes:
        project_dir: Resolved project directory.
        agent_id: The agent that was set up.
        policy_path: Path to the generated/updated policy file.
        policy_valid: Whether the generated policy passed validation.
        policy_warnings: Warnings from policy validation.
        installation: The :class:`InstallationResult` from the installer.
        next_commands: Suggested next commands to run.
    """

    project_dir: str
    agent_id: str
    policy_path: str
    policy_valid: bool = True
    policy_warnings: list[str] = Field(default_factory=list)
    installation: InstallationResult | None = None
    next_commands: list[str] = Field(default_factory=list)


class SetupError(Exception):
    """Raised when setup cannot proceed (e.g. project dir missing)."""


# ---------------------------------------------------------------------------
# IntegrationInstaller Protocol
# ---------------------------------------------------------------------------


class IntegrationInstaller(Protocol):
    """Protocol for an agent-specific integration installer.

    Every installer:

    * Has a stable ``id`` and human-readable ``display_name``.
    * Can ``detect`` whether an existing installation is present.
    * Can ``plan`` the filesystem changes it would make (without executing them).
    * Can ``install`` the integration, returning what was actually done.

    Implementations must be idempotent: running ``install`` twice must not
    duplicate configuration or corrupt existing files.
    """

    id: str
    display_name: str

    def detect(self, project_root: Path) -> bool:
        """Return ``True`` if an existing installation is detected."""
        ...

    def plan(self, project_root: Path) -> list[PlannedChange]:
        """Return the list of changes that ``install`` would make."""
        ...

    def install(self, project_root: Path) -> InstallationResult:
        """Execute the installation and return the result."""
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_integration_prompt(agent_id: str) -> str:
    """Read the INSTALL_BOUND.md prompt for *agent_id* from the integrations dir.

    Args:
        agent_id: The agent key (``generic``, ``codex``, ``claude-code``, ``cline``).

    Returns:
        The full markdown prompt text.

    Raises:
        FileNotFoundError: If the integration prompt file does not exist.
    """
    prompt_path = INTEGRATIONS_SRC / agent_id / "INSTALL_BOUND.md"
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Integration prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _ensure_bound_dirs(project_root: Path) -> list[PlannedChange]:
    """Create the ``.bound/`` directory and its essential subdirectories.

    Args:
        project_root: The project root directory.

    Returns:
        List of changes describing what was created.
    """
    changes: list[PlannedChange] = []
    bound_dir = project_root / BOUND_DIR

    for subdir in ("", "runs", "checkpoints"):
        d = bound_dir / subdir if subdir else bound_dir
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            label = f"{BOUND_DIR}/{subdir}" if subdir else BOUND_DIR
            changes.append(
                PlannedChange(
                    path=label,
                    kind=ChangeKind.CREATE,
                    description=f"Created directory {label}/",
                ),
            )
    return changes


def _plan_bound_dirs(project_root: Path) -> list[PlannedChange]:
    """Plan the .bound/ directory structure without creating anything.

    Args:
        project_root: The project root.

    Returns:
        A list of :class:`PlannedChange` entries.
    """
    changes: list[PlannedChange] = []
    bound_dir = project_root / BOUND_DIR

    for subdir in ("", "runs", "checkpoints"):
        d = bound_dir / subdir if subdir else bound_dir
        if not d.exists():
            label = f"{BOUND_DIR}/{subdir}" if subdir else BOUND_DIR
            changes.append(
                PlannedChange(
                    path=label,
                    kind=ChangeKind.CREATE,
                    description=f"Create directory {label}/",
                ),
            )
    return changes


# ---------------------------------------------------------------------------
# Concrete installers
# ---------------------------------------------------------------------------


class GenericPromptInstaller:
    """Installer for the ``generic`` agent.

    Writes the integration prompt to ``.bound/integration-prompt.md`` so any
    coding agent can read and follow it.
    """

    id = "generic"
    display_name = "Generic (prompt-based)"

    def detect(self, project_root: Path) -> bool:
        return (project_root / BOUND_DIR / INTEGRATION_PROMPT_FILENAME).is_file()

    def plan(self, project_root: Path) -> list[PlannedChange]:
        changes: list[PlannedChange] = []
        target = project_root / BOUND_DIR / INTEGRATION_PROMPT_FILENAME

        if not (project_root / BOUND_DIR).exists():
            changes.append(
                PlannedChange(
                    path=f"{BOUND_DIR}/",
                    kind=ChangeKind.CREATE,
                    description="Create .bound/ directory",
                ),
            )

        if target.exists():
            changes.append(
                PlannedChange(
                    path=f"{BOUND_DIR}/{INTEGRATION_PROMPT_FILENAME}",
                    kind=ChangeKind.MODIFY,
                    description="Overwrite existing integration prompt",
                ),
            )
        else:
            changes.append(
                PlannedChange(
                    path=f"{BOUND_DIR}/{INTEGRATION_PROMPT_FILENAME}",
                    kind=ChangeKind.CREATE,
                    description="Write integration prompt for any coding agent",
                ),
            )
        return changes

    def install(self, project_root: Path) -> InstallationResult:
        changes: list[PlannedChange] = []
        bound_dir = project_root / BOUND_DIR
        bound_dir.mkdir(parents=True, exist_ok=True)

        target = bound_dir / INTEGRATION_PROMPT_FILENAME
        prompt = _read_integration_prompt("generic")
        existed = target.exists()
        target.write_text(prompt, encoding="utf-8")

        changes.append(
            PlannedChange(
                path=f"{BOUND_DIR}/{INTEGRATION_PROMPT_FILENAME}",
                kind=ChangeKind.MODIFY if existed else ChangeKind.CREATE,
                description=(
                    "Updated integration prompt" if existed else "Wrote integration prompt"
                ),
                content_preview=prompt[:120],
            ),
        )

        return InstallationResult(
            agent_id=self.id,
            display_name=self.display_name,
            changes=changes,
        )


class CodexInstaller:
    """Installer for the Codex agent.

    Writes the integration prompt to ``.codex/instructions.md``.
    """

    id = "codex"
    display_name = "Codex"

    def detect(self, project_root: Path) -> bool:
        return (project_root / ".codex" / "instructions.md").is_file()

    def plan(self, project_root: Path) -> list[PlannedChange]:
        changes: list[PlannedChange] = []
        codex_dir = project_root / ".codex"
        target = codex_dir / "instructions.md"

        if not codex_dir.exists():
            changes.append(
                PlannedChange(
                    path=".codex/",
                    kind=ChangeKind.CREATE,
                    description="Create .codex/ directory",
                ),
            )

        if target.exists():
            changes.append(
                PlannedChange(
                    path=".codex/instructions.md",
                    kind=ChangeKind.MODIFY,
                    description="Overwrite existing Codex integration instructions",
                ),
            )
        else:
            changes.append(
                PlannedChange(
                    path=".codex/instructions.md",
                    kind=ChangeKind.CREATE,
                    description="Write Codex integration instructions",
                ),
            )
        return changes

    def install(self, project_root: Path) -> InstallationResult:
        changes: list[PlannedChange] = []
        codex_dir = project_root / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)

        target = codex_dir / "instructions.md"
        prompt = _read_integration_prompt("codex")
        existed = target.exists()
        target.write_text(prompt, encoding="utf-8")

        changes.append(
            PlannedChange(
                path=".codex/instructions.md",
                kind=ChangeKind.MODIFY if existed else ChangeKind.CREATE,
                description=(
                    "Updated Codex integration instructions"
                    if existed
                    else "Wrote Codex integration instructions"
                ),
                content_preview=prompt[:120],
            ),
        )

        return InstallationResult(
            agent_id=self.id,
            display_name=self.display_name,
            changes=changes,
        )


class ClaudeCodeInstaller:
    """Installer for Claude Code.

    Writes the integration prompt to ``CLAUDE.md`` in the project root.
    """

    id = "claude-code"
    display_name = "Claude Code"

    def detect(self, project_root: Path) -> bool:
        claude_md = project_root / "CLAUDE.md"
        if not claude_md.is_file():
            return False
        content = claude_md.read_text(encoding="utf-8")
        return "BOUND" in content and "bounded-utility" in content

    def plan(self, project_root: Path) -> list[PlannedChange]:
        changes: list[PlannedChange] = []
        target = project_root / "CLAUDE.md"

        if target.exists():
            changes.append(
                PlannedChange(
                    path="CLAUDE.md",
                    kind=ChangeKind.MODIFY,
                    description="Append or update BOUND section in CLAUDE.md",
                ),
            )
        else:
            changes.append(
                PlannedChange(
                    path="CLAUDE.md",
                    kind=ChangeKind.CREATE,
                    description="Create CLAUDE.md with BOUND integration",
                ),
            )
        return changes

    def install(self, project_root: Path) -> InstallationResult:
        changes: list[PlannedChange] = []
        target = project_root / "CLAUDE.md"
        prompt = _read_integration_prompt("claude-code")

        existed = target.exists()

        if existed:
            existing = target.read_text(encoding="utf-8")
            if "BOUND — a deterministic" in existing:
                new_content = re.sub(
                    r"(?s)You are Claude Code\. Your job to integrate \*\*BOUND\*\*.*$",
                    prompt.split("---\n", 1)[1] if "---\n" in prompt else prompt,
                    existing,
                )
                target.write_text(new_content, encoding="utf-8")
                changes.append(
                    PlannedChange(
                        path="CLAUDE.md",
                        kind=ChangeKind.MODIFY,
                        description="Updated BOUND section in CLAUDE.md",
                        content_preview=prompt[:120],
                    ),
                )
            else:
                new_content = existing.rstrip("\n") + "\n\n" + prompt + "\n"
                target.write_text(new_content, encoding="utf-8")
                changes.append(
                    PlannedChange(
                        path="CLAUDE.md",
                        kind=ChangeKind.MODIFY,
                        description="Appended BOUND integration to CLAUDE.md",
                        content_preview=prompt[:120],
                    ),
                )
        else:
            target.write_text(prompt, encoding="utf-8")
            changes.append(
                PlannedChange(
                    path="CLAUDE.md",
                    kind=ChangeKind.CREATE,
                    description="Created CLAUDE.md with BOUND integration",
                    content_preview=prompt[:120],
                ),
            )

        return InstallationResult(
            agent_id=self.id,
            display_name=self.display_name,
            changes=changes,
        )


class ClineInstaller:
    """Installer for Cline.

    Writes the integration prompt to ``.clinerules``.
    """

    id = "cline"
    display_name = "Cline"

    def detect(self, project_root: Path) -> bool:
        rules = project_root / ".clinerules"
        if not rules.is_file():
            return False
        content = rules.read_text(encoding="utf-8")
        return "BOUND" in content and "bounded-utility" in content

    def plan(self, project_root: Path) -> list[PlannedChange]:
        changes: list[PlannedChange] = []
        target = project_root / ".clinerules"

        if target.exists():
            changes.append(
                PlannedChange(
                    path=".clinerules",
                    kind=ChangeKind.MODIFY,
                    description="Append or update BOUND section in .clinerules",
                ),
            )
        else:
            changes.append(
                PlannedChange(
                    path=".clinerules",
                    kind=ChangeKind.CREATE,
                    description="Create .clinerules with BOUND integration",
                ),
            )
        return changes

    def install(self, project_root: Path) -> InstallationResult:
        changes: list[PlannedChange] = []
        target = project_root / ".clinerules"
        prompt = _read_integration_prompt("cline")

        existed = target.exists()

        if existed:
            existing = target.read_text(encoding="utf-8")
            if "BOUND — a deterministic" in existing:
                new_content = re.sub(
                    r"(?s)You are Cline\. Your job to integrate \*\*BOUND\*\*.*$",
                    prompt.split("---\n", 1)[1] if "---\n" in prompt else prompt,
                    existing,
                )
                target.write_text(new_content, encoding="utf-8")
                changes.append(
                    PlannedChange(
                        path=".clinerules",
                        kind=ChangeKind.MODIFY,
                        description="Updated BOUND section in .clinerules",
                        content_preview=prompt[:120],
                    ),
                )
            else:
                new_content = existing.rstrip("\n") + "\n\n" + prompt + "\n"
                target.write_text(new_content, encoding="utf-8")
                changes.append(
                    PlannedChange(
                        path=".clinerules",
                        kind=ChangeKind.MODIFY,
                        description="Appended BOUND integration to .clinerules",
                        content_preview=prompt[:120],
                    ),
                )
        else:
            target.write_text(prompt, encoding="utf-8")
            changes.append(
                PlannedChange(
                    path=".clinerules",
                    kind=ChangeKind.CREATE,
                    description="Created .clinerules with BOUND integration",
                    content_preview=prompt[:120],
                ),
            )

        return InstallationResult(
            agent_id=self.id,
            display_name=self.display_name,
            changes=changes,
        )


# ---------------------------------------------------------------------------
# Installer registry
# ---------------------------------------------------------------------------

_INSTALLERS: dict[str, IntegrationInstaller] = {
    "generic": GenericPromptInstaller(),
    "codex": CodexInstaller(),
    "claude-code": ClaudeCodeInstaller(),
    "cline": ClineInstaller(),
}


def get_installer(agent_id: str) -> IntegrationInstaller:
    """Look up an installer by agent id.

    Args:
        agent_id: One of ``generic``, ``codex``, ``claude-code``, ``cline``.

    Returns:
        The corresponding :class:`IntegrationInstaller`.

    Raises:
        KeyError: If *agent_id* is not recognised.
    """
    if agent_id not in _INSTALLERS:
        raise KeyError(f"Unknown agent '{agent_id}'. Choices: {', '.join(sorted(_INSTALLERS))}")
    return _INSTALLERS[agent_id]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def setup_project(
    project_dir: str | Path,
    agent_id: str = "generic",
    *,
    dry_run: bool = False,
    force: bool = False,
    verify: bool = False,
) -> SetupResult:
    """Orchestrate the full ``bound setup`` flow.

    Args:
        project_dir: Path to the project root.
        agent_id: Agent to install integration for.
        dry_run: If ``True``, report planned changes without writing anything.
        force: If ``True``, overwrite an existing policy without prompting.
        verify: If ``True``, run the smoke evaluation (validates policy).

    Returns:
        A :class:`SetupResult` describing everything that happened.

    Raises:
        SetupError: If the project directory does not exist.
        FileNotFoundError: If the integration prompt source is missing.
    """
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise SetupError(f"Project directory not found: {root}")

    installer = get_installer(agent_id)
    next_commands: list[str] = []
    warnings: list[str] = []

    # 1. Detect tooling ---------------------------------------------------
    detections = detect_tooling(root)

    # 2. Generate policy --------------------------------------------------
    policy_path = root / POLICY_FILENAME
    policy_existed = policy_path.exists()

    if policy_existed and not force:
        warnings.append(
            f"{POLICY_FILENAME} already exists. "
            "Use --force to overwrite, or review manually before re-running.",
        )
    else:
        yaml_content = generate_policy(detections)
        if not dry_run:
            policy_path.write_text(yaml_content, encoding="utf-8")

    # 3. Plan / execute integration installation --------------------------
    if dry_run:
        planned_changes = installer.plan(root)
        bound_changes = _plan_bound_dirs(root)
        installation = InstallationResult(
            agent_id=installer.id,
            display_name=installer.display_name,
            changes=planned_changes + bound_changes,
        )
    else:
        _ensure_bound_dirs(root)
        installation = installer.install(root)

    # 4. Validate policy --------------------------------------------------
    policy_valid = True
    if not dry_run and (not policy_existed or force):
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(policy_path.read_text(encoding="utf-8"))
            tmp_path = tmp.name
        try:
            response = PolicyService.validate(PolicyValidateRequest(path=tmp_path))
            policy_valid = response.valid
            if response.warnings:
                warnings.extend(response.warnings)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # 5. Smoke evaluation (only with --verify) ----------------------------
    if verify and not dry_run and policy_path.exists():
        try:
            response = PolicyService.validate(PolicyValidateRequest(path=str(policy_path)))
            if not response.valid:
                warnings.append(
                    "Smoke evaluation: policy validation failed — " + "; ".join(response.errors),
                )
            else:
                p = response.policy
                warnings.append(
                    "Smoke evaluation: policy validated successfully"
                    f" (hash={p.hash if p else '?'})",
                )
        except Exception as exc:
            warnings.append(f"Smoke evaluation error: {exc}")

    # 6. Next commands ----------------------------------------------------
    if agent_id == "codex":
        next_commands.append('codex "Implement the requested change"')
    elif agent_id == "claude-code":
        next_commands.append('claude "Implement the requested change"')
    elif agent_id == "cline":
        next_commands.append("Open Cline and start a new task")
    else:
        next_commands.append("Review .bound/integration-prompt.md and paste into your coding agent")
    next_commands.append("bound ui --open")
    next_commands.append("bound policy validate bound-policy.yaml")

    return SetupResult(
        project_dir=str(root),
        agent_id=agent_id,
        policy_path=str(policy_path),
        policy_valid=policy_valid,
        policy_warnings=warnings,
        installation=installation,
        next_commands=next_commands,
    )
