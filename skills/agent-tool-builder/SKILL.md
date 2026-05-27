---
name: agent-tool-builder
description: >
  Use this skill when building or reviewing Python scripts intended to be called
  by AI agents as tools. Activates when the user asks you to write, scaffold,
  improve, or audit a script an agent will invoke — including CLI tools,
  automation helpers, data-fetching scripts, query utilities, or any script
  where machine-readable output, single-turn completeness, predictable exit
  codes, and per-call performance matter. Trigger even when the user doesn't
  say "agent tool" — e.g. "write me a script to query the database", "build a
  CLI for this API", "make a script the agent can call", "I need a tool that
  fetches X", "build something I can run from Claude to check Z", or "this
  agent script feels slow". Also trigger when improving a script that returns
  unstructured output, uses interactive prompts, requires multiple invocations,
  or shows perf anti-patterns (O(n²) string concat, recompiled regexes,
  list-membership tests).
metadata:
  version: "0.7"
---

# Agent Tool Builder

Build Python scripts that agents can call reliably in a single turn. The core
constraint: an agent cannot interactively query a script — it must express
exactly what it needs via flags on one invocation, and the script must return
structured, parseable output with a meaningful exit code.

## When you reach for this skill

The user is asking you to:

- Write a new Python script an agent will invoke as a tool
- Scaffold a CLI utility for agent-driven automation
- Improve an existing script so an agent can use it without multiple round-trips
- Add machine-readable output, proper exit codes, or flag-based control to a script
- Build a data-fetching, query, or reporting tool that needs to be agent-callable

## Workflow

### 1. Pin down the design decisions

Before writing code, run a decision-forcing interview. Most tools that need a
rewrite a few hours later fail because this step was hand-waved. The discipline:

- **Enumerate the unresolved decisions** below before asking anything. A
  decision is any choice not yet pinned down: alternatives without a pick,
  ambiguous scope, missing constraints, hidden assumptions phrased as
  "obviously we'd…".
- **Order by dependency.** Data model constrains operations; operations
  constrain the flag set; the flag set constrains the JSON output shape.
- **Ask one question at a time, with your recommended answer and a one-line
  rationale.** The user reviews a recommendation; they don't redo the analysis
  from scratch.
- **After each answer, re-scan for new branches** the answer opened up and
  append them to the queue.
- **Stop** when every decision below has an explicit answer.

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

**Questions to answer by reading code or docs, not the user:** how the
underlying API/DB structures pagination, what field names already exist, what
auth mechanism is in place. Reserve the user's attention for choices only they
can make.

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
`1`/`2`; see the interface contract reference loaded above.

**JSON output shape:**

```json
{
  "data": [...],
  "meta": {"count": 10, "total": 150, "next_cursor": "abc123"}
}
```

For single-record operations, `data` may be an object rather than an array.
Always include `meta` even when empty — it gives the agent a stable key.

**Structured errors on stderr (never stdout):**

```json
{"error": "Resource not found", "code": "NOT_FOUND", "hint": "List with: list-things --json"}
```

Stdout must remain clean JSON (or empty) so the agent can `json.loads()` it
unconditionally. Optional fields `"input"` (echo failing arg) and
`"transient": true` (retryable) help the agent decide between retry and
surface-to-user.

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
       r = subprocess.run(
           [sys.executable, str(TOOL), "--id=42"], capture_output=True, text=True
       )
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

The agent cannot ask follow-up questions mid-script. Design flags so any
legitimate need is expressible in one invocation:

- [ ] Can the agent filter without a separate list call? (`--filter key=value`)
- [ ] Can the agent select only the fields it needs? (`--fields id,name`)
- [ ] Can the agent page through large result sets? (`--limit` / `--cursor`)
- [ ] Can the agent combine logical conditions in one call?
- [ ] Can the agent suppress noise? (`--quiet`)
- [ ] Are all required identifiers passable as flags? (no interactive ID selection)

Read [references/single-turn-design.md](references/single-turn-design.md)
when: the operation naturally requires "first list, then act" (usually
resolvable with a combined `--name` or `--query` flag); you're tempted to add
a `--mode` or `--action` flag (split instead); the output shape varies
significantly between invocations.

### 5. Validate the interface

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/validate_agent_tool.py" <script-path> --format json
```

Checks: argparse present, `--format` / `--quiet` flags, reachable exit codes
`0/1/2/3`, no `input()`, stdout/stderr not mixed, PEP 723 block (with both
`requires-python` and `dependencies`) when non-stdlib imports are detected,
`epilog=` with examples, structured error JSON on stderr.

### 6. Check for performance anti-patterns

Agent tools are called many times per session, so per-call overhead compounds.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/perf_check.py" <script-or-dir> --format json
```

Accepts a file or a directory (walked recursively). Treat **HIGH** findings as
blockers. Read [references/perf-findings.md](references/perf-findings.md) when
interpreting findings, deciding whether a flagged pattern matters in this
specific tool, or running the runtime profiler against a slow script.

## Gotchas

- **Don't skip the decision-forcing interview.** "We'll just have it return
  the records" is the agent-tool equivalent of "we'll figure it out later" —
  those decisions still exist and surface as bugs, awkward flag retrofits,
  or whole-script rewrites.
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
- **Cursor tokens are opaque.** Pass them through verbatim in
  `meta.next_cursor`; don't decode or transform.
- **Catch all exceptions at `main()`.** An unhandled traceback on stderr is
  confusing to the agent. Emit structured `{"error", "code", "hint"}` JSON and
  exit `2`.
- **Compile regexes at module scope.** Defining a pattern inside `main()` or a
  per-record loop recompiles it on every call. The perf linter flags this.
- **Build output structures, then serialise once.** `out += json.dumps(record) + "\n"`
  inside a loop is O(n²). Collect into a list, then dump the assembled
  structure.
- **Use sets for membership tests against literals.** `if x in {"a","b","c"}`
  is O(1); `if x in ["a","b","c"]` rebuilds and scans the list each call.
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

## Anti-patterns to flag

- Banner, table header, or "Fetching data..." on **stdout** — breaks JSON parsing
- `sys.exit(0)` on empty results instead of `sys.exit(3)`
- `argparse` with no `--format` flag — agents have no way to request structured output
- A single script with `--action create|update|delete` — split into separate scripts
- `input()` or `getpass()` anywhere in the call path
- Hardcoded page size with no `--limit`
- Raising exceptions that produce Python tracebacks instead of structured `{"error"}` JSON
- Returning different JSON shapes by result count (object vs array) — agent parsing breaks
- Required business logic in the text formatter that's absent from JSON
- A script requiring two sequential calls for one logical operation
- `argparse.ArgumentParser` without `epilog=` examples — the validator flags this
- Writing every test up front then writing the script (horizontal slicing — see the `tdd` skill)
- Mocking internal functions instead of running the script as a subprocess
- Inconsistent units or field names across sibling tools in a suite
- A confirmation prompt without `--yes` AND without `sys.stdin.isatty()` — silent hang

## Worked examples

Read [references/patterns.md](references/patterns.md) when:

- You're auditing existing code against the rules above and want a side-by-side comparison
- A user asks *why* one of the rules matters and code lands the point faster than prose
- You're scaffolding from scratch and want to confirm the shape of the
  happy-path output, validation block, JSONL streaming, or stderr-logging idiom
