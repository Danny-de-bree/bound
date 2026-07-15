<p align="center">
  <strong>Agents that know when good enough is enough.</strong>
</p>

<p align="center">
  <a href="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml"><img src="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/v/bound-policy.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/pyversions/bound-policy.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/Danny-de-bree/bound"><img src="https://img.shields.io/badge/tests-239%20passed-brightgreen.svg" alt="Tests"></a>
  <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv"></a>
</p>

---

BOUND is a **deterministic bounded-utility policy** for agentic systems.

Most agents are optimized to find the *best possible* action. BOUND helps an
agent decide whether a proposed action is **good enough to continue** toward the
larger goal — and when to retry, replan, or roll back.

```text
S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)
```

| Variable | Meaning                     |
| -------- | --------------------------- |
| `S`      | Final bounded utility score |
| `A`      | Acceptance score            |
| `I`      | Downstream influence        |
| `R`      | Risk penalty                |
| `C`      | Resource penalty            |
| `W_A`    | Acceptance weight           |
| `W_I`    | Influence weight            |
| `W_R`    | Risk weight                 |
| `W_C`    | Cost weight                 |
| `T`      | Acceptance threshold         |

Every weight defaults to `1.0`, so v0.1 callers using the legacy scalar `weight`
keep working unchanged: `W_A = W`, `W_I = W_R = W_C = 1.0` reproduces the original
`S = (W × A) + I - R - C` exactly.

The success condition is not *maximize `S`* — it is *cross the threshold and
continue*:

```text
S >= T
```

The BOUND core is **deterministic, model-agnostic, and network-free**. No LLM
SDK is required. LLM-as-judge is a *later, optional* source of evaluation
scores that lives behind an `Evaluator` protocol — never in the core.

---

## Install

```bash
pip install bound-policy
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add bound-policy
```

> **Note on the name:** the PyPI package is `bound-policy` (the name `bound`
> was already taken by an unrelated project). The Python import name is simply
> `bound`, and the CLI command is `bound`.

## Quickstart (CLI)

```bash
bound evaluate \
  --action "Book the direct flight" \
  --goal "Travel from Paris to New York" \
  --acceptance 0.9 \
  --influence 0.2 \
  --risk 0.1 \
  --cost 0.2 \
  --weight 1.0 \
  --threshold 0.6
```

The legacy `--weight` flag is kept as a backward-compatible alias for
`--acceptance-weight`. v0.2 also accepts the four independent weights directly
(`--acceptance-weight`, `--influence-weight`, `--risk-weight`, `--cost-weight`)
and a new `evaluate-workflow` subcommand that scores coding-agent workflow
signals without an LLM (see [Deterministic workflow signals](#deterministic-workflow-signals)).

**stdout** — an auditable JSON result:

```json
{
  "scores": { "acceptance": 0.9, "influence": 0.2, "risk": 0.1, "cost": 0.2 },
  "weights": { "acceptance": 1.0, "influence": 1.0, "risk": 1.0, "cost": 1.0 },
  "threshold": 0.6,
  "acceptance_component": 0.9,
  "influence_component": 0.2,
  "risk_component": 0.1,
  "cost_component": 0.2,
  "score": 0.8,
  "distance_to_threshold": 0.2,
  "decision": "ACCEPT"
}
```

The payload exposes every term of `S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)` —
including the per-dimension `weights`, the four weighted components, the final
`score`, and the signed `distance_to_threshold` (`S - T`) — so a consumer can
reconstruct the score from the JSON alone. Scores are emitted without their
optional `reasoning` field to keep the output minimal and stable.

**stderr** — a deterministic steering prompt:

```text
[BOUND evaluation]

Decision: ACCEPT

Bounded utility:
S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)
S = (1.00×0.90) + (1.00×0.20) - (1.00×0.10) - (1.00×0.20)
S = 0.80

Acceptance threshold:
T = 0.60

The proposed action meets the required acceptance threshold.
Further optimization is not required.
Proceed with the action and continue toward the larger goal.
```

No API key. No network call. Fully reproducible from the inputs alone.

## Quickstart (Python)

```python
from bound.models import Action, BoundCriteria, EvaluationScores
from bound.evaluator import StaticEvaluator
from bound.policy import BoundPolicy

action = Action(
    description="Book the direct flight",
    goal="Travel from Paris to New York",
)
scores = EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
criteria = BoundCriteria(weight=1.0, threshold=0.6)

result = BoundPolicy(StaticEvaluator(scores)).evaluate(action, criteria)

print(result.score)      # 0.8
print(result.decision)   # ACCEPT
```

The `Evaluator` protocol is the single seam where scores enter the system.
`StaticEvaluator` returns pre-supplied scores (used by tests, examples, and the
CLI). v0.2 adds `CodingWorkflowEvaluator`, which derives the same `A / I / R / C`
from deterministic coding-agent signals with full provenance. Other evaluators
(LLM-as-judge, rule-based, reward-model, …) implement the same protocol without
touching the decision rule.

---

## Why?

Humans rarely optimize every decision.

When planning a vacation, we do not search forever for the perfect flight.
We search until we find a flight that satisfies our requirements and move on.

Modern agents often do the opposite — they continue searching, planning, and
refining long after a satisfactory outcome has already been found.

BOUND applies a different philosophy:

```text
Good enough
+
Forward progress
```

instead of:

```text
Perfect
+
Endless optimization
```

## Example

Goal:

```text
Take a vacation from Paris to New York
```

| Flight     | Price | Stops |
| ---------- | ----- | ----- |
| Direct     | €650  | 0     |
| One Stop   | €820  | 1     |
| Two Stops  | €540  | 2     |

Acceptance criteria:

```text
Price <= €1200
Stops <= 1
```

Evaluation:

```text
✓ Direct Flight     ACCEPTED
✓ One Stop Flight   ACCEPTED
✗ Two Stop Flight   REJECTED
```

The agent does not need the best flight — it needs a flight that satisfies the
goal. Once the goal is satisfied, the system continues.

---

## Mathematical Formulation

BOUND evaluates outcomes using bounded utility.

```text
S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)
```

Success condition:

```text
S >= T
```

where `T` is the acceptance threshold. The objective is not to maximize `S`
indefinitely — it is to cross the threshold and continue making progress toward
the final goal. The threshold is intentionally **not** capped at `1.0`: when a
weight exceeds `1.0`, `S` can exceed `1.0`, so a legitimate threshold may too.

### The four dimensions

| Dimension      | Range     | Measures                                            |
| -------------- | --------- | --------------------------------------------------- |
| `A` acceptance | `[0, 1]`  | How well does this satisfy the goal?                |
| `I` influence  | `[-1, 1]` | How does this affect downstream goals? (±)          |
| `R` risk       | `[0, 1]`  | What is the potential downside?                     |
| `C` cost       | `[0, 1]`  | Normalized resource consumption (time, tokens, …)   |

### Decisions

The policy applies four checks in a fixed order. `ROLLBACK` is a **peer
outcome** triggered by a hard safety boundary — not "risk is the largest
negative component" and not something an action "fails into" after `REPLAN`.

```text
1. risk >= rollback_risk_threshold   -> ROLLBACK   # hard safety boundary
2. S >= T                            -> ACCEPT     # good enough — stop, continue
3. gap = T - S; gap <= retry_margin  -> RETRY      # close — try same approach again
4. otherwise                         -> REPLAN     # too far — different strategy
```

Canonical meanings:

| Decision  | Meaning |
| --------- | ------- |
| `ACCEPT`  | Good enough — stop optimizing this action and continue toward the larger goal. |
| `RETRY`   | Close enough to acceptable — try again within the same action space. |
| `REPLAN`  | Too far below the threshold — choose a materially different strategy. |
| `ROLLBACK`| Exceeds the configured hard risk boundary — revert or avoid the action. |

A high-scoring action may still produce `ROLLBACK` if it violates the configured
hard risk threshold. This is intentional: the utility threshold and the safety
boundary are independent concerns, and the safety boundary is checked first.

`distance_to_threshold` (`S - T`) is carried on every result so the gap that
drove a `RETRY` vs `REPLAN` decision is inspectable.

## Why Influence Matters

Some decisions affect future goals.

```text
Flight A
✓ Cheapest
✓ Direct
✗ Difficult hotel transfer
✗ Higher chance of late check-in
```

```text
Flight B
✓ Slightly more expensive
✓ Better arrival time
✓ Easier transfer
✓ Lower risk for remaining goals
```

BOUND may prefer Flight B because it increases the probability of success for
the entire goal chain.

---

## Architecture

```text
Action
  │
  ▼
Evaluator            (replaceable: StaticEvaluator, CodingWorkflowEvaluator, …)
  │  ← provenance (ScoreEvidence) flows up here when the evaluator exposes it
  ▼
EvaluationScores     (A, I, R, C)
  │
  ▼
BoundCalculator      S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)   (deterministic, raw)
  │
  ▼
BoundPolicy          fixed-order decision:
  │                    1. risk >= rollback_risk_threshold -> ROLLBACK
  │                    2. S >= T                           -> ACCEPT
  │                    3. gap = T - S <= retry_margin       -> RETRY
  │                    4. otherwise                         -> REPLAN
  ▼
EvaluationResult     (weights, components, score, distance_to_threshold,
                      decision, provenance)
  ├── JSON           (auditable — reconstruct S from the output alone)
  └── Steering prompt
```

The evaluator is **replaceable**. The mathematical calculation and the decision
rule are **not** — they are the deterministic, provider-agnostic core.

The core enforces, and the test suite asserts at runtime, that no network
access, no API key, and no LLM SDK is required to reach a decision.

## When to use BOUND (and when not to)

**Use BOUND when:**

- You want an explicit, inspectable stop/continue/replan policy for an agent loop.
- You can produce or estimate `A / I / R / C` from any source (deterministic
  workflow signals, a model, or a human).
- You want the decision rule to be simple, auditable, and provider-agnostic.

**Do not expect BOUND to:**

- Find the globally optimal action — by design it stops at "good enough."
- Produce the four scores for you by default — `bound evaluate` takes them as
  inputs. v0.2 adds a deterministic `CodingWorkflowEvaluator` that derives them
  from coding-agent workflow signals (no LLM); other automatic generation
  (LLM-as-judge) is optional and on the roadmap.
- Drive a multi-step agent loop on its own — BOUND is a single-action policy. A
  loop driver and persistent mission state are deferred (see `roadmap.md`).
- Improve agent performance on its own — the v0.2 experiment harness produces
  reproducible evidence of *where* BOUND would stop an agent trajectory; it does
  not yet prove a measured reduction in unnecessary work.

## Deterministic workflow signals

v0.2 ships `CodingWorkflowEvaluator`, the first evaluator that derives `A / I /
R / C` from **real, deterministic** evidence instead of asking an LLM. It
consumes provider-agnostic `CodingWorkflowSignals` captured from a coding-agent
run (test pass rate, lint/type-check status, retry counts, tool calls, token
usage, file changes, …) and maps them to scores using visible, documented rules:

- **Acceptance `A`** — mean of available completion signals (test pass rate,
  required-checks rate, lint, type-check); missing signals are ignored.
- **Risk `R`** — mean of available risk indicators (unexpected file changes,
  rollback unavailable, large change surface, failed checks).
- **Cost `C`** — cap-normalized mean of retry/tool-call/token/runtime terms.
- **Influence `I`** — `0.0` by default (v0.2 derives no downstream influence
  from workflow signals) or supplied externally.

Every mapping is marked a **v0.2 reference heuristic**: the constants are
deliberate, visible policy knobs, *not* scientifically calibrated weights. The
point is to prove BOUND inputs can be derived without an LLM and to make the
derivation auditable through `ScoreEvidence` provenance, so a consumer can
answer "why is `A = 0.85`?".

## What "bounded" means

"BOUND" does **not** mean the utility function itself has a bounded or concave
mathematical shape. The score `S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)` is an
ordinary linear combination and is unbounded above and below.

"BOUND" means **optimization is bounded by an explicit acceptance threshold** —
a satisficing policy:

```text
once S >= T:
    stop optimizing this step
```

Once the threshold is crossed, further optimization of the current action is not
required; the agent continues toward the larger goal. We make no claim of
mathematical novelty for the one-line formula. The value is in the explicit stop
condition and the auditable derivation of the inputs, not in the arithmetic.

## Current status

BOUND v0.2 is an experimental deterministic control policy. The score formula,
the default workflow heuristics, and the threshold defaults are **hypotheses**.
They have not yet been broadly validated across production agent workloads.

`A / I / R / C` are not naturally commensurable quantities; the weights are
explicit policy parameters, and the defaults are not implied to be universally
correct. The v0.2 experiment harness is designed to produce reproducible
evidence of where BOUND would stop a trajectory and how much work would have been
avoided — not to assert that BOUND already improves outcomes.

## Competitive positioning

BOUND is not a model provider, a judge, or an agent framework. Its intended
differentiation is:

```text
provider-agnostic
deterministic final policy
auditable score decomposition
explicit stop condition
no mandatory LLM judge
workflow evidence before semantic judgement
```

The future value is primarily in:

```text
signal collection
score derivation
threshold calibration
agent-loop integration
```

— not in the one-line score formula alone. Deterministic, inspectable workflow
evidence (tests passing, files changed, retries) is gathered *before* any
optional semantic judgement, and an LLM judge is never a required dependency of
the core.

## Roadmap

See [`roadmap.md`](roadmap.md) for the full staged plan. Highlights:

- **v0.1** — deterministic core, Pydantic models, CLI, unit tests, prompts.
- **v0.2** — symmetric weights, coherent decision semantics, deterministic
  coding-workflow signals + `CodingWorkflowEvaluator` with provenance, threshold
  introspection, experiment harness. *(this release)*
- **v0.3** — composite evaluators, optional semantic evaluator, hybrid scoring.
- **v0.4** — integration into a real coding-agent workflow, production data
  collection, threshold calibration.
- **Later** — hierarchical BOUND, adaptive/learned thresholds, mission-level
  policies.

## Development

```bash
git clone https://github.com/Danny-de-bree/bound.git
cd bound
uv sync
uv run pytest          # 239 tests
uv run ruff check .
```

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md). The one
rule that matters most: **the core must remain deterministic once evaluation
scores are provided.**

## License

[MIT](LICENSE) © Danny de Bree
