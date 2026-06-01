---
name: skill-optimizer
description: Audit, optimize, validate, scaffold, security-audit, and trigger-eval Claude Agent Skills (SKILL.md files). Use this skill when the user wants to create a new skill, improve an existing one, debug why a skill isn't activating, restructure a bloated SKILL.md, validate frontmatter against the Agent Skills specification, run a trigger-rate eval, detect description overlap between sibling skills, audit a skill's bundled scripts, check a skill for security issues against the OWASP Agentic Skills Top 10 (over-privileged tools, hardcoded secrets, unsafe deserialization, supply-chain risks), or apply patterns like gotchas, validation loops, and progressive disclosure. Trigger even when the user doesn't say "skill" — e.g., "this prompt file isn't activating", "rewrite my SKILL.md", "why doesn't Claude pick up my custom command", "scaffold a new skill", "audit my .claude/skills directory", "is this skill safe to install", or "test whether my description triggers reliably".
compatibility: Designed for Claude Code. Requires Python 3.14+ (stdlib only on most paths). eval_triggers.py and optimize_description.py require the `claude` CLI on PATH. count_tokens.py uses the anthropic SDK if ANTHROPIC_API_KEY is set, else falls back to a heuristic. Scripts using PEP 723 metadata run cleanest under `uv run`.
effort: high
allowed-tools: Bash(python3 *) Read Edit Write
metadata:
  version: "2.0"
  author: Rafe Hart
---

# Skill Optimizer

Improve a Claude skill so it activates reliably, fits inside its token budget, follows the Agent Skills specification, and bundles real tooling instead of asking the agent to reinvent it on every run.

## When you reach for this skill

The user is asking you to:

- Scaffold a new skill from scratch
- Audit or improve an existing skill (description, body, structure)
- Diagnose why a skill is not triggering
- Validate frontmatter or layout against the spec
- Run a trigger-rate eval against a labeled query set
- Iterate the description toward a higher validation pass rate
- Detect description overlap between sibling skills
- Audit a skill's bundled scripts for interface, structure, and performance issues
- Security-audit a skill against the OWASP Agentic Skills Top 10 (before installing or publishing one)

## Workflow

### 1. Locate or scaffold the skill

If the user is **starting fresh**, run:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/init_skill.py" \
  ~/.claude/skills --name <slug> --description "<text>" --json
```

This produces a layout that already passes the validators: SKILL.md from `assets/templates/SKILL.md.template`, an `example.py` from `assets/templates/script.py.template`, and `references/` + `assets/` + `tests/` placeholders. Pass `--minimal` for SKILL.md only.

If the skill **already exists**, find its `SKILL.md`. Skills live under `~/.claude/skills/<name>/SKILL.md` (user) or `<repo>/.claude/skills/<name>/SKILL.md` (project). The frontmatter `name` field MUST match the parent directory name.

### 2. Run the static validators

Always start with the bundled scripts before reading the body. All accept `--json` for machine-readable output and exit non-zero on FAIL-level issues.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/validate_skill.py" <skill-dir>
python3 "${CLAUDE_SKILL_DIR}/scripts/analyze_skill.py" <skill-dir>
python3 "${CLAUDE_SKILL_DIR}/scripts/count_tokens.py" <skill-dir>/SKILL.md
```

`${CLAUDE_SKILL_DIR}` resolves to the directory containing this SKILL.md.

- `validate_skill.py` enforces the spec: required fields, name regex (lowercase, hyphens, no leading/trailing/consecutive hyphens, max 64 chars), description length (1–1024), name matches directory, optional field constraints, broken file references. Codes follow `validate.<surface>.<concern>` (e.g. `validate.name.dir-mismatch`, `validate.description.too-long`, `validate.reference.broken`).
- `analyze_skill.py` flags content anti-patterns: declarative description openings, missing trigger contexts, body over 500 lines / 5000 tokens, generic filler, references introduced without a load condition. Codes follow `analyze.<surface>.<concern>`.
- `count_tokens.py` reports an exact token count via the anthropic SDK when `ANTHROPIC_API_KEY` is set, else a calibrated `len(text) / 3.5` heuristic. Use this when you want a number tighter than the bucketed thresholds in `analyze_skill.py`.

Pass `--exit-on-warn` when treating warnings as failures (CI / pre-commit).

### 3. Optimize the description

The `description` field carries the entire triggering burden. The agent only loads `name` + `description` at startup; if those don't say *when* to use the skill, it never gets activated.

Read [references/description-guide.md](references/description-guide.md) when:

- The user reports the skill isn't activating
- The current description is under ~150 characters
- The description starts with "This skill...", "A skill that...", or describes mechanics rather than user intent
- You're about to revise the description and want the writing rules

Quick rules (full guide in the reference):

- **Imperative**: "Use this skill when..." not "This skill does..."
- **Intent-focused**: describe what the user is trying to achieve, not internals
- **Pushy**: list trigger contexts including ones where the user doesn't name the domain
- **Hard limit**: 1024 characters

### 4. Optimize the body

Read [references/content-patterns.md](references/content-patterns.md) before restructuring the body. Apply these rules in order:

1. **Cut what the agent already knows.** If removing a paragraph wouldn't degrade behavior, delete it.
2. **Convert declarations into procedures.** Replace one-shot answers with reusable methods.
3. **Add a Gotchas section** for non-obvious environment facts. Keep gotchas in `SKILL.md` itself — the agent must read them before hitting the situation.
4. **Provide defaults, not menus.** Pick one library/approach; mention alternatives in one line.
5. **Match prescriptiveness to fragility.** Code review = freedom + "why". Migrations = exact command sequence with "do not modify".
6. **Bundle scripts** for any logic the agent would otherwise reinvent each run. Put them in `scripts/`.
7. **Apply progressive disclosure.** Keep `SKILL.md` under 500 lines / ~5000 tokens. Move detail into `references/`, `scripts/`, `assets/`. Tell the agent *when* to load each reference file.

### 5. Audit bundled scripts

Three complementary checks. **5a** is skill-level (what should *become* a script); **5b** and **5c** are per-script and are delegated to the [agent-tool-builder](../agent-tool-builder/SKILL.md) skill, which owns the canonical contract for agent-callable Python.

**5a. SKILL.md-level recommendations.**

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/recommend_scripts.py" <skill-dir>
```

Surfaces procedures the agent would re-derive on every run:

- `recommend.script.extract-procedure` — long bash blocks (≥6 lines) that should become scripts
- `recommend.skill-md.missing` — the skill has no SKILL.md

**5b. Per-script contract compliance.**

```bash
for s in <skill-dir>/scripts/*.py; do
  python3 "${CLAUDE_SKILL_DIR}/../agent-tool-builder/scripts/validate_agent_tool.py" "$s" --json
done
```

Checks each script against the agent-tool interface contract: `argparse` present, `--format` and `--quiet` flags, reachable exit codes `0/1/2/3`, no `input()`, no error-JSON on stdout, PEP 723 block (with both `requires-python` and `dependencies`) when non-stdlib imports are detected. See [agent-tool-builder/SKILL.md](../agent-tool-builder/SKILL.md) and [agent-tool-builder/references/interface-contract.md](../agent-tool-builder/references/interface-contract.md).

**5c. Implementation quality.**

```bash
python3 "${CLAUDE_SKILL_DIR}/../agent-tool-builder/scripts/perf_check.py" <skill-dir>/scripts/ --json
```

Static analysis for performance anti-patterns: string concatenation in loops, regex recompiled per iteration, `.append()` in tight loops, repeated subscripts, exception-as-control-flow, pandas `.iterrows()`, etc. See [agent-tool-builder/references/perf-findings.md](../agent-tool-builder/references/perf-findings.md) for interpreting findings. Skip a script's findings only with conscious justification.

The skill-optimizer's own conventions for bundled scripts (lifted from [agentskills.io/skill-creation/using-scripts](https://agentskills.io/skill-creation/using-scripts)):

- Stdout = data; stderr = diagnostics. Never mix.
- Scripts expose `--format json|text` (default `json`) so agents get structured output without an extra flag.
- Reject ambiguous input with a clear error rather than guessing.
- Add `--dry-run` for stateful or destructive operations.
- Sanitize echoed user content (ANSI escapes, control characters) when scripts read SKILL.md or vault data — see `scripts/skill_lib.py::sanitize_for_echo`.
- Pin output size; default to summary, support pagination flags for detail.

### 6. Security audit (OWASP Agentic Skills Top 10)

A skill is executable trust: SKILL.md becomes agent instructions and bundled scripts run with the agent's privileges. Audit any skill you didn't write or intend to publish.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/audit_security.py" <skill-dir>
```

Static, high-signal scan mapped to the OWASP Agentic Skills Top 10. Findings carry an `AST##` id; exit is non-zero only on FAIL (hardcoded secrets), so pass `--exit-on-warn` in CI. Codes follow `security.<surface>.<concern>`:

- `security.tools.unrestricted-bash` / `security.tools.broad-bash-glob` (AST03) — `allowed-tools` grants bare `Bash` or a `Bash(*)` wildcard. Scope to the command prefix actually used.
- `security.secret.hardcoded` (AST04, **FAIL**) — an embedded API key, token, or private-key block. Move it to an env var.
- `security.script.unsafe-deserialization` (AST05) — pickle, `yaml.load`, eval/exec, marshal on untrusted data.
- `security.script.shell-injection` / `security.script.dangerous-fs` (AST06) — `shell=True`, `os.system`, `shutil.rmtree`.
- `security.exec.curl-pipe-shell` / `security.deps.unpinned` (AST02) — fetch-and-run, or unpinned PEP 723 deps.
- `security.body.hidden-unicode` (AST01) — invisible/bidi-override characters hidden in instructions.

The scan covers the mechanically-detectable subset. The process risks — AST07 (update drift), AST09 (no governance), AST10 (cross-platform reuse) — have no static signal and need a human pass.

Read [references/security.md](references/security.md) when:

- The auditor reports a finding and you want the rationale behind its `AST##` code
- You're deciding how tightly to scope `allowed-tools`, or how to handle a skill that needs secrets, shells out, or deserializes data
- You're doing the pre-publication checklist for a skill you intend to share

### 7. Cross-skill overlap check

When the skill lives alongside others, run:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/detect_skill_overlap.py" ~/.claude/skills/ --json
```

Or for a single skill against siblings:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/detect_skill_overlap.py" <skill-dir> \
  --against ~/.claude/skills/
```

Read [references/cross-skill-design.md](references/cross-skill-design.md) when:

- The script flags a pair above the threshold
- You're scaffolding a skill that overlaps an existing one
- A user reports the wrong skill activated for their request

### 8. Trigger eval and description optimization

This is the most direct way to know whether a description actually works.

**Build a labeled query set.** When starting from scratch, read [assets/templates/eval-queries.json.template](assets/templates/eval-queries.json.template) for the file shape and ~12 worked examples covering positive/negative/near-miss patterns. Before running the eval, validate the query set against [assets/schemas/eval-queries.schema.json](assets/schemas/eval-queries.schema.json) if you've added new fields or want machine confirmation that the structure is right.

**Run a single eval round:**

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/eval_triggers.py" \
  --queries queries.json --skill-name <name> --runs 3 --json
```

**Iterate the description automatically:**

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/optimize_description.py" \
  <skill-dir> --queries queries.json --rounds 3 --candidates 3 --json
```

Default is propose-only; pass `--apply` to write the winner to SKILL.md (creates `SKILL.md.bak`).

Read [references/evaluation.md](references/evaluation.md) when:

- Designing the query set (positive/negative balance, near-misses)
- Interpreting eval output (what counts as a pass)
- The optimization loop plateaus and you need to diagnose the queries themselves

### 9. Re-validate

Run all the static validators again. Iterate until `validate_skill.py` passes, `analyze_skill.py` warnings are addressed (or consciously ignored), `recommend_scripts.py`, `validate_agent_tool.py`, and `perf_check.py` either report nothing material or you've made conscious decisions about each remaining opportunity, `audit_security.py` shows no FAIL findings and each WARN is fixed or consciously accepted, and `detect_skill_overlap.py` shows no unintended collisions.

## Gotchas

- The `claude -p --output-format json` schema may shift between Claude Code versions. If `eval_triggers.py` reports zero triggers across every query in a previously-passing eval, suspect a schema change before assuming the description regressed.
- `optimize_description.py` is nondeterministic — running it twice on the same inputs can produce different "winning" descriptions. The `.bak` file is the safe rollback path; git is the safer one.
- `count_tokens.py` returns `exact: false` whenever it falls back to the heuristic. The agent should treat heuristic counts as ±20% — fine for "is this over budget" decisions, not fine for fine-grained budget arithmetic.
- `detect_skill_overlap.py` flags pairs above the threshold; it does not prove a misfire. Use the `shared_keywords` field as the actionable signal — three or more shared domain keywords is the real overlap.
- The `name` field MUST equal the parent directory name. Renaming a skill means renaming both the directory and the frontmatter, in lockstep, or `validate_skill.py` reports `validate.name.dir-mismatch`.
- `audit_security.py` is a regex/token scanner: it blanks Python strings and comments before the code-construct checks (so `eval(` in a docstring isn't a hit) and skips `tests/` (mock secrets live there). It still surfaces true positives you *accept* after triage — an intentional `exec()`, a deliberately loose pin. A WARN is a prompt for a conscious decision, not an automatic defect. Conversely, a clean scan is not a safety guarantee: AST07/09/10 are process risks with no static signal — read the SKILL.md as the instructions an agent will follow.

## Anti-patterns to flag immediately

- `description: Helps with PDFs.` — too short, no trigger contexts.
- `description: This skill processes CSVs.` — declarative; rewrite imperative.
- 800-line `SKILL.md` with embedded reference tables — split via progressive disclosure.
- Mega-skill bundling unrelated workflows — one skill, one job.
- Generic filler (`follow best practices`, `handle errors appropriately`) — replace with concrete gotchas.
- Reference files mentioned without a load trigger ("see references/ for details") — agent won't load them.
- `name`: uppercase, leading/trailing hyphen, consecutive hyphens, or mismatch with directory.
- Bundled scripts that hardcode secrets or `cd` into absolute paths outside the skill. (AST04)
- Over-privileged `allowed-tools` — bare `Bash` or `Bash(*)` instead of a scoped prefix like `Bash(python3 *)`. (AST03)
- `curl … | sh` fetch-and-run, or unpinned PEP 723 dependencies (`pkg>=1.0` instead of `pkg==1.2.3`). (AST02)
- Unsafe deserialization of untrusted data — `pickle.loads`, `yaml.load` without `SafeLoader`, `eval`/`exec`, or `subprocess(..., shell=True)`. (AST05/AST06)
- Bundled scripts that lack `argparse` (so `--help` is missing) or print free-form text only (so agents can't structure-parse).
- Scripts that echo SKILL.md content verbatim without sanitization — ANSI escapes and control characters in untrusted skill files can confuse the calling agent or terminal.
- Two skills with bag-of-words cosine ≥ 0.5 over their descriptions and no explicit "NOT for X — see Y" disambiguator.

## Platform notes

The scripts work on macOS, Linux, and Windows (Python 3.14+). On Windows:

- Use `python` or `py -3` instead of `python3` if your install lacks the `python3` alias.
- The `~` in paths is expanded by Python via `Path.expanduser()`, so command-line args like `~/.claude/skills/...` work even from `cmd.exe`.
- All file I/O is explicit UTF-8.
- Symlink-based tests are auto-skipped if your account lacks symlink privileges (no Developer Mode / not admin); other tests run normally.

For PEP 723 scripts (`count_tokens.py` is the only current one), invoking via `uv run scripts/<name>.py` resolves the dependencies automatically. Without `uv`, the script falls back to its heuristic path when an import fails.

## Specification quick reference

**Base spec** (`name`, `description` required; optional: `license`, `compatibility` ≤500 chars, `metadata`, `allowed-tools`).

**Claude Code extensions** (all optional): `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, `shell`.

The full JSON Schema lives at [assets/schemas/frontmatter.schema.json](assets/schemas/frontmatter.schema.json).

Read [references/specification.md](references/specification.md) when:

- You need the full field constraints or the invocation-control matrix
- You're checking valid `name` examples or string substitutions (`$ARGUMENTS`, `${CLAUDE_SKILL_DIR}`)
- You're using dynamic context injection (`` !`command` `` syntax) or `context: fork`
- You're reviewing directory layout rules
