<p align="center">
  <strong>Agents that know when good enough is enough.</strong>
</p>

<p align="center">
  <a href="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml"><img src="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/v/bound-policy.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/pyversions/bound-policy.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/Danny-de-bree/bound"><img src="https://img.shields.io/badge/tests-121%20passed-brightgreen.svg" alt="Tests"></a>
  <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv"></a>
</p>

---

BOUND is a **deterministic bounded-utility policy** for agentic systems.

Most agents are optimized to find the *best possible* action. BOUND helps an
agent decide whether a proposed action is **good enough to continue** toward the
larger goal — and when to retry, replan, or roll back.

```text
S = (W × A) + I - R - C
```

| Variable | Meaning                     |
| -------- | --------------------------- |
| `S`      | Final bounded utility score |
| `W`      | Goal weight                 |
| `A`      | Acceptance score            |
| `I`      | Downstream influence        |
| `R`      | Risk penalty                |
| `C`      | Resource penalty            |

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

**stdout** — an auditable JSON result:

```json
{
  "scores": { "acceptance": 0.9, "influence": 0.2, "risk": 0.1, "cost": 0.2 },
  "weight": 1.0,
  "threshold": 0.6,
  "acceptance_component": 0.9,
  "influence_component": 0.2,
  "risk_component": 0.1,
  "cost_component": 0.2,
  "score": 0.8,
  "decision": "ACCEPT"
}
```

**stderr** — a deterministic steering prompt:

```text
[BOUND evaluation]

Decision: ACCEPT

Bounded utility:
S = (W × A) + I - R - C
S = (1.00 × 0.90) + 0.20 - 0.10 - 0.20
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
CLI). Future evaluators (LLM-as-judge, rule-based, reward-model, …) implement
the same protocol without touching the decision rule.

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
S = (W × A) + I - R - C
```

Success condition:

```text
S >= T
```

where `T` is the acceptance threshold. The objective is not to maximize `S`
indefinitely — it is to cross the threshold and continue making progress toward
the final goal.

### The four dimensions

| Dimension      | Range     | Measures                                            |
| -------------- | --------- | --------------------------------------------------- |
| `A` acceptance | `[0, 1]`  | How well does this satisfy the goal?                |
| `I` influence  | `[-1, 1]` | How does this affect downstream goals? (±)          |
| `R` risk       | `[0, 1]`  | What is the potential downside?                     |
| `C` cost       | `[0, 1]`  | Normalized resource consumption (time, tokens, …)   |

### Decisions

```text
if S >= T:                 ACCEPT    # good enough — continue
elif risk > cost:          ROLLBACK  # downside dominates — revert
elif cost > risk:          RETRY     # too expensive — try leaner
else:                      REPLAN    # below threshold — new strategy
```

The threshold `T` is intentionally **not** capped at `1.0`: when `W > 1`, `S`
can exceed `1.0`, so a legitimate threshold may too.

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
Evaluator            (replaceable: StaticEvaluator, LLMEvaluator, …)
  │
  ▼
EvaluationScores     (A, I, R, C)
  │
  ▼
BoundCalculator      S = (W × A) + I - R - C   (deterministic, raw)
  │
  ▼
BoundPolicy          S >= T ?                  (deterministic decision)
  │
  ▼
EvaluationResult
  ├── JSON           (auditable — reconstruct S from the output alone)
  └── Steering prompt
```

The evaluator is **replaceable**. The mathematical calculation is **not**.

The core enforces, and the test suite asserts at runtime, that no network
access, no API key, and no LLM SDK is required to reach a decision.

## When to use BOUND (and when not to)

**Use BOUND when:**

- You want an explicit, inspectable stop/continue/replan policy for an agent loop.
- You can produce or estimate `A / I / R / C` from any source (deterministic
  signals, a model, or a human).
- You want the decision rule to be simple, auditable, and provider-agnostic.

**Do not expect BOUND to:**

- Find the globally optimal action — by design it stops at "good enough."
- Produce the four scores for you — v0.1 takes them as inputs. Automatic score
  generation (LLM-as-judge, deterministic workflow signals) is on the roadmap.
- Drive a multi-step agent loop on its own — v0.1 is a single-action policy. A
  loop driver and persistent mission state are deferred (see `roadmap.md`).

## Roadmap

See [`roadmap.md`](roadmap.md) for the full staged plan. Highlights:

- **v0.1** — deterministic core, Pydantic models, CLI, unit tests, prompts. *(this release)*
- **v0.2** — evaluator abstraction, configurable score components, richer explanations.
- **v0.3** — deterministic coding-agent signals (test/lint evidence, cost & retry tracking).
- **v0.4** — composite evaluators, optional semantic evaluator, hybrid scoring.
- **v0.5** — integration into a real coding-agent workflow, production data collection.

## Development

```bash
git clone https://github.com/Danny-de-bree/bound.git
cd bound
uv sync
uv run pytest          # 121 tests
uv run ruff check .
```

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md). The one
rule that matters most: **the core must remain deterministic once evaluation
scores are provided.**

## License

[MIT](LICENSE) © Danny de Bree
