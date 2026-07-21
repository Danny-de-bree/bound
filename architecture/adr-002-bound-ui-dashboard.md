# ADR-002: `bound ui` — Local Read-Only Dashboard Architecture

**Status:** Accepted (v0.8.0 Sprint 1)
**Date:** 2026-07-20
**Author:** UI Developer
**Source:** [task_0019 / S1-ARCH-1]

## Context

BOUND v0.7.1 ships `bound inspect --html` which renders a single-run decision
timeline as a self-contained HTML file. Sprint 1 ("Make it visible") requires a
**live local dashboard** that:

1. Shows **all** local runs at a glance, not one at a time.
2. Displays task, status, latest decision, assurance level, and timing for each run.
3. Opens one run as a **plan → step → attempt → decision** tree.
4. Shows **candidate versus final decision** (the assurance gate).
5. Renders evidence provenance badges clearly: `VERIFIED`, `CLAIMED`, `MISSING`,
   `INVALID`, `STALE`, `UNVERIFIED`.
6. **Highlights the exact evidence or gate** that caused a `RETRY`, `REPLAN`,
   or `ROLLBACK`.
7. Remains **local and read-only** — no hosted backend, no account, no data
   leaves the machine.
8. Achieves **<1s refresh latency** on the local loopback.

The existing `bound inspect --html` renderer already builds the Step → Attempt →
Outcome tree with provenance colouring. The dashboard must **build on that
renderer** rather than duplicating its logic.

## Decision

Create a **single-file HTTP server** (`bound/ui.py`) that serves two HTML pages
and two JSON API endpoints using only Python's standard library
(`http.server.HTTPServer` / `BaseHTTPRequestHandler`). No web framework, no
build step, no external assets.

### Architecture diagram

```text
┌─────────────────────────────────────────────────┐
│  bound ui (CLI entry point in bound/cli.py)     │
│  ┌─────────────────────────────────────────────┐│
│  │  serve()  ← port, open_browser, run_id      ││
│  │  ┌──────────────────────────────────────┐   ││
│  │  │  HTTPServer (127.0.0.1:8765)         │   ││
│  │  │  ┌────────────────────────────────┐  │   ││
│  │  │  │  _DashboardHandler (do_GET)    │  │   ││
│  │  │  │  ┌──────────────────────────┐  │  │   ││
│  │  │  │  │  /        → overview HTML│  │  │   ││
│  │  │  │  │  /run/<id> → detail HTML │  │  │   ││
│  │  │  │  │  /api/runs → JSON list   │  │  │   ││
│  │  │  │  │  /api/run/<id> → JSON    │  │  │   ││
│  │  │  │  └──────────────────────────┘  │  │   ││
│  │  │  └────────────────────────────────┘  │   ││
│  │  └──────────────────────────────────────┘   ││
│  └─────────────────────────────────────────────┘│
│                                                  │
│  Reuses from bound/cli.py:                       │
│  · _RunAuditIndex  — groups audit events by step │
│  · _DECISION_COLORS / _PROVENANCE_COLORS — CSS   │
│  · _html_escape / _fmt_dt / _sv — helpers        │
│  · _INDEPENDENTLY_VERIFIED — provenance set      │
│                                                  │
│  Reads from bound/lineage_store.py:              │
│  · LineageStore.list_runs()  → RunSummary[]      │
│  · LineageStore.read_run()   → RunLog            │
└─────────────────────────────────────────────────┘
```

### HTTP Server

| Aspect              | Choice                                   |
|---------------------|------------------------------------------|
| Framework           | `http.server.HTTPServer` (stdlib)        |
| Handler             | `BaseHTTPRequestHandler` subclass        |
| Host                | `127.0.0.1` only (no remote binding)     |
| Default port        | `8765`                                   |
| Protocol            | HTTP/1.0 (stdlib default)                |
| Concurrency         | Synchronous, single-threaded (sufficient for local read-only) |

**Rationale for stdlib only:**

- Zero dependencies beyond what BOUND already requires.
- The dashboard is read-only and local; concurrent-write pressure is absent.
- A framework (FastAPI, Flask) would add startup latency and a dependency chain
  for a trivial routing surface (4 routes).
- If a future Sprint needs async/SSE for live-updates, the handler can be
  swapped for `aiohttp` without changing the HTML rendering functions.
### API Endpoints

| Route              | Method | Response Type | Description                              |
|--------------------|--------|---------------|------------------------------------------|
| `/`                | GET    | `text/html`   | Run overview page (cards + table)        |
| `/run/<run_id>`    | GET    | `text/html`   | Single-run detail page (decision tree)   |
| `/api/runs`        | GET    | `application/json` | JSON list of all run summaries      |
| `/api/run/<run_id>`| GET    | `application/json` | Full run log as JSON (run + steps + evaluations + outcomes + events) |

All responses include `Cache-Control: no-cache, no-store, must-revalidate` to
ensure the browser always fetches fresh data from the append-only event log.

### HTML rendering strategy

The dashboard reuses the same rendering helpers as `bound inspect --html`:

```
bound/cli.py                  bound/ui.py
─────────────                 ────────────
_RunAuditIndex    ──────────→ _iter_latest_decisions()
                                 _render_overview_page()
_DECISION_COLORS  ──────────→  _status_badge()
_PROVENANCE_COLORS             _assurance_badge()
_html_escape()                  _evidence_status_badge()
_fmt_dt()                      _render_run_detail()
_sv()
_INDEPENDENTLY_VERIFIED
```

**Overview page** (`_render_overview_page`):

- Header with BOUND branding and store path.
- Empty state ("No BOUND runs yet") when no runs exist.
- Run cards (clickable `<a>` elements) in a CSS grid, each showing:
  - Status badge (started/completed/interrupted/failed colour-coded).
  - Task name (truncated to 80 characters).
  - Shortened run ID, start time, step count.
- Table view below the cards for quick scanning: run ID, task, status, steps,
  started, finished.
- Footer: "No data leaves your machine."

**Run detail page** (`_render_run_detail`):

- Back-navigation link to overview.
- Run metadata header: run ID, status badge, start/finish time, policy
  identifier and hash, evidence coverage summary (independently verified / total).
- Evidence provenance legend (colour-coded badges for each provenance level).
- Step sections, each containing:
  - Step header with contract ID, step ID, status.
  - Attempt boxes, each showing:
    - Decision badge (ACCEPT green, RETRY orange, REPLAN blue, ROLLBACK red).
    - Attempt number and score.
    - Evidence rows: provenance badge + evidence status badge + check label.
    - **Trigger highlight** when the decision was RETRY/REPLAN/ROLLBACK:
      "Blocking evidence missing/invalid" or "Critical check failed" with
      reason codes.
    - Decision gate: candidate decision → final decision + assurance level.
    - Outcome: decision → next_action.
- Raw events accordion: JSON-serialised append-only event log for debugging.
- Auto-refresh via `<meta http-equiv='refresh' content='5'>` every 5 seconds.

### Refresh mechanism

The detail page includes a `<meta http-equiv='refresh' content='5'>` tag that
causes the browser to re-fetch the entire page every 5 seconds. This is
sufficient because:

1. The lineage store is **append-only** — events are never rewritten.
2. Re-rendering from the stored `RunLog` is fast (<100ms for typical runs).
3. The 5-second interval keeps the dashboard current without polling pressure.
4. The `Cache-Control: no-cache` header ensures the browser always re-fetches.

**Future option:** A lightweight `/api/run/<id>/events/since?seq=N` endpoint
could reduce bandwidth, but the current approach meets the <1s refresh latency
target on localhost without needing it.

### Integration with the lineage store

The dashboard reads from the **same append-only store** that `bound run start` /
`bound evaluate` / `bound outcome` write to:

```
.bound/runs/
├── <run_id>/
│   ├── run.json          ← metadata (status, task, timestamps)
│   └── events.jsonl      ← append-only event stream
│
└── ... (one directory per run)
```

The `LineageStore` class (`bound/lineage_store.py`) provides:

- `store.list_runs()` → `list[RunSummary]` — lightweight metadata for the
  overview page.
- `store.read_run(run_id, strict=False)` → `RunLog` — full replay for the
  detail page, including `run`, `steps`, `evaluations`, `outcomes`, and
  raw `events`.

The dashboard never writes to the store. It calls these methods on every HTTP
request (no caching layer), which is acceptable because:

- `list_runs()` reads one small JSON file per run directory.
- `read_run()` replays the events.jsonl file in memory (typically <500 events).
- Both operations complete in under 200ms for stores with up to 200 runs.

### CLI integration

The `bound ui` CLI command is defined in `bound/cli.py`:

```python
def _run_ui(args: argparse.Namespace) -> int:
    from bound.ui import serve
    serve(port=args.port, open_browser=args.open_browser, run_id=args.run_id)
    return 0
```

Arguments:

| Argument       | Default | Description                                      |
|----------------|---------|--------------------------------------------------|
| `RUN_ID`       | None    | Optional run id to open directly on detail page  |
| `--port`       | 8765    | TCP port for the HTTP server                     |
| `--open`       | False   | Open the dashboard URL in the default browser    |

Usage examples:

```bash
bound ui                          # overview page at http://127.0.0.1:8765
bound ui --open                   # overview page + browser launch
bound ui --port 8888              # custom port
bound ui run_abc123               # open directly to run_abc123 detail
bound ui run_abc123 --open        # detail page + browser launch
```

### Port collision handling

When the port is already in use, the server catches `OSError` and prints an
actionable message:

```
error: port 8765 is already in use.
       Try a different port: bound ui --port 8766
       Or kill the process using port 8765:
         lsof -ti tcp:8765 | xargs kill
       (the dashboard needs a free port to start)
```

### Startup redirect

When a `run_id` is supplied on the CLI, the server sets a one-shot
`startup_redirect` class attribute on the handler. The first request to `/`
returns a 302 redirect to `/run/<run_id>` instead of the overview page.
Subsequent requests serve the normal overview.

### Evidence status badges

| Status       | Colour   | Meaning                                 |
|--------------|----------|-----------------------------------------|
| `VERIFIED`   | `#2e7d32`| Independently verified by BOUND collector|
| `CLAIMED`    | `#c62828`| Agent self-report, not independently verified|
| `MISSING`    | `#9e9e9e`| Expected evidence was not collected     |
| `INVALID`    | `#d32f2f`| Collected evidence failed validation    |
| `STALE`      | `#f57c00`| Evidence is from a previous (stale) state|
| `UNVERIFIED` | `#9e9e9e`| Evidence exists but not yet verified    |

### Decision colours

| Decision    | Colour   |
|-------------|----------|
| `ACCEPT`    | `#2e7d32`|
| `RETRY`     | `#ef6c00`|
| `REPLAN`    | `#1565c0`|
| `ROLLBACK`  | `#c62828`|

### Trigger highlighting

When a step's latest decision is `RETRY`, `REPLAN`, or `ROLLBACK`, the
dashboard highlights the blocking evidence or gate:

- **RETRY** → "Blocking evidence missing/invalid" + reason codes.
- **REPLAN** → "Blocking evidence missing/invalid" + reason codes.
- **ROLLBACK** → "Critical check failed" + reason codes.

The highlight is rendered as a prominent `<div class='trigger-highlight'>`
directly inside the attempt box, making it visible at a glance.

### Test coverage

Tests live in `tests/test_ui_visual.py` and cover:

1. **Determinism**: identical inputs produce identical HTML output.
2. **HTML structure**: DOCTYPE, `<html>`, `<title>`, structural elements.
3. **Content stability**: run cards, decision tree, evidence badges, links.
4. **Empty state**: "No BOUND runs yet" message.
5. **Populated state**: correct rendering of multiple runs with different statuses.

## Consequences

### Positive

1. **Zero external dependencies** — the dashboard uses only Python stdlib.
2. **Builds on existing code** — reuses `_RunAuditIndex`, colour maps, helpers.
3. **Fast startup** — `bound ui` is ready to serve in <200ms.
4. **Read-only by construction** — the handler never calls store write methods.
5. **Append-only refresh** — the 5-second meta-refresh is simple and reliable
   for a local dashboard.
6. **Portable** — works on any Python 3.11+ system without npm, webpack, or
   a browser extension.
7. **Testable** — HTML rendering functions are pure functions: `RunLog` in,
   `str` out.

### Negative

1. **No live-push** — the meta-refresh approach means a full page reload every
   5 seconds, which is not suitable for real-time agent monitoring at sub-second
   granularity.
2. **Single-threaded** — one slow request blocks subsequent requests (acceptable
   for local single-user use).
3. **No SSE/WebSocket** — the dashboard cannot push updates; the client must
   poll.

### Risks

| Risk | Mitigation |
|------|------------|
| Large event logs slow down rendering | `read_run(strict=False)` skips corrupt lines; retention pruning (`enforce_retention`) limits total runs |
| Port conflicts with other tools | Clear error message with actionable alternative port + kill command |
| Browser caching stale HTML | `Cache-Control: no-cache, no-store, must-revalidate` on every response |
| Security: binding to 0.0.0.0 | Server is hard-coded to `127.0.0.1` only |

## References

- `src/bound/ui.py` — the dashboard implementation
- `src/bound/cli.py` — CLI adapter (`_run_ui`, `_render_inspect_html`, `_RunAuditIndex`)
- `src/bound/lineage_store.py` — `LineageStore`, `RunSummary`, `RunLog`
- `src/bound/lineage.py` — lineage event schema
- `tests/test_ui_visual.py` — visual regression tests
- `architecture/adr-001-cli-mcp-hooks-ui.md` — parent ADR for the service layer
- `todo.md` — Sprint 1 tasks (S1-UI-1 through S1-UI-4)