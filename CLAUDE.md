# CLAUDE.md

## What this repo is

A Claude Code plugin marketplace of Agent Skills that comply with (and enforce) the
[agentskills.io](https://agentskills.io/) spec. Not a distributable library: `pyproject.toml` sets
`package = false`, scripts run in place by path, nothing publishes to PyPI or npm.

`skills/` is the single source for every skill; each one is a published plugin listed in
`.claude-plugin/marketplace.json`. `.claude/skills/` symlinks the subset active while working *in*
this repo. `.gitignore` excludes `.claude/*` but re-includes `.claude/skills/`, so those symlinks
are tracked source and local settings are not. The one non-symlink there is `run-skills/`, the
repo's own smoke driver — repo-local tooling, deliberately not a published plugin.

Script-bearing skills — held to the contracts below:

| Skill | Language | Role |
|---|---|---|
| `skills/skill-optimizer` | Python | Audit / eval SKILL.md files whose bundled scripts are Python |
| `skills/skill-optimizer-ts` | TypeScript | Same workflow, for skills whose scripts are TypeScript |
| `skills/agent-tool-builder` | Python | Build & audit Python scripts that agents call as tools |

The rest (`architecture`, `plan`, `refactor`, `tdd`) are prose-only — `SKILL.md` plus optional
`references/`, no `scripts/` — so the agent-tool contract does not apply to them.

## Commands

Python (repo root, uv-managed venv at `.venv`):

```bash
uv sync                                   # dev deps: pytest, ruff, pyright
uv run pytest
uv run pytest skills/skill-optimizer/scripts/tests/test_validate_skill.py::TestCli::test_format_json_flag
uv run ruff check . --fix && uv run ruff format .
uv run pyright                            # strict over skills/
uv run pre-commit run --all-files         # ruff + ruff-format + pyright + hygiene hooks
```

TypeScript (`skills/skill-optimizer-ts/`, one-time `npm ci`):

```bash
npm test                                  # vitest run
npx vitest run scripts/validate_skill.test.ts -t "rejects uppercase name"
npm run typecheck                         # tsc --noEmit
npm run lint                              # eslint
npm run format                            # prettier --write
```

## Architecture

### Skill layout is a contract, not a convention

A skill is `SKILL.md` + optional `scripts/` / `references/` / `assets/` + its own
`.claude-plugin/plugin.json`. `validate_skill.py` enforces that frontmatter `name` equals the parent
directory name (`validate.name.dir-mismatch`) and that every `references/*.md` link and
`` `scripts/*.py` `` mention resolves to a real file — so renaming a skill directory breaks
validation until the frontmatter follows.

Adding or renaming a skill means editing **two** manifests: the root
`.claude-plugin/marketplace.json` (the plugin list, keyed by `source: ./skills/<name>`) and the
skill's own `.claude-plugin/plugin.json`. There is no plugin manifest at the repo root — the root is
the marketplace. Skills version independently; a plugin.json `version` mirrors its SKILL.md
`metadata.version` where the skill declares one.

### Scripts are agent tools and are validated as such

Every bundled script is subject to the interface contract in
[interface-contract.md](skills/agent-tool-builder/references/interface-contract.md), machine-checked
by `validate_agent_tool.py`:

- `--format json|text`, `--json` as shorthand alias, `--quiet` to suppress informational stderr.
- Exit codes: `0` success, `1` user/invocation error, `2` system error, `3` not-found/empty. Never
  force the agent to parse text to tell "nothing matched" from "it broke". The validator itself only
  enforces reachable `0/1/2`.
- No `input()`, no free-form stdout errors, argparse-based.
- Library modules that are *not* agent tools carry `# agent-tool: false` within the first 10 lines
  (`skill_lib.py`, `skill_lib.ts`) so the validator skips them.

The reference contract prescribes a JSON default; most scripts here default to `text` because their
primary audience is a human reading validator output. That deviation is deliberate — keep it.

When writing a new script, this repo's own tooling is the spec: run `validate_agent_tool.py` and
`perf_check.py` against it.

### Cross-skill dependency

`skill-optimizer`'s SKILL.md invokes `../agent-tool-builder/scripts/validate_agent_tool.py` and
`perf_check.py` by relative path and links to `agent-tool-builder/references/perf-findings.md`.
Moving or renaming those breaks the Python audit workflow. `skill-optimizer-ts` deliberately has
**no** such dependency — it folds in its own `validate_agent_tool.ts` and `perf_check.ts` so it runs
on machines without Python.

### Python/TypeScript parity

`skill-optimizer` and `skill-optimizer-ts` are twins: same script names, same modes (Audit / Eval),
same OWASP AST## finding codes, parallel `references/`. Mirror behavioral changes across both.
Legitimate divergence: perf checks (Python AST vs TypeScript compiler API, different anti-pattern
sets), security script-construct checks (`pickle`/`shell=True` vs `eval()`/`{shell: true}`), and
dependency pinning (PEP 723 vs `package.json`).

Python scripts import siblings flatly (`from skill_lib import ...`) because they run as
`python3 path/to/script.py`; tests bootstrap `sys.path` via
`skills/skill-optimizer/scripts/tests/conftest.py`. Test placement differs per skill —
`skill-optimizer` in `scripts/tests/`, `agent-tool-builder` in a top-level `tests/`, TypeScript
co-located `*.test.ts`. Python tests are black-box: invoke the script under `subprocess`, assert on
JSON stdout + exit code, don't import functions.

### Descriptions are tuned artifacts

The script-bearing skills overlap by design, so each `description` ends with an explicit
disambiguator (`NOT for skills whose bundled scripts are TypeScript — use skill-optimizer-ts for
that`). `detect_skill_overlap.py` flags sibling pairs at cosine ≥ 0.5 without one. When editing a
description, preserve the disambiguator and the concrete trigger phrases — they were measured by
`eval_triggers.py`, not written for prose.

SKILL.md bodies write `${SKILL_DIR}` for the skill's own directory. It is notation, not a shell
variable; Claude Code resolves it from `${CLAUDE_SKILL_DIR}`.

## Conventions

Ruff runs a wide select list (`S` bandit, `PERF`, `TRY`, `PL`, `ERA`) at line-length 100,
`target-version = py314`. Exceptions go in `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml`
**with a comment explaining why**, not as scattered inline `# noqa`. Pyright is `strict` over
`skills/`.

**Abstraction is opt-in.** Its cost is concepts a reader must hold — functions, parameters, branches,
files, config keys — not lines. A helper or indirection layer earns its keep through repeated use;
until then, change the call sites. Grep first: a near-miss you extend beats a new one alongside.
Don't compress by density either — one wide line doing three things costs more than three doing one
each.

**Keep diffs scoped.** No drive-by restructuring of code the task didn't touch. Deletion is the
exception: remove grep-verified dead code (no callers, no tests) inside functions you're already
editing. Anything larger is a `refactor` pass.

**Comments and docs carry intent, not history or restatement.** Keep the *why* — a workaround, a
vendor quirk, a non-obvious constraint. Drop what the line already says and never narrate the change
("renamed from", "was previously", "new in v2"); git holds that. Strip such detail when you find it.

There is no `docs/` tree. `README.md`, `SKILL.md` bodies, and this file are the whole doc surface —
update whatever your change makes wrong, in the same change. Keep this file under 8 KB.
