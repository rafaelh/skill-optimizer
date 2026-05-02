---
name: skill-optimizer
description: Audit, optimize, and validate Claude skill files (SKILL.md). Use this skill when the user wants to create a new skill, improve an existing one, debug why a skill isn't activating, restructure a bloated SKILL.md, validate frontmatter against the Agent Skills specification, or apply patterns like gotchas, validation loops, and progressive disclosure. Trigger even when the user doesn't say "skill" — e.g., "this prompt file isn't activating", "rewrite my SKILL.md", "why doesn't Claude pick up my custom command", or "audit my .claude/skills directory".
---

# Skill Optimizer

Improve a Claude skill so it activates reliably, fits inside its token budget, and follows the Agent Skills specification.

## When you reach for this skill

The user is asking you to:

- Create a new skill from scratch
- Audit or improve an existing skill (description, body, structure)
- Diagnose why a skill is not triggering
- Validate frontmatter or layout against the spec

## Workflow

### 1. Locate the skill

Find the target `SKILL.md`. Skills live under `~/.claude/skills/<name>/SKILL.md` (user) or `<repo>/.claude/skills/<name>/SKILL.md` (project). The frontmatter `name` field MUST match the parent directory name.

### 2. Run the validators first

Always start with the bundled scripts to surface mechanical issues before reading the body. All three use stdlib only — no install step. Each accepts `--json` for machine-readable output and exits non-zero on FAIL-level issues.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/validate_skill.py" <skill-dir>
python3 "${CLAUDE_SKILL_DIR}/scripts/analyze_skill.py" <skill-dir>
python3 "${CLAUDE_SKILL_DIR}/scripts/recommend_scripts.py" <skill-dir>
```

`${CLAUDE_SKILL_DIR}` is set by Claude Code to the directory containing this `SKILL.md`, so these commands work whether the skill is symlinked under `~/.claude/skills/`, installed via `/plugin install`, or bundled in a project's `.claude/skills/`.

- `validate_skill.py` enforces the spec: required fields, name regex (lowercase, hyphens, no leading/trailing/consecutive hyphens, max 64 chars), description length (1–1024), name matches directory, optional field constraints, broken file references. Each issue carries a stable `code` (e.g. `name-mismatch-dir`, `description-too-long`, `broken-reference`).
- `analyze_skill.py` flags content anti-patterns: declarative description openings, missing trigger contexts, body over 500 lines / 5000 tokens, generic filler, references introduced without a load condition. Codes: `declarative-description`, `description-no-when`, `body-too-many-lines`, `generic-filler`, `reference-no-load-trigger`.
- `recommend_scripts.py` surfaces opportunities to *add or improve* bundled scripts (see step 5).

Pass `--json` when chaining into other tooling. Pass `--exit-on-warn` when treating warnings as failures (CI / pre-commit).

### 3. Optimize the description

The `description` field carries the entire triggering burden. The agent only loads `name` + `description` at startup; if those don't say *when* to use the skill, it never gets activated.

Read [references/description-guide.md](references/description-guide.md) when:

- The user reports the skill isn't activating
- The current description is under ~150 characters
- The description starts with "This skill...", "A skill that...", or describes mechanics rather than user intent
- You want to set up a trigger-rate eval

Quick rules (full guide in the reference):

- **Imperative**: "Use this skill when..." not "This skill does..."
- **Intent-focused**: describe what the user is trying to achieve, not internals
- **Pushy**: list trigger contexts including ones where the user doesn't name the domain
- **Hard limit**: 1024 characters

### 4. Optimize the body

Read [references/content-patterns.md](references/content-patterns.md) before restructuring the body. Apply these rules in order:

1. **Cut what the agent already knows.** If removing a paragraph wouldn't degrade behavior, delete it. No "what is a PDF" preamble.
2. **Convert declarations into procedures.** Replace one-shot answers with reusable methods.
3. **Add a Gotchas section** for non-obvious environment facts (soft deletes, naming mismatches, misleading endpoints). Keep gotchas in `SKILL.md` itself — the agent must read them before hitting the situation.
4. **Provide defaults, not menus.** Pick one library/approach; mention alternatives in one line.
5. **Match prescriptiveness to fragility.** Code review = freedom + "why". Migrations = exact command sequence with "do not modify".
6. **Bundle scripts** for any logic the agent would otherwise reinvent each run. Put them in `scripts/`.
7. **Apply progressive disclosure.** Keep `SKILL.md` under 500 lines / ~5000 tokens. Move detail into `references/`, `scripts/`, `assets/`. Tell the agent *when* to load each reference file.

### 5. Identify scripting opportunities

Run `recommend_scripts.py <skill-dir>` after the body is in shape. It surfaces:

- **`extract-procedure`** — a long bash block in `SKILL.md` (≥6 lines) that the agent will re-derive every run; extract into `scripts/<name>.py` and replace with a one-line invocation.
- **`missing-argparse`** — a bundled script with `if __name__` or `sys.argv` usage but no `argparse.ArgumentParser`; agents can't discover its interface via `--help`.
- **`add-json-output`** — a bundled script without a `--json` flag; structured output is easier for agents to consume than free-form text.
- **`add-pep723-metadata`** — a script imports non-stdlib modules without declaring deps via [PEP 723](https://peps.python.org/pep-0723/); `uv run scripts/<name>.py` would fail to install them.

Apply the suggested fixes only when they earn their keep. The skill-optimizer's own conventions (lifted from <https://agentskills.io/skill-creation/using-scripts>):

- Stdout = data; stderr = diagnostics. Never mix.
- Reject ambiguous input with a clear error rather than guessing.
- Add `--dry-run` for stateful or destructive operations.
- Sanitize echoed user content (ANSI escapes, control characters) when scripts read SKILL.md or vault data — see `scripts/skill_lib.py::sanitize_for_echo`.
- Pin output size; default to summary, support `--offset` for paginated detail.

### 6. Re-validate

Run all three scripts again. Iterate until `validate_skill.py` passes, `analyze_skill.py` warnings are addressed (or consciously ignored), and `recommend_scripts.py` either reports nothing material or you've made conscious decisions about each remaining opportunity.

### 7. Optional: trigger eval

If the user wants to verify activation against realistic prompts, follow the train/validation split workflow in [references/description-guide.md](references/description-guide.md).

## Anti-patterns to flag immediately

- `description: Helps with PDFs.` — too short, no trigger contexts.
- `description: This skill processes CSVs.` — declarative; rewrite imperative.
- 800-line `SKILL.md` with embedded reference tables — split via progressive disclosure.
- Mega-skill bundling unrelated workflows — one skill, one job.
- Generic filler (`follow best practices`, `handle errors appropriately`) — replace with concrete gotchas.
- Reference files mentioned without a load trigger ("see references/ for details") — agent won't load them.
- `name`: uppercase, leading/trailing hyphen, consecutive hyphens, or mismatch with directory.
- Bundled scripts that hardcode secrets or `cd` into absolute paths outside the skill.
- Bundled scripts that lack `argparse` (so `--help` is missing) or print free-form text only (so agents can't structure-parse). Run `recommend_scripts.py` to find them.
- Scripts that echo SKILL.md content verbatim without sanitization — ANSI escapes and control characters in untrusted skill files can confuse the calling agent or terminal.

## Platform notes

The scripts work on macOS, Linux, and Windows (Python 3.10+). On Windows:

- Use `python` or `py -3` instead of `python3` if your install lacks the `python3` alias.
- The `~` in paths is expanded by Python via `Path.expanduser()`, so command-line args like `~/.claude/skills/...` work even from `cmd.exe`.
- All file I/O is explicit UTF-8.
- Symlink-based tests are auto-skipped if your account lacks symlink privileges (no Developer Mode / not admin); other tests run normally.

## Specification quick reference

Required frontmatter: `name`, `description`. Optional: `license`, `compatibility` (≤500 chars), `metadata`, `allowed-tools`. Read [references/specification.md](references/specification.md) when you need the full field constraints, valid `name` examples, or directory layout rules.
