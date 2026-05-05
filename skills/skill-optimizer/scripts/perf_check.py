#!/usr/bin/env python3
"""perf_check.py — Static and runtime Python performance analysis."""

import argparse
import ast
import cProfile
from dataclasses import asdict, dataclass
import io
import json
from pathlib import Path
import pstats
import sys
import textwrap
from typing import Any

_C = {
    "HIGH": "\033[91m",
    "MEDIUM": "\033[93m",
    "LOW": "\033[94m",
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
}
_SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@dataclass
class Issue:
    file: str
    line: int
    severity: str
    category: str
    message: str
    fix: str


class _SubscriptCounter(ast.NodeVisitor):
    """Counts constant-key subscript accesses within a subtree."""

    def __init__(self) -> None:
        self.counts: dict[tuple[str, object], list[int]] = {}

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and isinstance(node.slice, ast.Constant):
            key = (node.value.id, node.slice.value)
            if key not in self.counts:
                self.counts[key] = [0, node.lineno]
            self.counts[key][0] += 1
        self.generic_visit(node)


class PerfVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.issues: list[Issue] = []
        self._depth = 0

    def _flag(self, node_or_line: ast.AST | int, sev: str, cat: str, msg: str, fix: str) -> None:
        line = node_or_line if isinstance(node_or_line, int) else node_or_line.lineno  # type: ignore[attr-defined]
        self.issues.append(Issue(self.filename, line, sev, cat, msg, fix))

    def _enter(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def _check_loop_body(self, node: ast.AST) -> None:
        counter = _SubscriptCounter()
        for stmt in getattr(node, "body", []):
            counter.visit(stmt)
        for (obj, key), (count, lineno) in counter.counts.items():
            if count >= 3:
                self._flag(
                    lineno,
                    "LOW",
                    "repeated-subscript",
                    f"'{obj}[{key!r}]' accessed {count}× per loop iteration",
                    "Cache in a local variable at the top of the loop body",
                )

    def visit_For(self, node: ast.For) -> None:
        c = node.iter
        if (
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "range"
            and c.args
            and isinstance(c.args[-1], ast.Call)
            and isinstance(c.args[-1].func, ast.Name)
            and c.args[-1].func.id == "len"
        ):
            self._flag(
                node,
                "LOW",
                "range-len",
                "for i in range(len(seq)) — index-based iteration over a sequence",
                "Use 'for item in seq:' or 'for i, item in enumerate(seq):'",
            )
        self._check_loop_body(node)
        self._enter(node)

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
            control_flow_excs = {"KeyError", "IndexError", "StopIteration", "AttributeError"}
            for handler in node.handlers:
                if handler.type is None:
                    continue
                names = (
                    [n.id for n in handler.type.elts if isinstance(n, ast.Name)]
                    if isinstance(handler.type, ast.Tuple)
                    else ([handler.type.id] if isinstance(handler.type, ast.Name) else [])
                )
                matched = control_flow_excs & set(names)
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

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._depth > 0 and isinstance(node.op, ast.Add):
            rhs = node.value
            is_fstr = isinstance(rhs, ast.JoinedStr)
            is_str = isinstance(rhs, ast.Constant) and isinstance(rhs.value, str)
            if is_str or is_fstr:
                self._flag(
                    node,
                    "HIGH",
                    "string-concat-loop",
                    f"{'f-string' if is_fstr else 'String'} += inside a loop — O(n²) copies",
                    "Collect into a list, then ''.join(parts) after the loop",
                )
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
        if self._depth > 0:
            self._check_regex_recompile(node, f)
            self._check_open_in_loop(node, f)
            self._check_append_in_loop(node, f)
            self._check_globals_in_loop(node, f)
            self._check_sort_in_loop(node, f)
        self.generic_visit(node)

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

    def _check_append_in_loop(self, node: ast.Call, f: ast.expr) -> None:
        if isinstance(f, ast.Attribute) and f.attr == "append" and isinstance(f.value, ast.Name):
            self._flag(
                node,
                "LOW",
                "append-in-loop",
                ".append() in a loop — ~30% slower than a list comprehension",
                "Use a list comprehension: result = [expr for item in seq]",
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


def analyze(path: Path) -> list[Issue]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        print(f"  syntax error in {path}: {e}", file=sys.stderr)
        return []
    v = PerfVisitor(str(path))
    v.visit(tree)
    return v.issues


def profile_script(
    script: Path, script_args: list[str], top: int, as_data: bool = False
) -> list[dict[str, object]] | None:
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

    if as_data:
        return _profile_entries(pr, top)

    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(top)
    lines = buf.getvalue().splitlines()

    c = _C if sys.stdout.isatty() else dict.fromkeys(_C, "")
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
    return None


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
    c = _C if sys.stdout.isatty() else dict.fromkeys(_C, "")
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


def main() -> None:
    argv = sys.argv[1:]
    if "--" in argv:
        idx = argv.index("--")
        script_args, argv = argv[idx + 1 :], argv[:idx]
    else:
        script_args = []

    p = argparse.ArgumentParser(
        prog="perf_check.py",
        description="Detect Python performance anti-patterns (static + runtime).",
        epilog=textwrap.dedent("""\
            examples:
              perf_check.py app.py                         static analysis only
              perf_check.py --profile slow.py              profile + static analysis
              perf_check.py app.py --profile slow.py       both
              perf_check.py --profile script.py -- a b c   pass args to profiled script
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("files", nargs="*", metavar="FILE", help="Python files to analyze statically")
    p.add_argument("--profile", metavar="SCRIPT", help="script to profile at runtime")
    p.add_argument(
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="top N functions in profile output (default: 20)",
    )
    p.add_argument("--json", action="store_true", help="output results as JSON")
    args = p.parse_args(argv)

    if not args.files and not args.profile:
        p.print_help()
        sys.exit(0)

    if args.json:
        result: dict[str, Any] = {}

        if args.files:
            all_issues: list[Issue] = []
            for f in args.files:
                path = Path(f)
                if not path.exists():
                    print(f"not found: {f}", file=sys.stderr)
                    continue
                all_issues.extend(analyze(path))
            result["static"] = [asdict(i) for i in all_issues]

        if args.profile:
            script = Path(args.profile)
            if not script.exists():
                print(f"not found: {args.profile}", file=sys.stderr)
                sys.exit(1)
            if args.profile not in (args.files or []):
                result.setdefault("static", []).extend(asdict(i) for i in analyze(script))
            result["profile"] = profile_script(script, script_args, top=args.top, as_data=True)

        print(json.dumps(result, indent=2))
        return

    c = _C if sys.stdout.isatty() else dict.fromkeys(_C, "")

    if args.files:
        all_issues_text: list[Issue] = []
        for f in args.files:
            path = Path(f)
            if not path.exists():
                print(f"not found: {f}", file=sys.stderr)
                continue
            all_issues_text.extend(analyze(path))
        print(f"\n{c['BOLD']}Static Analysis{c['RESET']}")
        print("─" * 72)
        print_issues(all_issues_text)

    if args.profile:
        script = Path(args.profile)
        if not script.exists():
            print(f"not found: {args.profile}", file=sys.stderr)
            sys.exit(1)
        if args.profile not in (args.files or []):
            issues = analyze(script)
            if issues:
                print(f"\n{c['BOLD']}Static Analysis — {script}{c['RESET']}")
                print("─" * 72)
                print_issues(issues)
        profile_script(script, script_args, top=args.top)


if __name__ == "__main__":
    main()
