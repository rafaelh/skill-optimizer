---
name: agent-tool-builder
description: >
  Build or audit Python scripts called by AI agents as tools. BUILD mode: when
  the user asks you to write, scaffold, or improve a script an agent will
  invoke — CLI tools, automation helpers, data-fetching or query utilities,
  anything where machine-readable output, single-turn completeness, predictable
  exit codes, and per-call performance matter. Trigger without the words "agent
  tool" — "write me a script to query the database", "build a CLI for this
  API", "this agent script feels slow".
  AUDIT mode: when the user asks you to review, critique, check, or trim an
  existing agent-callable script — "review this tool", "is this script
  agent-friendly", "check my CLI against best practices", "this tool has too
  many flags", "trim this down", "why does the agent keep calling this twice".
  Also trigger on scripts returning unstructured output, using interactive
  prompts, requiring multiple invocations, or showing perf anti-patterns (O(n²)
  string concat, recompiled regexes, list-membership tests).
metadata:
  version: "0.8"
---

# Agent Tool Builder

Build Python scripts that agents can call reliably in a single turn. The core
constraint: an agent cannot interactively query a script — it must express
exactly what it needs via flags on one invocation, and the script must return
structured, parseable output with a meaningful exit code.

This skill operates in two modes. Identify which mode applies, then follow that
mode's workflow exclusively.

## Mode selection

| Mode      | Use when the user wants to…                                          |
|-----------|----------------------------------------------------------------------|
| **Build** | Write a new agent tool, scaffold a CLI, or add agent-callable behaviour (machine-readable output, exit codes, flag-based control) to an existing script |
| **Audit** | Review, critique, or trim an existing tool — check it against the contract, find flaws, or cut surface that has accreted |

"Improve this script so the agent can use it" is Build if the script is not yet
agent-callable, Audit if it already is; when genuinely unclear, ask. If the
request spans both ("audit this and fix what you find"), run Audit first, then
fix. The **interface contract** (Build step 2), **Gotchas**, and
**Anti-patterns** sections are shared — both modes use them.

---

## Mode 1 — Build

### 1. Pin down the design decisions

Before writing code, run a decision-forcing interview. Most tools that need a
rewrite a few hours later fail because this step was hand-waved.

Enumerate the unresolved decisions below before asking anything, order them by
dependency (data model → operations → flag set → JSON shape), and ask one at a
time with your recommended answer and a one-line rationale — the user reviews a
recommendation rather than redoing the analysis. Re-scan for new branches after
each answer. Stop when every decision below has an explicit answer.

**Canonical decisions for an agent tool:**

1. **What does the tool operate on?** (files, DB, HTTP API, local state, queue)
2. **What are the discrete operations?** One script per operation is usually
   cleaner than one mega-script with `--mode`. Confirm whether to split.
3. **What's the natural identifier?** UUID, slug, composite key? Is there a
   human-readable alternative the agent might prefer?
4. **What does a successful result look like?** Sketch the exact JSON shape —
   field names, nesting, types — and confirm one example payload.
5. **Is the operation destructive or stateful?** If yes, `--dry-run` is
   required; pin down what dry-run means precisely (does it hit the network?).
6. **What are the failure modes?** Map each to an exit code: bad input → 1,
   infrastructure → 2, not-found → 3. Confirm there isn't a fourth category.
7. **What's the expected result-set size?** Single record, bounded list, or
   unbounded? This determines whether `--limit` / `--cursor` are required.

**Answer these by reading code or docs, not by asking:** how the underlying
API/DB paginates, what field names exist, what auth is in place. Reserve the
user's attention for choices only they can make.

### 2. Apply the standard interface contract

Every agent tool must satisfy this interface. Read the full contract at
[references/interface-contract.md](references/interface-contract.md) when
designing a new tool or auditing an existing one.

**Mandatory flags:**

| Flag       | Behaviour                                                            |
|------------|----------------------------------------------------------------------|
| `--format` | `json` (default), `text`, or `csv` — agents should never need to ask for JSON explicitly |
| `--quiet`  | Suppress informational stderr; errors still emit                     |

**Conditional flags (add when relevant):**

| Flag                       | When required                                          |
|----------------------------|--------------------------------------------------------|
| `--fields field1,field2`   | Any fetch/list operation with ≥4 returnable fields     |
| `--limit N` / `--offset N` | Any operation that may return multiple records         |
| `--cursor TOKEN`           | Prefer cursor over offset when the backend supports it |
| `--dry-run`                | Any create/update/delete/write operation               |

**Exit codes — non-negotiable:**

| Code | Meaning                                                                     |
|------|-----------------------------------------------------------------------------|
| `0`  | Success — data returned or operation completed                              |
| `1`  | User/invocation error — bad args, missing required flag, validation failed  |
| `2`  | System/infrastructure error — network failure, DB unreachable, file missing |
| `3`  | Not found / empty result — query succeeded but nothing matched              |

Exit code `3` is critical: it lets the agent distinguish "the thing doesn't
exist" from "an error occurred" without parsing output. Add `4` (permission
denied) or `5` (conflict) only when the agent's recovery path differs from
`1`/`2`.

**Output shapes.** Stdout carries clean JSON or nothing, so the agent can
`json.loads()` it unconditionally; errors go to stderr as their own JSON
object. Always include `meta`, even empty — it gives the agent a stable key.
`data` may be an object for single-record operations, but the shape must not
change with result count.

```json
{"data": [...], "meta": {"count": 10, "total": 150, "next_cursor": "abc123"}}
```
```json
{"error": "Resource not found", "code": "NOT_FOUND", "hint": "List with: list-things --json"}
```

Optional error fields `"input"` (echo the failing arg) and `"transient": true`
(retryable) help the agent choose between retry and surface-to-user.

**Help text must include examples.** Use `argparse.RawDescriptionHelpFormatter`
and put 2–3 realistic invocations in `epilog`. `--help` is the agent's primary
reference; flag lists without examples leave it guessing.

### 3. Scaffold, then build test-first

**3a. Scaffold the boilerplate.** Run the bundled scaffolder rather than
copying the template by hand:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/init_tool.py" path/to/tool.py --name fetch_user --with-tests
```

This writes a fully-formed tool with `{{script_name}}` substituted, makes it
executable, and (with `--with-tests`) drops a paired subprocess test
alongside. The scaffold provides the argparse skeleton, exit-code constants,
`_emit_error()`, and the `--format` / `--quiet` wiring. Skip this step when
modifying an existing script. (The underlying template lives at
`assets/templates/agent_tool.py.template` — `init_tool.py` reads it for you,
no need to load it by hand.)

**3b. Apply PEP 723 if the script is standalone.** Standalone means: anything
in `bash/scripts/`, any `claude/skills/<name>/scripts/` file, or any one-off
helper you might copy to another machine. Skip for modules inside a package
(e.g. `src/tools/`, `src/actions/`) where `pyproject.toml` owns deps.

Read [references/pep723.md](references/pep723.md) for: shebang choice,
`requires-python` and `dependencies` rules, drift modes the validator catches.

**3c. Drive the implementation with TDD.** This skill delegates the
red-green-refactor mechanics to the [`tdd` skill](../tdd/SKILL.md) — load it
if you're shaky on vertical-slice TDD or the horizontal-slicing trap. Two
agent-tool specifics layer on top:

1. **Test the CLI as a black box.** Drive every test via `subprocess.run`
   against the script. Patching internal functions like `fetch_record()`
   tests the adapter, not the CLI an agent will invoke.

   ```python
   def test_happy_path():
       r = subprocess.run([sys.executable, str(TOOL), "--id=42"], capture_output=True, text=True)
       assert r.returncode == 0, r.stderr
       payload = json.loads(r.stdout)
       assert payload["data"]["id"] == "42"
   ```

2. **The test agenda for an agent tool** — drive each as one vertical slice:

   1. Happy path → exit `0`, expected JSON shape on stdout
   2. Validation failure → exit `1`, structured error on stderr
   3. Not-found → exit `3`, no stdout
   4. Field selection (if `--fields` is in scope) → output limited to keys
   5. Pagination (if multi-record per step 1.7) → correct slice + `meta.next_cursor`
   6. Dry-run (if destructive per step 1.5) → exit `0`, no side effects
   7. System error → exit `2`, structured error on stderr

   Drop entries whose gating condition didn't fire in step 1.

**Structural rules that govern every cycle:**

- **Validate all arguments before doing any work.** Check required flags,
  validate enums, resolve paths — then fail fast with exit `1`. Never start a
  network call or DB query before validation passes.
- **Separate pure logic from I/O.** Put the core operation in a function
  returning a dataclass or dict; keep all `print()` / `sys.exit()` in `main()`.
- **Never use `input()` or any interactive prompt.** If a required value is
  missing, exit `1` with a hint about which flag to add.
- **Make operations idempotent where possible.** A `create` that already
  exists should return the existing record with `"created": false`, not error.

### 4. Design for single-turn completeness

The agent cannot ask follow-up questions mid-script. Beyond the flags the
contract already mandates, confirm it can filter without a separate list call
(`--filter key=value`), combine logical conditions in one invocation, and pass
every required identifier as a flag — no interactive ID selection.

Read [references/single-turn-design.md](references/single-turn-design.md)
when: the operation naturally requires "first list, then act" (usually
resolvable with a combined `--name` or `--query` flag); you're tempted to add
a `--mode` or `--action` flag (split instead); the output shape varies
significantly between invocations.

### 5. Validate the interface and check performance

Run both validators. For a single small tool, run them inline. For a suite, or
when either produces long output, delegate to one subagent so the raw per-file
dumps stay out of the main context.

> **Subagent prompt:** Run these two commands against `<script-or-dir>` and
> return only FAIL, WARN, and HIGH/MEDIUM findings, each with file, line, code,
> and a one-line fix. Omit passes and LOW findings unless nothing else fired.
>
> ```bash
> python3 "${CLAUDE_SKILL_DIR}/scripts/validate_agent_tool.py" <script-path> --format json
> python3 "${CLAUDE_SKILL_DIR}/scripts/perf_check.py" <script-or-dir> --format json
> ```
>
> If any perf finding needs interpretation, read
> [references/perf-findings.md](references/perf-findings.md) and include the
> recommended fix. If both are clean, return "Validators pass."

`validate_agent_tool.py` checks the contract mechanically: argparse present,
`--format` / `--quiet`, reachable exit codes `0/1/2/3`, no `input()`,
stdout/stderr not mixed, PEP 723 block when non-stdlib imports are detected,
`epilog=` examples, structured error JSON on stderr.

`perf_check.py` accepts a file or a directory (walked recursively). Treat
**HIGH** findings as blockers. Read
[references/perf-findings.md](references/perf-findings.md) when deciding
whether a flagged pattern matters in this specific tool, or when running the
runtime profiler against a slow script.

### 6. Justify anything the design didn't call for

If implementation surfaced a flag that wasn't in the step-1 decisions, it needs
a justification, not just a plausible use. Read
[references/minimization.md](references/minimization.md) and apply the removal
heuristics before it ships — the cheapest surface to cut is surface never added.

---

## Mode 2 — Audit

Assess an existing agent tool from three independent angles, then reconcile.
The reviews run as parallel subagents: they produce verbose output, they must
not anchor on each other, and only their findings need reach the main context.

### 1. Build a context brief

Do this yourself before dispatching — the reviewers are far less useful
guessing at it. One short paragraph covering: what the tool does and operates
on; who invokes it (agent, human, CI, or a mix); where it is actually called
from (grep the script name across the repo, sibling skills, and `.md` docs, and
note real call sites); and whether it is published anywhere that makes removals
breaking.

### 2. Dispatch three reviewers in parallel

Read [references/audit-reviewers.md](references/audit-reviewers.md) now — it
holds the three ready-to-dispatch subagent prompts. Fill in the tool path and
your context brief, then launch all three in one batch:

- **A — contract audit.** Runs both validators, reads the contract and perf
  references, then hand-checks what the scripts cannot verify.
- **B — unconstrained review.** No checklist, no references. Reviews as a
  critical engineer: bugs, design flaws, anything a linter never catches.
- **C — minimization.** Follows
  [references/minimization.md](references/minimization.md) when inventorying the
  tool's surface, hunting for what never earned its keep. Proposes only cuts.

### 3. Reconcile

Merge the three reports yourself. Order matters:

1. **Deduplicate.** A finding raised by two reviewers is stronger, not two
   findings. Note the corroboration.
2. **Resolve the built-in conflict.** Reviewer B proposes additions; Reviewer C
   proposes cuts. Where they collide — B wants a flag, C flags that area as
   accreting — **default to not adding.** An addition ships only if it removes a
   round-trip the agent currently has to make, fixes a correctness bug, or the
   user asks for it. "Would be nice to have" loses to "keeps `--help`
   readable". This default is the whole reason C exists; do not quietly
   override it because B's argument reads well.
3. **Rank by cost to the caller.** A broken JSON shape or an unreachable exit
   `3` costs the agent every call. A LOW perf finding costs nothing. Sort
   accordingly, not by severity label.

### 4. Report, apply, re-validate

Present the reconciled findings grouped as **Blockers** (contract violations,
correctness bugs, HIGH perf), **Improvements**, and **Removals**, noting which
reviewer raised each and whether it was corroborated.

Apply blockers and approved improvements. Do not apply removals unilaterally —
deleting a flag someone relies on is a breaking change the user should sign off
on, even when the evidence looks conclusive.

Re-run Reviewer A's two commands after changes land: fixes to one contract rule
routinely break another — cutting a flag can orphan an `epilog` example,
restructuring output can make exit `3` unreachable.

### Gotchas (Audit)

- **Don't hand the reviewers each other's output.** Running them sequentially
  collapses three lenses into one — the second confirms the first instead of
  seeing past it.
- **A clean validator run is not a clean tool.** `validate_agent_tool.py`
  checks structure, not judgment: it can't tell that a flag is useless or that
  the tool needs two calls to do one thing. Reviewers B and C exist for exactly
  that gap — don't skip them when A comes back green.
- **Human-facing surface is not dead surface.** `--format text`, colour output,
  and profiling modes serve the maintainer. If a report proposes cutting one,
  check whether it mistook audience for disuse.
- **Findings are not automatically a rewrite.** Three MEDIUM findings on a
  stable call surface want three fixes, not a regeneration from the scaffolder.

---

## Gotchas (both modes)

- **Don't skip the decision-forcing interview.** "We'll just have it return the
  records" is "we'll figure it out later" — those decisions still exist, and
  resurface as bugs, awkward flag retrofits, or whole-script rewrites.
- **Stdout must be clean JSON or empty.** A startup message, progress spinner,
  or "done!" line on stdout breaks the agent's `json.loads()`. Route
  informational output to stderr, gated on `--quiet`.
- **Exit code `3` is not optional.** Without it, the agent cannot distinguish
  "not found" from "error" without parsing output, and will retry or assume
  failure.
- **`--format text` is for humans, not agents.** It exists for developers
  reading local output. Agents always use JSON (the default). Don't put logic
  in the text formatter that's absent from JSON.
- **Argparse `required=True` exits `2`, not `1`.** If you want exit `1` for
  missing args (it's a user error, not a system error), override
  `ArgumentParser.error()` or validate manually after `parse_args()`.
- **Dry-run is not the same as offline.** If your `--dry-run` path makes any
  network calls to check state, document this clearly in `--help`.
- **`--fields` filtering should happen before serialisation.** Fetching the
  full record then dropping keys wastes bandwidth. Pass through to the
  underlying API/query when the backend supports field selection.
- **Cursor tokens are opaque.** Pass them verbatim in `meta.next_cursor`.
- **Catch all exceptions at `main()`.** An unhandled traceback on stderr is
  confusing to the agent. Emit structured `{"error", "code", "hint"}` JSON and
  exit `2`.
- **Per-call overhead compounds.** Compile regexes at module scope, build
  output structures and serialise once (`out += json.dumps(r)` in a loop is
  O(n²)), and use set literals for membership tests. `perf_check.py` flags all
  three; read [references/perf-findings.md](references/perf-findings.md) when
  you need the rationale or the severity policy.
- **Keep units consistent across a tool suite.** Pick one timestamp format
  (ISO-8601), one duration unit (seconds), one size unit (bytes), and write
  the convention into the suite's `_common.py`. Mixing ISO-8601 in one tool
  and Unix epoch in another forces the agent to branch on representation.
- **For destructive ops adapted from human-facing CLIs, add `--yes` AND
  detect non-TTY.** Both are non-negotiable: a `--yes`/`--force` flag, plus a
  `sys.stdin.isatty()` check that auto-bypasses prompts when stdin isn't a
  terminal. Without the TTY check, an unflagged subprocess invocation hangs
  forever — the worst failure mode for an agent. Greenfield agent tools
  should just not have prompts (per the "no `input()`" rule).

## Anti-patterns to flag (both modes)

Every Gotcha above is also a scan target. These are the ones with no gotcha of
their own:

- `argparse` with no `--format` flag — agents have no way to request structured output
- A single script with `--action create|update|delete` — split into separate scripts
- `input()` or `getpass()` anywhere in the call path
- Hardcoded page size with no `--limit`
- Returning different JSON shapes by result count (object vs array) — agent parsing breaks
- A script requiring two sequential calls for one logical operation
- `argparse.ArgumentParser` without `epilog=` examples — the validator flags this
- Writing every test up front then writing the script (horizontal slicing — see the `tdd` skill)
- Mocking internal functions instead of running the script as a subprocess
- A confirmation prompt without `--yes` AND without `sys.stdin.isatty()` — silent hang
- Accreted surface: flags reimplementing shell primitives (`--sort-by`,
  `--head`, `--grep` on JSON the agent could pipe to `jq`), tuning knobs no call
  site has ever set (`--timeout`, `--retries`), both halves of one axis
  (`--verbose` alongside `--quiet`), escape hatches that bypass the tool's own
  interface (`--raw`, `--extra-args`), or a `--help` long enough that choosing
  an invocation takes two readings. Read
  [references/minimization.md](references/minimization.md) before adding one
  more flag, or when auditing what is already there.

## Worked examples

Read [references/patterns.md](references/patterns.md) when you want a
side-by-side good/bad comparison to audit against, when a user asks *why* a
rule matters and code lands the point faster than prose, or when you need to
confirm the shape of the happy-path output, validation block, JSONL streaming,
or stderr-logging idiom.
