# Optimizing skill descriptions

The `description` field is the single point at which an agent decides whether to load your skill. Get it wrong and the skill never runs. This guide covers what to write, how to test it, and the iterative loop for improving it.

## Why the description carries everything

Agents use progressive disclosure: at startup they load only `name` + `description` for every available skill. Full `SKILL.md` content loads only when they decide a skill matches the user's task. So:

- An under-specified description means the skill won't trigger when it should.
- An over-broad description means it triggers when it shouldn't and pollutes the agent's context.

One nuance: agents typically only consult skills for tasks they can't easily handle alone. "Read this PDF" may not trigger a PDF skill even with a perfect description, because the agent can do it directly. Specialized knowledge, unfamiliar APIs, and uncommon formats are where descriptions earn their keep.

## `description` vs `when_to_use`

Claude Code supports a companion field `when_to_use` for supplementary trigger context. The two fields are appended together and shown in the skill listing, **truncated at 1,536 characters combined**. Use `description` for the core identity of the skill; use `when_to_use` for additional trigger phrases, aliases, or domain synonyms that would make `description` feel padded:

```yaml
description: >
  Analyze CSV and tabular data — compute statistics, add derived columns,
  generate charts, and clean messy data. Use when the user has a CSV, TSV,
  or Excel file and wants to explore, transform, or visualize it.
when_to_use: >
  Trigger even without explicit "CSV": spreadsheet, tabular data, pivot
  table, column calculations, data cleaning, bulk row operations.
```

If your `description` is already under ~800 chars and reads clearly, keep everything there. Only split into `when_to_use` if the description would otherwise feel bloated with synonyms and alternate phrasings.

**Hard limit for `description` alone: 1,024 chars** (enforced by `validate_skill.py`). The combined listing truncation at 1,536 chars is a display concern, not a validation concern.

## Writing rules

- **Imperative phrasing.** "Use this skill when..." not "This skill does...". The agent is choosing whether to act — tell it when to act.
- **User intent, not implementation.** Match against what users say, not what the skill does internally.
- **Be pushy.** Explicitly list triggering contexts, including ones where the user doesn't name the domain ("even if they don't explicitly mention 'CSV'").
- **Concise.** A few sentences to a short paragraph. Hard limit 1024 chars for `description`; useful range usually 200–600.
- **Include keywords** that real users would say (file extensions, tool names, problem statements).
- **Put the highest-signal content first.** Both `description` and `when_to_use` are truncated in the listing at 1,536 chars combined — front-load the words that most clearly distinguish your skill.

## Before / after

```yaml
# Before
description: Process CSV files.
```

```yaml
# After
description: >
  Analyze CSV and tabular data files — compute summary statistics,
  add derived columns, generate charts, and clean messy data. Use
  this skill when the user has a CSV, TSV, or Excel file and wants
  to explore, transform, or visualize the data, even if they don't
  explicitly mention "CSV" or "analysis."
```

The improved version is **more specific** about what the skill does (summary stats, derived columns, charts, cleaning) and **broader** about when it applies (CSV, TSV, Excel; even without explicit keywords).

## Designing trigger eval queries

Build a JSON file of ~20 realistic user prompts labeled with whether they should trigger:

```json
[
  {"query": "I've got a spreadsheet in ~/data/q4_results.xlsx with revenue in col C and expenses in col D — can you add a profit margin column and highlight anything under 10%?", "should_trigger": true},
  {"query": "whats the quickest way to convert this json file to yaml", "should_trigger": false}
]
```

Aim for 8–10 should-trigger queries and 8–10 should-not-trigger queries. Include:

- **Phrasing variation**: formal, casual, typos, abbreviations.
- **Explicitness variation**: some name the domain directly, others describe the need without naming it.
- **Detail variation**: terse vs. context-heavy prompts.
- **Complexity variation**: single-step vs. multi-step workflows where the skill's task is buried.
- **Realism**: file paths, "my manager asked me to...", actual column names, casual language.

The most valuable should-trigger queries are ones where the connection isn't obvious. The most valuable should-not-trigger queries are **near-misses** — same keywords, different actual need (e.g., "update Excel formulas" vs. CSV analysis; "upload CSV rows to postgres" vs. CSV analysis).

## Running the eval

Model behavior is nondeterministic, so run each query 3+ times and compute a trigger rate. A should-trigger query passes if rate > 0.5; a should-not-trigger passes if rate < 0.5.

Skeleton — replace `check_triggered` with whatever your client exposes:

```bash
#!/bin/bash
QUERIES_FILE="${1:?Usage: $0 <queries.json>}"
SKILL_NAME="my-skill"
RUNS=3

check_triggered() {
  local query="$1"
  claude -p "$query" --output-format json 2>/dev/null \
    | jq -e --arg skill "$SKILL_NAME" \
      'any(.messages[].content[]; .type == "tool_use" and .name == "Skill" and .input.skill == $skill)' \
      > /dev/null 2>&1
}

count=$(jq length "$QUERIES_FILE")
for i in $(seq 0 $((count - 1))); do
  query=$(jq -r ".[$i].query" "$QUERIES_FILE")
  should=$(jq -r ".[$i].should_trigger" "$QUERIES_FILE")
  triggers=0
  for run in $(seq 1 $RUNS); do
    check_triggered "$query" && triggers=$((triggers + 1))
  done
  jq -n --arg q "$query" --argjson s "$should" --argjson t "$triggers" --argjson r "$RUNS" \
    '{query:$q, should_trigger:$s, triggers:$t, runs:$r, trigger_rate:($t/$r)}'
done | jq -s '.'
```

## Avoiding overfitting

Split queries into **train (~60%)** and **validation (~40%)**, with proportional positive/negative balance in each. Use train-set failures to guide changes; only consult validation results to confirm changes generalize. Keep the split fixed across iterations.

## The optimization loop

1. **Evaluate** the current description on both sets.
2. **Identify train-set failures.** Which should-trigger queries didn't? Which should-not-trigger ones did?
3. **Revise:**
   - Should-trigger failures → broaden scope or add explicit trigger contexts.
   - Should-not-trigger false-triggers → add specificity, clarify boundary with adjacent skills.
   - Don't paste keywords from failed queries — that's overfitting. Address the underlying category.
   - If incremental tweaks stall, try a structurally different framing.
   - Watch the 1024-char limit; descriptions tend to grow.
4. **Repeat** 3–5 times.
5. **Pick the iteration with the best validation pass rate** — not necessarily the last one.

If performance plateaus after a few iterations, the queries themselves may be the issue (too easy, too hard, mislabeled).

## Final sanity check

After applying the winning description:

1. Verify it's under 1024 chars (`validate_skill.py` does this).
2. Manually try 5–10 fresh prompts that were never in the eval set.
3. Confirm the skill loads on positives and stays out of the way on negatives.
