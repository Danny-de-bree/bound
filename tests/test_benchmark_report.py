"""Tests for benchmark HTML/JSON reports (benchmark_report.py)."""

from __future__ import annotations

from bound.benchmark import AggregateMetrics, BenchmarkRun, TaskBenchmarkResult
from bound.benchmark_report import _escape, render_html, render_json
from bound.controller_eval import ControllerHealth

# ---------------------------------------------------------------------------
# _escape
# ---------------------------------------------------------------------------


def test_escape_ampersand() -> None:
    """& becomes &amp;."""
    assert _escape("a & b") == "a &amp; b"


def test_escape_less_than() -> None:
    """< becomes &lt;."""
    assert _escape("a < b") == "a &lt; b"


def test_escape_greater_than() -> None:
    """> becomes &gt;."""
    assert _escape("a > b") == "a &gt; b"


def test_escape_combined() -> None:
    """Multiple special chars are all escaped."""
    assert _escape("<script>&</script>") == ("&lt;script&gt;&amp;&lt;/script&gt;")


def test_escape_no_special_chars() -> None:
    """String with no special chars is unchanged."""
    assert _escape("hello world") == "hello world"


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def _make_run() -> BenchmarkRun:
    """Build a minimal BenchmarkRun for testing."""
    return BenchmarkRun(
        run_id="abc123def456",
        suite_name="smoke",
        timestamp="2026-01-01T00:00:00+00:00",
        tasks=[
            TaskBenchmarkResult(
                task_id="task-a",
                accepted=True,
                bound_stop_step=1,
                actual_stop_step=3,
                steps_saved=2,
                tool_calls_saved=5,
                tokens_saved=1000,
                runtime_saved=10.0,
                tests_pass_at_bound_stop=True,
                regressions_after_accept=0,
            ),
            TaskBenchmarkResult(
                task_id="task-b",
                accepted=False,
                steps_saved=None,
                tool_calls_saved=None,
                tokens_saved=None,
                runtime_saved=None,
                tests_pass_at_bound_stop=None,
                regressions_after_accept=0,
            ),
        ],
        aggregate=AggregateMetrics(
            total_tasks=2,
            tasks_accepted=1,
            total_steps_saved=2,
            total_tool_calls_saved=5,
            total_tokens_saved=1000,
            total_runtime_saved=10.0,
            tasks_with_regressions=0,
            acceptance_rate=0.5,
            mean_steps_saved=1.0,
        ),
    )


def test_render_html_contains_doctype() -> None:
    """HTML report starts with DOCTYPE."""
    run = _make_run()
    html = render_html(run)
    assert html.strip().startswith("<!DOCTYPE html>")


def test_render_html_contains_suite_name() -> None:
    """HTML report includes suite name."""
    run = _make_run()
    html = render_html(run)
    assert "smoke" in html


def test_render_html_contains_run_id() -> None:
    """HTML report includes run id."""
    run = _make_run()
    html = render_html(run)
    assert "abc123def456" in html


def test_render_html_contains_task_ids() -> None:
    """HTML report includes per-task rows."""
    run = _make_run()
    html = render_html(run)
    assert "task-a" in html
    assert "task-b" in html


def test_render_html_no_external_css() -> None:
    """HTML report has no external CSS/JS references."""
    run = _make_run()
    html = render_html(run)
    # No external links to CSS/JS
    assert 'href="http' not in html
    assert 'src="http' not in html
    assert "<link" not in html
    assert "<script src" not in html


def test_render_html_includes_aggregate_metrics() -> None:
    """HTML report includes summary metrics."""
    run = _make_run()
    html = render_html(run)
    assert "Total Tasks" in html
    assert "50%" in html  # acceptance rate
    assert "2</div>" in html  # total steps saved


def test_render_html_with_health() -> None:
    """HTML report includes controller health section when health is provided."""
    run = _make_run()
    health = ControllerHealth(
        total_decisions=10,
        correct_decisions=9,
        overall_accuracy=0.9,
        grade="A",
    )
    html = render_html(run, health=health)
    assert "Controller Health" in html
    assert "A</span>" in html


def test_render_html_without_health() -> None:
    """HTML report shows placeholder when health is None."""
    run = _make_run()
    html = render_html(run, health=None)
    assert "not available" in html


def test_render_html_closes_all_tags() -> None:
    """HTML report closes html and body tags."""
    run = _make_run()
    html = render_html(run)
    assert "</html>" in html
    assert "</body>" in html


def test_render_html_escapes_special_chars() -> None:
    """Task IDs with special chars are escaped in HTML."""
    run = BenchmarkRun(
        run_id="test",
        suite_name="test",
        timestamp="2026-01-01T00:00:00+00:00",
        tasks=[
            TaskBenchmarkResult(
                task_id="<script>alert(1)</script>",
                accepted=True,
                steps_saved=0,
            ),
        ],
        aggregate=AggregateMetrics(total_tasks=1, tasks_accepted=1),
    )
    html = render_html(run)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------


def test_render_json_is_valid_json() -> None:
    """render_json returns parseable JSON."""
    import json

    run = _make_run()
    result = render_json(run)
    parsed = json.loads(result)

    assert parsed["report_version"] == "1.0.0"
    assert parsed["run"]["run_id"] == "abc123def456"


def test_render_json_includes_health_when_provided() -> None:
    """JSON report includes controller_health when provided."""
    import json

    run = _make_run()
    health = ControllerHealth(
        total_decisions=5,
        correct_decisions=5,
        overall_accuracy=1.0,
        grade="A",
    )
    result = render_json(run, health=health)
    parsed = json.loads(result)

    assert "controller_health" in parsed
    assert parsed["controller_health"]["grade"] == "A"


def test_render_json_no_health_when_none() -> None:
    """JSON report omits controller_health when None."""
    import json

    run = _make_run()
    result = render_json(run, health=None)
    parsed = json.loads(result)

    assert "controller_health" not in parsed


def test_render_json_pretty_printed() -> None:
    """JSON output is indented with 2 spaces."""
    run = _make_run()
    result = render_json(run)
    lines = result.split("\n")
    # Second line should start with 2 spaces
    assert lines[1].startswith("  ")


def test_render_json_aggregate_metrics() -> None:
    """JSON includes aggregate metrics verbatim."""
    import json

    run = _make_run()
    result = render_json(run)
    parsed = json.loads(result)
    agg = parsed["run"]["aggregate"]
    assert agg["total_tasks"] == 2
    assert agg["acceptance_rate"] == 0.5
    assert agg["total_steps_saved"] == 2
