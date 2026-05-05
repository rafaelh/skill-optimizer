# Evaluating skills

A skill activates if and only if its `description` matches the user's request — so improving a skill is mostly improving its description, and that requires measurement, not intuition. This guide covers how to build a query set, run an eval, and iterate.

## Why measure?

Model behavior is nondeterministic. A description that triggered for "analyze this CSV" yesterday might miss it tomorrow. Without an eval, you can't tell whether a description change improved things, made them worse, or made no difference.

There's also a publication bias: descriptions that *feel* clear to the author often miss the phrasings real users actually type. Evals expose this gap.

## The optimization loop at a glance

1. Build a labeled query set (~20 queries, half should-trigger, half should-not-trigger).
2. Split into train (~60%) / validation (~40%) with proportional positive/negative balance. Keep the split fixed across iterations.
3. Run the eval against train + validation.
4. Use train failures to guide description revisions; touch validation results only to confirm changes generalize.
5. Iterate 3–5 times. Pick the iteration with the best validation pass rate, not necessarily the last.

`scripts/optimize_description.py` runs this loop end-to-end. `scripts/eval_triggers.py` runs a single round.

## Designing the query set

Use [assets/templates/eval-queries.json.template](../assets/templates/eval-queries.json.template) as a starting point. Validate against [assets/schemas/eval-queries.schema.json](../assets/schemas/eval-queries.schema.json) before running.

Aim for 8–10 should-trigger and 8–10 should-not-trigger queries. Across the set, vary:

- **Phrasing**: formal, casual, typos, abbreviations
- **Explicitness**: some name the domain ("CSV"), others describe the need without naming it
- **Detail**: terse vs. context-heavy
- **Complexity**: single-step vs. multi-step where the skill's task is buried inside a broader request
- **Realism**: include file paths, casual interjections, "my manager asked me to…", actual column names

The most valuable queries are the boundary cases. For positives, include prompts where the connection isn't obvious. For negatives, include **near-misses** — same keywords, different actual need:

| Skill | Trap negative | Why |
|---|---|---|
| CSV analysis | "upload these csv rows to postgres" | "csv" appears, but task is database loading |
| CSV analysis | "fix the failing test in test_csv_parser.py" | csv mentioned, but task is debugging |
| PDF processing | "explain how pypdf reads encrypted PDFs" | library-internals question, not document work |
| Code review | "review this AWS bill" | non-code review |

If your description over-triggers on near-misses, the description is keyword-matching instead of intent-matching.

## Running a single eval

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/eval_triggers.py" \
  --queries assets/templates/eval-queries.json.template \
  --skill-name <skill-name> \
  --runs 3 \
  --json
```

`eval_triggers.py` invokes `claude -p --output-format json` per query, looks for a `Skill` tool use matching `<skill-name>`, and computes per-query trigger rate over `--runs` invocations. A should-trigger query passes if rate > 0.5; a should-not-trigger passes if rate < 0.5.

Output (abbreviated):

```json
{
  "skill_name": "csv-analysis",
  "runs_per_query": 3,
  "by_query": [
    {"query": "...", "should_trigger": true, "triggers": 3, "rate": 1.0, "passed": true},
    {"query": "...", "should_trigger": false, "triggers": 2, "rate": 0.667, "passed": false}
  ],
  "summary": {"train": {"pass_rate": 0.83}, "validation": {"pass_rate": 0.75}}
}
```

## Iterating: the revision rules

Use train-set failures only.

| Failure type | Revision direction |
|---|---|
| should-trigger missed | Broaden scope; add explicit trigger contexts ("even when the user doesn't say 'CSV'") |
| should-not-trigger fired | Add specificity; clarify boundary with adjacent skills |
| Mixed near-miss confusion | Add a disambiguating phrase ("for analysis, not loading or migration") |

Two anti-patterns to avoid:

- **Don't paste failure-query keywords** into the description. That's overfitting; the next phrasing variation will miss again. Address the underlying *category*.
- **Don't keep tweaking when stalled.** If three rounds of incremental edits haven't moved the validation rate, try a structurally different framing (lead with intent vs. domain, switch from imperative to question form, reorder triggers).

`optimize_description.py` automates this loop by feeding train failures to `claude -p` for revision candidates, evaluating each, and recording all candidates' validation scores.

## Sanity checks

After committing the winning description:

1. Verify length is under 1024 chars (`validate_skill.py` enforces this).
2. Manually try 5–10 fresh prompts that were never in the eval set. Trust real usage over the eval; the eval is a safety net, not a substitute.
3. If the skill is one of many, run `detect_skill_overlap.py` to confirm the new description didn't collide with siblings.

## When the eval itself is the problem

If the validation pass rate plateaus and revising further makes things worse, suspect the query set. Re-examine for:

- **Mislabels** — should-trigger queries that an attentive human wouldn't expect to trigger.
- **Near-impossible positives** — queries that name an entirely different domain and expect activation by miracle.
- **Trivial negatives** — queries so unrelated they don't actually probe the boundary.

Edit the query set, fix the split, and rerun. Don't fix evals to match a description; fix them to match reality.
