"""BOUND-owned checkpoint model for safe state preservation and rollback.

A BOUND checkpoint is *not* a git commit reference — it is a structured
data record that captures the exact repository state at a point in time,
including HEAD, index/worktree diff, untracked files within a declared
scope, and content hashes for every tracked artifact.

This module is the single source of truth for:

* :class:`Checkpoint` — the on-disk checkpoint record.
* :func:`generate_checkpoint_id` — deterministic checkpoint identifier.
* :func:`capture_checkpoint` — capture the current git state into a record.
* :func:`save_checkpoint` / :func:`load_checkpoint` — persistence under
  ``.bound/checkpoints/<run_id>/<checkpoint_id>.json``.
* :func:`verify_checkpoint_integrity` — confirm a stored checkpoint is
  still valid (HEAD matches, hashes unchanged).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Default root directory for checkpoint storage under the project root.
DEFAULT_CHECKPOINTS_DIR: Path = Path(".bound/checkpoints")

#: Environment variable that overrides the checkpoints directory.
ENV_CHECKPOINTS_DIR: str = "BOUND_CHECKPOINTS_DIR"


# ---------------------------------------------------------------------------
# Identification
# ---------------------------------------------------------------------------


def generate_checkpoint_id(*, run_id: str, step_id: str, timestamp: datetime) -> str:
    """Return a deterministic, reproducible ``checkpoint_id``."""
    utc = timestamp.astimezone(timezone.utc)
    payload = f"{run_id}|{step_id}|{utc.isoformat()}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"cp_{digest}"


# ---------------------------------------------------------------------------
# File hash utility
# ---------------------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 hex of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
# ---------------------------------------------------------------------------
# Git command helpers
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _git_head(cwd: Path) -> str | None:
    """Return the current HEAD commit SHA, or None if unavailable."""
    proc = _git("rev-parse", "HEAD", cwd=cwd)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _git_status_porcelain(cwd: Path) -> str:
    """Return ``git status --porcelain`` output."""
    proc = _git("status", "--porcelain", cwd=cwd)
    return proc.stdout


def _git_diff_index(cwd: Path) -> str:
    """Return ``git diff`` of staged+unstaged changes vs HEAD.

    This captures the *entire* worktree diff (all paths) and is used for
    merge-conflict detection.
    """
    proc = _git("diff", "HEAD", cwd=cwd)
    return proc.stdout


def _git_diff_scoped(cwd: Path, scope: list[str]) -> str:
    """Return ``git diff HEAD`` filtered to the given scope path prefixes.

    When ``scope`` is empty the full worktree diff is returned (identical to
    :func:`_git_diff_index`).  When ``scope`` is non-empty only the hunks for
    in-scope paths are captured, so that restoring the diff later can never
    touch out-of-scope files.
    """
    if scope:
        proc = _git("diff", "HEAD", "--", *scope, cwd=cwd)
    else:
        proc = _git("diff", "HEAD", cwd=cwd)
    return proc.stdout


def _git_ls_untracked(cwd: Path) -> list[str]:
    """Return list of untracked files (relative paths)."""
    proc = _git("ls-files", "--others", "--exclude-standard", cwd=cwd)
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.strip().split("\n") if p]


def _git_branch(cwd: Path) -> str | None:
    """Return the current branch name, or None."""
    proc = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    return branch if branch != "HEAD" else None
# ---------------------------------------------------------------------------
# Checkpoint data model
# ---------------------------------------------------------------------------


class CheckpointFileEntry(BaseModel):
    """A single file tracked in a checkpoint.

    Attributes:
        path: Relative path from repo root.
        status: Git status flag (e.g. ``M``, ``A``, ``??``).
        content_hash: SHA-256 of the file contents at checkpoint time, or
            ``None`` for deleted/untrackable files.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    status: str
    content_hash: str | None = None


class Checkpoint(BaseModel):
    """A BOUND-owned snapshot of the repository state.

    Every checkpoint is scoped to a specific run and step and records
    exactly what the repo looked like at capture time.

    Attributes:
        checkpoint_id: The unique checkpoint identifier (``cp_<hex>``).
        run_id: The owning run id.
        step_id: The step this checkpoint belongs to.
        head_commit: The HEAD commit SHA at checkpoint time, or ``None``.
        branch: The current branch name, or ``None``.
        worktree_diff: The raw ``git diff HEAD`` output capturing all
            uncommitted changes.
        changed_files: List of :class:`CheckpointFileEntry` for tracked
            files with changes (status M/A/D/R).
        untracked_files: List of relative paths of untracked files within
            the declared scope.
        scope: The allowed path prefixes this checkpoint covers.
        timestamp: UTC datetime of checkpoint creation.
        artifact_hashes: Mapping of relative path → SHA-256 for every file
            recorded in the checkpoint.
        untracked_content: Mapping of relative path → base64-encoded content
            for untracked in-scope files, so they can be restored even if
            deleted.
        metadata: Arbitrary key-value metadata.
        signature: HMAC-SHA256 hex digest over the canonical JSON of the
            checkpoint (excluding this field), used for tamper detection.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_id: str
    run_id: str
    step_id: str
    head_commit: str | None = None
    branch: str | None = None
    worktree_diff: str = ""
    changed_files: list[CheckpointFileEntry] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    timestamp: str = ""
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    untracked_content: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    signature: str | None = None
# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _is_within_scope(path: str, scope: list[str]) -> bool:
    """Check if a path is within at least one allowed scope prefix.

    An empty scope means *everything* is in scope.
    """
    if not scope:
        return True
    normalized = path.replace("\\", "/")
    return any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in scope
    )


def _parse_porcelain_status(status_line: str) -> tuple[str, str]:
    """Parse a ``git status --porcelain`` line into (status, path).

    Returns:
        ``(status_flags, relative_path)`` — e.g. ``(" M", "src/foo.py")``.
    """
    if len(status_line) < 4:
        return ("", status_line.strip())
    status = status_line[:2]
    path_part = status_line[3:].strip()
    if " -> " in path_part and status[0] == "R":
        path_part = path_part.split(" -> ")[-1]
    return (status, path_part)
def capture_checkpoint(
    *,
    run_id: str,
    step_id: str,
    scope: list[str] | None = None,
    cwd: Path | None = None,
    metadata: dict[str, str] | None = None,
) -> Checkpoint:
    """Capture the current git repository state into a :class:`Checkpoint`.

    Args:
        run_id: The owning run id.
        step_id: The step this checkpoint is for.
        scope: Allowed path prefixes. Files outside scope are excluded from
            the checkpoint. An empty list means everything.
        cwd: Working directory (defaults to current directory).
        metadata: Optional key-value metadata to store.

    Returns:
        A populated :class:`Checkpoint`.

    Raises:
        RuntimeError: If the repository state cannot be captured safely
            (e.g. merge conflicts detected).
    """
    cwd = cwd or Path.cwd()
    scope = scope or []
    now = datetime.now(timezone.utc)
    checkpoint_id = generate_checkpoint_id(
        run_id=run_id, step_id=step_id, timestamp=now
    )

    head = _git_head(cwd)
    # C4: Refuse to create a checkpoint when git HEAD is unavailable —
    # otherwise the integrity check short-circuits and verification is
    # undermined.
    if head is None:
        raise RuntimeError(
            "Cannot create checkpoint: git HEAD unavailable"
        )
    branch = _git_branch(cwd)
    # Full diff for merge-conflict detection (all paths).
    full_diff = _git_diff_index(cwd)
    # C1: Scope-filtered diff for storage — only in-scope changes are
    # captured so that restoring the diff can never modify out-of-scope
    # files.
    diff = _git_diff_scoped(cwd, scope)
    status_output = _git_status_porcelain(cwd)
    all_untracked = _git_ls_untracked(cwd)

    # Detect unsafe state: merge conflicts
    if full_diff:
        for line in full_diff.split("\n"):
            if line.startswith("<<<<<<<") or line.startswith(">>>>>>>"):
                raise RuntimeError(
                    "Cannot create checkpoint: merge conflicts detected in worktree"
                )

    # Parse changed files from porcelain status
    changed_files: list[CheckpointFileEntry] = []
    artifact_hashes: dict[str, str] = {}

    for line in status_output.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        status_flags, path = _parse_porcelain_status(line)
        if not path:
            continue

        # Only include files within scope
        if scope and not _is_within_scope(path, scope):
            continue

        content_hash = None
        full_path = cwd / path
        if full_path.exists() and full_path.is_file():
            try:
                content_hash = _file_sha256(full_path)
                artifact_hashes[path] = content_hash
            except OSError:
                pass

        changed_files.append(CheckpointFileEntry(
            path=path,
            status=status_flags,
            content_hash=content_hash,
        ))

    # Filter untracked files by scope
    in_scope_untracked = [
        p for p in all_untracked
        if not scope or _is_within_scope(p, scope)
    ]
    # C3: Store the actual content of untracked in-scope files so they can
    # be restored even if deleted (git cannot restore untracked files).
    untracked_content: dict[str, str] = {}
    for p in in_scope_untracked:
        full_path = cwd / p
        if full_path.exists() and full_path.is_file():
            try:
                artifact_hashes[p] = _file_sha256(full_path)
                untracked_content[p] = base64.b64encode(
                    full_path.read_bytes()
                ).decode("ascii")
            except OSError:
                pass

    return Checkpoint(
        checkpoint_id=checkpoint_id,
        run_id=run_id,
        step_id=step_id,
        head_commit=head,
        branch=branch,
        worktree_diff=diff,
        changed_files=changed_files,
        untracked_files=in_scope_untracked,
        scope=scope,
        timestamp=now.isoformat(),
        artifact_hashes=artifact_hashes,
        untracked_content=untracked_content,
        metadata=metadata or {},
    )
# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _signature_key(base_dir: Path | None = None) -> bytes:
    """Derive a project-local HMAC key from the checkpoints storage path.

    The key is derived from the resolved absolute path of the checkpoints
    root directory.  This is not cryptographic security — it simply ties a
    checkpoint to its storage location so that accidental cross-project
    corruption or manual tampering of the JSON is detected at load time.

    Args:
        base_dir: Optional override for the checkpoints root.

    Returns:
        A 32-byte HMAC key.
    """
    env_dir = os.environ.get(ENV_CHECKPOINTS_DIR)
    if env_dir:
        root = Path(env_dir)
    elif base_dir is not None:
        root = base_dir
    else:
        root = DEFAULT_CHECKPOINTS_DIR
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest().encode("utf-8")


def _compute_signature(data: dict[str, Any], key: bytes) -> str:
    """Compute the HMAC-SHA256 signature over the canonical JSON of ``data``.

    The ``signature`` key (if present) is excluded from the signed payload so
    that the signature is stable regardless of whether it has been added.

    Args:
        data: The checkpoint dict (may or may not contain ``signature``).
        key: The HMAC key.

    Returns:
        A hex-encoded HMAC-SHA256 digest.
    """
    payload = {k: v for k, v in data.items() if k != "signature"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _checkpoints_dir(run_id: str, base_dir: Path | None = None) -> Path:
    """Return the directory where checkpoints for a run are stored."""
    import os

    env_dir = os.environ.get(ENV_CHECKPOINTS_DIR)
    if env_dir:
        root = Path(env_dir)
    elif base_dir is not None:
        root = base_dir
    else:
        root = DEFAULT_CHECKPOINTS_DIR
    return root / run_id


def _checkpoint_path(
    run_id: str, checkpoint_id: str, base_dir: Path | None = None
) -> Path:
    """Return the full path to a checkpoint JSON file."""
    return _checkpoints_dir(run_id, base_dir) / f"{checkpoint_id}.json"


def checkpoint_to_dict(cp: Checkpoint) -> dict[str, Any]:
    """Serialize a :class:`Checkpoint` to a JSON-compatible dict."""
    return {
        "checkpoint_id": cp.checkpoint_id,
        "run_id": cp.run_id,
        "step_id": cp.step_id,
        "head_commit": cp.head_commit,
        "branch": cp.branch,
        "worktree_diff": cp.worktree_diff,
        "changed_files": [
            {"path": f.path, "status": f.status, "content_hash": f.content_hash}
            for f in cp.changed_files
        ],
        "untracked_files": cp.untracked_files,
        "scope": cp.scope,
        "timestamp": cp.timestamp,
        "artifact_hashes": cp.artifact_hashes,
        "untracked_content": cp.untracked_content,
        "metadata": cp.metadata,
    }


def checkpoint_from_dict(data: dict[str, Any]) -> Checkpoint:
    """Deserialize a dict into a :class:`Checkpoint`."""
    return Checkpoint(
        checkpoint_id=data["checkpoint_id"],
        run_id=data["run_id"],
        step_id=data["step_id"],
        head_commit=data.get("head_commit"),
        branch=data.get("branch"),
        worktree_diff=data.get("worktree_diff", ""),
        changed_files=[
            CheckpointFileEntry(
                path=f["path"],
                status=f["status"],
                content_hash=f.get("content_hash"),
            )
            for f in data.get("changed_files", [])
        ],
        untracked_files=data.get("untracked_files", []),
        scope=data.get("scope", []),
        timestamp=data.get("timestamp", ""),
        artifact_hashes=data.get("artifact_hashes", {}),
        untracked_content=data.get("untracked_content", {}),
        metadata=data.get("metadata", {}),
        signature=data.get("signature"),
    )


def save_checkpoint(
    cp: Checkpoint, base_dir: Path | None = None
) -> Path:
    """Persist a :class:`Checkpoint` to disk.

    Args:
        cp: The checkpoint to save.
        base_dir: Optional override for the checkpoints root.

    Returns:
        The path where the checkpoint was written.

    Raises:
        OSError: If the directory cannot be created or the file cannot be
            written.
    """
    dir_path = _checkpoints_dir(cp.run_id, base_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = _checkpoint_path(cp.run_id, cp.checkpoint_id, base_dir)

    data = checkpoint_to_dict(cp)
    # C5: Compute HMAC-SHA256 signature over the canonical JSON (excluding
    # the signature field itself) and store it alongside the data.
    key = _signature_key(base_dir)
    data["signature"] = _compute_signature(data, key)
    # Atomic write: write to temp file, then rename
    tmp_path = file_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(file_path)

    logger.info("Checkpoint saved: %s → %s", cp.checkpoint_id, file_path)
    return file_path


def load_checkpoint(
    run_id: str, checkpoint_id: str, base_dir: Path | None = None
) -> Checkpoint:
    """Load a :class:`Checkpoint` from disk.

    Args:
        run_id: The owning run id.
        checkpoint_id: The checkpoint to load.
        base_dir: Optional override for the checkpoints root.

    Returns:
        The deserialized :class:`Checkpoint`.

    Raises:
        FileNotFoundError: If the checkpoint file does not exist.
        RuntimeError: If the checkpoint signature verification fails.
    """
    file_path = _checkpoint_path(run_id, checkpoint_id, base_dir)
    if not file_path.exists():
        raise FileNotFoundError(
            f"Checkpoint not found: {checkpoint_id} (run: {run_id})"
        )
    data = json.loads(file_path.read_text(encoding="utf-8"))
    # C5: Verify the HMAC-SHA256 signature to detect tampering or
    # corruption.  A missing or mismatched signature is rejected.
    stored_signature = data.get("signature")
    if not stored_signature:
        raise RuntimeError("Checkpoint signature verification failed")
    key = _signature_key(base_dir)
    expected_signature = _compute_signature(data, key)
    if not hmac.compare_digest(stored_signature, expected_signature):
        raise RuntimeError("Checkpoint signature verification failed")
    return checkpoint_from_dict(data)


def list_checkpoints(
    run_id: str, base_dir: Path | None = None
) -> list[str]:
    """List all checkpoint ids for a given run.

    Args:
        run_id: The run to list checkpoints for.
        base_dir: Optional override for the checkpoints root.

    Returns:
        Sorted list of checkpoint ids (newest first by timestamp).
    """
    dir_path = _checkpoints_dir(run_id, base_dir)
    if not dir_path.exists():
        return []
    checkpoint_ids: list[str] = []
    for f in sorted(dir_path.glob("*.json")):
        if f.name.endswith(".tmp"):
            continue
        cp_id = f.stem
        checkpoint_ids.append(cp_id)
    return checkpoint_ids
# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------


def verify_checkpoint_integrity(
    cp: Checkpoint,
    cwd: Path | None = None,
) -> tuple[bool, list[str]]:
    """Verify that a checkpoint's recorded state still matches the repo.

    Checks:
    1. HEAD still matches (detached HEAD or different commit = divergence).
    2. All artifact hashes still match for files that exist.
    3. No merge conflicts are present.

    Args:
        cp: The checkpoint to verify.
        cwd: Working directory (defaults to current).

    Returns:
        ``(is_valid, issues)`` — ``is_valid`` is ``True`` when the
        checkpoint is verified; ``issues`` is a list of human-readable
        problem descriptions.
    """
    cwd = cwd or Path.cwd()
    issues: list[str] = []

    current_head = _git_head(cwd)
    # C4: A checkpoint with no recorded head_commit cannot be verified —
    # treat it as invalid rather than silently short-circuiting the check.
    if cp.head_commit is None:
        issues.append("Cannot verify HEAD: checkpoint has no recorded head_commit")
    elif current_head != cp.head_commit:
        issues.append(
            f"HEAD diverged: checkpoint had {cp.head_commit[:12]}, "
            f"current is {current_head[:12] if current_head else 'None'}"
        )

    for path, expected_hash in cp.artifact_hashes.items():
        full_path = cwd / path
        if not full_path.exists():
            issues.append(f"File missing: {path}")
            continue
        if not full_path.is_file():
            issues.append(f"Path is not a file: {path}")
            continue
        try:
            actual_hash = _file_sha256(full_path)
            if actual_hash != expected_hash:
                issues.append(
                    f"Hash mismatch for {path}: "
                    f"expected {expected_hash[:12]}, got {actual_hash[:12]}"
                )
        except OSError as exc:
            issues.append(f"Cannot read {path}: {exc}")

    diff = _git_diff_index(cwd)
    for line in diff.split("\n"):
        if line.startswith("<<<<<<<") or line.startswith(">>>>>>>"):
            issues.append("Merge conflicts detected in worktree")
            break

    return len(issues) == 0, issues


# ---------------------------------------------------------------------------
# Rollback preview and file restoration
# ---------------------------------------------------------------------------


def _git_checkout_file(path: str, cwd: Path) -> tuple[bool, str]:
    """Check out a single file from HEAD using ``git checkout HEAD -- <path>``.

    Args:
        path: Relative path of the file to restore.
        cwd: Repository working directory.

    Returns:
        ``(success, error_message)``.
    """
    proc = _git("checkout", "HEAD", "--", path, cwd=cwd)
    if proc.returncode != 0:
        return False, proc.stderr.strip() or f"git checkout failed for {path}"
    return True, ""


def _git_show_head(path: str, cwd: Path) -> str | None:
    """Return the committed content of a file at HEAD.

    Args:
        path: Relative path of the file.
        cwd: Repository working directory.

    Returns:
        The file content as a string, or ``None`` if the file is not tracked.
    """
    proc = _git("show", f"HEAD:{path}", cwd=cwd)
    if proc.returncode != 0:
        return None
    return proc.stdout


def _diverged_files_outside_scope(
    cp: Checkpoint, cwd: Path
) -> list[str]:
    """Check for divergent changes outside the checkpoint's recorded scope.

    Returns a list of file paths that have been modified outside the scope
    since the checkpoint was created.  An empty list means no divergence.

    Args:
        cp: The checkpoint to compare against.
        cwd: Repository working directory.

    Returns:
        List of relative paths with divergent changes outside scope.
    """
    scope = cp.scope or []
    current_status = _git_status_porcelain(cwd)
    diverged: list[str] = []

    for line in current_status.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        if len(line) < 4:
            continue
        status_flags = line[:2].strip()
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ")[-1]
        if not path_part:
            continue

        # Only flag files that are outside the checkpoint scope
        if scope and _is_within_scope(path_part, scope):
            continue

        # Skip files that are in the checkpoint's artifact_hashes
        if path_part in cp.artifact_hashes:
            continue

        # Skip untracked files (they are unrelated user data)
        if status_flags == "??":
            continue

        # If the file has changes (staged or unstaged) outside scope, flag it
        if status_flags:
            diverged.append(path_part)

    return diverged


def _git_apply_patch(patch_content: str, cwd: Path) -> tuple[bool, str]:
    """Apply a patch string using ``git apply``.

    Args:
        patch_content: The unified-diff patch content.
        cwd: Repository working directory.

    Returns:
        ``(success, error_message)``.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, encoding="utf-8"
    ) as f:
        f.write(patch_content)
        patch_path = f.name

    try:
        proc = _git("apply", patch_path, cwd=cwd)
        if proc.returncode != 0:
            return False, proc.stderr.strip() or "git apply failed"
        return True, ""
    finally:
        Path(patch_path).unlink(missing_ok=True)
def restore_checkpoint_files(
    cp: Checkpoint,
    cwd: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Restore the working tree to the state recorded in a checkpoint.

    **Safety guarantees:**

    * Only files within the checkpoint's recorded scope are touched.
    * Files outside the checkpoint scope are **never** modified or deleted.
    * Pre-existing user changes on files outside the checkpoint scope are
      preserved.
    * ``git reset --hard`` is **never** used.
    * Unrelated untracked files are **never** deleted.
    * Rollback is refused when the workspace has diverged outside the
      recorded scope.

    The restoration works by:
    1. Verifying HEAD still matches the checkpoint's ``head_commit``.
    2. Checking for divergent changes outside the recorded scope.
    3. Resetting tracked files within the scope to HEAD via
       ``git checkout HEAD -- <path>``.
    4. Applying the checkpoint's ``worktree_diff`` patch to restore the
       exact working tree state.

    Args:
        cp: The checkpoint to restore from.
        cwd: Working directory (defaults to current).

    Returns:
        ``(restored_files, failed_files)`` — two lists of relative file
        paths.

    Raises:
        RuntimeError: If HEAD has diverged or the workspace has diverged
            outside the recorded scope.
    """
    cwd = cwd or Path.cwd()

    # ------------------------------------------------------------------
    # 1. Verify HEAD matches
    # ------------------------------------------------------------------
    current_head = _git_head(cwd)
    # C4: A checkpoint without a recorded head_commit cannot be safely
    # rolled back — the HEAD-match check would silently pass.
    if cp.head_commit is None:
        raise RuntimeError(
            "Cannot rollback: checkpoint has no recorded head_commit"
        )
    if current_head != cp.head_commit:
        raise RuntimeError(
            f"Cannot rollback: HEAD has diverged. "
            f"Checkpoint had {cp.head_commit[:12]}, "
            f"current is {current_head[:12] if current_head else 'None'}. "
            f"Commit or stash your changes and try again."
        )

    # ------------------------------------------------------------------
    # 2. Check for divergent changes outside the recorded scope
    # ------------------------------------------------------------------
    diverged = _diverged_files_outside_scope(cp, cwd)
    if diverged:
        sample = ", ".join(diverged[:10])
        raise RuntimeError(
            f"Cannot rollback: workspace has diverged outside the recorded scope. "
            f"Found {len(diverged)} file(s) with changes outside the checkpoint scope: "
            f"{sample}. "
            f"Commit or stash these changes first, or create a new checkpoint."
        )

    # ------------------------------------------------------------------
    # 3. Restore files
    # ------------------------------------------------------------------
    restored: list[str] = []
    failed: list[str] = []

    # C2: Backups of current content for tracked in-scope files, so that we
    # can undo the ``git checkout HEAD`` if the subsequent patch fails.
    # Each entry is (existed_before, content_bytes_or_None).
    backups: dict[str, tuple[bool, bytes | None]] = {}

    # 3a. Reset tracked files within the scope to HEAD; restore untracked
    #     in-scope files from stored content (C3).
    for path in cp.artifact_hashes:
        head_content = _git_show_head(path, cwd)
        if head_content is not None:
            # Tracked file — back up current state before checkout (C2).
            full_path = cwd / path
            existed_before = full_path.exists()
            backup_bytes: bytes | None = None
            if existed_before and full_path.is_file():
                try:
                    backup_bytes = full_path.read_bytes()
                except OSError:
                    backup_bytes = None
            backups[path] = (existed_before, backup_bytes)

            success, err = _git_checkout_file(path, cwd)
            if success:
                restored.append(path)
            else:
                failed.append(path)
                logger.warning("Failed to checkout %s from HEAD: %s", path, err)
        else:
            # C3: Untracked in-scope file — git cannot restore it, so use
            # the content captured at checkpoint time (if available).
            if path in cp.untracked_content:
                full_path = cwd / path
                if not full_path.exists():
                    try:
                        content = base64.b64decode(cp.untracked_content[path])
                        full_path.parent.mkdir(parents=True, exist_ok=True)
                        full_path.write_bytes(content)
                        restored.append(path)
                    except OSError as exc:
                        failed.append(path)
                        logger.warning(
                            "Failed to restore untracked file %s: %s", path, exc
                        )
                else:
                    # File still exists — already present, count as restored
                    # only if it matches the recorded hash.
                    try:
                        if _file_sha256(full_path) == cp.artifact_hashes.get(path):
                            restored.append(path)
                    except OSError:
                        failed.append(path)
            else:
                logger.info(
                    "Skipping untracked file %s (no stored content, "
                    "not in HEAD, cannot restore from git)",
                    path,
                )

    # 3b. Apply the checkpoint's worktree_diff to restore the exact state
    if cp.worktree_diff:
        success, err = _git_apply_patch(cp.worktree_diff, cwd)
        if not success:
            logger.warning(
                "Worktree diff patch application failed: %s. "
                "Restoring user backups so no work is lost.", err,
            )
            # C2: Restore backups so the user's pre-rollback work is not
            # lost when the patch fails to apply.
            for path, (existed, content) in backups.items():
                full_path = cwd / path
                if existed:
                    if content is not None:
                        try:
                            full_path.write_bytes(content)
                        except OSError:
                            logger.warning(
                                "Failed to restore backup for %s", path
                            )
                    # else: could not read backup, leave as-is
                else:
                    # File did not exist before rollback — remove what
                    # ``git checkout`` created so we return to the original
                    # (missing) state.
                    if full_path.exists():
                        try:
                            full_path.unlink()
                        except OSError:
                            logger.warning(
                                "Failed to remove %s after failed patch", path
                            )

            # Re-evaluate which files match the expected checkpoint state.
            for path in list(restored):
                full_path = cwd / path
                if full_path.exists() and full_path.is_file():
                    try:
                        actual_hash = _file_sha256(full_path)
                        expected_hash = cp.artifact_hashes.get(path)
                        if expected_hash and actual_hash != expected_hash:
                            failed.append(path)
                            restored.remove(path)
                    except OSError:
                        failed.append(path)
                        restored.remove(path)
                else:
                    # File is missing after backup restoration.
                    failed.append(path)
                    restored.remove(path)

    return restored, failed


def compute_rollback_preview(
    cp: Checkpoint,
    cwd: Path | None = None,
) -> dict[str, object]:
    """Compare the current working tree against a checkpoint's recorded state.

    Scans every file in the checkpoint's artifact_hashes and reports
    which files would be changed, added, or left unchanged by a rollback.

    Args:
        cp: The checkpoint to compare against.
        cwd: Working directory (defaults to current).

    Returns:
        A dict with keys:
        * "changed" — list of file paths that exist but whose content
          differs from the checkpoint.
        * "added" — list of paths that are in the checkpoint but are
          missing from the working tree (would be restored).
        * "removed" — list of paths that exist in the working tree
          but are not in the checkpoint (would be left in place).
        * "unchanged" — list of paths whose content matches the
          checkpoint.
        * "total" — total number of files in the checkpoint.
        * "head_match" — True if the current HEAD matches the
          checkpoint's head_commit.
    """
    cwd = cwd or Path.cwd()

    current_head = _git_head(cwd)
    head_match = (
        cp.head_commit is not None and current_head == cp.head_commit
    )

    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    unchanged: list[str] = []

    for path, expected_hash in cp.artifact_hashes.items():
        full_path = cwd / path
        if not full_path.exists():
            added.append(path)
            continue
        if not full_path.is_file():
            changed.append(path)
            continue
        try:
            actual_hash = _file_sha256(full_path)
            if actual_hash == expected_hash:
                unchanged.append(path)
            else:
                changed.append(path)
        except OSError:
            changed.append(path)

    return {
        "changed": changed,
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "total": len(cp.artifact_hashes),
        "head_match": head_match,
    }

def compute_rollback_preview(
    cp: Checkpoint,
    cwd: Path | None = None,
) -> dict[str, object]:
    """Compare the current working tree against a checkpoint's recorded state.

    Scans every file in the checkpoint's artifact_hashes and reports
    which files would be changed, added, or left unchanged by a rollback.

    Args:
        cp: The checkpoint to compare against.
        cwd: Working directory (defaults to current).

    Returns:
        A dict with keys:
        * "changed" — list of file paths that exist but whose content
          differs from the checkpoint.
        * "added" — list of paths that are in the checkpoint but are
          missing from the working tree (would be restored).
        * "removed" — list of paths that exist in the working tree
          but are not in the checkpoint (would be left in place).
        * "unchanged" — list of paths whose content matches the
          checkpoint.
        * "total" — total number of files in the checkpoint.
        * "head_match" — True if the current HEAD matches the
          checkpoint's head_commit.
    """
    cwd = cwd or Path.cwd()

    current_head = _git_head(cwd)
    head_match = (
        cp.head_commit is not None and current_head == cp.head_commit
    )

    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    unchanged: list[str] = []

    for path, expected_hash in cp.artifact_hashes.items():
        full_path = cwd / path
        if not full_path.exists():
            added.append(path)
            continue
        if not full_path.is_file():
            changed.append(path)
            continue
        try:
            actual_hash = _file_sha256(full_path)
            if actual_hash == expected_hash:
                unchanged.append(path)
            else:
                changed.append(path)
        except OSError:
            changed.append(path)

    return {
        "changed": changed,
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "total": len(cp.artifact_hashes),
        "head_match": head_match,
    }
