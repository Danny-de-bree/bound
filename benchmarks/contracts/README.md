# Contract-quality benchmark corpus (BOUND v0.3 Phase 14)

This directory is the experiment corpus for the central Phase 14 question:

> Did the generated contract define useful success criteria?

Each `*.json` file is a validated `BoundPlan` (it round-trips through
`BoundPlan.model_validate_json`). Together the 12 fixtures span the quality
spectrum a generated contract can land on. The assessment is produced by
`bound.contract_quality.assess_contract` — **deterministic, no LLM, no network** —
and the whole corpus is replayed by:

```python
from bound.contract_quality import run_contract_quality_experiment, summarize_contract_quality_experiment

print(summarize_contract_quality_experiment(run_contract_quality_experiment()))
```

## The measurability heuristic (honest)

A check *appears* measurable when its `id` **or** `description` contains a
curated "verification token" as a word fragment (e.g. `returns`, `raises`,
`passes`, `equals`, `exists`, `emits`, `valid`, `empty`, …). This is a **surface
lexical smell test**, not a proof: it says "the wording implies an observable,
binary predicate", not "an executable assertion exists". `measurable_ratio` is
the fraction of checks that pass this smell test.

Warnings are purely structural: *no acceptance checks*, *too many vague checks*
(description < 10 chars or a generic placeholder like `works`/`ok`), *duplicate
check ids* in a step, *no observable verification method* (no check reads as
measurable), and *extremely large contract* (> 15 acceptance checks in a step).

## What structural validation can and cannot judge

It **can**: that checks read as measurable, are non-vague, have unique ids, are
not absurdly numerous, that a budget exists, and that risk checks are present.

It **cannot**: judge *relevance* to the goal, *missing required checks* (no
ground truth), *unnecessary checks* (a small extra check is indistinguishable
from a necessary one), or whether a risk check is the *right* risk — only that
one exists with a valid severity. The `measurable_but_irrelevant_plan` fixture
makes the relevance blind spot concrete: it scores perfectly (ratio 1.0, no
warnings) while checking coffee-machine behaviour for a JSON-parser goal.

## Corpus

| Fixture | Intent | Structural verdict |
| --- | --- | --- |
| `good_plan.json` | measurable ids, relevant, budget, risks | clean, ratio 1.0 |
| `vague_plan.json` | short/generic descriptions | vague + no-observable warnings, ratio 0.0 |
| `missing_checks_plan.json` | one thin vague check | vague + no-observable, ratio 0.0 |
| `duplicate_id_plan.json` | same check id twice | duplicate warning, ratio 1.0 |
| `oversized_plan.json` | 16 checks (> 15) | extremely-large warning, ratio 1.0 |
| `no_budget_plan.json` | no explicit budget | clean, `has_budget=False` |
| `no_risk_checks_plan.json` | no risk checks | clean, `risk_check_count=0` |
| `non_measurable_ids_plan.json` | ids/descriptions with no verification tokens | no-observable warning, ratio 0.0 |
| `mixed_quality_plan.json` | some measurable, some vague | vague warning, ratio 0.5 |
| `multi_step_plan.json` | 3 steps of varying quality | per-step warnings (id-prefixed), ratio 0.5 |
| `advisory_only_plan.json` | all checks `required=False` | clean (no hard criteria — a blind spot) |
| `measurable_but_irrelevant_plan.json` | measurable but wrong domain | **clean, ratio 1.0 — the relevance blind spot** |

## Recorded findings (live summary)

Running the experiment over this corpus yields **12 plans assessed, 7 with
warnings, aggregate measurable ratio 0.745**. The full per-plan summary is
produced by `summarize_contract_quality_experiment(...)` and is reproducible:
the same corpus always yields the same report.
