---
name: run-skills
description: Build, run, smoke-test, and drive the skills marketplace and its bundled agent tools. Use when asked to run this repo, start or exercise the skill-optimizer / agent-tool-builder scripts, verify a change to a bundled script or SKILL.md actually works, check Python/TypeScript parity, run the tests, lint, typecheck, or confirm the marketplace manifests still line up.
---

# Run the skills marketplace

There is no server and no UI here. The "app" is the **19 agent-tool CLI scripts** under
`skills/*/scripts/` — 11 Python, 8 TypeScript. You drive them with
`.claude/skills/run-skills/smoke.py`, which launches every one of them for real, asserts the
interface contract holds, dogfoods the repo's own validators against its own skills, and checks
that the two manifests still agree with what is on disk.

All paths below are relative to the repo root (`/home/rafael/git/skills`).

## Prerequisites

Verified on Arch Linux with `uv 0.12.1`, `Python 3.14.6`, `node v26.5.1`, `npm 12.0.1`.

**Python 3.14 is a hard floor, not a preference** — see Gotchas.

## Setup

```bash
uv sync                                   # dev deps: pytest, ruff, pyright
cd skills/skill-optimizer-ts && npm ci    # tsx, vitest, eslint, prettier
```

`npm ci` takes ~1.3s and prints a warning about a blocked `esbuild` postinstall. That is harmless
— see Gotchas.

## Run (agent path)

```bash
uv run python3 .claude/skills/run-skills/smoke.py
```

59 checks in ~5.5s. Add `--tests` for 61 checks in ~14.4s. Tail of a green run:

```
  [PASS] manifests/plugin-json/tdd  name='tdd'
  [PASS] tests/pytest  162 passed in 3.20s
  [PASS] tests/vitest  Tests  170 passed (170)

61/61 checks passed in 14.7s
```

| flag | what it does |
|---|---|
| `--only python` | `--help` + JSON-on-stdout + self-audit for the 11 Python tools |
| `--only ts` | same for the 8 TypeScript tools, run through the vendored `tsx` |
| `--only parity` | runs both `validate_skill` twins on all 7 skills and diffs their summaries |
| `--only manifests` | `marketplace.json` ↔ `skills/` ↔ each `plugin.json` name |
| `--tests` | also runs pytest and vitest |
| `--json` / `--format json` | machine-readable; honoured on every exit path |
| `--quiet` | suppress the `repo: <path>` line on stderr |

Exit codes (all four exercised): `0` all passed, `1` at least one check failed, `2` toolchain
incomplete, `3` no tools found on disk. Non-zero exits also emit
`{"error":…,"code":…,"hint":…}` on stderr.

The parity check has real signal — a skill with one broken reference link makes both twins
independently report `warn=1`, and the check compares those summaries rather than trivially
matching empty output.

## Direct invocation — the layer most changes touch

Most work here edits one script. Run that script directly rather than the whole sweep:

```bash
# any Python tool
uv run python3 skills/skill-optimizer/scripts/validate_skill.py skills/architecture --json

# any TypeScript tool — use the vendored binary, not `npx`
skills/skill-optimizer-ts/node_modules/.bin/tsx \
  skills/skill-optimizer-ts/scripts/validate_skill.ts skills/architecture --json
```

Both print the same payload:

```json
{ "skill_dir": "…/skills/architecture", "issues": [], "summary": { "fail": 0, "warn": 0, "ok": true } }
```

Changed a script? Check it against the contract this repo enforces:

```bash
uv run python3 skills/agent-tool-builder/scripts/validate_agent_tool.py <script.py>
uv run python3 skills/agent-tool-builder/scripts/perf_check.py <script.py>
```

To see a validator actually fire, point it at a deliberately broken skill — a `Name_Like_This`
that mismatches its directory plus a dead `references/` link yields 2 fails and 3 warns, exit 1.

## Test, lint, typecheck

```bash
uv run pytest -q                                    # 162 passed in ~3.2s
uv run ruff check . --fix && uv run ruff format .
uv run pyright                                      # strict, 24 files, 0 errors
uv run pre-commit run --all-files                   # 10 hooks, all pass
```

```bash
cd skills/skill-optimizer-ts
npm test          # 170 passed (9 files) in ~5.7s
npm run typecheck
npm run lint
```

## Gotchas

- **`detect_skill_overlap` reports a collision on a healthy repo.** `skill-optimizer` and
  `skill-optimizer-ts` sit at cosine **0.8749**, well over the 0.5 threshold, despite both carrying
  disambiguators. Expected, not a regression — read `pairs`, don't treat it as a failure.
- **Findings never fail the exit code by default.** `validate_skill`, `analyze_skill`,
  `audit_security`, and `detect_skill_overlap` all exit 0 with findings — a skill tripping 5
  security warnings (bidi override, `curl | bash`, `pickle.loads`, `shell=True`, unpinned dep)
  still exits 0. Pass `--exit-on-warn` for CI. Exit 1 means bad arguments, nothing else.
- **`audit_security` reads prose as instructions — quote payloads as code.** AST01 now flags
  instruction-override, replacement-prompt, conceal-from-user, and exfiltration phrasing in
  SKILL.md and `references/*.md`. It blanks fenced blocks and inline code first, so documentation
  that *exhibits* an attack must wrap it in backticks, **on one line** — an inline span does not
  cross a newline, so a payload wrapped across two source lines still fires. That blanking is also
  an evasion route: a hostile skill can hide the same sentence in a fenced block. Treat AST01 as a
  tripwire for careless payloads, not an adversary-proof filter — and note that a clean report is
  never proof a skill is safe to install; the semantic risks
  (AST07/09/10) need the human checks in `references/security.md`.
- **Python 3.14 is enforced by syntax, not a version check.**
  `skills/skill-optimizer/scripts/audit_security.py:157` uses PEP 758 unparenthesized
  `except A, B:`. On 3.13 that is `SyntaxError: multiple exception types must be parenthesized` —
  no friendly message. And `ruff format` at `target-version = py314` will *rewrite* your
  parenthesized `except (A, B):` into that form, so this spreads on its own.
- **`npx tsx` prints `npm notice run …` lines** on npm 12. They go to **stderr**, so stdout stays
  parseable JSON — but `./node_modules/.bin/tsx` avoids the noise entirely and is what the driver
  uses.
- **`count_tokens.py` silently degrades without `ANTHROPIC_API_KEY`**, returning
  `"method": "heuristic", "exact": false` at ~3.5 chars/token. It never errors, so a token count
  from this repo is an estimate unless you check `exact`.
- **Distinguish exit 2 from exit 3.** A missing input path is exit **2** (`file missing` is a
  system error under the contract): `validate_skill.py skills/nope` → `validate.input.not-found`.
  Exit **3** means the run succeeded and found nothing — `detect_skill_overlap.py <empty-dir>`.
- **`perf_check.py` reports 3 MEDIUM findings on the driver itself** (one `sorted()[:4]` over a
  4-key dict, two `except KeyError` inside loops). Both are deliberate — defensive JSON parsing on
  a cold path — and were left rather than contorted to silence the linter.
- **`eval_triggers.py` and `optimize_description.py` are not exercised** by the driver beyond
  `--help`; they shell out to an agent CLI and cost tokens per run.

## Troubleshooting

- **`npm error enoent Could not read package.json … /home/rafael/git/skills/package.json`**: there
  is no root `package.json`. `cd skills/skill-optimizer-ts` first — every npm script lives there.
- **`npm warn install-scripts esbuild@0.28.1 (postinstall: node install.js)` blocked**: npm 12
  blocks unapproved install scripts. Harmless here — esbuild ships prebuilt platform binaries as
  optional deps. Verified after a fresh `npm ci`: `tsx` runs and all 168 vitest tests pass.
- **`SyntaxError: multiple exception types must be parenthesized`**: you are on Python ≤ 3.13.
  `uv sync` pins 3.14; check with `uv run python3 --version`.
- **`toolchain/tsx  missing …/node_modules/.bin/tsx`** (driver exits 2): run
  `cd skills/skill-optimizer-ts && npm ci`.
- **Driver reports `manifests/marketplace-covers-disk … only-on-disk=['<name>']`**: a skill
  directory exists without a `marketplace.json` entry. Adding a skill means editing **two**
  manifests — the root `.claude-plugin/marketplace.json` and the skill's own
  `.claude-plugin/plugin.json`.
