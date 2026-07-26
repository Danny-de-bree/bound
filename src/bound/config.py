"""Project configuration — ``.bound/config.yaml`` loader (v1.0).

Provides the Pydantic models and loader functions for the per-project
``.bound/config.yaml`` file.  When no configuration file exists every
call returns safe defaults so that existing workflows (no config file)
continue to work unchanged.

Usage::

    from bound.config import load_project_config, find_project_root

    root = find_project_root()
    cfg = load_project_config(root)  # returns defaults when absent
    print(cfg.agent.name)             # "auto"
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Per-project agent selection in ``.bound/config.yaml``.

    Attributes:
        name: Agent identifier (``"auto"``, ``"cline"``, ``"claude-code"``,
            ``"codex"``, or ``"generic"``).
        executable: Path to the agent binary, or ``"auto"`` to detect.
        integration: How BOUND communicates — ``"mcp"``, ``"subprocess"``,
            or ``"app-server"``.
        command: Explicit command list for generic/unlisted agents.
            Overrides ``executable`` when set.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="auto", min_length=1)
    executable: str = Field(default="auto", min_length=1)
    integration: str = Field(default="auto", min_length=1)
    command: list[str] | None = None


class PlanConfig(BaseModel):
    """Plan configuration in ``.bound/config.yaml``.

    Attributes:
        path: Path to the plan file relative to the project root.
        required: When ``True``, BOUND refuses to run when the plan is missing.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="plan.md", min_length=1)
    required: bool = False


class PolicyConfig(BaseModel):
    """Policy configuration in ``.bound/config.yaml``.

    Attributes:
        path: Path to the policy file relative to the project root.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="bound-policy.yaml", min_length=1)


class WorkspaceConfig(BaseModel):
    """Workspace isolation configuration in ``.bound/config.yaml``.

    Attributes:
        mode: Isolation strategy — ``"auto"``, ``"worktree"``, or ``"inplace"``.
    """

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="auto", min_length=1)


class ProjectConfig(BaseModel):
    """Root project configuration loaded from ``.bound/config.yaml``.

    Every field has a sensible default so that projects without a config
    file work exactly like today — the config file is purely additive.

    Attributes:
        project_root: The directory containing ``.bound/``.
        agent: :class:`AgentConfig` for agent selection.
        plan: :class:`PlanConfig` for plan file location.
        policy: :class:`PolicyConfig` for policy file location.
        workspace: :class:`WorkspaceConfig` for workspace isolation.
    """

    model_config = ConfigDict(extra="forbid")

    project_root: str = Field(default=".", min_length=1)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    plan: PlanConfig = Field(default_factory=PlanConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)


# ---------------------------------------------------------------------------
# Config file discovery
# ---------------------------------------------------------------------------

CONFIG_FILE_NAME: str = "config.yaml"
BOUND_DIR: str = ".bound"


def find_project_root(start_dir: Path | None = None) -> Path:
    """Walk up from *start_dir* looking for a ``.bound/`` or ``.git/`` directory.

    The search prefers ``.bound/`` (a BOUND-managed project) over ``.git/``
    (a plain Git repository), but falls back to ``.git/`` so that the
    command can be used in any repository.

    Args:
        start_dir: Directory to begin the search from.  Defaults to the
            current working directory.

    Returns:
        The absolute path to the first parent that contains ``.bound/`` or
        ``.git/``, or the current working directory when neither is found.
    """
    current = (start_dir or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / BOUND_DIR).is_dir():
            logger.debug("Found project root via %s at %s", BOUND_DIR, directory)
            return directory
    for directory in (current, *current.parents):
        if (directory / ".git").is_dir():
            logger.debug("Found project root via .git at %s", directory)
            return directory
    logger.debug("No .bound/ or .git/ found; using %s as project root", current)
    return current


def _config_path(project_dir: Path) -> Path:
    """Return the expected path to ``.bound/config.yaml``.

    Args:
        project_dir: The project root directory.

    Returns:
        ``<project_dir>/.bound/config.yaml``
    """
    return project_dir / BOUND_DIR / CONFIG_FILE_NAME


def load_project_config(project_dir: Path | None = None) -> ProjectConfig:
    """Load the project configuration from ``.bound/config.yaml``.

    Always succeeds — returns a fully-default :class:`ProjectConfig` when
    the config file does not exist or cannot be parsed.  This preserves
    backwards compatibility: projects without a config file behave exactly
    as they did in v0.9.x.

    Args:
        project_dir: Project root directory.  Defaults to the result of
            :func:`find_project_root`.

    Returns:
        A :class:`ProjectConfig` populated from the file, or defaults on
        any error.
    """
    root = (project_dir or find_project_root()).resolve()
    config_file = _config_path(root)

    if not config_file.is_file():
        logger.debug("No config file at %s; using defaults", config_file)
        return ProjectConfig(project_root=str(root))

    try:
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse %s: %s; using defaults", config_file, exc)
        return ProjectConfig(project_root=str(root))

    if not isinstance(raw, dict):
        logger.warning(
            "Config file %s is not a mapping (got %s); using defaults",
            config_file,
            type(raw).__name__,
        )
        return ProjectConfig(project_root=str(root))

    # Remove any credential-like keys before validation.
    _scrub_credentials(raw)

    try:
        return ProjectConfig(project_root=str(root), **raw)
    except Exception as exc:
        logger.warning(
            "Invalid config in %s: %s; using defaults",
            config_file,
            exc,
        )
        return ProjectConfig(project_root=str(root))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREDENTIAL_KEYS: frozenset[str] = frozenset({
    "api_key",
    "api_secret",
    "token",
    "password",
    "secret",
    "credential",
    "auth_token",
    "access_key",
    "private_key",
})


def _scrub_credentials(data: dict) -> None:
    """Remove credential-like keys from *data* in-place.

    Recurses into nested dicts.  Lists are not recursed into (config
    values are expected to be scalar or mapping).

    Args:
        data: The parsed YAML dict to scrub.
    """
    keys_to_remove: list[str] = []
    for key, value in data.items():
        lower = key.lower()
        if any(sensitive in lower for sensitive in _CREDENTIAL_KEYS):
            logger.warning(
                "Removing credential-like key %r from config; "
                "never store secrets in .bound/config.yaml",
                key,
            )
            keys_to_remove.append(key)
        elif isinstance(value, dict):
            _scrub_credentials(value)
    for key in keys_to_remove:
        del data[key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AgentConfig",
    "PlanConfig",
    "PolicyConfig",
    "ProjectConfig",
    "WorkspaceConfig",
    "find_project_root",
    "load_project_config",
]
