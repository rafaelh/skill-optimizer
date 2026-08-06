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
| HIGH | `string-concat-loop` | Building JSON output via `result += ...` (or `result = result + ...`) in a loop is O(n²) — `str` is immutable, so every step copies the whole buffer. Use a list + `''.join()`, an `io.StringIO`, or `json.dumps()` on the assembled structure. |
| HIGH | `bytes-concat-loop` | Same quadratic blowup for `bytes` — common when assembling a response body or reading chunks. Extend a `bytearray` in place instead, or `b''.join(parts)`. |
| HIGH | `list-as-queue` | `lst.pop(0)`, `lst.insert(0, x)` or `del lst[0]` in a loop shifts every remaining element, so draining a list this way is O(n²). A `collections.deque` with `popleft()`/`appendleft()` measured **97–99%** faster. |
| MED | `heavy-import` | The one finding that is about *startup*, not loop cost — and for agent tools startup is paid on every single invocation. Against a ~20ms bare interpreter, `logging` adds ~30ms and `urllib.request` ~28ms; `pandas` or `torch` add hundreds. Flagged only when exactly one function uses the import and nothing at module scope does, which is when moving it inside that function is both safe and free. |
| MED | `subprocess-in-loop` | ~20ms of fork/exec per spawn before the child does any work — a 100-item loop burns two seconds on process setup alone. |
| MED | `deepcopy-in-loop` | `deepcopy` rewalks the entire object graph on every call; copying just the fields you need measured ~95% faster. |
| HIGH/MED | `membership-seq` | `if x in [a, b, c]` does a linear scan each call. Filtering result records this way scales poorly. Use a set literal. |
| MED | `regex-recompile` | A validation regex called from `main()` recompiles on every invocation. Define it at module scope with `re.compile(...)`. |
| MED | `open-in-loop` | Reading a config file or credential file once per record, instead of once at startup. |
| MED | `sort-in-loop` | Sorting the result set inside the per-record loop instead of once after collection. |
| MED | `import-in-loop` | An `import` in a loop body repeats the `sys.modules` lookup every iteration (~40% of the loop's cost in a tight loop). A lazy import at the *top of a function* is a different thing — that one deliberately trades per-call cost for faster startup, and is not flagged. |
| MED | `cmp-to-key` | `functools.cmp_to_key` wraps every comparison in a Python call, so it runs O(n log n) times — roughly 15× slower than a plain `key=` function. |
| MED | `materialize-then-slice` | `list(rows)[:10]` or `fh.readlines()[:5]` consumes the entire source before discarding all but `n`. On a 200k-item generator that's 1.3s versus effectively zero for `itertools.islice`. Agent tools that page or preview results hit this constantly. |
| MED | `sort-then-slice` | `sorted(rows)[:n]` is O(n log n) to answer a top-n question: `heapq.nsmallest(n, rows)` is ~90% faster on large shuffled inputs. `sorted(rows)[0]` / `[-1]` should be `min()` / `max()`. |
| MED | `range-len` (offset 1) | `for i in range(len(seq) - 1)` indexing `seq[i]` and `seq[i+1]` is a sliding window — `itertools.pairwise(seq)` is ~60% faster and reads better. Plain `range(len(seq))` stays LOW; it's a style nit, not a cost. |
| LOW | `manual-flatten` | A nested loop whose only work is `out.append(x)` — `itertools.chain.from_iterable(nested)` is ~40% faster. |
| LOW | `dict-init-idiom` | `d.setdefault(k, [])` builds a throwaway list on every call (~2× the cost of `defaultdict(list)`); `d[k] = d.get(k, 0) + 1` is a hand-rolled `Counter`, which is ~3× faster built-in. |

## Findings that are advisory only

`append-in-loop` is a **readability** signal, not a speed one. On CPython 3.11+
the specializing interpreter closed the gap — a list comprehension measures no
faster than the equivalent `.append()` loop, and sometimes marginally slower.
Convert when it makes the code clearer; don't convert for throughput. It fires
only when the loop's *entire* body is one `.append()`, i.e. when the rewrite is
mechanical; a loop that also filters, transforms into locals, or does other work
alongside the append is left alone, as is a `while` loop, which has no iterable
to comprehend. The same goes for the old advice to cache method lookups
(`append = out.append`) — that now costs more than it saves, so `perf_check.py`
deliberately does not flag it.

Two `itertools` members are also deliberately never suggested. `groupby`
requires its input to be pre-sorted, so recommending it blind would introduce
correctness bugs; a `defaultdict` is usually both safer and faster. `tee`
buffers everything consumed between its slowest and fastest iterator, so it
frequently trades a time win for a much worse memory profile.

## Profiling for hotspots

When static analysis comes back clean but the script still feels slow:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/perf_check.py" --profile <script.py> -- <script-args>
```

Runs cProfile and prints the top functions by cumulative time. The `--`
separates `perf_check`'s own flags from arguments to be passed to the script
under profile.
