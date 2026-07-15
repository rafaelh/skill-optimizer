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

1. **Understand the skill with concrete examples.** Before scaffolding, establish what the skill does through concrete usage scenarios. Ask:

- "What would a user say that should trigger this skill?"
- "Can you give examples of how this skill would be used?"
- "What does the skill do that the agent can't already do alone?"

Conclude when you have 3–5 representative tasks the skill must handle. These become the basis for the description's trigger contexts and later eval queries.

2. **Plan reusable resources.** For each example, identify what should become bundled resources:

- **Script** — code the agent would rewrite each run (e.g., PDF rotation, form filling, data transformation)
- **Reference** — documentation the agent needs to make informed decisions (e.g., schemas, API docs, domain rules)
- **Asset** — files used in output, not loaded into context (e.g., templates, boilerplate, images)

Do NOT create resources speculatively. Each must trace back to a concrete example from step 1. Do NOT include README, CHANGELOG, INSTALLATION_GUIDE, or other auxiliary documentation — skills contain only what the agent needs to do the job.

3. **Run the scaffolder.**

```bash
python3 "${SKILL_DIR}/scripts/init_skill.py" \
  <parent-dir> --name <slug> --description "<text>" --json
```

This produces: SKILL.md from `assets/templates/SKILL.md.template`, an `example.py` from `assets/templates/script.py.template`, and `references/` + `assets/` + `tests/` placeholders. Pass `--minimal` for SKILL.md only.

Skills live in platform-specific locations:

- **Claude Code**: `~/.claude/skills/<name>/` (user) or `<repo>/.claude/skills/<name>/` (project)
- **GitHub Copilot / VS Code**: `~/.config/Code/User/prompts/<name>/` or `<repo>/.github/prompts/<name>/`
- **Codex**: `<repo>/.codex/skills/<name>/`

4. **Write the description.** Read [references/description-guide.md](references/description-guide.md) for the full writing rules. Quick rules:

- **Imperative**: "Use this skill when..." not "This skill does..."
- **Intent-focused**: describe what the user is trying to achieve, not internals
- **Pushy**: list trigger contexts including ones where the user doesn't name the domain
- **Hard limit**: 1024 characters

5. **Write the body.** Read [references/content-patterns.md](references/content-patterns.md) before structuring. Key principles:

- Cut what the agent already knows
- Convert declarations into procedures
- Add a Gotchas section for non-obvious environment facts
- Provide defaults, not menus
- Bundle scripts for any logic the agent would otherwise reinvent each run
- Keep SKILL.md under 500 lines / ~5000 tokens; move detail into `references/`

6. **Validate immediately.** Run the static validators (see Mode 2, step 1) to confirm the new skill passes before moving on.

### Gotchas (Create)

- The `name` field MUST equal the parent directory name. Renaming a skill means renaming both the directory and the frontmatter in lockstep.
- `init_skill.py` won't overwrite an existing directory — delete or rename first.

---

## Mode 2 — Audit

Validate, analyze, optimize, and security-audit an existing skill.

### Workflow

1. **Run static validators.** Delegate to a subagent to keep raw output out of the main context.

> **Subagent prompt:** Run the following three commands against `<skill-dir>` and return a single summary listing only FAIL and WARN items with their codes, messages, and affected files. Omit PASS items. Include the token count at the end.
>
> ```bash
> python3 "${SKILL_DIR}/scripts/validate_skill.py" <skill-dir> --json
> python3 "${SKILL_DIR}/scripts/analyze_skill.py" <skill-dir> --json
> python3 "${SKILL_DIR}/scripts/count_tokens.py" <skill-dir>/SKILL.md --json
> ```
>
> `${SKILL_DIR}` resolves to the directory containing this SKILL.md (`${CLAUDE_SKILL_DIR}` on Claude Code). If all pass with no warnings, return "All validators pass. Token count: N".

Interpreting codes (if reviewing results yourself):

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

4. **Audit bundled scripts.** Delegate to a subagent — this step iterates over every script and produces verbose per-file output.

> **Subagent prompt:** Audit all bundled scripts in `<skill-dir>`. Run these three steps and return only material findings (FAIL or WARN) with the affected file, line number, and code. Omit clean passes.
>
> **4a. What should become a script:**
> ```bash
> python3 "${SKILL_DIR}/scripts/recommend_scripts.py" <skill-dir> --json
> ```
>
> **4b. Per-script contract compliance:**
> ```bash
> for s in <skill-dir>/scripts/*.py; do
>   python3 "${SKILL_DIR}/../agent-tool-builder/scripts/validate_agent_tool.py" "$s" --json
> done
> ```
>
> **4c. Implementation quality:**
> ```bash
> python3 "${SKILL_DIR}/../agent-tool-builder/scripts/perf_check.py" <skill-dir>/scripts/ --json
> ```
>
> If a finding references a perf pattern, read [agent-tool-builder/references/perf-findings.md](../agent-tool-builder/references/perf-findings.md) and include the recommended fix in your summary. Return results grouped by file.

5. **Security audit (OWASP Agentic Skills Top 10).** Delegate to a subagent when combined with steps 1 and 4.

> **Subagent prompt:** Run the security scanner on `<skill-dir>` and return all FAIL and WARN findings with their AST codes, affected locations, and recommended fixes. If any findings are flagged, read [references/security.md](references/security.md) and include the relevant rationale from the pre-publication checklist.
>
> ```bash
> python3 "${SKILL_DIR}/scripts/audit_security.py" <skill-dir> --json
> ```
>
> If clean, return "Security audit: no findings."

Findings carry an `AST##` id. Codes: `security.<surface>.<concern>`. Key checks:

- `security.tools.unrestricted-bash` / `security.tools.broad-bash-glob` (AST03)
- `security.secret.hardcoded` (AST04, **FAIL**)
- `security.script.unsafe-deserialization` (AST05)
- `security.script.shell-injection` / `security.script.dangerous-fs` (AST06)
- `security.exec.curl-pipe-shell` / `security.deps.unpinned` (AST02)
- `security.body.hidden-unicode` (AST01)

Read [references/security.md](references/security.md) for rationale behind each code and the pre-publication checklist.

6. **Re-validate.** Delegate to the same subagent pattern as step 1. Iterate until: `validate_skill.py` passes, `analyze_skill.py` warnings are addressed or consciously accepted, `audit_security.py` shows no FAIL findings, and script audits report nothing material.

> **Combined audit subagent:** Steps 1, 4, 5, and 6 can be merged into a single subagent invocation when running a full audit. Prompt the subagent to run all validators, script audits, and security checks, then return one unified findings report grouped by severity (FAIL → WARN → INFO).

7. **Forward-test complex skills.** After substantial revisions or for tricky skills, launch a subagent to stress-test the skill on realistic tasks.

Forward-testing rules:
- The subagent should NOT know it's testing a skill. Prompt it as a real user would: `"Use <skill-name> at /path/to/skill to solve <problem>"` — not `"Review the skill at /path/to/skill and pretend a user asks…"`.
- Pass the artifact under validation (a file, a task description), not your diagnosis of what's wrong.
- Keep the prompt generic enough that success depends on transferable reasoning, not leaked ground truth.

Decision rule: err on the side of forward-testing. Skip only when the skill is trivial or the change is cosmetic. Ask for approval if forward-testing would take a long time, require additional user approvals, or modify live systems.

### Gotchas (Audit)

- `audit_security.py` blanks Python strings/comments before scanning (so `eval(` in a docstring isn't a hit) and skips `tests/`. A WARN is a prompt for a conscious decision, not an automatic defect.
- `count_tokens.py` heuristic counts are ±20% — fine for "over budget?" decisions, not for fine-grained arithmetic.
- The `name` field MUST equal the parent directory name or `validate_skill.py` reports `validate.name.dir-mismatch`.

---

## Mode 3 — Eval

Run trigger-rate evaluations, optimize a description for activation, and detect/resolve overlap between sibling skills.

### Workflow

1. **Check for overlap first.** Delegate to a subagent — overlap detection produces O(n²) pair output for large skill directories.

> **Subagent prompt:** Run overlap detection on the skill directory and return only pairs above the threshold. For each flagged pair, include the `shared_keywords` list and cosine score. If any pair scores ≥ 0.5, read [references/cross-skill-design.md](references/cross-skill-design.md) and include the recommended disambiguation strategy for that pair.
>
> ```bash
> python3 "${SKILL_DIR}/scripts/detect_skill_overlap.py" ~/.claude/skills/ --json
> ```
>
> Or for a single skill against siblings:
> ```bash
> python3 "${SKILL_DIR}/scripts/detect_skill_overlap.py" <skill-dir> \
>   --against ~/.claude/skills/ --json
> ```
>
> If no pairs exceed the threshold, return "No overlap detected."

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

All CLI scripts accept `--format json|text` (default `text`) and `--json` as a shorthand alias. Pass `--quiet` to suppress informational stderr (errors still print). Library modules (e.g. `skill_lib.py`) are marked `# agent-tool: false` and skipped by `validate_agent_tool.py`.

For PEP 723 scripts, `uv run scripts/<name>.py` resolves dependencies automatically.

## Specification quick reference

**Base spec** (`name`, `description` required; optional: `license`, `compatibility` ≤500 chars, `metadata`, `allowed-tools`).

**Platform extensions** (all optional):

- **Claude Code**: `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, `shell`.
- **GitHub Copilot / VS Code**: `description` and `mode` are the primary fields.
- **Codex**: follows the base spec; platform-specific extensions TBD.

Full JSON Schema: [assets/schemas/frontmatter.schema.json](assets/schemas/frontmatter.schema.json).

Read [references/specification.md](references/specification.md) when you need full field constraints, the invocation-control matrix, string substitutions, dynamic context injection, or directory layout rules.
