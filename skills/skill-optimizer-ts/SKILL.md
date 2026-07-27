---
name: skill-optimizer-ts
description: "For skills whose bundled scripts are TypeScript: audit, optimize, validate, and trigger-eval Agent Skills (SKILL.md files) for any agent compliant with the agentskills.io spec — Claude Code, GitHub Copilot, Codex, VS Code, and others. USE FOR: saving coding preferences; troubleshooting why instructions/skills/agents are ignored or not invoked; configuring applyTo patterns; defining tool restrictions; creating custom agent modes or specialized workflows; packaging domain knowledge; fixing YAML frontmatter syntax; auditing a skill on a machine without Python (e.g. Windows without a Python install). Also trigger when the user doesn't say \"skill\" — e.g., \"this prompt file isn't activating\", \"rewrite my SKILL.md\", \"why doesn't my agent pick up my custom command\", \"audit my skills directory\", or \"test whether my description triggers reliably\". NOT for skills whose bundled scripts are Python — use skill-optimizer for that."
compatibility: Works with any agent platform supporting the agentskills.io specification (Claude Code, GitHub Copilot, Codex, VS Code). Requires Node.js 18.3+ (20+ recommended) and a one-time `npm ci` to resolve tsx/typescript/vitest. eval_triggers.ts and optimize_description.ts require an agent CLI on PATH (claude, copilot, or codex — configurable via --cli-bin). count_tokens.ts uses @anthropic-ai/sdk if ANTHROPIC_API_KEY is set, else a heuristic. Scripts run via `npx tsx`.
argument-hint: "[skill-path]"
allowed-tools: Bash(npx tsx *) Bash(npm ci) Read Edit Write Grep Glob
metadata:
  version: "1.1"
  author: Rafe Hart
---

# Skill Optimizer (TypeScript)

This skill operates in two modes. Identify which mode applies, then follow that mode's workflow exclusively.

## Setup

**Resolve `${SKILL_DIR}` before running anything.** Every command below writes `${SKILL_DIR}` for this skill's own directory. It is notation, not a shell variable — no platform exports it. Substitute the absolute path yourself:

- **Claude Code** — the path is `${CLAUDE_SKILL_DIR}`. That token is expanded when the skill loads, so it should already read as a real absolute path; use it verbatim.
- **Other platforms** — use the base directory this skill was loaded from.

Then, one time per install, substituting that path for `<SKILL_DIR>`:

```bash
cd "<SKILL_DIR>" && npm ci
```

This resolves `tsx`, `typescript`, and `vitest` from the committed `package-lock.json`. Every workflow step below invokes scripts via `npx tsx`, which resolves against this local install.

## Mode selection

| Mode      | Use when the user wants to…                                                                               |
| --------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Audit** | Improve, validate, restructure, or security-audit an existing skill                                      |
| **Eval**  | Run trigger evals, optimize a description for activation rate, or resolve overlap between sibling skills |

If the request spans multiple modes (e.g. "audit this skill and make sure it doesn't overlap my others"), execute them in order: Audit → Eval.

---

## Mode 1 — Audit

Validate, analyze, optimize, and security-audit an existing skill.

### Workflow

1. **Run static validators.** Delegate to a subagent to keep raw output out of the main context.

> **Subagent prompt:** Run the following three commands against `<skill-dir>` and return a single summary listing only FAIL and WARN items with their codes, messages, and affected files. Omit PASS items. Include the token count at the end.
>
> ```bash
> npx tsx "${SKILL_DIR}/scripts/validate_skill.ts" <skill-dir> --json
> npx tsx "${SKILL_DIR}/scripts/analyze_skill.ts" <skill-dir> --json
> npx tsx "${SKILL_DIR}/scripts/count_tokens.ts" <skill-dir>/SKILL.md --json
> ```
>
> Substitute the resolved `${SKILL_DIR}` path (see Setup) into each command — the subagent cannot resolve it. If all pass with no warnings, return "All validators pass. Token count: N".

Interpreting codes (if reviewing results yourself):

- `validate_skill.ts` — spec enforcement: required fields, name regex, description length (1–1024), directory match, broken file references. Codes: `validate.<surface>.<concern>`.
- `analyze_skill.ts` — content anti-patterns: declarative openings, missing trigger contexts, body over 500 lines / 5000 tokens, generic filler, unguarded references. Codes: `analyze.<surface>.<concern>`.
- `count_tokens.ts` — exact count (`@anthropic-ai/sdk`, if installed and `ANTHROPIC_API_KEY` is set) or calibrated heuristic (`len(text) / 3.5`). Returns `exact: false` on heuristic; treat as ±20%.

`validate_skill.ts`, `analyze_skill.ts`, `audit_security.ts`, and `validate_agent_tool.ts` accept `--exit-on-warn` to treat warnings as failures (CI / pre-commit). `count_tokens.ts`, `perf_check.ts`, and `recommend_scripts.ts` do not — they emit no warnings.

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
>
> ```bash
> npx tsx "${SKILL_DIR}/scripts/recommend_scripts.ts" <skill-dir> --json
> ```
>
> **4b. Per-script contract compliance:**
>
> ```bash
> for s in <skill-dir>/scripts/*.ts; do
>   npx tsx "${SKILL_DIR}/scripts/validate_agent_tool.ts" "$s" --format json
> done
> ```
>
> **4c. Implementation quality:**
>
> ```bash
> npx tsx "${SKILL_DIR}/scripts/perf_check.ts" <skill-dir>/scripts/ --format json
> ```
>
> If a finding references a perf pattern, read [references/perf-findings.md](references/perf-findings.md) and include the recommended fix in your summary. Return results grouped by file.

5. **Security audit (OWASP Agentic Skills Top 10).** Delegate to a subagent when combined with steps 1 and 4.

> **Subagent prompt:** Run the security scanner on `<skill-dir>` and return all FAIL and WARN findings with their AST codes, affected locations, and recommended fixes. If any findings are flagged, read [references/security.md](references/security.md) and include the relevant rationale from the pre-publication checklist.
>
> ```bash
> npx tsx "${SKILL_DIR}/scripts/audit_security.ts" <skill-dir> --json
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

6. **Re-validate.** Delegate to the same subagent pattern as step 1. Iterate until: `validate_skill.ts` passes, `analyze_skill.ts` warnings are addressed or consciously accepted, `audit_security.ts` shows no FAIL findings, and script audits report nothing material.

> **Combined audit subagent:** Steps 1, 4, 5, and 6 can be merged into a single subagent invocation when running a full audit. Prompt the subagent to run all validators, script audits, and security checks, then return one unified findings report grouped by severity (FAIL → WARN → INFO).

7. **Forward-test complex skills.** After substantial revisions or for tricky skills, launch a subagent to stress-test the skill on realistic tasks.

Forward-testing rules:

- The subagent should NOT know it's testing a skill. Prompt it as a real user would: `"Use <skill-name> at /path/to/skill to solve <problem>"` — not `"Review the skill at /path/to/skill and pretend a user asks…"`.
- Pass the artifact under validation (a file, a task description), not your diagnosis of what's wrong.
- Keep the prompt generic enough that success depends on transferable reasoning, not leaked ground truth.

Decision rule: err on the side of forward-testing. Skip only when the skill is trivial or the change is cosmetic. Ask for approval if forward-testing would take a long time, require additional user approvals, or modify live systems.

### Gotchas (Audit)

- A command that runs as `npx tsx "/scripts/<name>.ts"` — leading slash, no skill path — means `${SKILL_DIR}` went through unsubstituted. Node reports `ERR_MODULE_NOT_FOUND` for `file:///scripts/...`. Re-read Setup; don't retry the command verbatim.
- Exit codes: `0` clean, `1` findings, `2` bad invocation, `3` nothing matched. A `2` means the path or flags were wrong, not that the skill failed validation.
- `audit_security.ts` blanks TypeScript strings, template literals, and comments (via the TypeScript compiler's scanner) before scanning (so `eval(` in a comment isn't a hit) and skips `tests/`, `node_modules/`, and `*.test.ts` files. A WARN is a prompt for a conscious decision, not an automatic defect.
- `count_tokens.ts` heuristic counts are ±20% — fine for "over budget?" decisions, not for fine-grained arithmetic.
- The `name` field MUST equal the parent directory name or `validate_skill.ts` reports `validate.name.dir-mismatch`.
- A fresh clone without `node_modules/` fails to resolve `tsx` — run the Setup `npm ci` first.

---

## Mode 2 — Eval

Run trigger-rate evaluations, optimize a description for activation, and detect/resolve overlap between sibling skills.

### Workflow

1. **Check for overlap first.** Delegate to a subagent — overlap detection produces O(n²) pair output for large skill directories.

> **Subagent prompt:** Run overlap detection on the skill directory and return only pairs above the threshold. For each flagged pair, include the `shared_keywords` list and cosine score. If any pair scores ≥ 0.5, read [references/cross-skill-design.md](references/cross-skill-design.md) and include the recommended disambiguation strategy for that pair.
>
> ```bash
> npx tsx "${SKILL_DIR}/scripts/detect_skill_overlap.ts" ~/.claude/skills/ --json
> ```
>
> Or for a single skill against siblings:
>
> ```bash
> npx tsx "${SKILL_DIR}/scripts/detect_skill_overlap.ts" <skill-dir> \
>   --against ~/.claude/skills/ --json
> ```
>
> If no pairs exceed the threshold, return "No overlap detected."

Read [references/cross-skill-design.md](references/cross-skill-design.md) when the script flags a pair above the threshold or the wrong skill activated for a request.

2. **Build a labeled query set.** Read [assets/templates/eval-queries.json.template](assets/templates/eval-queries.json.template) for the file shape and ~12 worked examples. Validate the query set against [assets/schemas/eval-queries.schema.json](assets/schemas/eval-queries.schema.json).

3. **Run a trigger eval round:**

```bash
npx tsx "${SKILL_DIR}/scripts/eval_triggers.ts" \
  --queries queries.json --skill-name <name> --runs 3 --json
```

4. **Iterate the description automatically:**

```bash
npx tsx "${SKILL_DIR}/scripts/optimize_description.ts" \
  <skill-dir> --queries queries.json --rounds 3 --candidates 3 --json
```

Default is propose-only; pass `--apply` to write the winner to SKILL.md (creates `SKILL.md.bak`).

5. **Re-run overlap detection** after changing the description to confirm you haven't created new collisions.

Read [references/evaluation.md](references/evaluation.md) when:

- Designing the query set (positive/negative balance, near-misses)
- Interpreting eval output (what counts as a pass)
- The optimization loop plateaus and you need to diagnose the queries themselves

### Gotchas (Eval)

- The `claude -p --output-format json` schema may shift between CLI versions. If `eval_triggers.ts` reports zero triggers across every query, suspect a schema change before assuming regression.
- `optimize_description.ts` is nondeterministic — running it twice can produce different winners. The `.bak` file is the safe rollback; git is safer.
- `detect_skill_overlap.ts` flags pairs above a threshold; it doesn't prove a misfire. Use the `shared_keywords` field as the actionable signal — three or more shared domain keywords is real overlap.
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
- `curl … | sh` or unpinned `package.json` dependencies (`^`, `~`, `*`, `git:`, `latest` instead of an exact version) (AST02).
- Unsafe dynamic evaluation — `eval()`, `new Function(...)`, `vm.runInThisContext`/`runInNewContext`, `child_process.exec(..., {shell: true})` (AST05/AST06).
- Scripts without `parseArgs`/`commander` or that print free-form text only.
- Scripts that echo SKILL.md content without sanitization.
- A `package.json` with no committed `package-lock.json` — `npm ci` won't resolve reproducibly.

## Platform notes

Scripts work on macOS, Linux, and Windows (Node.js 18.3+, 20+ recommended). `npx tsx` resolves and runs `.ts` files directly — no separate build step, and no `python`/`py -3` distinction to worry about on Windows. All file I/O is explicit UTF-8 via Node's `node:fs`; path handling goes through `node:path` so separators are normalized automatically.

All CLI scripts accept `--format json|text` (default `text`) and `--json` as a shorthand alias. Pass `--quiet` to suppress informational stderr (errors still print). Library modules (e.g. `skill_lib.ts`, `test-helpers.ts`) are marked `// agent-tool: false` and skipped by `validate_agent_tool.ts`.

## Specification quick reference

**Base spec** (`name`, `description` required; optional: `license`, `compatibility` ≤500 chars, `metadata`, `allowed-tools`).

**Platform extensions** (all optional):

- **Claude Code**: `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, `shell`.
- **GitHub Copilot / VS Code**: `description` and `mode` are the primary fields.
- **Codex**: follows the base spec; platform-specific extensions TBD.

Full JSON Schema: [assets/schemas/frontmatter.schema.json](assets/schemas/frontmatter.schema.json).

Read [references/specification.md](references/specification.md) when you need full field constraints, the invocation-control matrix, string substitutions, dynamic context injection, or directory layout rules.
