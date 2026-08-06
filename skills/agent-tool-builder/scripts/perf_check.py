#!/usr/bin/env python3
"""perf_check.py — Static and runtime Python performance analysis for agent tools.

Walks Python files (or directories, recursively) and flags common per-call
performance anti-patterns: quadratic string/bytes concatenation, lists used as
queues, regex recompilation, linear-scan membership tests, pandas row
iteration, and others. Also reports import-time cost, which an agent tool pays
on every invocation. Optionally profiles a script at runtime via cProfile.

Usage:
    perf_check.py <path> [path ...]                # static analysis
    perf_check.py --profile <script.py> -- <args>  # runtime profile
    perf_check.py <path> --format json             # machine-readable

Exit codes:
    0   Analysis ran; no HIGH findings (or no findings at all)
    1   User/invocation error — bad args, paths not found
    2   System/infrastructure error — unexpected exception while parsing
    3   Analysis ran; no Python files matched the inputs
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import cProfile
from dataclasses import asdict, dataclass
import io
import json
from pathlib import Path
import pstats
import sys
import textwrap
from typing import Any

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_SYSTEM_ERROR = 2
EXIT_NOT_FOUND = 3

_C = {
    "HIGH": "\033[91m",
    "MEDIUM": "\033[93m",
    "LOW": "\033[94m",
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
}
_SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# The AST nodes a finding can be reported against — those carrying a source line.
_Located = ast.stmt | ast.expr | ast.excepthandler

# Accumulator kinds tracked for the quadratic-concatenation checks.
_STR = "str"
_BYTES = "bytes"
_CONCAT_CATEGORY = {_STR: "string-concat-loop", _BYTES: "bytes-concat-loop"}
_CONCAT_FIX = {
    _STR: "Collect into a list, then ''.join(parts) after the loop (or write into an io.StringIO)",
    _BYTES: "Extend a bytearray in place (buf = bytearray(); buf += chunk), or b''.join(parts)",
}


def _buffer_kind(node: ast.expr) -> str | None:
    """Classify an assignment RHS as an immutable str/bytes accumulator seed.

    Returns None for anything else — notably bytearray() and io.StringIO(), the
    idioms the Python FAQ recommends, which must never be flagged.
    """
    if isinstance(node, ast.JoinedStr):
        return _STR
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return _STR
        if isinstance(node.value, bytes):
            return _BYTES
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "str":
            return _STR
        if node.func.id == "bytes":
            return _BYTES
    return None


def _int_index(node: ast.expr) -> int | None:
    """Extract a literal int subscript; negatives parse as UnaryOp(USub, Constant)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and node.value is not True:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _int_index(node.operand)
        return None if inner is None else -inner
    return None


def _is_head_slice(node: ast.Subscript) -> bool:
    """True for a plain leading slice, x[:n] — no lower bound, no step."""
    s = node.slice
    return isinstance(s, ast.Slice) and s.lower is None and s.step is None and s.upper is not None


def _range_len_offset(expr: ast.expr) -> int | None:
    """For range(len(x)) return 0, for range(len(x) - k) return k, else None."""
    offset = 0
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Sub):
        k = _int_index(expr.right)
        if k is None:
            return None
        offset, expr = k, expr.left
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "len":
        return offset
    return None


def _self_concat_operand(value: ast.expr, name: str) -> ast.expr | None:
    """For 'name + x' / 'x + name', return the other operand; else None."""
    if not isinstance(value, ast.BinOp) or not isinstance(value.op, ast.Add):
        return None
    if isinstance(value.left, ast.Name) and value.left.id == name:
        return value.right
    if isinstance(value.right, ast.Name) and value.right.id == name:
        return value.left
    return None


# Exceptions cheap enough to pre-check for, so catching them per iteration is waste.
_CONTROL_FLOW_EXCS = frozenset({"KeyError", "IndexError", "StopIteration", "AttributeError"})


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    """Exception names a handler catches; a bare 'except:' or an expression gives none."""
    if isinstance(handler.type, ast.Tuple):
        return {n.id for n in handler.type.elts if isinstance(n, ast.Name)}
    if isinstance(handler.type, ast.Name):
        return {handler.type.id}
    return set()


def _called_name(func: ast.expr) -> str:
    """The bare name of a call target — 'x' for both x(...) and obj.x(...)."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _colors() -> dict[str, str]:
    """ANSI codes when stdout is a terminal, empty strings when it is piped."""
    return _C if sys.stdout.isatty() else dict.fromkeys(_C, "")


def _emit_error(error: str, code: str, hint: str = "") -> None:
    """Structured error to stderr; never to stdout."""
    payload: dict[str, str] = {"error": error, "code": code}
    if hint:
        payload["hint"] = hint
    print(json.dumps(payload), file=sys.stderr)


def _log(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


@dataclass
class Issue:
    file: str
    line: int
    severity: str
    category: str
    message: str
    fix: str


class _SubscriptCounter(ast.NodeVisitor):
    """Collects constant-key subscript accesses in a subtree, grouped by (name, key)."""

    def __init__(self) -> None:
        self.accesses: defaultdict[tuple[str, object], list[ast.Subscript]] = defaultdict(list)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and isinstance(node.slice, ast.Constant):
            self.accesses[node.value.id, node.slice.value].append(node)
        self.generic_visit(node)


class PerfVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.issues: list[Issue] = []
        self._depth = 0
        self._buffers: dict[str, str] = {}
        self._flattened: set[ast.AST] = set()

    def _flag(self, node: _Located, sev: str, cat: str, msg: str, fix: str) -> None:
        self.issues.append(Issue(self.filename, node.lineno, sev, cat, msg, fix))

    def _enter(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def _visit_scope(self, node: ast.AST) -> None:
        """Visit a function body with a fresh accumulator map, then restore."""
        outer = self._buffers
        self._buffers = {}
        self.generic_visit(node)
        self._buffers = outer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def _check_loop_body(self, node: ast.For | ast.While) -> None:
        counter = _SubscriptCounter()
        for stmt in node.body:
            counter.visit(stmt)
        for (obj, key), accesses in counter.accesses.items():
            if len(accesses) >= 3:
                self._flag(
                    accesses[0],
                    "LOW",
                    "repeated-subscript",
                    f"'{obj}[{key!r}]' accessed {len(accesses)}x per loop iteration",
                    "Cache in a local variable at the top of the loop body",
                )

    def visit_For(self, node: ast.For) -> None:
        c = node.iter
        if (
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "range"
            and c.args
        ):
            offset = _range_len_offset(c.args[-1])
            if offset == 1:
                self._flag(
                    node,
                    "MEDIUM",
                    "range-len",
                    "for i in range(len(seq) - 1) — a sliding window over adjacent pairs",
                    "Use itertools.pairwise(seq): 'for a, b in pairwise(seq)' — ~60% faster",
                )
            elif offset is not None:
                self._flag(
                    node,
                    "LOW",
                    "range-len",
                    "for i in range(len(seq)) — index-based iteration over a sequence",
                    "Use 'for item in seq:' or 'for i, item in enumerate(seq):'",
                )
        if self._check_manual_flatten(node):
            self._flattened.add(node.body[0])  # don't also report its inner append
        self._check_lone_append(node)
        for name in ast.walk(node.target):
            if isinstance(name, ast.Name):
                self._buffers.pop(name.id, None)  # the loop variable rebinds it
        self._check_loop_body(node)
        self._enter(node)

    def _lone_append(self, node: ast.For) -> ast.Call | None:
        """Return the append() call if the loop body is exactly one .append(...) on a name."""
        if len(node.body) != 1 or node.orelse or not isinstance(node.body[0], ast.Expr):
            return None
        call = node.body[0].value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "append"
            and isinstance(call.func.value, ast.Name)
            and len(call.args) == 1
            and not call.keywords
        ):
            return call
        return None

    def _check_lone_append(self, node: ast.For) -> None:
        """A loop whose whole body is one append converts mechanically to a comprehension."""
        if node in self._flattened or self._lone_append(node) is None:
            return
        self._flag(
            node,
            "LOW",
            "append-in-loop",
            "loop body is a single .append() — this is a list comprehension written long-hand",
            "Readability only, not speed: result = [expr for item in seq]",
        )

    def _check_manual_flatten(self, node: ast.For) -> bool:
        """Flag 'for sub in nested: for x in sub: out.append(x)' — a hand-rolled flatten."""
        if len(node.body) != 1 or not isinstance(node.body[0], ast.For):
            return False
        inner = node.body[0]
        call = self._lone_append(inner)
        if call is None:
            return False
        # The inner loop must walk the outer variable and append only its own variable.
        if not (
            isinstance(node.target, ast.Name)
            and isinstance(inner.iter, ast.Name)
            and inner.iter.id == node.target.id
            and isinstance(inner.target, ast.Name)
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == inner.target.id
        ):
            return False
        self._flag(
            node,
            "LOW",
            "manual-flatten",
            "nested loop whose only work is appending inner items — a hand-rolled flatten",
            "Use itertools.chain.from_iterable(nested) — ~40% faster and one line",
        )
        return True

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._check_sort_then_slice(node)
        self._check_materialize_then_slice(node)
        self.generic_visit(node)

    def _check_sort_then_slice(self, node: ast.Subscript) -> None:
        call = node.value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "sorted"
        ):
            return
        reverse = any(
            kw.arg == "reverse"
            and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
            for kw in call.keywords
        )
        if _is_head_slice(node):
            picker = "nlargest" if reverse else "nsmallest"
            self._flag(
                node,
                "MEDIUM",
                "sort-then-slice",
                "sorted(...)[:n] — sorts the whole sequence just to keep n items",
                f"Use heapq.{picker}(n, seq, key=...) — ~90% faster on large inputs",
            )
            return
        idx = _int_index(node.slice)
        if idx in {0, -1}:
            want = "min" if (idx == 0) != reverse else "max"
            self._flag(
                node,
                "MEDIUM",
                "sort-then-slice",
                f"sorted(...)[{idx}] — a full O(n log n) sort to take one element",
                f"Use {want}(seq, key=...) — a single O(n) pass",
            )

    def _check_materialize_then_slice(self, node: ast.Subscript) -> None:
        call = node.value
        if not (_is_head_slice(node) and isinstance(call, ast.Call)):
            return
        if isinstance(call.func, ast.Name) and call.func.id in {"list", "tuple"}:
            what = f"{call.func.id}(...)[:n]"
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "readlines":
            what = ".readlines()[:n]"
        else:
            return
        self._flag(
            node,
            "MEDIUM",
            "materialize-then-slice",
            f"{what} — builds the whole sequence in memory, then throws all but n away",
            "Use itertools.islice(iterable, n) to stop consuming after n items",
        )

    def visit_While(self, node: ast.While) -> None:
        self._check_loop_body(node)
        self._enter(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._enter(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._enter(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._enter(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._enter(node)

    def visit_Try(self, node: ast.Try) -> None:
        if self._depth > 0:
            for handler in node.handlers:
                matched = _CONTROL_FLOW_EXCS & _handler_names(handler)
                if matched:
                    exc_names = ", ".join(sorted(matched))
                    self._flag(
                        handler,
                        "MEDIUM",
                        "except-as-control-flow",
                        f"Catching {exc_names} inside a loop — exception overhead per miss",
                        "Use 'if key in dict' / 'if i < len(seq)' checks before access",
                    )
        self.generic_visit(node)

    def _flag_concat(self, node: _Located, kind: str, what: str) -> None:
        self._flag(
            node,
            "HIGH",
            _CONCAT_CATEGORY[kind],
            f"{what} inside a loop — {kind} is immutable, so this is O(n²)",
            _CONCAT_FIX[kind],
        )

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._depth > 0 and isinstance(node.op, ast.Add):
            target = node.target
            tracked = self._buffers.get(target.id) if isinstance(target, ast.Name) else None
            kind = _buffer_kind(node.value) or tracked
            if kind:
                if isinstance(node.value, ast.JoinedStr):
                    what = "f-string +="
                else:
                    what = "String +=" if kind == _STR else "bytes +="
                self._flag_concat(node, kind, what)
        self.generic_visit(node)

    def _check_manual_counter(self, node: ast.Assign) -> None:
        """Flag 'counts[k] = counts.get(k, 0) + 1' — a hand-rolled Counter."""
        target = node.targets[0] if len(node.targets) == 1 else None
        if not (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)):
            return
        value = node.value
        if not (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)):
            return
        for side in (value.left, value.right):
            if (
                isinstance(side, ast.Call)
                and isinstance(side.func, ast.Attribute)
                and side.func.attr == "get"
                and isinstance(side.func.value, ast.Name)
                and side.func.value.id == target.value.id
                and len(side.args) == 2
            ):
                name = target.value.id
                self._flag(
                    node,
                    "LOW",
                    "dict-init-idiom",
                    f"'{name}[k] = {name}.get(k, ...) + ...' — a hand-rolled tally",
                    "Use collections.Counter(items), or defaultdict(int) with counts[k] += 1",
                )
                return

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._depth > 0:
            self._check_manual_counter(node)
        seed = _buffer_kind(node.value)
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            other = _self_concat_operand(node.value, target.id)
            if other is not None:
                kind = self._buffers.get(target.id) or _buffer_kind(other)
                if kind and self._depth > 0:
                    self._flag_concat(node, kind, f"'{target.id} = {target.id} + ...'")
            elif seed:
                self._buffers[target.id] = seed
            else:
                self._buffers.pop(target.id, None)  # rebound to something else
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for i, op in enumerate(node.ops):
            if not isinstance(op, ast.In):
                continue
            comp = node.comparators[i]
            if isinstance(comp, ast.List | ast.Tuple) and len(comp.elts) > 1:
                kind = "list" if isinstance(comp, ast.List) else "tuple"
                self._flag(
                    node,
                    "HIGH" if self._depth > 0 else "MEDIUM",
                    "membership-seq",
                    f"'in {kind}' literal — O(n) scan each time",
                    "Use a set literal {a, b, ...} for O(1) membership tests",
                )
            elif (
                isinstance(comp, ast.Call)
                and isinstance(comp.func, ast.Attribute)
                and comp.func.attr == "keys"
                and not comp.args
            ):
                self._flag(
                    node,
                    "LOW",
                    "dict-keys-membership",
                    "'in dict.keys()' — .keys() is redundant for membership tests",
                    "Use 'in dict' directly",
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        self._check_logging_fstring(node, f)
        self._check_pandas_iter(node, f)
        self._check_cmp_to_key(node, f)
        if self._depth > 0:
            self._check_regex_recompile(node, f)
            self._check_open_in_loop(node, f)
            self._check_globals_in_loop(node, f)
            self._check_sort_in_loop(node, f)
            self._check_setdefault_mutable(node, f)
            self._check_list_as_queue(node, f)
            self._check_deepcopy_in_loop(node, f)
            self._check_subprocess_in_loop(node, f)
        self.generic_visit(node)

    def _check_list_as_queue(self, node: ast.Call, f: ast.expr) -> None:
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
            return
        if f.attr == "pop" and len(node.args) == 1 and _int_index(node.args[0]) == 0:
            op, repl = f"{f.value.id}.pop(0)", "popleft()"
        elif f.attr == "insert" and node.args and _int_index(node.args[0]) == 0:
            op, repl = f"{f.value.id}.insert(0, ...)", "appendleft(...)"
        else:
            return
        self._flag(
            node,
            "HIGH",
            "list-as-queue",
            f"{op} in a loop — every element shifts up, making the loop O(n²)",
            f"Use collections.deque and {repl} — O(1) at both ends",
        )

    def visit_Delete(self, node: ast.Delete) -> None:
        if self._depth > 0:
            for t in node.targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and _int_index(t.slice) == 0
                ):
                    self._flag(
                        node,
                        "HIGH",
                        "list-as-queue",
                        f"del {t.value.id}[0] in a loop — every element shifts up, making it O(n²)",
                        "Use collections.deque and popleft() — O(1) at both ends",
                    )
        self.generic_visit(node)

    def _check_deepcopy_in_loop(self, node: ast.Call, f: ast.expr) -> None:
        if _called_name(f) == "deepcopy":
            self._flag(
                node,
                "MEDIUM",
                "deepcopy-in-loop",
                "deepcopy() per iteration — it rewalks the whole object graph every call",
                "Copy the parts you need explicitly, or deepcopy once before the loop",
            )

    def _check_subprocess_in_loop(self, node: ast.Call, f: ast.expr) -> None:
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
            return
        spawns = {"run", "Popen", "call", "check_call", "check_output"}
        if f.value.id == "subprocess" and f.attr in spawns:
            what = f"subprocess.{f.attr}()"
        elif f.value.id == "os" and f.attr in {"system", "popen"}:
            what = f"os.{f.attr}()"
        else:
            return
        self._flag(
            node,
            "MEDIUM",
            "subprocess-in-loop",
            f"{what} in a loop — each spawn costs ~20ms of fork/exec before any work happens",
            "Batch the inputs into a single invocation, or use a native library equivalent",
        )

    def visit_Import(self, node: ast.Import) -> None:
        self._check_import_in_loop(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_import_in_loop(node)
        self.generic_visit(node)

    def _check_import_in_loop(self, node: ast.Import | ast.ImportFrom) -> None:
        if self._depth > 0:
            self._flag(
                node,
                "MEDIUM",
                "import-in-loop",
                "import inside a loop — repeats the sys.modules lookup every iteration",
                "Hoist to module scope, or to the top of the function for a deliberate lazy import",
            )

    def _check_cmp_to_key(self, node: ast.Call, f: ast.expr) -> None:
        if _called_name(f) == "cmp_to_key":
            self._flag(
                node,
                "MEDIUM",
                "cmp-to-key",
                "cmp_to_key() — the wrapper is invoked once per comparison, O(n log n) times",
                "Pass a key function directly: key=operator.itemgetter(0) or key=lambda r: r.field",
            )

    def _check_setdefault_mutable(self, node: ast.Call, f: ast.expr) -> None:
        if not (isinstance(f, ast.Attribute) and f.attr == "setdefault" and len(node.args) == 2):
            return
        default = node.args[1]
        is_container = isinstance(default, ast.List | ast.Dict | ast.Set) or (
            isinstance(default, ast.Call)
            and isinstance(default.func, ast.Name)
            and default.func.id in {"list", "dict", "set"}
        )
        if is_container:
            self._flag(
                node,
                "LOW",
                "dict-init-idiom",
                "setdefault() with a container default — the default is built on every call",
                "Use collections.defaultdict(list) and index directly: groups[key].append(v)",
            )

    def _check_regex_recompile(self, node: ast.Call, f: ast.expr) -> None:
        re_ops = {"match", "search", "findall", "finditer", "sub", "subn", "split", "fullmatch"}
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "re"
            and f.attr in re_ops
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            self._flag(
                node,
                "MEDIUM",
                "regex-recompile",
                f"re.{f.attr}(string_pattern) in loop — pattern recompiled each call",
                "Call re.compile(r'...') once before the loop",
            )

    def _check_open_in_loop(self, node: ast.Call, f: ast.expr) -> None:
        if isinstance(f, ast.Name) and f.id == "open":
            self._flag(
                node,
                "MEDIUM",
                "open-in-loop",
                "open() called inside a loop",
                "Open the file once before the loop",
            )

    def _check_globals_in_loop(self, node: ast.Call, f: ast.expr) -> None:
        if isinstance(f, ast.Name) and f.id in {"globals", "locals"}:
            self._flag(
                node,
                "LOW",
                "globals-in-loop",
                f"{f.id}() inside a loop — rebuilds a dict of all variables each call",
                f"Cache before the loop: ns = {f.id}()",
            )

    def _check_sort_in_loop(self, node: ast.Call, f: ast.expr) -> None:
        if isinstance(f, ast.Attribute) and f.attr in {"sort", "reverse"}:
            self._flag(
                node,
                "MEDIUM",
                "sort-in-loop",
                f".{f.attr}() called inside a loop — O(n log n) work every iteration",
                "Sort once after the loop, or use bisect.insort() to maintain order incrementally",
            )

    def _check_logging_fstring(self, node: ast.Call, f: ast.expr) -> None:
        log_methods = {"debug", "info", "warning", "error", "critical", "exception"}
        if (
            isinstance(f, ast.Attribute)
            and f.attr in log_methods
            and node.args
            and isinstance(node.args[0], ast.JoinedStr)
        ):
            self._flag(
                node,
                "LOW",
                "logging-fstring",
                f"f-string passed to .{f.attr}() — formatted even when log level suppresses output",
                f"Use lazy args: logger.{f.attr}('%s', val) or guard with isEnabledFor()",
            )

    def _check_pandas_iter(self, node: ast.Call, f: ast.expr) -> None:
        if isinstance(f, ast.Attribute) and f.attr in {"iterrows", "itertuples"}:
            self._flag(
                node,
                "HIGH" if self._depth > 0 else "MEDIUM",
                "pandas-iter",
                f".{f.attr}() — row-by-row Python iteration over a DataFrame is slow",
                "Use vectorized operations, df.apply(), or convert to numpy arrays",
            )


# Approximate cold-import cost in milliseconds, against a ~20ms interpreter baseline.
# Only modules worth deferring are listed; cheap ones (pathlib, dataclasses, json)
# are omitted deliberately — deferring those is noise, not a saving.
_HEAVY_IMPORTS = {
    "logging": 30,
    "urllib.request": 28,
    "http.client": 20,
    "xmlrpc": 30,
    "yaml": 40,
    "PIL": 60,
    "requests": 80,
    "cryptography": 90,
    "numpy": 150,
    "sqlalchemy": 150,
    "scipy": 250,
    "matplotlib": 300,
    "boto3": 300,
    "django": 300,
    "pandas": 400,
    "sklearn": 400,
    "torch": 800,
    "transformers": 900,
}


def _import_cost(module: str) -> int | None:
    """Cost for a module or the heaviest package it lives under."""
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        cost = _HEAVY_IMPORTS.get(".".join(parts[:i]))
        if cost is not None:
            return cost
    return None


def _module_bindings(stmt: ast.stmt) -> list[tuple[str, str]]:
    """Names bound by a top-level import, as (bound_name, module)."""
    if isinstance(stmt, ast.Import):
        return [(a.asname or a.name.split(".")[0], a.name) for a in stmt.names]
    if isinstance(stmt, ast.ImportFrom) and stmt.module and not stmt.level:
        return [(a.asname or a.name, stmt.module) for a in stmt.names]
    return []


def check_heavy_imports(tree: ast.Module, filename: str) -> list[Issue]:
    """Flag an expensive module-scope import whose only consumer is one function.

    An agent tool pays every module-scope import on every invocation, so an
    import used by a single (possibly rarely reached) function is pure overhead
    on all the other calls. Deferring is only safe when nothing at module level
    — including class bodies and annotations, which run at import — needs it.
    """
    funcs: dict[str, set[str]] = {}
    module_names: set[str] = set()
    for stmt in tree.body:
        names = {n.id for n in ast.walk(stmt) if isinstance(n, ast.Name)}
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            funcs[stmt.name] = names
        else:
            module_names |= names

    issues: list[Issue] = []
    for stmt in tree.body:
        for bound, module in _module_bindings(stmt):
            cost = _import_cost(module)
            if cost is None or bound in module_names:
                continue
            users = [fn for fn, used in funcs.items() if bound in used]
            if len(users) == 1:
                issues.append(
                    Issue(
                        filename,
                        stmt.lineno,
                        "MEDIUM",
                        "heavy-import",
                        f"'{module}' costs ~{cost}ms to import, but only {users[0]}() uses it",
                        f"Import it inside {users[0]}() so calls that never reach it don't pay",
                    )
                )
    return issues


def analyze(path: Path) -> list[Issue]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        raise SyntaxError(f"syntax error in {path}: {e}") from e
    v = PerfVisitor(str(path))
    v.visit(tree)
    return v.issues + check_heavy_imports(tree, str(path))


def _analyze_all(files: list[Path]) -> list[Issue]:
    issues: list[Issue] = []
    for path in files:
        issues.extend(analyze(path))
    return issues


def _profile_target(raw: str) -> Path | None:
    """Resolve --profile's script path, reporting the error if it doesn't exist."""
    script = Path(raw)
    if script.exists():
        return script
    _emit_error(f"Profile target not found: {raw}", "PROFILE_TARGET_NOT_FOUND")
    return None


def _resolve_inputs(paths: list[str]) -> tuple[list[Path], list[str]]:
    """Expand paths into a flat list of .py files. Directories are walked recursively.

    Returns (files, missing) — missing is a list of paths that didn't exist.
    """
    files: list[Path] = []
    missing: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.exists():
            missing.append(raw)
            continue
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        else:
            files.append(p)  # any explicit file is analyzed; a SyntaxError bubbles up
    return files, missing


def run_profile(script: Path, script_args: list[str]) -> cProfile.Profile:
    """Execute a script under cProfile, as if it had been run directly."""
    pr = cProfile.Profile()
    old_argv = sys.argv[:]
    sys.argv = [str(script), *script_args]
    script_dir = str(script.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        code = compile(script.read_text(encoding="utf-8"), str(script), "exec")
        pr.enable()
        exec(code, {"__name__": "__main__", "__file__": str(script)})
    except SystemExit:
        pass
    except Exception as e:
        print(f"  script raised {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        pr.disable()
        sys.argv = old_argv
    return pr


def print_profile(pr: cProfile.Profile, script: Path, top: int) -> None:
    """Render pstats output, highlighting frames from the profiled script itself."""
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(top)
    lines = buf.getvalue().splitlines()

    c = _colors()
    print(f"\n{c['BOLD']}Runtime Profile — top {top} by cumulative time{c['RESET']}")
    print("─" * 72)
    in_table = False
    for line in lines:
        if "ncalls" in line and "tottime" in line:
            in_table = True
            print(f"  {c['BOLD']}{line.strip()}{c['RESET']}")
        elif in_table and line.strip():
            hi = script.name in line or "__main__" in line
            print(f"  {c['HIGH'] if hi else ''}{line}{c['RESET'] if hi else ''}")


def _profile_entries(pr: cProfile.Profile, top: int) -> list[dict[str, object]]:
    stats = pstats.Stats(pr, stream=io.StringIO())
    stats.sort_stats("cumulative")
    raw: Any = getattr(stats, "stats", {})
    entries = [
        {
            "function": funcname,
            "file": filename,
            "line": lineno,
            "calls": nc,
            "primitive_calls": cc,
            "tottime": round(tt, 6),
            "cumtime": round(ct, 6),
        }
        for (filename, lineno, funcname), (cc, nc, tt, ct, _) in raw.items()
    ]
    entries.sort(key=lambda e: e["cumtime"], reverse=True)
    return entries[:top]


def print_issues(issues: list[Issue]) -> None:
    c = _colors()
    if not issues:
        print("  No issues found.\n")
        return
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for issue in sorted(issues, key=lambda i: (_SEV_ORDER[i.severity], i.line)):
        counts[issue.severity] += 1
        col = c[issue.severity]
        print(
            f"  {col}{c['BOLD']}{issue.severity:6}{c['RESET']}  {issue.file}:{issue.line}"
            f"  [{issue.category}]"
        )
        print(f"         {issue.message}")
        print(f"         {c['BOLD']}Fix:{c['RESET']} {issue.fix}\n")
    print(f"  Summary: {counts['HIGH']} high  {counts['MEDIUM']} medium  {counts['LOW']} low\n")


def _report_json(files: list[Path], profile: str | None, script_args: list[str], top: int) -> int:
    result: dict[str, Any] = {}
    if files:
        issues = _analyze_all(files)
        result["static"] = [asdict(i) for i in issues]
        result["meta"] = {
            "files_analyzed": len(files),
            "issues_total": len(issues),
            "issues_high": sum(1 for i in issues if i.severity == "HIGH"),
        }
    if profile:
        script = _profile_target(profile)
        if script is None:
            return EXIT_USER_ERROR
        if script not in files:
            result.setdefault("static", []).extend(asdict(i) for i in analyze(script))
        result["profile"] = _profile_entries(run_profile(script, script_args), top)
    print(json.dumps(result, indent=2))
    return EXIT_OK


def _report_text(files: list[Path], profile: str | None, script_args: list[str], top: int) -> int:
    c = _colors()
    if files:
        issues = _analyze_all(files)  # analyze first: a SyntaxError must precede any output
        print(f"\n{c['BOLD']}Static Analysis ({len(files)} file(s)){c['RESET']}")
        print("─" * 72)
        print_issues(issues)
    if profile:
        script = _profile_target(profile)
        if script is None:
            return EXIT_USER_ERROR
        if script not in files:
            issues = analyze(script)
            if issues:
                print(f"\n{c['BOLD']}Static Analysis — {script}{c['RESET']}")
                print("─" * 72)
                print_issues(issues)
        print_profile(run_profile(script, script_args), script, top)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw:
        idx = raw.index("--")
        script_args, raw = raw[idx + 1 :], raw[:idx]
    else:
        script_args = []

    p = argparse.ArgumentParser(
        prog="perf_check.py",
        description="Detect Python performance anti-patterns (static + runtime).",
        epilog=textwrap.dedent("""\
            examples:
              perf_check.py app.py                            # static analysis of one file
              perf_check.py src/tools/                        # walk a directory recursively
              perf_check.py app.py --format json              # machine-readable output
              perf_check.py app.py --quiet                    # suppress informational stderr
              perf_check.py --profile slow.py                 # profile at runtime
              perf_check.py --profile script.py -- a b c      # pass args to profiled script
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "files",
        nargs="*",
        metavar="PATH",
        help="Python files or directories to analyze statically (directories walked recursively)",
    )
    p.add_argument("--profile", metavar="SCRIPT", help="script to profile at runtime")
    p.add_argument(
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="top N functions in profile output (default: 20)",
    )
    p.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text). Use --format json for agent-callable output.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Alias for --format json (kept for back-compat).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational stderr (file counts, etc.). Errors still emit.",
    )
    args = p.parse_args(raw)

    if not args.files and not args.profile:
        p.print_help()
        return EXIT_USER_ERROR

    use_json = args.json or args.format == "json"

    try:
        files, missing = _resolve_inputs(args.files) if args.files else ([], [])
    except Exception as exc:
        _emit_error(
            f"Unexpected error resolving inputs: {exc}",
            "RESOLVE_FAILED",
            hint="Check that paths exist and are readable",
        )
        return EXIT_SYSTEM_ERROR

    if missing:
        _emit_error(
            f"Path(s) not found: {', '.join(missing)}",
            "PATH_NOT_FOUND",
            hint="Pass an existing file or directory; directories are walked recursively",
        )
        return EXIT_USER_ERROR

    if args.files and not files:
        _log("No Python files matched the given paths.", quiet=args.quiet)
        return EXIT_NOT_FOUND

    _log(f"Analyzing {len(files)} file(s)...", quiet=args.quiet)

    report = _report_json if use_json else _report_text
    try:
        return report(files, args.profile, script_args, args.top)
    except SyntaxError as exc:
        _emit_error(
            str(exc),
            "PARSE_FAILED",
            hint="Fix the syntax error in the listed file before re-running",
        )
        return EXIT_SYSTEM_ERROR
    except Exception as exc:
        _emit_error(
            f"{type(exc).__name__}: {exc}",
            "UNEXPECTED_ERROR",
            hint="Re-run with the file directly to isolate the failing input",
        )
        return EXIT_SYSTEM_ERROR


if __name__ == "__main__":
    sys.exit(main())
