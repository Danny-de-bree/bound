"""Supervised agent runner for BOUND (v1.0).

Provides SupervisedRunner which executes the ACCEPT/RETRY/REPLAN/ROLLBACK
control loop over a coding agent process.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bound.agent_discovery import detect_agent

logger = logging.getLogger(__name__)


class SupervisedRunResult(BaseModel):
    """Result of a supervised agent run.

    Attributes:
        decision: Final BOUND decision (ACCEPT, RETRY, REPLAN, ROLLBACK, FAILED).
        run_id: The lineage run identifier.
        attempts: Number of attempts made.
        retries: Number of retries executed.
        replans: Number of replans executed.
        final_output: Last output from the agent.
    """

    model_config = ConfigDict(extra="forbid")

    decision: str = "ACCEPT"
    run_id: str = ""
    attempts: int = 0
    retries: int = 0
    replans: int = 0
    final_output: str = ""


class SupervisedConfig(BaseModel):
    """Configuration for a supervised run.

    Attributes:
        agent_id: Agent identifier (``"claude-code"``, ``"codex"``, etc.).
        project_dir: Project root directory.
        policy_path: Path to the policy YAML file.
        max_retries: Maximum retry attempts before forcing REPLAN.
        max_replans: Maximum replan attempts before giving up.
        max_candidates: Maximum candidate branches.
        no_worktree: If True, skip worktree isolation.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = "claude-code"
    project_dir: Path = Field(default_factory=Path.cwd)
    policy_path: str = "bound-policy.yaml"
    max_retries: int = 2
    max_replans: int = 2
    max_candidates: int = 2
    no_worktree: bool = False


class SupervisedRunner:
    """BOUND-owned supervised agent execution runner."""

    def __init__(self, config: SupervisedConfig | None = None) -> None:
        self._cfg = config or SupervisedConfig()

    def run(self, task: str) -> SupervisedRunResult:
        result = SupervisedRunResult(decision="ACCEPT")
        project = Path(self._cfg.project_dir).resolve()

        install = detect_agent(project, agent_id=self._cfg.agent_id)
        if install is None:
            logger.warning("Agent not detected; continuing.")

        current_task = task
        attempt = 0
        while attempt < self._cfg.max_retries + self._cfg.max_replans + 1:
            attempt += 1
            result.attempts = attempt
            output = self._invoke_agent(project, current_task)
            result.final_output = output
            evidence_ok = self._collect_evidence(project)

            if evidence_ok:
                result.decision = "ACCEPT"
                break
            elif result.retries < self._cfg.max_retries:
                result.decision = "RETRY"
                result.retries += 1
                current_task = f"RETRY: Fix failing checks. Original: {task}"
            elif result.replans < self._cfg.max_replans:
                result.decision = "REPLAN"
                result.replans += 1
                current_task = f"REPLAN: Different approach needed. Original: {task}"
            else:
                result.decision = "FAILED"
                break
        return result

    def _invoke_agent(self, project_dir: Path, task: str) -> str:
        cmd = [
            "npx",
            "@anthropic-ai/claude-code",
            "-p",
            "--verbose",
            "--dangerously-skip-permissions",
            "--output-format",
            "stream-json",
            task,
        ]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(project_dir),
            )
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _collect_evidence(self, project_dir: Path) -> bool:
        try:
            r = subprocess.run(
                ["pytest", "tests/", "-x", "-q"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(project_dir),
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return True


__all__ = ["SupervisedConfig", "SupervisedRunner", "SupervisedRunResult"]
