```
  ___________   .__.__  .__    ________          __  .__        .__
 /   _____/  | _|__|  | |  |   \_____  \ _______/  |_|__| _____ |__|_______ ___________
 \_____  \|  |/ /  |  | |  |    /   |   \\____ \   __\  |/     \|  \___   // __ \_  __ \
 /        \    <|  |  |_|  |__ /    |    \  |_> >  | |  |  Y Y  \  |/    /\  ___/|  | \/
/_______  /__|_ \__|____/____/ \_______  /   __/|__| |__|__|_|  /__/_____ \\___  >__|
        \/     \/                      \/|__|                 \/         \/    \/
```

A Claude Code plugin marketplace with two skills for building and maintaining high-quality AI agent tooling. Uses the [agentskills.io](https://agentskills.io/) specification.

## Skills

### skill-optimizer

Audits, optimizes, validates, scaffolds, and trigger-evals Claude Agent Skills (SKILL.md files).

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

Scripts live in `skills/skill-optimizer/scripts/` and accept `--json` for machine-readable output.

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

---

### agent-tool-builder

Builds and reviews Python scripts intended to be called by AI agents as tools. Enforces a standard interface contract (structured JSON output, predictable exit codes, `--format`, `--quiet`, `--dry-run`) and catches performance anti-patterns before they cost agent round-trips.

**Design interview**

- Runs a decision-forcing interview to pin down the tool's data model, operations, flag set, JSON output shape, and failure modes before writing a line of code

**Interface contract enforcement**

- Validates mandatory flags (`--format`, `--quiet`), conditional flags (`--dry-run`, `--limit`, `--cursor`), exit code conventions, and stderr discipline
- Ensures output is parseable JSON by default — agents should never need to parse human-readable text

**Scaffolding**

- Scaffolds new agent tools from a PEP 723 template with argparse, `--format json/text/csv`, structured error output, and stub tests

**Performance auditing**

- Detects O(n²) string concatenation, recompiled regexes, and list-membership anti-patterns via AST analysis + optional cProfile profiling

Scripts live in `skills/agent-tool-builder/scripts/` and accept `--json` for machine-readable output.

| Script | Purpose |
|---|---|
| `validate_agent_tool.py` | Interface contract validation — flags, exit codes, output shape |
| `perf_check.py` | AST-based performance checker + optional cProfile profiling |
| `init_tool.py` | Scaffold a new agent tool from the bundled PEP 723 template |

## Installing

This repo is a Claude Code plugin marketplace. From inside Claude Code, add the marketplace and install either or both skills:

```
/plugin marketplace add rafaelh/skill-optimizer
/plugin install skill-optimizer@rafaelh-skill-optimizer
/plugin install agent-tool-builder@rafaelh-skill-optimizer
/reload-plugins
```

Once installed, skills activate automatically based on context — `skill-optimizer` when you ask Claude to audit or scaffold a SKILL.md; `agent-tool-builder` when you ask Claude to write or improve a script an agent will call.

## Requirements

- Python 3.14+
- Claude Code CLI (for trigger evals and description optimization in `skill-optimizer`)
- `ANTHROPIC_API_KEY` in environment (optional — enables exact token counts via the SDK)
