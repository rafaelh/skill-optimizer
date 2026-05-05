```
  ___________   .__.__  .__    ________          __  .__        .__
 /   _____/  | _|__|  | |  |   \_____  \ _______/  |_|__| _____ |__|_______ ___________
 \_____  \|  |/ /  |  | |  |    /   |   \\____ \   __\  |/     \|  \___   // __ \_  __ \
 /        \    <|  |  |_|  |__ /    |    \  |_> >  | |  |  Y Y  \  |/    /\  ___/|  | \/
/_______  /__|_ \__|____/____/ \_______  /   __/|__| |__|__|_|  /__/_____ \\___  >__|
        \/     \/                      \/|__|                 \/         \/    \/
```

A Claude Code skill that builds, audits, and improves other Claude Agent Skills. Uses the [agentskills.io](https://agentskills.io/) specification. It incorporates anthropic's guidance, and content from their skill-builder as well.

## What it does

**Validation and analysis**

- Validates SKILL.md frontmatter, body structure, & script references against the spec
- Analyzes token budget, section balance, progressive disclosure quality, & gotchas coverage
- Recommends whether to introduce helper scripts, and what patterns they should follow
- Detects description overlap between sibling skills (bag-of-words cosine similarity)

**Description optimization**

- Rewrites the `description` field with imperative phrasing and concrete trigger contexts
- Runs a trigger-rate eval: invokes `claude -p` against a labeled query set and counts how often the skill activates
- Iterates candidate descriptions against train failures, scores against a held-out validation set, and proposes the winner

**Scaffolding**

- Scaffolds new skills from templates: SKILL.md, `scripts/example.py` (PEP 723, argparse, `--json`), `references/`, `assets/`, and `tests/`

**Script quality**

- Checks helper scripts for performance anti-patterns (AST static analysis + optional cProfile runtime profiling)
- Counts tokens via the Anthropic SDK when available, heuristic fallback otherwise

## Scripts

All scripts live in `skills/skill-optimizer/scripts/` and accept `--json` for machine-readable output.

| Script | Purpose |
|---|---|
| `validate_skill.py` | Frontmatter + body validation, exit 1 on any failure |
| `analyze_skill.py` | Token count, section balance, progressive disclosure quality |
| `recommend_scripts.py` | Advises on helper scripts — what to add and what patterns to follow |
| `detect_skill_overlap.py` | Cosine similarity between skill descriptions; single-skill or all-pairs mode |
| `eval_triggers.py` | Trigger-rate eval against a labeled query set; train/validation split |
| `optimize_description.py` | Multi-round description optimizer; propose-only by default, `--apply` to write |
| `init_skill.py` | Scaffold a new skill directory from bundled templates |
| `count_tokens.py` | Token counter; exact via Anthropic SDK, heuristic fallback |
| `perf_check.py` | AST-based performance checker + optional cProfile profiling |

## Installing

This repo is a Claude Code plugin marketplace. From inside Claude Code, add the marketplace and install the skill:

```
/plugin marketplace add rafaelh/skill-optimizer
/plugin install skill-optimizer@rafaelh-skill-optimizer
/reload-plugins
```

Once installed, the `skill-optimizer` skill activates automatically when you ask Claude to create, audit, or fix a skill — e.g. "audit my SKILL.md", "why isn't this skill activating?", "scaffold a new skill called search-jira".

## Requirements

- Python 3.14+
- Claude Code CLI (for trigger evals and description optimization)
- `ANTHROPIC_API_KEY` in environment (optional — enables exact token counts via the SDK)
