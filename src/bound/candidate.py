"""Candidate abstraction for BOUND (v0.9.0).

Each candidate owns an isolated git worktree, a lineage run context,
evidence lists, decision history, and checkpoints.  The candidate is the
unit of execution: an agent executes inside the worktree, and BOUND
observes, evaluates, and gates every step.

Usage::

    from bound.candidate import Candidate

    with Candidate(
        project_root="/path/to/repo",
        task="Implement input validation",
        base_commit="HEAD",
    ) as candidate:
        evidence = candidate.collect_evidence(contract)
        result = candidate.evaluate(contract, evidence)
        cp = candidate.capture_checkpoint("step-001")

The context manager guarantees cleanup: on exit the git worktree is
removed and any stale state is pruned.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from bound.checkpoint import Checkpoint as CheckpointModel
from bound.checkpoint import capture_checkpoint as _capture_checkpoint_impl
from bound.checkpoint import (
    load_checkpoint,
    restore_checkpoint_files,
    save_checkpoint,
)
from bound.lineage import generate_run_id

if TYPE_CHECKING:
    from bound.contracts import StepContract
    from bound.models import BoundCriteria, EvaluationResult

logger = logging.getLogger(__name__)

#: Default directory under the project root where worktrees are created.
DEFAULT_WORKTREES_DIR: str = ".bound/worktrees"


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _git_require(*args: str, cwd: Path | None = None) -> str:
    """Run a git command and return stdout, raising on failure."""
    proc = _git(*args, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout.strip()


class CandidateDecision(BaseModel):
    """A single decision record logged by the candidate.

    Attributes:
        step_id: The step identifier the decision applies to.
        decision: The BOUND decision (e.g. ``ACCEPT``, ``RETRY``).
        score: The computed evaluation score.
        threshold: The threshold used for the decision.
        reason_code: The reason code from the evaluation.
        timestamp: ISO-8601 UTC timestamp of the decision.
        evaluation_id: Optional lineage evaluation id.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    score: float
    threshold: float
    reason_code: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    evaluation_id: str | None = None


def _make_candidate_id(run_id: str) -> str:
    """Derive a short, stable candidate id from a run id."""
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    return f"cand_{digest}"


class Candidate:
    """A single execution attempt with an isolated git worktree.

    A candidate owns a dedicated git worktree under
    ``<project_root>/.bound/worktrees/<candidate_id>/``, isolating every
    execution from other candidates and from the main working tree.  The
    worktree is created on ``__enter__`` and removed on ``__exit__``.

    Attributes:
        workspace: The absolute path to the git worktree directory.
        run_id: The lineage run identifier for this candidate.
        candidate_id: A unique, stable identifier for this candidate.
        task: The task description.
        project_root: The main repository root.
        evidence: List of collected
            :class:`~bound.evidence.ExecutionEvidence`.
        decisions: List of :class:`CandidateDecision` records.
        checkpoints: Dict of checkpoint_id ->
            :class:`~bound.checkpoint.Checkpoint`.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        task: str,
        base_commit: str = "HEAD",
        worktrees_dir: str | Path | None = None,
        run_id: str | None = None,
    ) -> None:
        """Create a candidate (not yet active — use as context manager).

        The git worktree is **not** created until :meth:`__enter__` is
        called.  Use the context-manager form
        (``with Candidate(...) as c:``) to guarantee cleanup.

        Args:
            project_root: Path to the main git repository.
            task: Human-readable task description for this candidate.
            base_commit: The git ref or commit SHA to base the worktree on.
                Defaults to ``HEAD``.
            worktrees_dir: Optional custom directory for worktrees.
                Defaults to ``<project_root>/.bound/worktrees/``.
            run_id: Optional explicit lineage ``run_id``.  When omitted, a
                new id is generated via
                :func:`~bound.lineage.generate_run_id`.

        Raises:
            FileNotFoundError: If ``project_root`` does not exist or is not
                a git repository.
        """
        self._project_root = Path(project_root).resolve()
        if not self._project_root.is_dir():
            raise FileNotFoundError(
                f"Project root not found: {self._project_root}"
            )
        if not (self._project_root / ".git").exists():
            raise FileNotFoundError(
                f"Project root is not a git repository: {self._project_root}"
            )

        self._task = task
        self._base_commit = base_commit
        self._worktrees_root = (
            Path(worktrees_dir).resolve()
            if worktrees_dir is not None
            else self._project_root / DEFAULT_WORKTREES_DIR
        )

        self._run_id: str = run_id or generate_run_id(
            task=task,
            started_at=datetime.now(UTC),
        )
        self._candidate_id: str = _make_candidate_id(self._run_id)
        self._workspace: Path | None = None
        self._active: bool = False

        # Accumulated state
        self._evidence: list[Any] = []
        self._decisions: list[CandidateDecision] = []
        self._checkpoints: dict[str, CheckpointModel] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def workspace(self) -> Path:
        """The absolute path to the candidate's git worktree.

        Raises:
            RuntimeError: If the candidate is not active (worktree not
                created).
        """
        if self._workspace is None:
            raise RuntimeError(
                "Candidate is not active — use as a context manager "
                "(`with candidate:`) to create the worktree."
            )
        return self._workspace

    @property
    def run_id(self) -> str:
        """The lineage run identifier for this candidate."""
        return self._run_id

    @property
    def candidate_id(self) -> str:
        """The unique candidate identifier."""
        return self._candidate_id

    @property
    def task(self) -> str:
        """The task description."""
        return self._task

    @property
    def project_root(self) -> Path:
        """The main repository root."""
        return self._project_root

    @property
    def evidence(self) -> list[Any]:
        """Accumulated evidence collected during execution."""
        return list(self._evidence)

    @property
    def decisions(self) -> list[CandidateDecision]:
        """Decision history, in chronological order."""
        return list(self._decisions)

    @property
    def checkpoints(self) -> dict[str, CheckpointModel]:
        """Captured checkpoints, keyed by ``checkpoint_id``."""
        return dict(self._checkpoints)

    @property
    def is_active(self) -> bool:
        """``True`` when the worktree exists and the candidate is active."""
        return self._active and self._workspace is not None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> Candidate:
        """Create the git worktree and activate the candidate.

        Creates an isolated worktree at
        ``<worktrees_root>/<candidate_id>/`` detached at ``base_commit``.
        """
        self._workspace = self._worktrees_root / self._candidate_id
        self._workspace.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Creating worktree for candidate %s at %s (base=%s)",
            self._candidate_id,
            self._workspace,
            self._base_commit,
        )
        _git_require(
            "worktree",
            "add",
            "--detach",
            str(self._workspace),
            self._base_commit,
            cwd=self._project_root,
        )
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Remove the git worktree and clean up.

        The worktree is removed unconditionally, even on exception.
        Errors during cleanup are logged but never re-raised.
        """
        if self._workspace is not None and self._workspace.exists():
            try:
                logger.info(
                    "Removing worktree for candidate %s at %s",
                    self._candidate_id,
                    self._workspace,
                )
                _git_require(
                    "worktree",
                    "remove",
                    "--force",
                    str(self._workspace),
                    cwd=self._project_root,
                )
            except Exception:
                logger.exception(
                    "Failed to remove git worktree %s; falling back "
                    "to manual removal.",
                    self._workspace,
                )
                try:
                    _git_require(
                        "worktree", "prune", cwd=self._project_root
                    )
                    shutil.rmtree(self._workspace, ignore_errors=True)
                except Exception:
                    logger.exception(
                        "Manual worktree cleanup also failed for %s.",
                        self._workspace,
                    )

        self._active = False
        self._workspace = None

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def collect_evidence(
        self,
        contract: StepContract,
        *,
        collectors: dict[str, Any] | None = None,
    ) -> Any:
        """Collect evidence for a step contract inside the worktree.

        Args:
            contract: The :class:`~bound.contracts.StepContract` to collect
                evidence for.
            collectors: Optional dict of collector instances keyed by name.

        Returns:
            The collected :class:`~bound.evidence.ExecutionEvidence`.

        Raises:
            RuntimeError: If the candidate is not active.
        """
        if not self.is_active:
            raise RuntimeError(
                "Candidate is not active — use as context manager."
            )

        from bound.evidence import (
            ExecutionEvidence,
            migrate_legacy_execution_evidence,
        )

        evidence = ExecutionEvidence(
            step_id=contract.id,
            acceptance_evidence=[],
            risk_evidence=[],
            budget_metrics={},
            agent_self_report=None,
        )

        evidence = migrate_legacy_execution_evidence(evidence)
        self._evidence.append(evidence)
        return evidence

    def evaluate(
        self,
        contract: StepContract,
        evidence: Any,
        *,
        criteria: BoundCriteria | None = None,
    ) -> EvaluationResult:
        """Evaluate a step against the contract using collected evidence.

        Routes through the deterministic contract pipeline
        (:class:`~bound.contract_evaluator.ContractEvaluator` ->
        :class:`~bound.policy.BoundPolicy`).  Appends the decision to
        :attr:`decisions`.

        Args:
            contract: The :class:`~bound.contracts.StepContract` to
                evaluate.
            evidence: The :class:`~bound.evidence.ExecutionEvidence`
                collected for this step.
            criteria: Optional :class:`~bound.models.BoundCriteria`;
                defaults to a threshold of ``0.5``.

        Returns:
            An :class:`~bound.models.EvaluationResult`.

        Raises:
            RuntimeError: If the candidate is not active.
        """
        if not self.is_active:
            raise RuntimeError(
                "Candidate is not active — use as context manager."
            )

        from bound.bound_workflow import BoundWorkflow
        from bound.models import BoundCriteria as _BoundCriteria

        criteria = criteria or _BoundCriteria(threshold=0.5)

        workflow = BoundWorkflow()
        result = workflow.evaluate_step(
            contract, evidence, criteria=criteria
        )

        now_utc = datetime.now(UTC).isoformat()
        self._decisions.append(
            CandidateDecision(
                step_id=contract.id,
                decision=str(result.decision),
                score=result.score,
                threshold=result.threshold,
                reason_code=(
                    str(result.reason_code)
                    if result.reason_code
                    else "OK"
                ),
                timestamp=now_utc,
                evaluation_id=getattr(result, "evaluation_id", None),
            )
        )
        return result

    def capture_checkpoint(
        self,
        step_id: str,
        *,
        scope: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> CheckpointModel:
        """Capture the current git state as a named checkpoint.

        Records HEAD, diff, artifact hashes, and untracked content for the
        candidate's worktree.  The checkpoint is persisted to disk under
        ``.bound/checkpoints/<run_id>/<checkpoint_id>.json``.

        Args:
            step_id: The step identifier to associate with this checkpoint.
            scope: Optional path prefixes to scope the checkpoint to.
            metadata: Optional key-value metadata to store.

        Returns:
            The captured :class:`~bound.checkpoint.Checkpoint`.

        Raises:
            RuntimeError: If the candidate is not active or git state
                cannot be captured safely.
        """
        if not self.is_active:
            raise RuntimeError(
                "Candidate is not active — use as context manager."
            )
        assert self._workspace is not None

        checkpoint = _capture_checkpoint_impl(
            run_id=self._run_id,
            step_id=step_id,
            scope=scope,
            cwd=self._workspace,
            metadata=metadata,
        )

        # Persist to disk.
        save_checkpoint(
            checkpoint,
            base_dir=(self._project_root / ".bound/checkpoints"),
        )

        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        logger.info(
            "Checkpoint %s captured for candidate %s (step=%s, head=%s)",
            checkpoint.checkpoint_id,
            self._candidate_id,
            step_id,
            checkpoint.head_commit,
        )
        return checkpoint

    def restore_checkpoint(
        self, checkpoint_id: str
    ) -> CheckpointModel:
        """Restore the worktree to a previously captured checkpoint.

        The worktree's files are restored to the state recorded in the
        checkpoint.

        Args:
            checkpoint_id: The identifier of the checkpoint to restore.

        Returns:
            The restored :class:`~bound.checkpoint.Checkpoint`.

        Raises:
            KeyError: If ``checkpoint_id`` is not known to this candidate.
            RuntimeError: If the candidate is not active or if HEAD has
                diverged.
        """
        if not self.is_active:
            raise RuntimeError(
                "Candidate is not active — use as context manager."
            )
        assert self._workspace is not None

        if checkpoint_id not in self._checkpoints:
            try:
                cp = load_checkpoint(
                    self._run_id,
                    checkpoint_id,
                    base_dir=(
                        self._project_root / ".bound/checkpoints"
                    ),
                )
                self._checkpoints[checkpoint_id] = cp
            except FileNotFoundError:
                raise KeyError(
                    f"Checkpoint {checkpoint_id} not found in "
                    f"candidate {self._candidate_id}"
                ) from None

        checkpoint = self._checkpoints[checkpoint_id]
        restored, failed = restore_checkpoint_files(
            checkpoint,
            cwd=self._workspace,
        )
        if failed:
            logger.warning(
                "Checkpoint restore for %s had %d failed files: %s",
                checkpoint_id,
                len(failed),
                failed,
            )

        logger.info(
            "Checkpoint %s restored for candidate %s "
            "(%d files restored)",
            checkpoint_id,
            self._candidate_id,
            len(restored),
        )
        return checkpoint


__all__ = [
    "Candidate",
    "CandidateDecision",
    "DEFAULT_WORKTREES_DIR",
]
