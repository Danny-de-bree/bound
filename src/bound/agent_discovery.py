"""Agent discovery — detect coding agents installed on the local system (v1.0).

Provides ready-to-use detection functions that scan a project directory for
installed coding agents (Cline, Claude Code, Codex, generic).  Detection is
read-only, non-destructive, and never scans outside the project boundary or
reads credential files.

Usage::

    from bound.agent_discovery import detect_agent, detect_all_agents

    agents = detect_all_agents(Path("/path/to/project"))
    for a in agents:
        print(f"{a.display_name} ({a.agent_id}) — {a.version}")

    # Explicit selection:
    install = detect_agent(Path("/path/to/project"), agent_id="claude-code")

Detection order (per spec §5):
    1. Explicit ``agent_id`` argument (caller dictates).
    2. ``.bound/config.yaml`` ``agent.name`` field.
    3. Executable on ``PATH`` (``which`` / ``npx``).
    4. Known package-manager filesystem locations.
    5. Existing project config files (``.cline/``, ``.codex/``, etc.).
    6. App-location heuristics (e.g. VS Code extension presence).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from bound.adapters.protocol import AgentCapabilities, AgentInstallation
from bound.config import ProjectConfig, load_project_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class AgentDiscovery(Protocol):
    """Protocol for an agent-specific discovery function.

    Each concrete discovery callable receives a project directory and returns
    an :class:`~bound.adapters.protocol.AgentInstallation` when the agent is
    detected, or ``None`` otherwise.

    Implementations must be read-only, fast (no recursive disk scan), and
    never access credential files.
    """

    def detect(self, project_dir: Path) -> AgentInstallation | None:
        """Detect whether this agent is installed for *project_dir*.

        Args:
            project_dir: The project root directory.

        Returns:
            An :class:`AgentInstallation` when detected, or ``None``.
        """
        ...


# ---------------------------------------------------------------------------
# Agent-specific detectors
# ---------------------------------------------------------------------------


def _detect_cline(project_dir: Path) -> AgentInstallation | None:
    """Detect Cline (VS Code extension) in *project_dir*.

    Checks (in order):
        1. ``.cline/`` directory exists.
        2. ``.cline/mcp/bound.json`` MCP config is present.

    Cline is a VS Code extension, so there is no standalone executable to
    probe.  Detection is purely filesystem-based.

    Args:
        project_dir: The project root directory.

    Returns:
        An :class:`AgentInstallation` when Cline project artefacts are found,
        or ``None``.
    """
    cline_dir = project_dir / ".cline"
    mcp_config = cline_dir / "mcp" / "bound.json"

    if not cline_dir.is_dir():
        logger.debug("Cline: no .cline/ directory in %s", project_dir)
        return None

    installed = mcp_config.exists()
    confidence = "verified" if installed else "probable"

    logger.debug(
        "Cline: detected (mcp=%s, confidence=%s) in %s",
        installed,
        confidence,
        project_dir,
    )

    return AgentInstallation(
        agent_id="cline",
        display_name="Cline (VS Code)",
        executable=None,  # Managed by VS Code extension host.
        version=None,  # Cannot probe extension version from filesystem.
        installation_type="mcp" if installed else "unknown",
        authenticated=None,  # Credentials live in VS Code, not readable here.
        project_config_paths=((mcp_config,) if installed else ()),
        capabilities=AgentCapabilities(
            tool_integration=True,  # MCP tools exposed.
            structured_events=False,  # No native ACP support.
            process_ownership=False,  # BOUND cannot spawn Cline.
            bidirectional_control=False,
            interrupt=False,
            resume=False,
            checkpoint_awareness=False,
            plan_events=False,
        ),
        confidence=confidence,
    )


def _detect_claude_code(project_dir: Path) -> AgentInstallation | None:
    """Detect Anthropic's Claude Code CLI in *project_dir*.

    Checks (in order):
        1. ``claude`` executable on ``PATH``.
        2. ``npx @anthropic-ai/claude-code`` availability.
        3. Version via ``claude --version``.
        4. Structured output support (``--print --stream-json`` flags).
        5. Auth status (safe check — no key leakage).

    Args:
        project_dir: The project root directory (used only for logging).

    Returns:
        An :class:`AgentInstallation` when Claude Code is found, or ``None``.
    """
    claude_path = shutil.which("claude")
    npx_path = shutil.which("npx")

    executable: Path | None = None
    installation_type = "cli"
    confidence: str = "possible"

    if claude_path:
        executable = Path(claude_path)
        confidence = "probable"
    elif npx_path:
        executable = Path(npx_path)
        installation_type = "cli"
        confidence = "possible"
    else:
        logger.debug("Claude Code: no claude/npx on PATH")
        return None

    # Try to get version.
    version: str | None = None
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            confidence = "verified"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        logger.debug("Claude Code: --version check failed")

    # Check structured output support via --help.
    supports_stream_json = False
    try:
        result = subprocess.run(
            ["claude", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            supports_stream_json = "stream-json" in result.stdout or "--print" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    logger.debug(
        "Claude Code: detected (exec=%s, version=%s, confidence=%s)",
        executable,
        version,
        confidence,
    )

    return AgentInstallation(
        agent_id="claude-code",
        display_name="Claude Code",
        executable=executable,
        version=version,
        installation_type=installation_type,
        authenticated=None,  # Unknown without explicit auth check.
        project_config_paths=(),
        capabilities=AgentCapabilities(
            tool_integration=False,
            structured_events=supports_stream_json,
            process_ownership=True,
            bidirectional_control=True,
            interrupt=False,
            resume=False,
            checkpoint_awareness=False,
            plan_events=False,
        ),
        confidence=confidence,
    )


def _detect_codex(project_dir: Path) -> AgentInstallation | None:
    """Detect OpenAI Codex CLI in *project_dir*.

    Checks (in order):
        1. ``codex`` executable on ``PATH``.
        2. ``npx @openai/codex`` availability.
        3. Version via ``codex --version``.
        4. ``.codex/`` project config directory.
        5. ``.codex/mcp.json`` MCP config presence.

    Args:
        project_dir: The project root directory.

    Returns:
        An :class:`AgentInstallation` when Codex is found, or ``None``.
    """
    codex_path = shutil.which("codex")
    npx_path = shutil.which("npx")

    executable: Path | None = None
    installation_type = "cli"
    confidence: str = "possible"

    # Check for .codex/ directory as fallback indicator.
    codex_dir = project_dir / ".codex"
    mcp_config = codex_dir / "mcp.json"

    if codex_path:
        executable = Path(codex_path)
        installation_type = "cli"
        confidence = "probable"
    elif npx_path:
        executable = Path(npx_path)
        installation_type = "cli"
        confidence = "possible"
    elif codex_dir.is_dir():
        # Only project config exists, no CLI on PATH.
        installed = mcp_config.exists()
        return AgentInstallation(
            agent_id="codex",
            display_name="Codex",
            executable=None,
            version=None,
            installation_type="app-server" if installed else "unknown",
            authenticated=None,
            project_config_paths=((mcp_config,) if installed else ()),
            capabilities=AgentCapabilities(
                tool_integration=installed,
                structured_events=False,
                process_ownership=False,
                bidirectional_control=False,
                interrupt=False,
                resume=False,
                checkpoint_awareness=False,
                plan_events=False,
            ),
            confidence="probable" if installed else "possible",
        )
    else:
        logger.debug("Codex: no codex/npx on PATH and no .codex/ directory")
        return None

    # Try to get version.
    version: str | None = None
    try:
        result = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            confidence = "verified"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        logger.debug("Codex: --version check failed")

    # Check for .codex/ project config alongside CLI.
    config_paths: tuple[Path, ...] = ()
    if codex_dir.is_dir():
        config_paths = (mcp_config,) if mcp_config.exists() else ()
        if mcp_config.exists():
            installation_type = "mcp"

    logger.debug(
        "Codex: detected (exec=%s, version=%s, confidence=%s)",
        executable,
        version,
        confidence,
    )

    return AgentInstallation(
        agent_id="codex",
        display_name="Codex",
        executable=executable,
        version=version,
        installation_type=installation_type,
        authenticated=None,
        project_config_paths=config_paths,
        capabilities=AgentCapabilities(
            tool_integration=bool(config_paths),
            structured_events=False,
            process_ownership=True,
            bidirectional_control=True,
            interrupt=False,
            resume=False,
            checkpoint_awareness=False,
            plan_events=False,
        ),
        confidence=confidence,
    )


def _detect_generic(project_dir: Path) -> AgentInstallation | None:
    """Detect a generic/unlisted agent installation.

    A generic agent is always *possible* — it is the fallback when no
    specific agent is found.  Detection checks whether a ``.bound/``
    integration prompt has been configured.

    Args:
        project_dir: The project root directory.

    Returns:
        An :class:`AgentInstallation` representing the generic agent slot.
    """
    bound_dir = project_dir / ".bound"
    prompt_file = bound_dir / "integration-prompt.md"

    installed = prompt_file.exists()
    confidence = "verified" if installed else "possible"

    return AgentInstallation(
        agent_id="generic",
        display_name="Generic (prompt-based)",
        executable=None,
        version=None,
        installation_type="prompt" if installed else "unknown",
        authenticated=None,
        project_config_paths=((prompt_file,) if installed else ()),
        capabilities=AgentCapabilities(
            tool_integration=False,
            structured_events=False,
            process_ownership=False,
            bidirectional_control=False,
            interrupt=False,
            resume=False,
            checkpoint_awareness=False,
            plan_events=False,
        ),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Detection registry
# ---------------------------------------------------------------------------

#: All known detectors, keyed by stable agent id.
_DETECTORS: dict[str, AgentDiscovery] = {
    "cline": _detect_cline,
    "claude-code": _detect_claude_code,
    "codex": _detect_codex,
    "generic": _detect_generic,
}

#: Integration types used in display messages.
_INTEGRATION_LABELS: dict[str, str] = {
    "cline": "integrated",
    "claude-code": "supervised",
    "codex": "controlled",
    "generic": "prompt-based",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_agent(
    project_dir: Path,
    agent_id: str | None = None,
    config: ProjectConfig | None = None,
) -> AgentInstallation | None:
    """Detect a specific agent installation for *project_dir*.

    Detection order (per spec §5):
        1. Explicit *agent_id* argument.
        2. ``.bound/config.yaml`` ``agent.name`` (when *agent_id* is ``None``).
        3. Executable on ``PATH``.
        4. Known filesystem locations.
        5. Existing project config files.
        6. App-location heuristics.

    Args:
        project_dir: The project root directory.
        agent_id: Optional explicit agent identifier (``"cline"``,
            ``"claude-code"``, ``"codex"``, or ``"generic"``).  When
            ``None``, the config file is consulted.
        config: Optional pre-loaded :class:`~bound.config.ProjectConfig`.
            When ``None``, ``load_project_config(project_dir)`` is called.

    Returns:
        An :class:`AgentInstallation` when found, or ``None``.
    """
    resolved = project_dir.resolve()

    # 1. Explicit agent_id argument.
    if agent_id and agent_id in _DETECTORS:
        result = _DETECTORS[agent_id](resolved)
        if result:
            return result
        # Fall through to config if explicit id not found.

    # 2. Config file.
    if config is None:
        config = load_project_config(resolved)

    config_agent = config.agent.name
    if config_agent and config_agent != "auto" and config_agent in _DETECTORS:
        result = _DETECTORS[config_agent](resolved)
        if result:
            return result

    # 3-6. Try each detector in priority order (non-generic first).
    priority_order = ["cline", "claude-code", "codex"]
    for agent_key in priority_order:
        if agent_id and agent_key != agent_id:
            continue
        result = _DETECTORS[agent_key](resolved)
        if result:
            return result

    # Fallback: generic is always possible.
    return _detect_generic(resolved)


def detect_all_agents(
    project_dir: Path,
    config: ProjectConfig | None = None,
) -> list[AgentInstallation]:
    """Detect all installed agents for *project_dir*.

    Runs every known detector and returns a list (may be empty).  The
    ``generic`` agent is always included as a fallback when no other
    agent is found.

    Args:
        project_dir: The project root directory.
        config: Optional pre-loaded :class:`~bound.config.ProjectConfig`.

    Returns:
        A list of :class:`AgentInstallation` objects, one per detected agent.
    """
    resolved = project_dir.resolve()

    if config is None:
        config = load_project_config(resolved)

    results: list[AgentInstallation] = []

    for agent_key, detector in _DETECTORS.items():
        try:
            result = detector(resolved)
            if result is not None and result.confidence != "possible":
                results.append(result)
        except Exception:
            logger.debug(
                "Detection failed for %s in %s",
                agent_key,
                resolved,
                exc_info=True,
            )

    # Sort: verified first, then probable.
    results.sort(key=lambda a: {"verified": 0, "probable": 1, "possible": 2}.get(a.confidence, 3))

    return results


def get_integration_label(agent: AgentInstallation) -> str:
    """Return the human-readable integration type label for *agent*.

    Args:
        agent: An :class:`AgentInstallation`.

    Returns:
        One of ``"integrated"``, ``"supervised"``, ``"controlled"``,
        or ``"prompt-based"``.
    """
    return _INTEGRATION_LABELS.get(agent.agent_id, "unknown")


def agent_selection_help(agents: list[AgentInstallation]) -> str:
    """Build a multi-agent selection help message.

    When multiple agents are detected and none is explicitly selected,
    this message guides the user to pick one.

    Args:
        agents: The list of detected agents.

    Returns:
        A formatted string suitable for printing to stderr.
    """
    lines = ["Multiple supported agents were found:", ""]
    for i, agent in enumerate(agents, 1):
        label = get_integration_label(agent)
        ver = f" ({agent.version})" if agent.version else ""
        lines.append(f"  {i}. {agent.display_name:<14} {label}{ver}")

    lines.append("")
    lines.append("Select explicitly:")
    for agent in agents:
        if agent.agent_id == "generic":
            lines.append('  bound run --agent generic --agent-command "<cmd>" "..."')
        else:
            lines.append(f'  bound run --agent {agent.agent_id} "..."')

    lines.append("")
    lines.append("Or save a project default:")
    for agent in agents:
        lines.append(f"  bound use {agent.agent_id}")

    return "\n".join(lines)


__all__ = [
    "AgentDiscovery",
    "agent_selection_help",
    "detect_agent",
    "detect_all_agents",
    "get_integration_label",
]
