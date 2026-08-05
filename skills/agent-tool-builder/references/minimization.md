# Tool minimization — cutting surface that never earns its keep

Load this when auditing an existing tool for unnecessary surface, or when
deciding whether a proposed addition is worth its cost.

## Why this exists

Every review round proposes additions. Almost nothing proposes removals. Left
alone, a tool's flag surface grows monotonically — each round adds a
"completeness" flag, a second output mode, a config knob — until `--help` is
three screens long and the agent has to reason about twenty options to make one
call. That degrades the exact property the tool exists for: an agent picking the
right single invocation on the first try.

Minimization is the counterweight. It runs in the same audit as the improvement
review, deliberately, so growth and pruning are decided together.

## Step 1 — Inventory

Before judging anything, list it. Enumerate exhaustively:

- Every flag and its default
- Every subcommand or `--mode`-style branch
- Every field in the JSON output shape
- Every module-level function, and whether anything calls it
- Every output format beyond `json`

Work from the actual source, not from `--help` — help text drifts and omits
dead internals.

## Step 2 — Classify each item

| Class | Meaning | Action |
|-------|---------|--------|
| **Load-bearing** | An agent needs it to complete a realistic task in one call | Keep |
| **Human affordance** | Exists for a developer reading local output or debugging | Keep, but confirm it's documented as such |
| **Contract-mandated** | Required by the interface contract (see below) | Keep unconditionally |
| **Speculative** | Added "for completeness"; no call site, no realistic agent need | Cut |
| **Redundant** | A second way to express something another flag already covers | Unify |

Human affordances are legitimate. `--format text`, colourised output, a
`--profile` mode, a verbose traceback flag — these serve the person maintaining
the tool. Do not cut them just because an agent won't call them. Do flag any
whose purpose is ambiguous, and ask which audience it serves.

## Step 3 — Removal heuristics

Signals that an item is speculative rather than load-bearing:

- **No call site.** Grep the repo, the skill's own scripts, and any sibling
  tools. A flag that appears only in its own `add_argument` call and nowhere
  else has never been used.
- **Duplicates a shell primitive.** `--sort-by`, `--head N`, `--grep PATTERN`
  on JSON output are `jq` reimplemented badly. The agent can pipe or filter what
  it got back.
- **Configurable constant.** `--timeout`, `--retries`, `--batch-size` with a
  sensible default that no caller has ever overridden. Hardcode the default and
  delete the flag; reintroduce it the day someone needs a second value.
- **Second output mode with no consumer.** A `--format xml` or `--format yaml`
  added alongside `json` and `csv` because the list felt incomplete.
- **Flag that only matters in combination with another flag.** Usually a sign
  the two should be one flag taking a value.
- **Boolean pair.** `--verbose` alongside `--quiet`; `--include-archived`
  alongside `--only-active`. Pick one axis with a default.
- **Escape hatch nobody escapes through.** `--raw`, `--passthrough`,
  `--extra-args` that exist so callers can bypass the tool's own design.
- **Internal helper with one caller.** A function extracted for symmetry, called
  from exactly one place, adding an indirection an auditor has to follow.

## Step 4 — The unification test

Two operations should merge when all three hold:

1. They take the same identifier and return the same shape
2. Their difference is expressible as one flag value, not a branch in the logic
3. An agent choosing between them would have to guess

They should stay split when the difference is a *verb* — `create` vs `delete`
are separate scripts even though they share an identifier, because a mistaken
choice is destructive and because `--action` flags force the agent to reason
about a mode before it reasons about the task.

Similarly, collapse near-duplicate functions when they differ only by a
constant or a formatting choice; keep them apart when one is pure logic and the
other does I/O — that separation is structural, not duplication.

## What is never a cut candidate

These are contract surface. Removing them breaks the agent's ability to call
the tool reliably, however unused they look:

- `--format` and `--quiet`
- Exit codes `0` / `1` / `2` / `3`, including `3` on empty results
- `--dry-run` on any destructive operation
- `epilog=` examples in the argparse setup
- The `meta` key in JSON output, even when empty
- Structured `{"error", "code", "hint"}` on stderr
- Argument validation that runs before any I/O

## Evidence, not intuition

Every removal proposal needs a stated reason from evidence:

```bash
# Is this flag referenced anywhere outside its own definition?
grep -rn -- "--batch-size" . --include='*.py' --include='*.md' --include='*.sh'

# Was it added speculatively, or in response to a need?
git log -S'--batch-size' --oneline -- path/to/tool.py
```

A flag introduced in the same commit as five other flags, with a message like
"add filtering options", is speculative until proven otherwise. A flag
introduced alone with a message describing a concrete failure is load-bearing.

## Reporting

For each candidate, give: the item, its class, the evidence (call-site grep
result or commit context), and the removal's blast radius — does anything break,
and is the tool's version published anywhere that would make this a breaking
change? Sort by lines removed, descending. Do not apply cuts unilaterally;
removals are the user's call in a way additions are not.
