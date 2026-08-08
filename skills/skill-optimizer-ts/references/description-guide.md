# Optimizing skill descriptions

The `description` field is the single point at which an agent decides whether to load your skill. Get it wrong and the skill never runs. This guide covers what to write and how to revise one that isn't pulling its weight.

Measuring trigger rate by replaying real prompts through an agent is out of scope here — the native `skill-creator` skill does that. This guide is what you apply before and after that measurement.

## Why the description carries everything

Agents use progressive disclosure: at startup they load only `name` + `description` for every available skill. Full `SKILL.md` content loads only when they decide a skill matches the user's task. So:

- An under-specified description means the skill won't trigger when it should.
- An over-broad description means it triggers when it shouldn't and pollutes the agent's context.

One nuance: agents typically only consult skills for tasks they can't easily handle alone. "Read this PDF" may not trigger a PDF skill even with a perfect description, because the agent can do it directly. Specialized knowledge, unfamiliar APIs, and uncommon formats are where descriptions earn their keep.

## `description` vs `when_to_use`

Some platforms (notably Claude Code) support a companion field `when_to_use` for supplementary trigger context. The two fields are appended together and shown in the skill listing, **truncated at 1,536 characters combined**. Use `description` for the core identity of the skill; use `when_to_use` for additional trigger phrases, aliases, or domain synonyms that would make `description` feel padded:

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

**Hard limit for `description` alone: 1,024 chars** (enforced by `validate_skill.ts`). The combined listing truncation at 1,536 chars is a display concern, not a validation concern.

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

## Revising a weak description

Diagnose from the failure direction:

- **Should have triggered, didn't** → broaden scope, or add explicit trigger contexts for phrasings that don't name the domain.
- **Triggered when it shouldn't** → add specificity, and draw the boundary with the adjacent skill explicitly ("NOT for X — see Y"). See [cross-skill-design.md](cross-skill-design.md).
- **Don't paste keywords from a failing prompt** — that's overfitting to one phrasing. Address the underlying category it represents.
- **If incremental tweaks stall**, try a structurally different framing rather than another round of word-tuning.
- **Watch the 1024-char limit** — descriptions grow with every revision.

The cases that most often expose a weak description are the ones where the connection isn't obvious (a should-trigger prompt that never names the domain) and **near-misses** — same keywords, different actual need ("update Excel formulas" vs. CSV analysis; "upload CSV rows to postgres" vs. CSV analysis). Write the description so it separates those two classes, not so it matches a list of prompts.

## Final sanity check

After applying a revised description:

1. Verify it's under 1024 chars (`validate_skill.ts` does this).
2. Re-run `detect_skill_overlap.ts` against the sibling skills — a broadened description is the usual cause of a new collision.
3. Manually try 5–10 fresh prompts and confirm the skill loads on positives and stays out of the way on near-miss negatives.
