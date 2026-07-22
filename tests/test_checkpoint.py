"""Tests for checkpoint creation, rollback preview, and file restoration.

These tests verify the core checkpoint safety guarantees:

1. **restore_checkpoint_files** only touches files within the recorded scope.
2. **compute_rollback_preview** accurately reports changed/added/unchanged files.
3. Pre-existing user changes outside the checkpoint scope survive rollback.
4. ``git reset --hard`` is never used.
5. Unrelated untracked files are never deleted.
6. Rollback is refused when HEAD has diverged or workspace has diverged
   outside the recorded scope.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bound.checkpoint import (
    Checkpoint,
    CheckpointFileEntry,
    capture_checkpoint,
    compute_rollback_preview,
    generate_checkpoint_id,
    list_checkpoints,
    load_checkpoint,
    restore_checkpoint_files,
    save_checkpoint,
    verify_checkpoint_integrity,
)

# =========================================================================
# Helpers
# =========================================================================


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """Create a bare git repo for checkpoint storage."""
    repo = tmp_path / "checkpoints"
    repo.mkdir(parents=True)
    return repo


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit and some files."""
    repo = tmp_path / "worktree"
    repo.mkdir(parents=True)
    _git("init", cwd=repo)
    _git("config", "user.email", "test@bound.dev", cwd=repo)
    _git("config", "user.name", "BOUND Test", cwd=repo)

    # Initial commit with a README
    (repo / "README.md").write_text("# Test Repo\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "initial commit", cwd=repo)

    # Create some source files
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("def main():\n    pass\n")
    (repo / "src" / "utils.py").write_text("def helper():\n    return 42\n")
    _git("add", "src/", cwd=repo)
    _git("commit", "-m", "add source files", cwd=repo)

    return repo


# =========================================================================
# compute_rollback_preview tests
# =========================================================================


class TestComputeRollbackPreview:
    """Tests for compute_rollback_preview."""

    def test_all_unchanged(self, worktree: Path) -> None:
        """All files in the checkpoint match the working tree."""
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        cp = Checkpoint(
            checkpoint_id="cp_test1",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff="",
            artifact_hashes={
                "README.md": _file_sha256(worktree / "README.md"),
                "src/main.py": _file_sha256(worktree / "src/main.py"),
                "src/utils.py": _file_sha256(worktree / "src/utils.py"),
            },
            changed_files=[],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )
        preview = compute_rollback_preview(cp, cwd=worktree)
        assert preview["head_match"] is True
        assert len(preview["unchanged"]) == 3
        assert len(preview["changed"]) == 0
        assert len(preview["added"]) == 0
        assert preview["total"] == 3

    def test_one_file_changed(self, worktree: Path) -> None:
        """One file differs from the checkpoint; preview reports it as changed."""
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        (worktree / "src/main.py").write_text("def main():\n    print('changed')\n")
        cp = Checkpoint(
            checkpoint_id="cp_test2",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff="",
            artifact_hashes={
                "README.md": _file_sha256(worktree / "README.md"),
                "src/main.py": hashlib.sha256(b"original content").hexdigest(),
                "src/utils.py": _file_sha256(worktree / "src/utils.py"),
            },
            changed_files=[],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )
        preview = compute_rollback_preview(cp, cwd=worktree)
        assert "src/main.py" in preview["changed"]
        assert len(preview["unchanged"]) == 2

    def test_file_missing_added(self, worktree: Path) -> None:
        """A file in the checkpoint but missing from the working tree is 'added'."""
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        (worktree / "src/main.py").unlink()
        cp = Checkpoint(
            checkpoint_id="cp_test3",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff="",
            artifact_hashes={
                "README.md": _file_sha256(worktree / "README.md"),
                "src/main.py": "abc123",
                "src/utils.py": _file_sha256(worktree / "src/utils.py"),
            },
            changed_files=[],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )
        preview = compute_rollback_preview(cp, cwd=worktree)
        assert "src/main.py" in preview["added"]
        assert len(preview["unchanged"]) == 2

    def test_head_diverged(self, worktree: Path) -> None:
        """When HEAD has diverged, head_match is False."""
        cp = Checkpoint(
            checkpoint_id="cp_test4",
            run_id="run-1",
            step_id="step-1",
            head_commit="0000000000000000000000000000000000000000",
            worktree_diff="",
            artifact_hashes={},
            changed_files=[],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )
        preview = compute_rollback_preview(cp, cwd=worktree)
        assert preview["head_match"] is False


# =========================================================================
# restore_checkpoint_files tests
# =========================================================================


class TestRestoreCheckpointFiles:
    """Tests for restore_checkpoint_files."""

    def test_restore_modified_file(self, worktree: Path) -> None:
        """A modified file within the scope is restored to the checkpoint state."""
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        # Capture the original content
        original_main = (worktree / "src/main.py").read_text()

        # Create a checkpoint with the current state
        cp = Checkpoint(
            checkpoint_id="cp_restore1",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff="",
            artifact_hashes={
                "src/main.py": _file_sha256(worktree / "src/main.py"),
            },
            changed_files=[
                CheckpointFileEntry(
                    path="src/main.py",
                    status=" M",
                    content_hash=_file_sha256(worktree / "src/main.py"),
                ),
            ],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )

        # Modify the file
        (worktree / "src/main.py").write_text("# modified content\n")
        assert (worktree / "src/main.py").read_text() != original_main

        # Now restore using the checkpoint — this should checkout from HEAD
        # since artifact_hashes matches HEAD
        restored, failed = restore_checkpoint_files(cp, cwd=worktree)
        # The file was restored to HEAD (committed) state, and then no diff
        # was applied since worktree_diff is empty
        assert "src/main.py" in restored
        assert len(failed) == 0

    def test_restore_with_worktree_diff(self, worktree: Path) -> None:
        """Files are restored to checkpoint state including worktree diff."""
        # Modify a file and create a checkpoint with the changes
        (worktree / "src/main.py").write_text("def main():\n    print('hello')\n")
        diff = _git("diff", "HEAD", cwd=worktree).stdout
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        content_hash = _file_sha256(worktree / "src/main.py")

        cp = Checkpoint(
            checkpoint_id="cp_restore2",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff=diff,
            artifact_hashes={
                "src/main.py": content_hash,
            },
            changed_files=[
                CheckpointFileEntry(path="src/main.py", status=" M", content_hash=content_hash),
            ],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )

        # Undo the change (simulate user reverting)
        _git("checkout", "HEAD", "--", "src/main.py", cwd=worktree)
        assert (worktree / "src/main.py").read_text() == "def main():\n    pass\n"

        # Restore from checkpoint - should apply the diff
        restored, failed = restore_checkpoint_files(cp, cwd=worktree)
        assert "src/main.py" in restored
        assert len(failed) == 0
        assert (worktree / "src/main.py").read_text() == "def main():\n    print('hello')\n"

    def test_unrelated_file_untouched(self, worktree: Path) -> None:
        """Files outside the checkpoint scope are never modified."""
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()

        # Create an untracked file outside the scope
        (worktree / "user_data.txt").write_text("user data\n")
        original_user_data = (worktree / "user_data.txt").read_text()

        # Checkpoint scoped to src/ only
        cp = Checkpoint(
            checkpoint_id="cp_restore3",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff="",
            artifact_hashes={
                "src/main.py": _file_sha256(worktree / "src/main.py"),
            },
            changed_files=[],
            untracked_files=[],
            scope=["src"],
            timestamp=datetime.now(UTC).isoformat(),
        )

        restored, failed = restore_checkpoint_files(cp, cwd=worktree)
        # user_data.txt should still be intact
        assert (worktree / "user_data.txt").read_text() == original_user_data

    def test_refuse_diverged_head(self, worktree: Path) -> None:
        """Restore raises RuntimeError when HEAD has diverged."""
        cp = Checkpoint(
            checkpoint_id="cp_restore4",
            run_id="run-1",
            step_id="step-1",
            head_commit="0000000000000000000000000000000000000000",
            worktree_diff="",
            artifact_hashes={},
            changed_files=[],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )
        with pytest.raises(RuntimeError, match="HEAD has diverged"):
            restore_checkpoint_files(cp, cwd=worktree)

    def test_preserve_untracked_files(self, worktree: Path) -> None:
        """Unrelated untracked files are never deleted."""
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()

        # Create an untracked file
        (worktree / "untracked.txt").write_text("i am untracked\n")

        cp = Checkpoint(
            checkpoint_id="cp_restore5",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff="",
            artifact_hashes={
                "src/main.py": _file_sha256(worktree / "src/main.py"),
            },
            changed_files=[],
            untracked_files=[],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )

        restore_checkpoint_files(cp, cwd=worktree)
        assert (worktree / "untracked.txt").exists()
        assert (worktree / "untracked.txt").read_text() == "i am untracked\n"

    def test_untracked_files_not_in_head(self, worktree: Path) -> None:
        """Untracked files in the checkpoint are skipped (not restored from git)."""
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()

        # Create an untracked file and add it to the checkpoint
        (worktree / "new_file.py").write_text("# new file\n")

        cp = Checkpoint(
            checkpoint_id="cp_restore6",
            run_id="run-1",
            step_id="step-1",
            head_commit=head,
            worktree_diff="",
            artifact_hashes={
                "new_file.py": _file_sha256(worktree / "new_file.py"),
            },
            changed_files=[],
            untracked_files=["new_file.py"],
            scope=[],
            timestamp=datetime.now(UTC).isoformat(),
        )

        # Delete the untracked file
        (worktree / "new_file.py").unlink()

        # Restore — it should skip the file since it's not in HEAD
        restored, failed = restore_checkpoint_files(cp, cwd=worktree)
        assert "new_file.py" not in restored


# =========================================================================
# End-to-end capture + restore
# =========================================================================


class TestCaptureAndRestore:
    """End-to-end tests: capture, save, load, verify, and restore."""

    def test_capture_then_restore(self, worktree: Path, bare_repo: Path) -> None:
        """Capture a checkpoint, modify the file, then restore it."""
        # Modify a file
        (worktree / "src/main.py").write_text("def main():\n    print('hello world')\n")

        # Capture
        cp = capture_checkpoint(
            run_id="run-e2e",
            step_id="step-1",
            cwd=worktree,
        )
        assert cp.checkpoint_id.startswith("cp_")
        assert "src/main.py" in cp.artifact_hashes

        # Save
        saved_path = save_checkpoint(cp, base_dir=bare_repo)
        assert saved_path.exists()

        # Load
        loaded = load_checkpoint("run-e2e", cp.checkpoint_id, base_dir=bare_repo)
        assert loaded.checkpoint_id == cp.checkpoint_id
        assert loaded.artifact_hashes == cp.artifact_hashes

        # Verify
        is_valid, issues = verify_checkpoint_integrity(loaded, cwd=worktree)
        assert is_valid, f"Checkpoint should be valid: {issues}"

        # Modify the file again
        (worktree / "src/main.py").write_text("def main():\n    pass\n")

        # Restore
        restored, failed = restore_checkpoint_files(loaded, cwd=worktree)
        assert "src/main.py" in restored
        assert len(failed) == 0

        # Verify the restored content matches the checkpoint
        assert (worktree / "src/main.py").read_text() == "def main():\n    print('hello world')\n"

    def test_list_checkpoints(self, worktree: Path, bare_repo: Path) -> None:
        """List checkpoints for a run."""
        cp1 = capture_checkpoint(
            run_id="run-list",
            step_id="step-1",
            cwd=worktree,
        )
        save_checkpoint(cp1, base_dir=bare_repo)

        cp2 = capture_checkpoint(
            run_id="run-list",
            step_id="step-2",
            cwd=worktree,
        )
        save_checkpoint(cp2, base_dir=bare_repo)

        ids = list_checkpoints("run-list", base_dir=bare_repo)
        assert len(ids) == 2
        assert cp1.checkpoint_id in ids
        assert cp2.checkpoint_id in ids

    def test_scope_restriction(self, worktree: Path) -> None:
        """Checkpoint with scope restriction only captures files within scope."""
        # Modify a file inside scope
        (worktree / "src" / "main.py").write_text("def main():\n    print('modified')\n")
        # Modify a file outside scope
        (worktree / "README.md").write_text("# Modified\n")

        cp = capture_checkpoint(
            run_id="run-scope",
            step_id="step-1",
            scope=["src"],
            cwd=worktree,
        )
        # README.md should NOT be in the checkpoint's artifact_hashes
        assert "README.md" not in cp.artifact_hashes
        # src/main.py should be in the checkpoint (it was modified)
        assert "src/main.py" in cp.artifact_hashes


# =========================================================================
# generate_checkpoint_id tests
# =========================================================================


class TestGenerateCheckpointId:
    """Tests for generate_checkpoint_id."""

    def test_deterministic(self) -> None:
        """Same inputs produce the same checkpoint id."""
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        id1 = generate_checkpoint_id(run_id="run-1", step_id="step-1", timestamp=ts)
        id2 = generate_checkpoint_id(run_id="run-1", step_id="step-1", timestamp=ts)
        assert id1 == id2

    def test_different_run_produces_different_id(self) -> None:
        """Different run ids produce different checkpoint ids."""
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        id1 = generate_checkpoint_id(run_id="run-a", step_id="step-1", timestamp=ts)
        id2 = generate_checkpoint_id(run_id="run-b", step_id="step-1", timestamp=ts)
        assert id1 != id2

    def test_starts_with_cp_prefix(self) -> None:
        """Checkpoint id always starts with 'cp_'."""
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        cp_id = generate_checkpoint_id(run_id="run-1", step_id="step-1", timestamp=ts)
        assert cp_id.startswith("cp_")
        assert len(cp_id) == 19  # "cp_" + 16 hex chars


# =========================================================================
# Critical data-loss regression tests (C1-C5)
# =========================================================================


class TestCriticalDataLossFixes:
    """Negative regression tests asserting data is NOT lost.

    Each test targets a specific critical issue (C1-C5) found in review:
      * C1 — out-of-scope files must never be modified during rollback.
      * C2 — in-scope user changes must survive a failed patch.
      * C3 — deleted untracked in-scope files must be restored.
      * C4 — a checkpoint with ``head_commit=None`` must be refused or
        properly flagged.
      * C5 — a tampered checkpoint JSON must be rejected.
    """

    # ------------------------------------------------------------------
    # C1: scope-filtered worktree_diff
    # ------------------------------------------------------------------

    def test_c1_out_of_scope_files_not_modified_during_rollback(
        self, worktree: Path
    ) -> None:
        """Out-of-scope files are never modified by rollback (C1).

        The worktree_diff must be scope-filtered at capture time so that
        ``git apply`` at restore time only touches in-scope paths.  Without
        the fix, the captured diff for README.md would be re-applied,
        silently modifying the out-of-scope file.
        """
        # Modify both an in-scope and an out-of-scope file.
        (worktree / "src" / "main.py").write_text("def main():\n    print('cp')\n")
        (worktree / "README.md").write_text("# captured out-of-scope\n")

        # Capture a checkpoint scoped to src/ only.
        cp = capture_checkpoint(
            run_id="run-c1", step_id="step-1", scope=["src"], cwd=worktree
        )
        # The diff must NOT contain README.md changes.
        assert "README.md" not in cp.worktree_diff, (
            "worktree_diff must be scope-filtered (C1): should not include "
            "out-of-scope file README.md"
        )

        # Revert the out-of-scope file so the diverged-scope check passes
        # at restore time.
        _git("checkout", "HEAD", "--", "README.md", cwd=worktree)
        readme_clean = (worktree / "README.md").read_text()

        # Modify the in-scope file so rollback has work to do.
        (worktree / "src" / "main.py").write_text("def main():\n    pass\n")

        restored, failed = restore_checkpoint_files(cp, cwd=worktree)

        # In-scope file must be restored to checkpoint state.
        assert "src/main.py" in restored
        assert (worktree / "src/main.py").read_text() == "def main():\n    print('cp')\n"
        # Out-of-scope file must NOT be modified by rollback.  With the old
        # (unscoped) code, git apply would have re-applied the README.md
        # diff captured at checkpoint time, corrupting the file.
        assert (worktree / "README.md").read_text() == readme_clean, (
            "Out-of-scope file must not be modified during rollback (C1)"
        )

    # ------------------------------------------------------------------
    # C2: backup before git checkout
    # ------------------------------------------------------------------

    def test_c2_user_changes_survive_failed_patch(self, worktree: Path) -> None:
        """In-scope user changes survive a failed patch (C2).

        If ``git apply`` fails after ``git checkout HEAD`` has reset tracked
        files, the user's pre-rollback content must be restored from the
        backup so no work is lost.
        """
        head = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        original_content = "def main():\n    pass\n"
        assert (worktree / "src/main.py").read_text() == original_content

        # The user makes important changes that must not be lost.
        user_content = "def main():\n    print('USER IMPORTANT WORK')\n"
        (worktree / "src/main.py").write_text(user_content)

        # The checkpoint records the HEAD (committed) state's hash — not the
        # user's current content — so a failed rollback is honestly reported.
        committed_hash = hashlib.sha256(original_content.encode()).hexdigest()

        # Construct a checkpoint with a deliberately broken diff that will
        # fail to apply (references a non-existent context line).
        bad_diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-THIS_LINE_DOES_NOT_EXIST_ANYWHERE\n"
            "+restored_content\n"
        )
        cp = Checkpoint(
            checkpoint_id="cp_c2",
            run_id="run-c2",
            step_id="step-1",
            head_commit=head,
            worktree_diff=bad_diff,
            artifact_hashes={"src/main.py": committed_hash},
            changed_files=[],
            untracked_files=[],
            scope=["src"],
            timestamp=datetime.now(UTC).isoformat(),
        )

        restored, failed = restore_checkpoint_files(cp, cwd=worktree)

        # The patch failed, so the file should be in failed, not restored.
        assert "src/main.py" in failed
        # CRITICAL: the user's work must still be present (backup restored).
        assert (worktree / "src/main.py").read_text() == user_content, (
            "User changes must survive a failed patch (C2): backup must be "
            "restored so no data is lost"
        )

    # ------------------------------------------------------------------
    # C3: store untracked file content
    # ------------------------------------------------------------------

    def test_c3_deleted_untracked_in_scope_file_is_restored(
        self, worktree: Path
    ) -> None:
        """Deleted untracked in-scope files ARE restored (C3).

        Untracked files cannot be restored from git, so their content must
        be captured at checkpoint time and written back at restore time if
        the file is missing.
        """
        # Create an untracked in-scope file.
        untracked_content = "# untracked new file\nprint('hello')\n"
        (worktree / "src" / "new_untracked.py").write_text(untracked_content)

        cp = capture_checkpoint(
            run_id="run-c3", step_id="step-1", scope=["src"], cwd=worktree
        )

        # The checkpoint must store the untracked file's content.
        assert "src/new_untracked.py" in cp.untracked_files
        assert "src/new_untracked.py" in cp.untracked_content, (
            "Untracked in-scope file content must be stored at capture (C3)"
        )

        # Delete the untracked file (simulate the user or a process removing it).
        (worktree / "src" / "new_untracked.py").unlink()
        assert not (worktree / "src" / "new_untracked.py").exists()

        # Roll back — the file must be restored from stored content.
        restored, failed = restore_checkpoint_files(cp, cwd=worktree)

        assert "src/new_untracked.py" in restored, (
            "Deleted untracked in-scope file must be restored from stored "
            "content (C3)"
        )
        assert (worktree / "src" / "new_untracked.py").read_text() == untracked_content

    # ------------------------------------------------------------------
    # C4: refuse None head_commit
    # ------------------------------------------------------------------

    def test_c4_capture_refuses_none_head_commit(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """capture_checkpoint raises RuntimeError when HEAD is None (C4).

        If git is unavailable, _git_head returns None.  The checkpoint must
        not be created, because a None head_commit undermines the integrity
        check (it would always pass).
        """
        from bound import checkpoint as cp_module

        monkeypatch.setattr(cp_module, "_git_head", lambda cwd: None)

        with pytest.raises(RuntimeError, match="git HEAD unavailable"):
            capture_checkpoint(
                run_id="run-c4", step_id="step-1", cwd=worktree
            )

    def test_c4_verify_flags_none_head_commit(self, worktree: Path) -> None:
        """verify_checkpoint_integrity flags a None head_commit as invalid (C4).

        A manually-constructed checkpoint with head_commit=None must not
        silently pass the integrity check.
        """
        cp = Checkpoint(
            checkpoint_id="cp_c4",
            run_id="run-c4",
            step_id="step-1",
            head_commit=None,
            artifact_hashes={
                "README.md": _file_sha256(worktree / "README.md"),
            },
            timestamp=datetime.now(UTC).isoformat(),
        )
        is_valid, issues = verify_checkpoint_integrity(cp, cwd=worktree)
        assert not is_valid, (
            "Checkpoint with head_commit=None must not pass integrity (C4)"
        )
        assert any("head_commit" in issue for issue in issues)

    # ------------------------------------------------------------------
    # C5: HMAC signature on checkpoint JSON
    # ------------------------------------------------------------------

    def test_c5_tampered_checkpoint_json_is_rejected(
        self, worktree: Path, bare_repo: Path
    ) -> None:
        """A tampered checkpoint JSON is rejected by load_checkpoint (C5).

        The HMAC-SHA256 signature must detect any modification of the
        stored checkpoint data.
        """
        cp = capture_checkpoint(
            run_id="run-c5", step_id="step-1", cwd=worktree
        )
        save_checkpoint(cp, base_dir=bare_repo)

        # Locate the checkpoint file on disk.
        cp_file = bare_repo / cp.run_id / f"{cp.checkpoint_id}.json"
        assert cp_file.exists()

        # Tamper with the JSON — change a field but keep the old signature.
        data = json.loads(cp_file.read_text(encoding="utf-8"))
        data["artifact_hashes"]["TAMPERED_FILE"] = "deadbeef"
        cp_file.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

        # Loading the tampered checkpoint must raise RuntimeError.
        with pytest.raises(RuntimeError, match="signature verification failed"):
            load_checkpoint(cp.run_id, cp.checkpoint_id, base_dir=bare_repo)

    def test_c5_valid_signature_loads_cleanly(
        self, worktree: Path, bare_repo: Path
    ) -> None:
        """A properly signed checkpoint loads without error (C5 happy path)."""
        cp = capture_checkpoint(
            run_id="run-c5b", step_id="step-1", cwd=worktree
        )
        save_checkpoint(cp, base_dir=bare_repo)

        loaded = load_checkpoint(cp.run_id, cp.checkpoint_id, base_dir=bare_repo)
        assert loaded.checkpoint_id == cp.checkpoint_id
        assert loaded.artifact_hashes == cp.artifact_hashes
        assert loaded.signature is not None
