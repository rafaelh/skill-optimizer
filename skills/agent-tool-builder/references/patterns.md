# Good vs bad patterns

Concrete code examples for the rules in `SKILL.md`. Load this file when
auditing existing code, when explaining *why* a rule matters with a worked
example, or when the user asks for a side-by-side comparison.

## Output shape and exit codes

**Bad — unstructured output, no exit codes, interactive:**

```python
items = fetch_items()
if not items:
    print("No items found.")
else:
    for item in items:
        print(f"- {item['name']}: {item['status']}")
```

The agent gets human-readable text, can't tell success from empty result, and
has no exit-code signal to branch on.

**Good — structured, exit-coded, single-turn complete:**

```python
items = fetch_items(query=args.query, limit=args.limit, cursor=args.cursor)
if not items:
    sys.exit(3)  # agent knows: not found, not an error
payload = {
    "data": items,
    "meta": {"count": len(items), "total": total, "next_cursor": next_cursor},
}
print(json.dumps(payload))
sys.exit(0)
```

## Argument validation

**Bad — validation after work starts:**

```python
result = expensive_api_call()
if args.output_dir is None:
    print("Error: --output-dir required")
    sys.exit(1)
```

You've already paid the cost (and possibly emitted partial side effects)
before discovering the call was invalid.

**Good — validate before any work:**

```python
if args.output_dir is None:
    _emit_error("--output-dir is required", "MISSING_ARG",
                hint="Pass --output-dir /path/to/dir")
    sys.exit(1)
result = expensive_api_call()
```

## Stdout / stderr separation

**Bad — mixed stdout/stderr:**

```python
print("Starting fetch...", file=sys.stderr)
data = fetch()
print(f"Fetched {len(data)} records", file=sys.stderr)
print(json.dumps(data))  # stdout
print("Done", file=sys.stderr)  # OK but noisy under --quiet
```

The chatter on stderr ignores `--quiet`. The agent can't suppress it.

**Good — stderr gated on --quiet:**

```python
def _log(msg: str) -> None:
    if not args.quiet:
        print(msg, file=sys.stderr)

_log("Starting fetch...")
data = fetch()
_log(f"Fetched {len(data)} records")
print(json.dumps({"data": data, "meta": {"count": len(data)}}))
```

## Streaming with JSONL

**Bad — buffer everything into a single `{"data": [...]}` array:**

```python
records = []
for row in fetch_unbounded():            # could be millions
    records.append(transform(row))
print(json.dumps({"data": records, "meta": {"count": len(records)}}))
```

Two problems: memory grows linearly with the result set (OOM risk for
unbounded sources), and the agent can't start parsing until the entire
stream has been buffered.

**Good — emit one JSON object per line, flush as you go:**

```python
count = 0
for row in fetch_unbounded():
    print(json.dumps(transform(row)), flush=True)
    count += 1
print(json.dumps({"_summary": {"count": count}}), file=sys.stderr)
```

Rules for JSONL output:

- Document with `--format jsonl` in addition to `--format json`.
- One complete JSON object per line. No trailing comma, no array brackets.
- Skip the `{"data": ..., "meta": ...}` envelope — the agent reads to EOF.
- Pagination meta doesn't fit cleanly; emit a final summary on stderr (as
  above) or a distinguished final line on stdout, e.g.
  `{"_summary": {"count": 12345, "next_cursor": "..."}}`.
- `flush=True` matters — buffered stdout defeats the streaming benefit.
