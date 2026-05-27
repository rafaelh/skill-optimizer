# Single-Turn Design

An agent calling a tool cannot ask a follow-up question mid-execution. Every
flag decision and input needed to complete the operation must be expressible in
a single invocation. This reference covers patterns for achieving that.

## The "list then act" smell

The most common agent tool design failure is a workflow that requires two calls:

```
# Bad — agent must make two round-trips
list-things --format json        # → get IDs
act-on-thing --id rec_123        # → do the work
```

If the agent always knows the thing's name or a query that identifies it
uniquely, combine the lookup into the action:

```
# Good — one call, agent passes what it knows
act-on-thing --name "Widget" --format json
act-on-thing --query "Widget" --format json
```

When to split anyway: if the "list" step returns genuinely ambiguous results
(multiple matches for a name), the agent legitimately needs to see the list and
choose. Design for this by returning a structured disambiguation error:

```json
{
  "error": "Ambiguous match",
  "code": "AMBIGUOUS_QUERY",
  "hint": "Use --id to specify: rec_123 (Widget v1) or rec_456 (Widget v2)",
  "matches": [
    {"id": "rec_123", "name": "Widget v1"},
    {"id": "rec_456", "name": "Widget v2"}
  ]
}
```

The agent can then re-call with `--id rec_123` — still two turns, but the first
turn returned actionable information, not just a list to page through.

## Flag combinatorics

Design flags so the agent can narrow from broad to specific in one pass:

| Need                 | Flag design                                            |
|----------------------|--------------------------------------------------------|
| Filter by status     | `--status active\|inactive\|all` (default: `all`)      |
| Filter by owner      | `--owner USER_ID`                                      |
| Filter by date range | `--since ISO8601` / `--until ISO8601`                  |
| Multiple conditions  | `--filter key=value` (repeatable)                      |
| Full-text search     | `--query TEXT`                                         |

Prefer **orthogonal** flags (each controls one dimension) over **mode** flags
that change the entire behaviour of the script.

## Mode flags vs. separate scripts

**Bad — one script, mode flag:**

```
data-tool.py --action list
data-tool.py --action create --name Foo
data-tool.py --action delete --id rec_123
```

The problem: the agent must know the `--action` values, and the output shape
varies completely between actions. Validators can't statically check that
`--name` is only required with `--action create`.

**Good — separate scripts:**

```
list-things.py --query Foo --format json
create-thing.py --name Foo --format json
delete-thing.py --id rec_123 --dry-run
```

Each script has a clear purpose, predictable flags, and consistent output shape.
The agent's tool selection (which script to call) does the mode-switching work.

## When a single script is appropriate

One script for multiple related operations is fine when:

1. The operations share ≥80% of their flags
2. The output shape is identical across operations
3. The "mode" is a natural noun modifier, not a verb — e.g. `--type summary|detail|raw`

Even then, use `--type` or `--view` rather than `--action` or `--mode` to
signal that it's an output variation, not a behavioural switch.

## Designing for retryability

The agent may retry a tool call if it receives a transient error (exit 2).
Design operations to be safe to retry:

- **Reads:** always safe
- **Creates:** use idempotent create (return existing if already present)
- **Updates:** use PATCH semantics (only change specified fields)
- **Deletes:** return exit 0 or 3 on "already deleted", never error

## Output shape consistency

The agent builds a mental model of what a tool returns after the first call.
Inconsistency breaks that model:

**Bad — shape varies by result count:**
```python
if len(results) == 1:
    return results[0]   # object
else:
    return results       # array
```

**Good — always the same shape:**
```python
return {"data": results, "meta": {"count": len(results)}}
```

**Bad — field names vary by operation type:**
```python
# List returns: {"items": [...]}
# Get returns:  {"record": {...}}
```

**Good — consistent key:**
```python
# Both return: {"data": ..., "meta": {...}}
```

## Handling large result sets

The agent can only act on data it receives. If a query can return thousands of
records, the agent needs a way to page through them without manual intervention.
Always add `--limit` and either `--cursor` or `--offset`.

Provide a sensible default limit (20–50) that keeps response times under 2s.
Document the maximum in `--help`.

The agent will typically:
1. Call with default limit, check `meta.next_cursor`
2. If cursor is present and it needs more data, call again with `--cursor TOKEN`
3. Stop when `meta.next_cursor` is null

Design the cursor to encode enough state that the backend can resume correctly
after the cursor was generated — don't use wall-clock offsets.
