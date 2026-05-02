# Agent Skills specification (quick reference)

Source of truth: <https://agentskills.io/specification>. This file captures the parts you need while editing a skill.

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

| Field           | Required | Constraints                                                                                                       |
| --------------- | -------- | ----------------------------------------------------------------------------------------------------------------- |
| `name`          | Yes      | 1–64 chars. Lowercase a–z, 0–9, hyphens. No leading/trailing/consecutive hyphens. Must match directory name.      |
| `description`   | Yes      | 1–1024 chars. Non-empty. Says what the skill does AND when to use it.                                             |
| `license`       | No       | License name or path to bundled license file.                                                                     |
| `compatibility` | No       | 1–500 chars. Environment requirements (intended product, system packages, network access).                        |
| `metadata`      | No       | Map of string keys → string values. Use unique key names to avoid conflicts.                                      |
| `allowed-tools` | No       | Space-separated string of pre-approved tools. **Experimental** — support varies by client.                        |

### `name` examples

| Valid              | Invalid              | Reason                          |
| ------------------ | -------------------- | ------------------------------- |
| `pdf-processing`   | `PDF-Processing`     | uppercase                       |
| `data-analysis`    | `-pdf`               | leading hyphen                  |
| `code-review`      | `pdf--processing`    | consecutive hyphens             |

### `description` examples

```yaml
# Good
description: Extracts text and tables from PDF files, fills PDF forms, and merges multiple PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction.

# Poor
description: Helps with PDFs.
```

### `compatibility` examples

```yaml
compatibility: Designed for Claude Code (or similar products)
compatibility: Requires git, docker, jq, and access to the internet
compatibility: Requires Python 3.14+ and uv
```

Most skills don't need this field.

### `allowed-tools` example

```yaml
allowed-tools: Bash(git:*) Bash(jq:*) Read
```

## Body content

No format restrictions. Recommended sections: step-by-step instructions, examples of inputs/outputs, common edge cases.

The entire body loads when the skill activates. Keep it under 500 lines / ~5000 tokens. Move detail into `references/`, `scripts/`, or `assets/` and tell the agent when to load each.

## File references

Use relative paths from the skill root, one level deep:

```markdown
See [the reference guide](references/REFERENCE.md) for details.
Run `scripts/extract.py`.
```

Avoid deeply nested reference chains.

## Progressive disclosure layers

| Layer        | Token budget          | When loaded                              |
| ------------ | --------------------- | ---------------------------------------- |
| Metadata     | ~100 (name + desc)    | At startup, for all skills               |
| Instructions | < 5000 (SKILL.md body) | When the skill activates                 |
| Resources    | as needed              | When SKILL.md or the agent references it |

## Validation

`scripts/validate_skill.py` (bundled with this skill) checks:

- Required fields present
- `name` regex compliance and directory match
- `description` length 1–1024
- `compatibility` length ≤ 500 if present
- Unknown frontmatter keys
- Broken relative file references
