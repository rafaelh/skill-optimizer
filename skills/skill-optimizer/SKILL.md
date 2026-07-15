---
name: skill-optimizer
description: "Audit, optimize, validate, scaffold, and trigger-eval Agent Skills (SKILL.md files) for any agent compliant with the agentskills.io spec — Claude Code, GitHub Copilot, Codex, VS Code, and others. USE FOR: saving coding preferences; troubleshooting why instructions/skills/agents are ignored or not invoked; configuring applyTo patterns; defining tool restrictions; creating custom agent modes or specialized workflows; packaging domain knowledge; fixing YAML frontmatter syntax. Also trigger when the user doesn't say \"skill\" — e.g., \"this prompt file isn't activating\", \"rewrite my SKILL.md\", \"why doesn't my agent pick up my custom command\", \"scaffold a new skill\", \"audit my skills directory\", or \"test whether my description triggers reliably\"."
compatibility: Works with any agent platform supporting the agentskills.io specification (Claude Code, GitHub Copilot, Codex, VS Code). Requires Python 3.14+ (stdlib only on most paths). eval_triggers.py and optimize_description.py require an agent CLI on PATH (claude, copilot, or codex — configurable via --cli-bin). count_tokens.py uses the anthropic SDK if ANTHROPIC_API_KEY is set, else falls back to a heuristic. Scripts using PEP 723 metadata run cleanest under `uv run`.
metadata:
  version: "3.0"
  author: Rafe Hart
---

# Skill Optimizer

This skill operates in three modes. Identify which mode applies, then follow that mode's workflow exclusively.

## Mode selection

| Mode       | Use when the user wants to…             |
|------------|-----------------------------------------|
| **Create** | Scaffold a new skill from scratch       |
| **Audit**  | Improve, validate, restructure, or security-audit an existing skill |
| **Eval**   | Run trigger evals, optimize a description for activation rate, or resolve overlap between sibling skills |

If the request spans multiple modes (e.g. "create a skill and make sure it doesn't overlap my others"), execute them in order: Create → Audit → Eval.

---

## Mode 1 — Create

Scaffold a new skill so it passes validators out of the box.

### Workflow

1. **Run the scaffolder.**

```bash
python3 "${SKILL_DIR}/scripts/init_skill.py" \
  <parent-dir> --name <slug> --description "<text>" --json
```

This produces: SKILL.md from `assets/templates/SKILL.md.template`, an `example.py` from `assets/templates/script.py.template`, and `references/` + `assets/` + `tests/` placeholders. Pass `--minimal` for SKILL.md only.

Skills live in platform-specific locations:

- **Claude Code**: `~/.claude/skills/<name>/` (user) or `<repo>/.claude/skills/<name>/` (project)
- **GitHub Copilot / VS Code**: `~/.config/Code/User/prompts/<name>/` or `<repo>/.github/prompts/<name>/`
- **Codex**: `<repo>/.codex/skills/<name>/`

2. **Write the description.** Read [references/description-guide.md](references/description-guide.md) for the full writing rules. Quick rules:

- **Imperative**: "Use this skill when..." not "This skill does..."
- **Intent-focused**: describe what the user is trying to achieve, not internals
- **Pushy**: list trigger contexts including ones where the user doesn't name the domain
- **Hard limit**: 1024 characters

3. **Write the body.** Read [references/content-patterns.md](references/content-patterns.md) before structuring. Key principles:

- Cut what the agent already knows
- Convert declarations into procedures
- Add a Gotchas section for non-obvious environment facts
- Provide defaults, not menus
- Bundle scripts for any logic the agent would otherwise reinvent each run
- Keep SKILL.md under 500 lines / ~5000 tokens; move detail into `references/`

4. **Validate immediately.** Run the static validators (see Mode 2, step 1) to confirm the new skill passes before moving on.

### Gotchas (Create)

- The `name` field MUST equal the parent directory name. Renaming a skill means renaming both the directory and the frontmatter in lockstep.
- `init_skill.py` won't overwrite an existing directory — delete or rename first.

---

## Mode 2 — Audit

Validate, analyze, optimize, and security-audit an existing skill.

### Workflow

1. **Run static validators.** All accept `--json` and exit non-zero on FAIL.

```bash
python3 "${SKILL_DIR}/scripts/validate_skill.py" <skill-dir>
python3 "${SKILL_DIR}/scripts/analyze_skill.py" <skill-dir>
python3 "${SKILL_DIR}/scripts/count_tokens.py" <skill-dir>/SKILL.md
```

`${SKILL_DIR}` resolves to the directory containing this SKILL.md (`${CLAUDE_SKILL_DIR}` on Claude Code).

- `validate_skill.py` — spec enforcement: required fields, name regex, description length (1–1024), directory match, broken file references. Codes: `validate.<surface>.<concern>`.
- `analyze_skill.py` — content anti-patterns: declarative openings, missing trigger contexts, body over 500 lines / 5000 tokens, generic filler, unguarded references. Codes: `analyze.<surface>.<concern>`.
- `count_tokens.py` — exact count (anthropic SDK) or calibrated heuristic (`len(text) / 3.5`). Returns `exact: false` on heuristic; treat as ±20%.

Pass `--exit-on-warn` to treat warnings as failures (CI / pre-commit).

2. **Optimize the description.** Read [references/description-guide.md](references/description-guide.md) when:

- The skill isn't activating
- Description is under ~150 characters or starts with "This skill..."
- You're about to revise the description

3. **Optimize the body.** Read [references/content-patterns.md](references/content-patterns.md), then apply in order:

- Cut what the agent already knows
- Convert declarations into procedures
- Add Gotchas for non-obvious environment facts
- Provide defaults, not menus
- Match prescriptiveness to fragility
- Bundle scripts for repeated logic
- Apply progressive disclosure (detail into `references/`, `scripts/`, `assets/`)

4. **Audit bundled scripts.**

**4a. SKILL.md-level — what should become a script:**

```bash
python3 "${SKILL_DIR}/scripts/recommend_scripts.py" <skill-dir>
```

**4b. Per-script contract compliance** (delegated to [agent-tool-builder](../agent-tool-builder/SKILL.md)):

```bash
for s in <skill-dir>/scripts/*.py; do
  python3 "${SKILL_DIR}/../agent-tool-builder/scripts/validate_agent_tool.py" "$s" --json
done
```

**4c. Implementation quality:**

```bash
python3 "${SKILL_DIR}/../agent-tool-builder/scripts/perf_check.py" <skill-dir>/scripts/ --json
```

See [agent-tool-builder/references/perf-findings.md](../agent-tool-builder/references/perf-findings.md) for interpreting findings.

5. **Security audit (OWASP Agentic Skills Top 10).**

```bash
python3 "${SKILL_DIR}/scripts/audit_security.py" <skill-dir>
```

Findings carry an `AST##` id. Codes: `security.<surface>.<concern>`. Key checks:

- `security.tools.unrestricted-bash` / `security.tools.broad-bash-glob` (AST03)
- `security.secret.hardcoded` (AST04, **FAIL**)
- `security.script.unsafe-deserialization` (AST05)
- `security.script.shell-injection` / `security.script.dangerous-fs` (AST06)
- `security.exec.curl-pipe-shell` / `security.deps.unpinned` (AST02)
- `security.body.hidden-unicode` (AST01)

Read [references/security.md](references/security.md) for rationale behind each code and the pre-publication checklist.

6. **Re-validate.** Run all validators again. Iterate until: `validate_skill.py` passes, `analyze_skill.py` warnings are addressed or consciously accepted, `audit_security.py` shows no FAIL findings, and script audits report nothing material.

### Gotchas (Audit)

- `audit_security.py` blanks Python strings/comments before scanning (so `eval(` in a docstring isn't a hit) and skips `tests/`. A WARN is a prompt for a conscious decision, not an automatic defect.
- `count_tokens.py` heuristic counts are ±20% — fine for "over budget?" decisions, not for fine-grained arithmetic.
- The `name` field MUST equal the parent directory name or `validate_skill.py` reports `validate.name.dir-mismatch`.

---

## Mode 3 — Eval

Run trigger-rate evaluations, optimize a description for activation, and detect/resolve overlap between sibling skills.

### Workflow

1. **Check for overlap first.** When the skill lives alongside others:

```bash
python3 "${SKILL_DIR}/scripts/detect_skill_overlap.py" ~/.claude/skills/ --json
```

Or for a single skill against siblings:

```bash
python3 "${SKILL_DIR}/scripts/detect_skill_overlap.py" <skill-dir> \
  --against ~/.claude/skills/
```

Read [references/cross-skill-design.md](references/cross-skill-design.md) when the script flags a pair above the threshold or the wrong skill activated for a request.

2. **Build a labeled query set.** Read [assets/templates/eval-queries.json.template](assets/templates/eval-queries.json.template) for the file shape and ~12 worked examples. Validate the query set against [assets/schemas/eval-queries.schema.json](assets/schemas/eval-queries.schema.json).

3. **Run a trigger eval round:**

```bash
python3 "${SKILL_DIR}/scripts/eval_triggers.py" \
  --queries queries.json --skill-name <name> --runs 3 --json
```

4. **Iterate the description automatically:**

```bash
python3 "${SKILL_DIR}/scripts/optimize_description.py" \
  <skill-dir> --queries queries.json --rounds 3 --candidates 3 --json
```

Default is propose-only; pass `--apply` to write the winner to SKILL.md (creates `SKILL.md.bak`).

5. **Re-run overlap detection** after changing the description to confirm you haven't created new collisions.

Read [references/evaluation.md](references/evaluation.md) when:

- Designing the query set (positive/negative balance, near-misses)
- Interpreting eval output (what counts as a pass)
- The optimization loop plateaus and you need to diagnose the queries themselves

### Gotchas (Eval)

- The `claude -p --output-format json` schema may shift between CLI versions. If `eval_triggers.py` reports zero triggers across every query, suspect a schema change before assuming regression.
- `optimize_description.py` is nondeterministic — running it twice can produce different winners. The `.bak` file is the safe rollback; git is safer.
- `detect_skill_overlap.py` flags pairs above a threshold; it doesn't prove a misfire. Use the `shared_keywords` field as the actionable signal — three or more shared domain keywords is real overlap.
- Two skills with bag-of-words cosine ≥ 0.5 and no explicit "NOT for X — see Y" disambiguator are a problem.

---

## Anti-patterns to flag immediately

- `description: Helps with PDFs.` — too short, no trigger contexts.
- `description: This skill processes CSVs.` — declarative; rewrite imperative.
- 800-line `SKILL.md` with embedded reference tables — split via progressive disclosure.
- Mega-skill bundling unrelated workflows — one skill, one job.
- Generic filler (`follow best practices`, `handle errors appropriately`) — replace with concrete gotchas.
- Reference files mentioned without a load trigger ("see references/ for details") — agent won't load them.
- `name`: uppercase, leading/trailing hyphen, consecutive hyphens, or mismatch with directory.
- Bundled scripts that hardcode secrets or `cd` into absolute paths outside the skill (AST04).
- Over-privileged `allowed-tools` — bare `Bash` or `Bash(*)` instead of a scoped prefix (AST03).
- `curl … | sh` or unpinned PEP 723 deps (AST02).
- Unsafe deserialization — `pickle.loads`, `yaml.load` without `SafeLoader`, `eval`/`exec`, `subprocess(..., shell=True)` (AST05/AST06).
- Scripts without `argparse` or that print free-form text only.
- Scripts that echo SKILL.md content without sanitization.

## Platform notes

Scripts work on macOS, Linux, and Windows (Python 3.14+). On Windows: use `python` or `py -3`; `~` is expanded via `Path.expanduser()`; all file I/O is explicit UTF-8; symlink tests are auto-skipped without privileges.

For PEP 723 scripts, `uv run scripts/<name>.py` resolves dependencies automatically.

## Specification quick reference

**Base spec** (`name`, `description` required; optional: `license`, `compatibility` ≤500 chars, `metadata`, `allowed-tools`).

**Platform extensions** (all optional):

- **Claude Code**: `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, `shell`.
- **GitHub Copilot / VS Code**: `description` and `mode` are the primary fields.
- **Codex**: follows the base spec; platform-specific extensions TBD.

Full JSON Schema: [assets/schemas/frontmatter.schema.json](assets/schemas/frontmatter.schema.json).

Read [references/specification.md](references/specification.md) when you need full field constraints, the invocation-control matrix, string substitutions, dynamic context injection, or directory layout rules.
