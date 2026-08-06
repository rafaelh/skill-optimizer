# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Claude Code plugin marketplace shipping three Agent Skills that comply with (and enforce) the [agentskills.io](https://agentskills.io/) spec. It is not a distributable library: `pyproject.toml` sets `package = false`, scripts are run in place by path, and nothing is published to PyPI or npm.

| Skill | Language | Role |
|---|---|---|
| `skills/skill-optimizer` | Python | Audit / eval SKILL.md files whose bundled scripts are Python |
| `skills/skill-optimizer-ts` | TypeScript | Same workflow, for skills whose scripts are TypeScript |
| `skills/agent-tool-builder` | Python | Build & audit Python scripts that agents call as tools |

Two skill locations, don't confuse them: `skills/` is the published marketplace source, while `.claude/skills/` holds the skills active when working *in* this repo (`plan`, `refactor`, `tdd`, plus a `skill-optimizer-ts` symlink back into `skills/`). `.gitignore` excludes `.claude/*` but re-includes `.claude/skills/`, so those are tracked source — local settings files are not.

## Commands

Python (repo root, uv-managed venv at `.venv`):

```bash
uv sync                                   # install dev deps (pytest, ruff, pyright)
uv run pytest                             # all 154 tests, both Python skills
uv run pytest skills/skill-optimizer/scripts/tests/test_validate_skill.py::TestCli::test_format_json_flag
uv run ruff check . --fix
uv run ruff format .
uv run pyright                            # strict mode over skills/
uv run pre-commit run --all-files         # ruff + ruff-format + pyright + hygiene hooks
```

TypeScript (`skills/skill-optimizer-ts/`, requires a one-time `npm ci`):

```bash
npm ci
npm test                                  # vitest run
npx vitest run scripts/validate_skill.test.ts -t "rejects uppercase name"
npm run typecheck                         # tsc --noEmit
npm run lint                              # eslint
npm run format                            # prettier --write
```

## Architecture

### Skill layout is a contract, not a convention

Each skill is `SKILL.md` + `scripts/` + `references/` + `assets/` + its own `.claude-plugin/plugin.json`. `validate_skill.py` enforces that frontmatter `name` equals the parent directory name (`validate.name.dir-mismatch`) and that every `references/*.md` link and `` `scripts/*.py` `` mention resolves to a real file. Renaming a skill directory therefore breaks validation until the frontmatter follows.

Adding or renaming a skill requires editing **two** manifests: the root `.claude-plugin/marketplace.json` (the plugin list) and the skill's own `.claude-plugin/plugin.json` (name/version). They version independently — the root plugin is 2.0.0, `agent-tool-builder` is 0.8.0.

### Scripts are agent tools and are validated as such

Every bundled script is subject to the interface contract in [interface-contract.md](skills/agent-tool-builder/references/interface-contract.md), machine-checked by `validate_agent_tool.py`:

- `--format json|text` with `--json` as shorthand alias, and `--quiet` to suppress informational stderr. The reference contract prescribes a JSON default; this repo's own scripts default to `text` because their primary audience is a human reading validator output — keep that deviation deliberate.
- Exit codes `0` success, `1` user/invocation error, `2` system error, `3` not-found/empty — never make the agent parse text to tell "nothing matched" from "it broke". The validator itself only enforces reachable `0/1/2`.
- No `input()`, no free-form stdout errors, argparse-based
- Library modules that are *not* agent tools carry a `# agent-tool: false` marker on line 2 (`skill_lib.py`, `skill_lib.ts`) so the validator skips them

When writing a new script here, the skill's own tooling is the spec — run `validate_agent_tool.py` and `perf_check.py` against it.

### Cross-skill dependency

`skill-optimizer`'s SKILL.md invokes `../agent-tool-builder/scripts/validate_agent_tool.py` and `perf_check.py` by relative path, and links to `agent-tool-builder/references/perf-findings.md`. Moving or renaming those files breaks the Python audit workflow. `skill-optimizer-ts` deliberately has **no** such dependency — it folds its own `validate_agent_tool.ts` and `perf_check.ts` in, so it works on machines without Python.

### Python/TypeScript parity

`skill-optimizer` and `skill-optimizer-ts` are twins: same script names, same modes (Audit / Eval), same OWASP AST## finding codes, parallel `references/`. A behavioral change to one should normally be mirrored in the other. What legitimately diverges: perf checks (Python AST vs TypeScript compiler API, different anti-pattern sets), security script-construct checks (`pickle`/`shell=True` vs `eval()`/`{shell: true}`), and dependency pinning (PEP 723 vs `package.json`).

Python scripts import siblings flatly (`from skill_lib import ...`) because they run as `python3 path/to/script.py`; tests bootstrap `sys.path` via `skills/skill-optimizer/scripts/tests/conftest.py`. Test placement differs by skill — `skill-optimizer` keeps tests in `scripts/tests/`, `agent-tool-builder` in a top-level `tests/`, TypeScript co-locates `*.test.ts` next to sources. Python tests are black-box: they invoke the script under `subprocess` and assert on JSON stdout + exit code, rather than importing functions.

### Descriptions are tuned artifacts

The three skills overlap by design, so each `description` ends with an explicit disambiguator (`NOT for skills whose bundled scripts are TypeScript — use skill-optimizer-ts for that`). `detect_skill_overlap.py` flags sibling pairs at cosine ≥ 0.5 without one. When editing a description, preserve the disambiguator and the concrete trigger phrases — they exist because they were measured by `eval_triggers.py`, not written for prose.

SKILL.md bodies write `${SKILL_DIR}` for the skill's own directory. It is notation, not a shell variable; Claude Code resolves it from `${CLAUDE_SKILL_DIR}`.

## Lint conventions

Ruff runs a wide select list (including `S` bandit, `PERF`, `TRY`, `PL`, `ERA`) at line-length 100, `target-version = py314`. Exceptions live in `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml` **with a comment explaining why** — follow that pattern instead of scattering inline `# noqa`. Pyright is `strict` over `skills/`.
