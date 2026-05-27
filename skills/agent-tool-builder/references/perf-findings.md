# Performance findings — interpretation guide

Reference for `perf_check.py` output. Load this when interpreting the linter's
findings against an agent tool, when explaining *why* a flagged pattern
matters in this specific context, or when deciding whether a finding is worth
acting on.

## Severity policy for agent tools

- **HIGH** — blocker. Fix before merging or shipping.
- **MEDIUM** — fix unless there's a clear reason not to. Document the reason
  in a comment if you skip.
- **LOW** — advisory. Worth a glance, not worth a fight.

Agent tools are called many times per session — per-call overhead compounds
in ways it wouldn't for a one-off script.

## Patterns most relevant to agent tools

| Severity | Pattern | Why it matters for agent tools |
|----------|---------|-------------------------------|
| HIGH | `string-concat-loop` | Building JSON output via `result += "..."` in a loop is O(n²). Use a list + `''.join()` or `json.dumps()` on the assembled structure. |
| HIGH/MED | `membership-seq` | `if x in [a, b, c]` does a linear scan each call. Filtering result records this way scales poorly. Use a set literal. |
| MED | `regex-recompile` | A validation regex called from `main()` recompiles on every invocation. Define it at module scope with `re.compile(...)`. |
| MED | `open-in-loop` | Reading a config file or credential file once per record, instead of once at startup. |
| MED | `sort-in-loop` | Sorting the result set inside the per-record loop instead of once after collection. |

## Profiling for hotspots

When static analysis comes back clean but the script still feels slow:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/perf_check.py" --profile <script.py> -- <script-args>
```

Runs cProfile and prints the top functions by cumulative time. The `--`
separates `perf_check`'s own flags from arguments to be passed to the script
under profile.
