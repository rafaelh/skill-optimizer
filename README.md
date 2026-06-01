```
  ___________   .__.__  .__    ________          __  .__        .__
 /   _____/  | _|__|  | |  |   \_____  \ _______/  |_|__| _____ |__|_______ ___________
 \_____  \|  |/ /  |  | |  |    /   |   \\____ \   __\  |/     \|  \___   // __ \_  __ \
 /        \    <|  |  |_|  |__ /    |    \  |_> >  | |  |  Y Y  \  |/    /\  ___/|  | \/
/_______  /__|_ \__|____/____/ \_______  /   __/|__| |__|__|_|  /__/_____ \\___  >__|
        \/     \/                      \/|__|                 \/         \/    \/
```

A Claude Code plugin marketplace with two skills for building and maintaining high-quality AI agent tooling. Uses the [agentskills.io](https://agentskills.io/) specification.

> **Python-first:** Both skills assume bundled scripts are written in Python (PEP 723 inline metadata, `argparse`, `--json` output). The validation, scaffolding, performance analysis, and security checks all target Python. If your skill's bundled scripts are in another language (TypeScript, Bash, Go, etc.), the script-level checks won't apply and you'll need to enforce your own interface contract and quality gates.

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
- Generated scripts follow Python best practices: stdlib-only by default, PEP 723 inline metadata for third-party deps, `argparse` for flags, structured JSON output

**Script quality** *(Python only)*

- Checks helper scripts for performance anti-patterns (AST static analysis + optional cProfile runtime profiling)
- Validates the agent-tool interface contract: `--format`, `--quiet`, `--dry-run` flags; exit codes `0/1/2/3`; no `input()`, no free-form stdout errors
- Counts tokens via the Anthropic SDK when available, heuristic fallback otherwise

**Security**

- Audits a skill against the [OWASP Agentic Skills Top 10](https://owasp.org/www-project-agentic-skills-top-10/): over-privileged `allowed-tools`, hardcoded secrets, unsafe deserialization, shell injection, supply-chain (fetch-and-run, unpinned deps), and hidden-unicode instructions
- Security checks cover SKILL.md and reference files regardless of language; Python-specific checks (unsafe deserialization, shell injection, dependency pinning) apply only to `.py` scripts

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
| `audit_security.py` | OWASP Agentic Skills Top 10 audit; FAILs on hardcoded secrets, WARNs on the rest |

---

### agent-tool-builder

Builds and reviews **Python** scripts intended to be called by AI agents as tools. Enforces a standard interface contract (structured JSON output, predictable exit codes, `--format`, `--quiet`, `--dry-run`) and catches performance anti-patterns before they cost agent round-trips.

> If your agent tools are written in TypeScript, Bash, or another language, this skill's validation and scaffolding won't apply. You'd need a separate contract enforcer for that language's conventions.

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

- Python 3.14+ (all bundled scripts are stdlib-only except where PEP 723 metadata declares third-party deps)
- Claude Code CLI (for trigger evals and description optimization in `skill-optimizer`)
- `ANTHROPIC_API_KEY` in environment (optional — enables exact token counts via the SDK)

## Language scope

These skills are opinionated about Python. Python is the default for skill scripts for a few concrete reasons:

- **Self-contained scripts.** PEP 723 inline metadata lets a single `.py` file declare its own dependencies. `uv run script.py` resolves and installs them in an isolated environment on first run — no `package.json`, no build step, no `node_modules`. An agent can call it immediately after the skill is installed.
- **No compilation.** The agent invokes scripts directly and reads their stdout. Languages that require a build step add fragility: the artifact may be stale, missing, or built for the wrong platform. Python runs from source.
- **Stdlib breadth.** `argparse`, `json`, `pathlib`, `subprocess`, `ast`, `tokenize` — the patterns that make a good agent tool are all in the standard library. Most scripts in this repo have zero third-party dependencies.
- **Ubiquity on agent hosts.** Python 3 ships with macOS, most Linux distributions, and WSL. An agent tool written in Python is more likely to just work across the environments Claude Code runs in than one that requires a language runtime to be separately installed.

If you're building skills whose bundled scripts use a different language:

- SKILL.md structure, frontmatter validation, description optimization, trigger evals, and the OWASP checks on SKILL.md/references are all language-agnostic and work as-is.
- Script-level checks (`validate_agent_tool.py`, `perf_check.py`, dependency pinning, unsafe deserialization) won't run or won't be meaningful.
- You'd define your own interface contract and quality gates for that language.
