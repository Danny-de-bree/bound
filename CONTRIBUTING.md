# Contributing to BOUND

Thanks for your interest in contributing to BOUND. This is a small project with
strong opinions about keeping the core deterministic and model-agnostic.

## The one rule that matters most

> The BOUND core must remain deterministic once evaluation scores are provided.

Everything below exists to protect that invariant.

## Development setup

BOUND uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management, and Python 3.12+.

```bash
git clone https://github.com/Danny-de-bree/bound.git
cd bound
uv sync          # creates the venv and installs deps
uv run pytest    # run the test suite
uv run ruff check .
```

## Phase-gate discipline

BOUND is developed phase by phase. Do not consider work done until **both** pass:

```bash
uv run pytest
uv run ruff check .
```

Every pull request must keep these green. CI enforces it on Python 3.12 and 3.13.

## What belongs in the core (and what does not)

| In scope for `bound` core | Out of scope |
|---|---|
| The deterministic formula `S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)` | LLM SDKs (OpenAI, Anthropic, …) |
| Pydantic domain models (`BoundWeights`, `CodingWorkflowSignals`, …) | Provider-specific dependencies |
| The `Evaluator` protocol + `StaticEvaluator` / `CodingWorkflowEvaluator` | External API calls at runtime |
| The deterministic decision rule (ROLLBACK → ACCEPT → RETRY → REPLAN) | Hiding policy logic inside prompts |
| Deterministic steering-prompt rendering | An evaluator returning a decision directly |
| The `bound evaluate` / `bound evaluate-workflow` CLI | |

LLM-as-judge and other evaluators are a *later*, optional concern. They must
live behind the `Evaluator` protocol and never leak into the core.

## Coding standards

- **Pydantic v2** for all data models and validation.
- **Full type hints** on every function; **Google-format** docstrings.
- **Logging via the `logging` module** — no ad-hoc `print` for diagnostics.
  (Intentional CLI output to stdout/stderr is fine.)
- Prefer the standard library and existing project dependencies. Do not pull in
  a new external library without justification.

## Commit messages

Follow the **how → why → what** pattern:

```
refactor: dedupe NaN-handling in summary parser (how) to keep JSON
serialisation stable (why) by routing all parsing through one helper (what)
```

Keep commits focused and within the scope of the change.

## Reporting issues

- Bugs in the deterministic core are the highest priority — please include the
  exact inputs, the expected vs. actual `S` and decision, and the BOUND version.
- Suggestions for the roadmap belong in Discussions, not as bug reports.

## License

By contributing, you agree that your contributions will be licensed under the
MIT License.
