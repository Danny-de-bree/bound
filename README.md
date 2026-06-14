# BOUND

> Agents that know when good enough is enough.

BOUND is a decision policy for agentic systems.

Prompt library:

- See `policies/standard-agent-prompts.md` for 50 reusable prompts for standard agent workflows.

Most agents are optimized to find the best possible action.

BOUND helps agents determine when an outcome is sufficiently complete to continue.

Instead of asking:

```text
What is the best possible action?
```

BOUND asks:

```text
What is the best acceptable action?
```

The objective is not perfect optimization.

The objective is progress toward the final goal.

---

## Why?

Humans rarely optimize every decision.

When planning a vacation, we do not search forever for the perfect flight.

We search until we find a flight that satisfies our requirements and move on.

Modern agents often do the opposite.

They continue searching, planning, and refining long after a satisfactory outcome has already been found.

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

---

## Example

Goal:

```text
Take a vacation from Paris to New York
```

Possible flights:

| Flight | Price | Stops |
|----------|---------|---------|
| Direct | €650 | 0 |
| One Stop | €820 | 1 |
| Two Stops | €540 | 2 |

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

The agent does not need the best flight.

It needs a flight that satisfies the goal.

Once the goal is satisfied, the system continues.

---

## Mathematical Formulation

BOUND evaluates outcomes using bounded utility.

```text
S = (W × A) + I - R - C
```

Where:

| Variable | Description |
|-----------|-------------|
| S | Final bounded score |
| W | Goal weight |
| A | Acceptance score |
| I | Downstream influence |
| R | Risk penalty |
| C | Resource penalty |

Success condition:

```text
S ≥ T
```

Where:

```text
T = acceptance threshold
```

The objective is not to maximize S indefinitely.

The objective is to cross the threshold and continue making progress toward the final goal.

---

## Why Influence Matters

Some decisions affect future goals.

Example:

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

BOUND may prefer Flight B because it increases the probability of success for the entire goal chain.

---

## BOUND Evaluation Policy

For every proposed action:

### 1. Evaluate Acceptance

```text
How well does this satisfy the goal?
```

Output:

```text
A ∈ [0,1]
```

---

### 2. Evaluate Influence

```text
How does this impact future goals?
```

Output:

```text
I ∈ [-1,1]
```

---

### 3. Evaluate Risk

```text
What is the downside if this goes wrong?
```

Output:

```text
R ∈ [0,1]
```

---

### 4. Evaluate Resource Cost

```text
How expensive is this action?
```

Examples:

- Tool calls
- Tokens
- Runtime
- Money

Output:

```text
C ∈ [0,1]
```

---

### 5. Compute Score

```text
S = (W × A) + I - R - C
```

---

### 6. Decide

```text
If S >= T:
    ACCEPT

Else:
    RETRY
    or
    REPLAN
```
