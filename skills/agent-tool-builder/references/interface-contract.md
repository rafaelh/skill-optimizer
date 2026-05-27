# Agent Tool Interface Contract

Full specification for Python scripts designed to be called by AI agents.

## Exit codes

### Always present

| Code | Meaning | When to use |
|------|---------|-------------|
| `0` | Success | Data returned, operation completed |
| `1` | User/invocation error | Bad args, missing required flag, enum out of range, validation failed before any work started |
| `2` | System/infrastructure error | Network failure, DB unreachable, file missing, credentials invalid, unexpected exception |
| `3` | Not found / empty result | Query succeeded but nothing matched; resource does not exist |

**Why `3` matters:** Without a dedicated not-found exit code, the agent must
parse output text to distinguish "found nothing" from "hit an error". That
coupling is fragile and breaks whenever output format changes. Exit `3` is the
contract signal: "I ran successfully; there's just nothing here."

### Conditional (add only when meaningfully distinct from `1`/`2`)

| Code | Meaning | When to use |
|------|---------|-------------|
| `4` | Permission denied | Credentials are valid but lack scope for this operation. Distinct from `2` because the agent shouldn't retry — it should surface the issue or escalate. |
| `5` | Conflict | Resource already exists (uniqueness constraint) or precondition failed (etag mismatch). Distinct from `1` because the input was syntactically valid; the agent's recovery is usually "fetch existing, merge, retry" rather than "fix args and retry". |

Don't reach for `4`/`5` reflexively. A login-required tool might genuinely need
`4`; a `--name` lookup that returns nothing should use `3`, not `4`. Most tools
will only use `0/1/2/3`.

## Standard flags

### Always present

```
--format json|text|csv    Output format. DEFAULT: json. Never require --format json explicitly.
--quiet                   Suppress informational stderr. Errors still emit.
```

### Add when the operation returns multiple records

```
--limit N                 Max records per page. Default 20. Cap at 1000.
--offset N                Skip first N records (offset-based pagination).
--cursor TOKEN            Resume from cursor (cursor-based; prefer over offset).
```

### Add when the result has ≥4 returnable fields

```
--fields field1,field2    Return only these fields. Reduces payload size.
```

### Add for any destructive or stateful operation

```
--dry-run                 Describe what would happen without making changes.
                          May still make read-only calls to check current state.
--yes / --force           Bypass any confirmation prompt. Required when the
                          tool is adapted from a human-facing CLI that has
                          interactive confirmations; greenfield agent tools
                          should have no prompts at all (see "Confirmation
                          bypass and TTY detection" below).
```

### Add for filter/search operations

```
--query TEXT              Full-text search string.
--filter key=value        Key-value filter; may be repeated for AND logic.
```

## Output format conventions

Beyond the envelope shape below, three rules govern *what goes inside* the
records:

1. **Flat over nested.** `{"user_id": "u_1", "user_name": "Sara"}` is
   addressable in one hop; `{"user": {"id": "u_1", "name": "Sara"}}` makes
   the agent reason about nesting depth. Nest only when the structure is
   genuinely hierarchical (order line items, file tree).
2. **Consistent units and types across a tool suite.** If one tool returns
   `received_at` as ISO-8601, every sibling tool must too. Durations: pick
   seconds and stick with it. Sizes: bytes. IDs: same prefix scheme. Encode
   the convention in the suite's `_common.py` so it's enforced at the
   formatter, not by per-tool review.
3. **Stable key presence.** A field that's sometimes present and sometimes
   absent forces the agent to write `result.get("x", default)` everywhere.
   Always include the key with `null` when the value is missing, unless the
   field is genuinely a polymorphic discriminator.

### List / search result

```json
{
  "data": [
    {"id": "rec_123", "name": "Example", "status": "active"},
    {"id": "rec_124", "name": "Another",  "status": "inactive"}
  ],
  "meta": {
    "count": 2,
    "total": 47,
    "next_cursor": "eyJwYWdlIjogMn0="
  }
}
```

- `data`: array of records (objects). Never return a bare array as the root.
- `meta.count`: records in this response.
- `meta.total`: total matching records across all pages (omit if expensive/unavailable).
- `meta.next_cursor`: cursor for the next page; `null` or absent if last page.

### Single-record result

```json
{
  "data": {"id": "rec_123", "name": "Example", "status": "active"},
  "meta": {"count": 1}
}
```

`data` may be an object (not array) for single-record fetches. Always include
`meta` with at minimum `"count": 1`.

### Mutating operation (create/update/delete)

```json
{
  "data": {"id": "rec_123", "created": true},
  "meta": {"count": 1}
}
```

For idempotent creates, return `"created": false` with the existing record when
it already exists — exit `0`, not an error.

For dry-run:

```json
{
  "dry_run": true,
  "would_create": {"name": "New Thing", "status": "active"},
  "meta": {"count": 0}
}
```

## Structured error on stderr

On any non-zero exit, write **one line of JSON** to **stderr**:

```json
{"error": "human-readable message", "code": "SCREAMING_SNAKE_CODE", "hint": "actionable next step"}
```

- `error`: what went wrong, in plain English
- `code`: machine-readable identifier for the error class (no spaces)
- `hint`: (optional) what the agent should try next — include the exact flag or command
- `input`: (optional) echo back the failing argument(s) verbatim, e.g.
  `"input": {"id": "rec_99"}`. Lets the agent see what it passed without
  re-deriving from its conversation history.
- `transient`: (optional, boolean) `true` for errors worth retrying with backoff
  (network timeout, 503, rate-limit); omit or `false` for permanent errors
  (permission denied, malformed ID, validation failure). The agent uses this
  to decide between retry and surface-to-user.

**Never write error JSON to stdout.** The agent unconditionally parses stdout
as the result payload. A stray error message there causes a parse failure.

## Streaming output (JSONL)

When the result set is large enough that buffering into one `{"data": [...]}`
array would blow memory (>10k records, or unbounded streams), emit JSON Lines:

```jsonl
{"id": "rec_1", "name": "..."}
{"id": "rec_2", "name": "..."}
{"id": "rec_3", "name": "..."}
```

Rules:

- Document this with `--format jsonl` (in addition to `--format json`).
- One complete JSON object per line. No trailing comma, no array brackets.
- Skip the `{"data": ..., "meta": ...}` envelope — the agent reads to EOF.
- Pagination meta (next_cursor, etc.) doesn't fit JSONL cleanly; if you need
  it, emit a final summary line with a distinguishing key, e.g.
  `{"_summary": {"count": 12345, "next_cursor": "..."}}`.
- Don't mix JSONL with stderr informational output mid-stream — it's fine to
  emit a one-line stderr summary at the end.

## Confirmation bypass and TTY detection

Greenfield agent tools should have **no prompts at all** — the "no `input()`"
rule in [agent_tool.py.template](../assets/templates/agent_tool.py.template)
already handles this. But when adapting an existing human-facing CLI to be
agent-callable, you may inherit a `"Are you sure? [y/N]"` prompt. Two
guardrails are non-negotiable:

1. **A `--yes` (or `--force`) flag** the agent can pass to skip the prompt:

   ```python
   parser.add_argument("--yes", action="store_true",
                       help="Skip confirmation prompts (required for non-interactive use)")
   ```

2. **TTY auto-detection** so an unflagged invocation from a subprocess doesn't
   hang waiting for input that will never come:

   ```python
   import sys
   needs_confirmation = not args.yes and sys.stdin.isatty()
   if needs_confirmation:
       reply = input("Proceed? [y/N] ").strip().lower()
       if reply != "y":
           return EXIT_USER_ERROR
   ```

   When `stdin` isn't a terminal (subprocess invocation, pipe), the prompt is
   skipped entirely — the script either proceeds (if you trust the implicit
   confirmation) or returns `EXIT_USER_ERROR` with a hint to pass `--yes`.

The worst failure mode for an agent tool is one that hangs indefinitely with
no signal — worse than crashing, because the harness can't tell whether to
wait longer or kill the process.

## Argument validation order

1. Parse args (argparse handles unknown flags → exit 2)
2. Validate enum values and ranges (`EXIT_USER_ERROR = 1`)
3. Validate required combinations (`--id` required with `--update`, etc.)
4. Resolve and verify paths/credentials (before network calls)
5. **Then and only then** start the actual operation

## Idempotency guidelines

| Operation                | Recommended behaviour                              |
|--------------------------|----------------------------------------------------|
| Create (already exists)  | Return existing record, `"created": false`, exit 0 |
| Update (no change)       | Return current record, `"updated": false`, exit 0  |
| Delete (already deleted) | Return `{"deleted": false, "reason": "not found"}`, exit 0 OR exit 3 (choose one and document it) |
| Create with unique constraint | Treat as "already exists" case above |

Idempotency makes agents safe to retry on transient failures.

## Pagination patterns

### Cursor-based (preferred)

```python
# First page
result = fetch(limit=20, cursor=None)
# → meta.next_cursor = "abc123"

# Second page
result = fetch(limit=20, cursor="abc123")
# → meta.next_cursor = null  (last page)
```

Cursor tokens must be passed through verbatim. Don't decode, transform, or
validate the cursor format on the client side.

### Offset-based (fallback)

```python
result = fetch(limit=20, offset=0)   # page 1
result = fetch(limit=20, offset=20)  # page 2
```

Offset-based pagination is prone to missing/duplicating records on concurrent
writes. Prefer cursor when the backend supports it.

## `--fields` implementation

Apply field filtering as close to the data source as possible:

1. **Ideal:** pass fields to the underlying API/query (SQL `SELECT col`, GraphQL field selection)
2. **Acceptable:** filter the response dict before serialising
3. **Avoid:** fetch everything, serialise everything, then strip keys from the JSON string

```python
# Acceptable — filter before serialise
records = [asdict(r) for r in results]
if fields:
    records = [{k: v for k, v in r.items() if k in fields} for r in records]
```

Always include `id` in every response even if the caller did not request it —
the agent needs it for follow-up operations.

## Top-level exception handler

Every agent tool must catch all exceptions at the `main()` boundary:

```python
def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        result = run(args)
    except KeyboardInterrupt:
        return EXIT_USER_ERROR
    except Exception as exc:
        _emit_error(str(exc), "UNEXPECTED_ERROR",
                    hint="Check logs or re-run with --debug for a traceback")
        return EXIT_SYSTEM_ERROR
```

An unhandled exception that prints a Python traceback to stderr is parseable by
a human but confusing to an agent. The structured error JSON gives the agent a
consistent format to report upstream.
