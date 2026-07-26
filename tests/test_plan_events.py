"""Integration tests for plan events in the lineage store (Step 3).

Verifies that:
1. LineageStore.load_plan_snapshot emits a PlanLoadedEvent.
2. Plan snapshot metadata is stored in run.json.
3. No plan found returns None cleanly.
4. The event appears in the run log.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bound.lineage import PlanLoadedEvent
from bound.lineage_store import LineageStore


def _write_plan(tmpdir: Path, content: str, name: str = "plan.md") -> Path:
    plan_path = tmpdir / name
    plan_path.write_text(content, encoding="utf-8")
    return plan_path


class TestLoadPlanSnapshot:
    """Tests for LineageStore.load_plan_snapshot."""

    def test_no_plan_returns_none(self) -> None:
        """When no plan.md exists, load_plan_snapshot returns None."""
        with tempfile.TemporaryDirectory() as td:
            store = LineageStore(base_dir=Path(td) / ".bound" / "runs")
            ev = store.start_run("Test task")
            result = store.load_plan_snapshot(ev.run_id, project_dir=td)
            assert result is None

    def test_plan_emits_event(self) -> None:
        """A found plan emits a plan.loaded event."""
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# Goal\n\n## Step\n- [ ] Task\n")
            store = LineageStore(base_dir=Path(td) / ".bound" / "runs")
            ev = store.start_run("Test task")
            result = store.load_plan_snapshot(ev.run_id, project_dir=td)
            assert result is not None

            log = store.read_run(ev.run_id)
            plan_events = [e for e in log.events if isinstance(e, PlanLoadedEvent)]
            assert len(plan_events) == 1
            assert plan_events[0].plan_id == result.plan_id
            assert plan_events[0].content_hash == result.hash

    def test_plan_stored_in_run_meta(self) -> None:
        """Plan snapshot metadata is persisted in run.json."""
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# Goal\n\n## Step\n- [ ] Task\n")
            store = LineageStore(base_dir=Path(td) / ".bound" / "runs")
            ev = store.start_run("Test task")
            result = store.load_plan_snapshot(ev.run_id, project_dir=td)
            assert result is not None

            # Read run.json directly
            run_meta_path = Path(td) / ".bound" / "runs" / ev.run_id / "run.json"
            meta = json.loads(run_meta_path.read_text())
            assert "plan_snapshot" in meta
            assert meta["plan_snapshot"]["plan_id"] == result.plan_id
            assert meta["plan_snapshot"]["hash"] == result.hash

    def test_explicit_path_works(self) -> None:
        """An explicit path bypasses discovery."""
        with tempfile.TemporaryDirectory() as td:
            _write_plan(Path(td), "# Custom\n\n## S\n- [ ] T\n", "custom.md")
            _write_plan(Path(td), "# Root\n\n## R\n- [ ] T\n")
            store = LineageStore(base_dir=Path(td) / ".bound" / "runs")
            ev = store.start_run("Test task")
            result = store.load_plan_snapshot(
                ev.run_id,
                project_dir=td,
                explicit_path="custom.md",
            )
            assert result is not None
            assert result.goal == "Custom"
