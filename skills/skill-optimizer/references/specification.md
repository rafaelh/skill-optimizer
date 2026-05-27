# Agent Skills specification (quick reference)

Source of truth: <https://agentskills.io/specification> for the base spec; <https://docs.anthropic.com/en/skills> for Claude Code extensions. This file captures the parts you need while editing a skill.

## Directory layout

```
skill-name/
├── SKILL.md          # Required: metadata + instructions
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
├── assets/           # Optional: templates, resources
└── ...               # Any additional files
```

The directory name MUST match the `name` field in frontmatter.

## SKILL.md structure

YAML frontmatter delimited by `---`, then markdown body.

```markdown
---
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
---

# Body content...
```

## Frontmatter fields

### Base spec (agentskills.io)

| Field | Required | Constraints |
|-----------------|-----|--------------------------------------------------------------------------------------------|
| `name` | Yes | 1–64 chars. Lowercase a–z, 0–9, hyphens. No leading/trailing/consecutive hyphens. Must match directory name. |
| `description`   | Yes | 1–1024 chars. Says what the skill does AND when to use it.                                 |
| `license`       | No  | License name or path to bundled license file.                                              |
| `compatibility` | No  | 1–500 chars. Environment requirements (intended product, system packages, network access). |
| `metadata`      | No  | Map of string keys → string values. Use unique key names to avoid conflicts.               |
| `allowed-tools` | No  | Tools Claude can use without asking permission when this skill is active.                  |

### Claude Code extensions

| Field                      | Description                                                                                                                                                      |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `when_to_use`              | Additional trigger context. Appended to `description` in the skill listing. Combined with `description`, truncated at 1,536 chars in the listing.               |
| `argument-hint`            | Hint shown during autocomplete. Example: `[issue-number]` or `[filename] [format]`.                                                                            |
| `arguments`                | Named positional arguments for `$name` substitution. Space-separated string or YAML list.                                                                      |
| `disable-model-invocation` | `true` to prevent Claude from auto-loading this skill. Use for side-effectful workflows (deploy, commit, send-message). Default: `false`.                      |
| `user-invocable`           | `false` to hide from the `/` menu. Use for background knowledge not meant as a direct command. Default: `true`.                                                |
| `model`                    | Model override while this skill is active. Applies for the current turn only; session model resumes next prompt.                                               |
| `effort`                   | Effort override: `low`, `medium`, `high`, `xhigh`, `max`. Reverts after the skill's turn.                                                                     |
| `context`                  | `fork` to run in an isolated subagent context. The skill body becomes the subagent's task prompt.                                                              |
| `agent`                    | Which subagent type to use with `context: fork`. Options: `Explore`, `Plan`, `general-purpose`, or any custom subagent. Defaults to `general-purpose`.         |
| `hooks`                    | Hooks scoped to this skill's lifecycle. See Claude Code hooks docs for format.                                                                                  |
| `paths`                    | Glob patterns limiting when Claude auto-activates the skill. Accepts comma-separated string or YAML list.                                                      |
| `shell`                    | Shell for `` !`command` `` blocks: `bash` (default) or `powershell`.                                                                                           |

**Invocation matrix:**

| Frontmatter                      | User can invoke | Claude can invoke |
|----------------------------------|-----------------|-------------------|
| (default)                        | Yes             | Yes               |
| `disable-model-invocation: true` | Yes             | No                |
| `user-invocable: false`          | No              | Yes               |

### `name` examples

| Valid            | Invalid           | Reason              |
|------------------|-------------------|---------------------|
| `pdf-processing` | `PDF-Processing`  | uppercase           |
| `data-analysis`  | `-pdf`            | leading hyphen      |
| `code-review`    | `pdf--processing` | consecutive hyphens |

### `description` and `when_to_use`

These two fields work together. `description` carries the primary triggering signal; `when_to_use` adds supplementary trigger phrases. Their combined text is truncated at 1,536 chars in the skill listing — put the highest-signal keywords first. The field-level hard limit for `description` is 1,024 chars.

```yaml
# Good — split across both fields
description: >
  Analyze CSV and tabular data — compute statistics, add derived columns,
  generate charts, clean messy data. Use when the user has a CSV, TSV, or
  Excel file and wants to explore, transform, or visualize it.
when_to_use: >
  Trigger even if the user doesn't say "CSV": spreadsheet, tabular data,
  pivot table, column calculations, data cleaning, bulk row operations.

# Poor
description: Helps with CSVs.
```

## String substitutions

Available anywhere in the skill body:

| Variable               | Expands to                                                                              |
|------------------------|-----------------------------------------------------------------------------------------|
| `$ARGUMENTS`           | All arguments passed at invocation. Appended as `ARGUMENTS: <value>` if not present.    |
| `$ARGUMENTS[N]`        | Specific argument by 0-based index.                                                     |
| `$N`                   | Shorthand for `$ARGUMENTS[N]`.                                                          |
| `$name`                | Named argument declared in `arguments` frontmatter (maps to positions in order).        |
| `${CLAUDE_SESSION_ID}` | Current session ID. Useful for per-session log files.                                   |
| `${CLAUDE_EFFORT}`     | Current effort level (`low` / `medium` / `high` / `xhigh` / `max`).                     |
| `${CLAUDE_SKILL_DIR}`  | Absolute path to the skill's directory. Use this to reference bundled scripts portably. |

**`${CLAUDE_SKILL_DIR}` is how bundled scripts stay portable across install locations.** Always use it instead of hardcoding paths:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/validate_skill.py" <skill-dir>
```

## Dynamic context injection

The `` !`<command>` `` syntax runs a shell command before the skill content is sent to Claude. The output replaces the placeholder inline — Claude sees the result, not the command.

```yaml
---
name: pr-summary
description: Summarize a pull request
---

## Current diff
!`gh pr diff`

## Instructions
Summarize the changes above...
```

For multi-line commands, use a fenced block opened with ` ```! `:

````markdown
## Environment
```!
node --version
npm --version
git status --short
```
````

This is preprocessing, not something Claude executes. Claude only sees the rendered output.

## Body content

No format restrictions. Recommended sections: step-by-step instructions, gotchas, examples.

The entire body loads when the skill activates. Keep it under 500 lines / ~5000 tokens. Move detail into `references/`, `scripts/`, or `assets/` and tell the agent when to load each.

Skill content stays in context for the rest of the session. After auto-compaction, the most recently invoked skills are re-attached (first 5,000 tokens each, 25,000 tokens shared budget across all skills).

## File references

Use relative paths from the skill root, one level deep:

```markdown
See [the reference guide](references/REFERENCE.md) for details.
Run `scripts/extract.py`.
```

Avoid deeply nested reference chains.

## Progressive disclosure layers

| Layer        | Token budget           | When loaded                              |
|--------------|------------------------|------------------------------------------|
| Metadata     | ~100 (name + desc)     | At startup, for all skills               |
| Instructions | < 5000 (SKILL.md body) | When the skill activates                 |
| Resources    | as needed              | When SKILL.md or the agent references it |

## Validation

`scripts/validate_skill.py` (bundled with this skill) checks:

- Required fields present
- `name` regex compliance and directory match
- `description` length 1–1024
- `compatibility` length ≤ 500 if present
- Unknown frontmatter keys (both base spec and Claude Code extensions are allowed)
- Broken relative file references
