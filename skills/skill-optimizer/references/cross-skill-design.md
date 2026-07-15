# Cross-skill design

When more than one skill is loaded, descriptions compete. An overlap between two descriptions doesn't always cause misfires, but it's the most common reason a skill triggers on prompts that should belong to a sibling — or fails to trigger because a more confident sibling captured the request.

## When this matters

Read this guide when:

- The user has multiple skills in their skills directory (e.g. `~/.claude/skills/`, `<repo>/.github/prompts/`, or a project's `.claude/skills/`).
- `detect_skill_overlap.py` has flagged a collision.
- Two skills cover related domains (e.g. "csv-analysis" + "excel-formulas") and you can predict near-miss confusion.
- A new skill is being scaffolded and you want to verify it doesn't collide with existing ones.

## Single coherent unit of work

The strongest defense against overlap is scope discipline. A skill should encapsulate **one coherent unit of work**:

| Scope                                         | Verdict                                                       |
|-----------------------------------------------|---------------------------------------------------------------|
| "Analyze CSV files"                           | One unit ✓                                                    |
| "Analyze CSV files and administer the database that stores them" | Two units ✗ — split |
| "Generate, analyze, and email weekly reports" | Three units (sometimes ✓ if always run together as one workflow, often ✗) |
| "Review code for security issues"             | One unit ✓                                                    |
| "Review code, run linters, and refactor"      | Borderline — depends on whether your team uses these together |

Too narrow → multiple skills load for one task, with overhead and conflicting instructions. Too broad → hard to activate precisely. When in doubt, split: it's easier to bundle two related skills via a higher-level skill that delegates than to extract one from a monolith.

## Disambiguation in descriptions

When two skills are intentionally close, lean into specificity in the descriptions:

```yaml
# Skill A — overlaps with Skill B on "csv"
description: >
  Analyze CSV / Excel data: compute summary statistics, generate charts,
  clean messy values. Use when the user wants to understand or transform
  the *data inside* a tabular file. NOT for loading, exporting, or
  migrating that data — see csv-loader for that.
```

```yaml
# Skill B — the sibling
description: >
  Load CSV / TSV files into databases (Postgres, MySQL, BigQuery) or
  export tables back to CSV. Use when the user wants to *move data
  between* a tabular file and a database. NOT for analysis, charts, or
  cleaning — see csv-analysis for that.
```

The explicit "NOT for X — see Y" pointer is the disambiguation. It costs ~50 chars of description budget and saves the agent a confused activation.

## Detecting overlap

`scripts/detect_skill_overlap.py` flags description pairs above a similarity threshold. Two modes:

```bash
# Mode 1: single skill against siblings
python3 detect_skill_overlap.py <skills-dir>/my-skill --against <skills-dir>

# Mode 2: all-pairs scan of a parent directory
python3 detect_skill_overlap.py <skills-dir>
```

The default similarity is bag-of-words cosine over description tokens (lowercased, stopwords dropped). Threshold defaults to 0.5; tune with `--threshold`.

A high score is a flag, not a verdict. Two genuinely-different skills can share heavy keyword overlap (both about "code" or both about "PDFs"). Use the script's output as input to a manual review.

## What the script reports

```json
{
  "pairs": [
    {
      "a": "csv-analysis",
      "b": "csv-loader",
      "similarity": 0.71,
      "shared_keywords": ["csv", "tsv", "excel", "data"],
      "code": "overlap.description.collision"
    }
  ]
}
```

The `shared_keywords` field is the most actionable. If three or more keywords are shared, the descriptions are competing for the same prompts. Fix by:

- Adding the explicit "NOT for X" disambiguator above.
- Changing one description's vocabulary to lead with the *operation* rather than the *domain* — "load tables into databases" vs. "work with CSV data."

## When overlap is acceptable

Some overlap is fine and even desirable:

- **Specialized variants of a parent.** A `pdf-forms` skill can overlap heavily with a `pdf-processing` skill if the parent always defers to the specialist.
- **Different surfaces of the same domain.** `git-rebase` and `git-bisect` both match "git" prompts; they only collide on prompts that don't specify the operation.
- **Chained workflows.** A `data-pipeline` skill that orchestrates `csv-analysis` and `csv-loader` may legitimately overlap both.

The pragmatic rule: overlap is a problem when the agent activates the *wrong* skill for a given user intent. If the agent always picks correctly despite a high similarity score, the overlap is harmless.

## Iterating with eval data

If `detect_skill_overlap.py` flags a pair, design eval queries that probe the boundary, run `eval_triggers.py` against both skills with the same query set, and look for:

- Queries where the wrong skill triggers (boundary failure).
- Queries where neither triggers (gap — neither description claims this kind of prompt).
- Queries where both trigger (over-broad descriptions).

Fix descriptions one at a time, re-running the eval after each change. Re-running [evaluation.md](evaluation.md)'s loop on each skill independently won't surface the cross-skill confusion — you have to evaluate them jointly.
