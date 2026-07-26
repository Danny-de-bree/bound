# v0.9.0 — BOUND Runtime UI · Design Specification

> **Status:** In Progress  
> **Target:** v0.9.0 ("Stable Runtime")  
> **Role:** Read-only execution observability dashboard  

---

## 1. Goal

The UI must become the **primary way to understand what a coding agent is doing while it is running**.

The first milestone is **observability**, not configuration. Users should open the dashboard and immediately answer:

- *What is the agent doing right now?*
- *Has the current step passed or failed?*
- *How many attempts have been made?*
- *What evidence supports the most recent decision?*

---

## 2. Design Principles

| # | Principle | Rationale |
|---|-----------|-----------|
| **P1** | Show **what is happening now** first | The current action is the most important signal; it belongs at the top |
| **P2** | Decisions understandable in <5 seconds | Color + icon + one-line reason — no scrolling required |
| **P3** | Timeline is primary (~70% of viewport) | Execution flow tells the full story; everything else supports it |
| **P4** | Evidence is secondary (but always visible) | Tests, lint, typecheck status must be glanceable at all times |
| **P5** | Raw lineage hidden by default | JSON event dumps are for debugging, not for runtime monitoring |
| **P6** | Read-only in v0.9.0 | No policy editing, no configuration — pure observation |
| **P7** | No external dependencies | Served from localhost; zero CDN assets; works fully offline |
| **P8** | Collapse secondary information | Run details (policy hash, workspace path, IDs) folded away during active runs |

---

## 3. Screen Layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  HEADER BAR                                                   [port] [v0.9] │
│  Task title · Status badge · Policy name                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  ╔═════════════════════════════════════════════════════════════════════════╗ │
│  ║  CURRENT ACTION                                          ⚡ LIVE        ║ │
│  ║                                                                         ║ │
│  ║  🔄  Running pytest --cov src/bound tests/                              ║ │
│  ║      Step PHASE-003 · Attempt 2 · 00:01:23 elapsed                      ║ │
│  ╚═════════════════════════════════════════════════════════════════════════╝ │
├─────────────────────────────────────────────────────────────────────────────┤
│  SUMMARY CARDS                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ ACCEPT   │  │ HIGH     │  │    4     │  │    2     │  │ 00:05:42 │     │
│  │ Decision │  │Assurance │  │  Steps   │  │Attempts  │  │ Duration │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
├───────────────────────────────────────────────────────┬─────────────────────┤
│                                                       │                     │
│  LIVE TIMELINE (~70% width)                          │  RUN DETAILS ▾      │
│                                                       │  (collapsed)        │
│  ● 14:32:01  Run started                              │                     │
│     Task: "Implement user authentication"             │  Policy: default    │
│     Policy: bound-policy.yaml (hash: a1b2c3d4)       │  Workspace: ...     │
│                                                       │  Checkpoints: 2     │
│  ● 14:32:05  Step PHASE-001 started                   │  Artifacts: 3       │
│     Contract: test_coverage ≥ 80%                     │                     │
│                                                       │  [Expand ▸]         │
│  ● 14:32:45  Evidence collected                       │                     │
│     ✅ test_coverage: 92% (threshold: 80%)            │                     │
│     ✅ lint: 0 errors                                 │                     │
│     ✅ typecheck: passed                              │                     │
│                                                       │                     │
│  ● 14:32:46  ACCEPT · score 0.92 · assurance HIGH     │                     │
│     Reason: All evidence above threshold              │                     │
│                                                       │                     │
│  ● 14:33:00  Step PHASE-002 started                   │                     │
│     Contract: build_passes == true                    │                     │
│                                                       │                     │
│  ● 14:33:12  Evidence collected                       │                     │
│     ❌ build: exit code 1 (expected: 0)               │                     │
│                                                       │                     │
│  ● 14:33:13  RETRY · score 0.23 · assurance LOW       │                     │
│     Reason: Build failed — agent must fix             │                     │
│                                                       │                     │
│  ● 14:33:14  REPLAN · forking Candidate B             │                     │
│     Reason: Agent changed approach                    │                     │
│                                                       │                     │
│  ● 14:34:00  Step PHASE-002 · Attempt 2 started       │                     │
│     ...                                               │                     │
│                                                       │                     │
│  ○ 14:35:00  (live — waiting for agent response...)   │                     │
│                                                       │                     │
├───────────────────────────────────────────────────────┴─────────────────────┤
│  EVIDENCE SUMMARY (always visible)                                          │
│                                                                             │
│  Tests ✅ 92%   Lint ✅ 0    Typecheck ✅   Build ❌ (1)   Risk MEDIUM      │
│  ─────────────────────────────────────────────────────────────────────────  │
│  Coverage: 92% (threshold: 80%) · 142 passed · 0 failed · 0 skipped         │
├─────────────────────────────────────────────────────────────────────────────┤
│  RAW LINEAGE ▸ (collapsed by default — click to expand JSON event log)      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Breakdown

### 4.1 Header Bar

| Element | Description | Source |
|---------|-------------|--------|
| Task title | First line of the run's task description | `run.started.task` |
| Status badge | Colored pill: `running` / `completed` / `interrupted` / `failed` | `run.finished.status` |
| Policy name | Config file basename (e.g., `bound-policy.yaml`) | `run.started.metadata.policy` |
| Port indicator | Shows `localhost:8765` for easy sharing | Static |
| Version | `v0.9.0` | `__version__` |

**Status badge colors:**

| Status | Color | Hex |
|--------|-------|-----|
| `running` | Blue | `#1565c0` |
| `completed` | Green | `#2e7d32` |
| `interrupted` | Amber | `#f57c00` |
| `failed` | Red | `#c62828` |

### 4.2 Current Action Bar

The most prominent element on the page. Always shows the **latest event** with:

- **Icon** + **Action description** (e.g., `🔄 Running pytest...`, `⏳ Waiting for agent...`, `✅ ACCEPT — step complete`)
- **Context line**: Step ID · Attempt number · Elapsed time
- **Live indicator**: Pulsing green dot + "LIVE" badge when a run is active
- **Background**: Subtle gradient or tint that reflects the current state

**States:**

| State | Icon | Label | Color |
|-------|------|-------|-------|
| Collecting evidence | 🔄 | Running `<collector>`... | Blue |
| Evaluating | 🧮 | Evaluating evidence... | Blue |
| Waiting for agent | ⏳ | Waiting for agent response... | Amber |
| ACCEPT | ✅ | ACCEPT — `<step_id>` complete | Green |
| RETRY | 🔁 | RETRY — agent fixing... | Amber |
| REPLAN | 🌿 | REPLAN — forking candidate... | Purple |
| ROLLBACK | ⏪ | ROLLBACK — restoring checkpoint... | Orange |
| Idle (no active run) | 💤 | No active run — select a run to monitor | Grey |

### 4.3 Summary Cards

Five cards in a horizontal row, each showing a single key metric:

| Card | Value | Source |
|------|-------|--------|
| **Decision** | Last decision: ACCEPT / RETRY / REPLAN / ROLLBACK / — | `decision_gated.decision` |
| **Assurance** | Last assurance level | `decision_gated.assurance` |
| **Steps** | Count of unique `step_id`s started | `step_started` events |
| **Attempts** | Total retry count across all steps | Sum of `attempt` field deltas |
| **Duration** | Wall clock from `run_started` → now (or `run_finished`) | Computed client-side |

**Decision card colors:**

| Decision | Color | Hex |
|----------|-------|-----|
| ACCEPT | Green | `#2e7d32` |
| RETRY | Amber | `#f57c00` |
| REPLAN | Purple | `#7b1fa2` |
| ROLLBACK | Orange | `#e65100` |
| — (none) | Grey | `#9e9e9e` |

### 4.4 Live Timeline

The **primary visual element** (~70% of page width). Renders every event as a chronologically ordered entry.

**Entry anatomy:**
```
 ●  14:32:45  Evidence collected
     ✅ test_coverage: 92% (threshold: 80%)
     ✅ lint: 0 errors
     ✅ typecheck: passed
```

| Part | Content |
|------|---------|
| Dot | `●` for past events, `○` (pulsing) for the current/live event |
| Timestamp | `HH:MM:SS` in local time |
| Event type | Bold: `Run started`, `Step started`, `Evidence collected`, etc. |
| Details | Evidence line-items with ✅/❌/— indicators |

**Event type styling:**

| Event | Icon | Left border accent |
|-------|------|--------------------|
| `run.started` | 🚀 | Blue `#1565c0` |
| `run.finished` | 🏁 | Green `#2e7d32` |
| `step.started` | 📋 | Blue `#1565c0` |
| `evidence.collected` | 🔍 | Grey `#616161` |
| `evidence.collection_failed` | ⚠️ | Red `#c62828` |
| `decision.gated` (ACCEPT) | ✅ | Green `#2e7d32` |
| `decision.gated` (RETRY) | 🔁 | Amber `#f57c00` |
| `decision.gated` (REPLAN) | 🌿 | Purple `#7b1fa2` |
| `decision.gated` (ROLLBACK) | ⏪ | Orange `#e65100` |
| `checkpoint.captured` | 💾 | Blue `#1565c0` |

**Evidence line-item indicators:**

| Indicator | Meaning |
|-----------|---------|
| `✅` | Check passed (value meets threshold) |
| `❌` | Check failed |
| `—` | Check not applicable / skipped |
| `⏳` | Check still running (live) |

**Auto-scroll:** Timeline scrolls to the latest event automatically when a run is active. Manual scrolling pauses auto-scroll; a "↓ Jump to live" button appears.

### 4.5 Run Details Panel

Collapsible sidebar (~30% width when expanded, ~40px when collapsed). Contains secondary metadata useful for debugging but not critical during an active run.

| Section | Content |
|---------|---------|
| Policy | Policy name, version, hash (truncated to 8 chars) |
| Workspace | Git worktree path or repo root |
| Checkpoints | Count + list of checkpoint IDs with timestamps |
| Artifacts | Count + file names |
| Run ID | Full UUID (copyable on click) |
| Candidate ID | If branched |

**Default state:** Collapsed during active runs. Auto-expands when a run completes.

### 4.6 Evidence Summary Bar

Always visible below the timeline. Shows a one-line summary of all evidence checks:

```
Tests ✅ 92%   Lint ✅ 0   Typecheck ✅   Build ❌ (1)   Risk MEDIUM
```

Each check type gets:
- **Name** (e.g., "Tests", "Lint")
- **Status icon** (✅ / ❌ / —)
- **Value** (e.g., coverage %, error count)

A thin progress-like bar below shows each check's status relative to its threshold:
```
[████████████████████░░░░] Coverage: 92% (threshold: 80%)
```

**Risk level** (computed from assurance): LOW → green, MEDIUM → amber, HIGH → red.

### 4.7 Raw Lineage

Collapsed by default. Clicking expands a scrollable `<pre>` block showing the raw JSON event log. Features:
- Syntax highlighting (optional, CSS-only)
- Copy-to-clipboard button
- Download as `.jsonl` button

---

## 5. Technical Implementation

### 5.1 Architecture

```
Browser (localhost:8765)
  │
  ├── GET /                → Overview page (list of runs)
  ├── GET /run/<run_id>    → Run detail page (timeline + evidence)
  ├── GET /api/runs        → JSON array of RunSummary
  ├── GET /api/run/<id>    → JSON RunLog (full event list)
  └── GET /events          → SSE stream (run_count changes)
```

### 5.2 Live Updates

The dashboard uses **Server-Sent Events (SSE)** for live updates:

```
GET /events
  → event: run_count
    data: 5
  → : heartbeat 2025-01-01T00:00:00Z
```

On the run detail page, the client polls `/api/run/<id>` every 2 seconds when a run is `status: "started"`. When `status` becomes `"completed"` / `"interrupted"` / `"failed"`, polling stops.

**Future (v0.9.5):** Replace polling with per-run SSE streams for true real-time updates.

### 5.3 Server

| Property | Value |
|----------|-------|
| Framework | `http.server.HTTPServer` (stdlib, zero deps) |
| Host | `127.0.0.1` (localhost only — no network exposure) |
| Default port | `8765` |
| Threading | Single-threaded (adequate for local single-user dashboard) |
| Content-Type | `text/html; charset=utf-8` |

### 5.4 CSS / Styling

- **No CSS framework** — all styles inline or in a single `<style>` block
- **Dark mode** by default (easier on eyes for long monitoring sessions)
- **Color palette:**

| Role | Hex |
|------|-----|
| Background | `#0d1117` |
| Surface / cards | `#161b22` |
| Border | `#30363d` |
| Text primary | `#e6edf3` |
| Text secondary | `#8b949e` |
| Accent (blue) | `#58a6ff` |
| Success (green) | `#3fb950` |
| Warning (amber) | `#d29922` |
| Error (red) | `#f85149` |
| Purple (replan) | `#a371f7` |

- **Font stack:** `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace`
- **Responsive:** Works at ≥1024px wide; below that, timeline takes full width and details move below

---

## 6. Page States

### 6.1 Overview Page (`/`)

| State | Display |
|-------|---------|
| No runs | Empty state: "No runs yet. Run `bound run ...` to see your first execution." |
| Runs exist | Table: Run ID (truncated), Task, Status badge, Started, Duration, Last Decision |
| Store error | Red banner: "Could not read lineage store at `.bound/runs/`" |

### 6.2 Run Detail Page (`/run/<id>`)

| State | Display |
|-------|---------|
| Loading | Skeleton placeholders (pulsing grey bars) |
| Active run | Live indicator + polling every 2s + auto-scroll timeline |
| Completed run | Static timeline + collapsed "Run Details" auto-expanded |
| Interrupted run | Amber warning banner: "Run was interrupted before completion" |
| Failed run | Red banner: "Run failed — see timeline for details" |
| Not found | 404: "Run `<id>` not found. It may have been cleaned up." |

### 6.3 Error States

| Error | Handling |
|-------|----------|
| Port in use | CLI prints alternative port suggestion |
| Store unreadable | Page shows error banner, not a crash |
| Corrupt event | Skip the event, show "⚠️ 1 event could not be parsed" |
| Browser disconnected | Clean SSE shutdown (catch `BrokenPipeError`) |

---

## 7. CLI Interface

```bash
# Start dashboard on default port (8765)
bound ui

# Start dashboard on a custom port
bound ui --port 9090

# Start and open browser automatically
bound ui --open

# Open directly to a specific run
bound ui --run <run_id>

# Start + open browser + specific run
bound ui --open --run <run_id>
```

---

## 8. Future UI (v0.9.5+)

Intentionally postponed until the realtime execution view is stable:

| Feature | Description |
|---------|-------------|
| Candidate tree | Visualize branched execution with expand/collapse nodes |
| Policy editor | YAML editor with live validation |
| Collector configuration | Add/remove collectors via UI |
| Benchmark dashboard | Run benchmarks and compare results side-by-side |
| Agent integrations panel | Configure and test agent adapters |
| Plugin manager | Discover and install BOUND plugins |
| Per-run SSE stream | True real-time updates for individual runs |
| Dark/Light theme toggle | User preference persisted in localStorage |

---

## 9. Implementation Checklist

### Phase 1 — Layout & Shell
- [ ] New HTML shell with grid/flexbox layout matching the spec
- [ ] Dark mode CSS with the defined color palette
- [ ] Header bar with task, status, policy
- [ ] Current Action bar (prominent, top of page)
- [ ] Summary cards row
- [ ] Empty states for all pages

### Phase 2 — Timeline
- [ ] Timeline rendering from event log
- [ ] Event type icons and colors
- [ ] Evidence line-items with ✅/❌/— indicators
- [ ] Auto-scroll with "jump to live" button
- [ ] Polling for active runs (2s interval)

### Phase 3 — Panels
- [ ] Collapsible Run Details sidebar
- [ ] Evidence Summary bar (always visible)
- [ ] Raw Lineage expandable section

### Phase 4 — Live Updates
- [ ] SSE for run count on overview page
- [ ] Live indicator (pulsing dot)
- [ ] Auto-stop polling when run completes

### Phase 5 — Polish
- [ ] Responsive layout for narrow viewports
- [ ] Copy-to-clipboard for Run ID
- [ ] Download lineage as `.jsonl`
- [ ] Error state handling throughout
- [ ] Smooth transitions and animations (CSS only)
