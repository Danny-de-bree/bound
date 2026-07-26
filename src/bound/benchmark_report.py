"""Self-contained HTML and JSON benchmark reports.

Produces single-file HTML reports with no external CSS/JS dependencies.
All styling is embedded via stdlib string templating.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from bound.benchmark import BenchmarkRun
from bound.controller_eval import ControllerHealth


def _escape(s: str) -> str:
    """Escape a string for safe HTML inclusion."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_task_rows(tasks: list) -> str:
    """Render HTML table rows for per-task results."""
    rows: list[str] = []
    for t in tasks:
        accepted_icon = "&#x2705;" if t.accepted else "&#x274C;"
        steps = str(t.steps_saved) if t.steps_saved is not None else "n/a"
        tools = str(t.tool_calls_saved) if t.tool_calls_saved is not None else "n/a"
        tokens = str(t.tokens_saved) if t.tokens_saved is not None else "n/a"
        runtime = f"{t.runtime_saved:.1f}s" if t.runtime_saved is not None else "n/a"
        tests = (
            "n/a"
            if t.tests_pass_at_bound_stop is None
            else ("yes" if t.tests_pass_at_bound_stop else "no")
        )
        regr = str(t.regressions_after_accept)
        rows.append(
            f"<tr>"
            f"<td>{_escape(t.task_id)}</td>"
            f"<td>{accepted_icon}</td>"
            f"<td>{steps}</td>"
            f"<td>{tools}</td>"
            f"<td>{tokens}</td>"
            f"<td>{runtime}</td>"
            f"<td>{tests}</td>"
            f"<td>{regr}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _render_health_section(health: ControllerHealth | None) -> str:
    """Render the controller health section as HTML."""
    if health is None:
        return "<p><em>Controller health data not available for this run.</em></p>"

    def _pct(v: float) -> str:
        return f"{v * 100:.1f}%"

    grade_color = {
        "A": "#2e7d32",
        "B": "#558b2f",
        "C": "#f9a825",
        "D": "#e65100",
        "F": "#c62828",
    }.get(health.grade, "#666")

    parts: list[str] = []
    parts.append('<div class="health-card">')
    parts.append(
        f"<h3>Controller Health: "
        f'<span style="color:{grade_color};font-size:2em;">{health.grade}</span>'
        f"</h3>"
    )
    parts.append("<table>")
    parts.append("<tr><th>Metric</th><th>Value</th></tr>")
    parts.append(f"<tr><td>Total Decisions</td><td>{health.total_decisions}</td></tr>")
    parts.append(f"<tr><td>Correct</td><td>{health.correct_decisions}</td></tr>")
    parts.append(f"<tr><td>Overall Accuracy</td><td>{_pct(health.overall_accuracy)}</td></tr>")
    parts.append(f"<tr><td>False ACCEPT Rate</td><td>{_pct(health.false_accept_rate)}</td></tr>")
    parts.append(f"<tr><td>False RETRY Rate</td><td>{_pct(health.false_retry_rate)}</td></tr>")
    parts.append(f"<tr><td>False REPLAN Rate</td><td>{_pct(health.false_replan_rate)}</td></tr>")
    parts.append(
        f"<tr><td>False ROLLBACK Rate</td><td>{_pct(health.false_rollback_rate)}</td></tr>"
    )
    replay = "&#x2705; Passed" if health.deterministic_replay_passed else "&#x274C; Failed"
    consist = "&#x2705; Passed" if health.policy_consistency_passed else "&#x274C; Failed"
    parts.append(f"<tr><td>Deterministic Replay</td><td>{replay}</td></tr>")
    parts.append(f"<tr><td>Policy Consistency</td><td>{consist}</td></tr>")
    parts.append("</table>")
    parts.append("</div>")
    return "\n".join(parts)


def _render_html_template(
    run: BenchmarkRun,
    task_rows: str,
    health_html: str,
) -> str:
    """Render the full HTML document template."""
    a = run.aggregate
    generated = datetime.now(UTC).isoformat()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOUND Benchmark — {_escape(run.suite_name)} — {run.run_id}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 960px; margin: 0 auto; padding: 2em;
    color: #222; background: #fafafa;
  }}
  h1 {{ border-bottom: 2px solid #1a73e8; padding-bottom: 0.3em; }}
  h2 {{ margin-top: 2em; color: #1a73e8; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  th {{ background: #1a73e8; color: white; }}
  tr:nth-child(even) {{ background: #f2f2f2; }}
  .summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1em;
  }}
  .metric {{
    background: white; border: 1px solid #ddd;
    border-radius: 8px; padding: 1em; text-align: center;
  }}
  .metric .value {{ font-size: 2em; font-weight: bold; color: #1a73e8; }}
  .metric .label {{ font-size: 0.85em; color: #666; }}
  .health-card {{
    background: white; border: 1px solid #ddd;
    border-radius: 8px; padding: 1.5em; margin: 1em 0;
  }}
  .footer {{
    margin-top: 3em; font-size: 0.8em; color: #999;
    border-top: 1px solid #ddd; padding-top: 1em;
  }}
  .bar {{ height: 20px; background: #e0e0e0; border-radius: 10px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 10px; }}
</style>
</head>
<body>

<h1>BOUND Benchmark Report</h1>
<p>
  Suite: <strong>{_escape(run.suite_name)}</strong> &middot;
  Run: <code>{run.run_id}</code> &middot;
  Timestamp: {run.timestamp}
</p>

<h2>Summary</h2>
<div class="summary">
  <div class="metric">
    <div class="value">{a.total_tasks}</div>
    <div class="label">Total Tasks</div>
  </div>
  <div class="metric">
    <div class="value">{a.tasks_accepted}</div>
    <div class="label">Tasks Accepted</div>
  </div>
  <div class="metric">
    <div class="value">{a.acceptance_rate * 100:.0f}%</div>
    <div class="label">Acceptance Rate</div>
  </div>
  <div class="metric">
    <div class="value">{a.total_steps_saved}</div>
    <div class="label">Total Steps Saved</div>
  </div>
  <div class="metric">
    <div class="value">{a.total_tool_calls_saved}</div>
    <div class="label">Tool Calls Saved</div>
  </div>
  <div class="metric">
    <div class="value">{a.total_tokens_saved:,}</div>
    <div class="label">Tokens Saved</div>
  </div>
  <div class="metric">
    <div class="value">{a.total_runtime_saved:.1f}s</div>
    <div class="label">Runtime Saved</div>
  </div>
  <div class="metric">
    <div class="value">{a.mean_steps_saved:.1f}</div>
    <div class="label">Mean Steps Saved / Task</div>
  </div>
</div>

{health_html}

<h2>Per-Task Results</h2>
<table>
  <thead>
    <tr>
      <th>Task</th>
      <th>Accepted</th>
      <th>Steps Saved</th>
      <th>Tool Calls Saved</th>
      <th>Tokens Saved</th>
      <th>Runtime Saved</th>
      <th>Tests Pass @ Stop</th>
      <th>Regressions</th>
    </tr>
  </thead>
  <tbody>
{task_rows}
  </tbody>
</table>

<h2>Efficiency</h2>
<div>
  <p>Acceptance Rate</p>
  <div class="bar"><div class="bar-fill" style="width:{a.acceptance_rate * 100:.0f}%;background:#1a73e8;"></div></div>
  <p>Tasks with Regressions: {a.tasks_with_regressions} / {a.total_tasks}</p>
</div>

<div class="footer">
  Generated: {generated} &middot; BOUND Benchmark v1.0.0
</div>

</body>
</html>"""


def render_html(
    run: BenchmarkRun,
    *,
    health: ControllerHealth | None = None,
) -> str:
    """Render a self-contained single-file HTML benchmark report.

    No external CSS/JS dependencies. All styling is embedded.

    Args:
        run: The :class:`BenchmarkRun` to report on.
        health: Optional :class:`ControllerHealth` from a controller evaluation.

    Returns:
        A complete HTML document as a string.
    """
    task_rows = _render_task_rows(run.tasks)
    health_html = _render_health_section(health)
    return _render_html_template(run, task_rows, health_html)


def render_json(
    run: BenchmarkRun,
    *,
    health: ControllerHealth | None = None,
) -> str:
    """Render a machine-consumable JSON benchmark report.

    Args:
        run: The :class:`BenchmarkRun` to report on.
        health: Optional :class:`ControllerHealth`.

    Returns:
        A pretty-printed JSON string.
    """
    payload: dict[str, object] = {
        "report_version": "1.0.0",
        "generated": datetime.now(UTC).isoformat(),
        "run": run.model_dump(),
    }
    if health is not None:
        payload["controller_health"] = health.model_dump()
    return json.dumps(payload, indent=2, default=str)


__all__ = [
    "render_html",
    "render_json",
]
