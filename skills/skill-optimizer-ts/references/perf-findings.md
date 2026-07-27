# Performance findings — interpretation guide

Reference for `perf_check.ts` output. Load this when interpreting the linter's findings against a bundled script, when explaining _why_ a flagged pattern matters in this specific context, or when deciding whether a finding is worth acting on.

## Severity policy for agent tools

- **HIGH** — blocker. Fix before merging or shipping.
- **MEDIUM** — fix unless there's a clear reason not to. Document the reason in a comment if you skip.
- **LOW** — advisory. Worth a glance, not worth a fight.

Agent tools are called many times per session — per-call overhead compounds in ways it wouldn't for a one-off script.

## Patterns most relevant to agent tools

| Severity | Pattern              | Why it matters for agent tools                                                                                                                                                               |
| -------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HIGH     | `string-concat-loop` | Building output via `result += "..."` in a loop is O(n²). Collect into an array and `array.join("")`, or build the object and `JSON.stringify` it once.                                      |
| HIGH/MED | `membership-seq`     | `["a","b","c"].includes(x)` (or `.indexOf(x)`) does a linear scan each call. Filtering result records this way scales poorly. Use a `Set` and `.has()`.                                      |
| MED      | `regex-recompile`    | `new RegExp("...")` constructed inside a loop rebuilds the pattern object every iteration. Hoist it above the loop (or use a regex literal at module scope, which the engine compiles once). |
| MED      | `read-in-loop`       | Reading a config file or credential file once per record via `readFileSync`, instead of once at startup.                                                                                     |
| MED      | `sort-in-loop`       | Sorting the result set inside the per-record loop instead of once after collection.                                                                                                          |

## Static analysis only

`perf_check.ts` is a static AST scan (via the TypeScript compiler API) — there is no runtime profiler bundled with this skill, unlike the Python `agent-tool-builder` skill's `perf_check.py --profile`. If static analysis comes back clean but a script still feels slow, profile it with Node's built-in tools directly:

```bash
node --prof scripts/<name>.ts
node --prof-process isolate-*.log > profile.txt
```

or run under the Node inspector (`node --inspect`) and use Chrome DevTools' profiler. This is a manual step outside this skill's bundled tooling.
