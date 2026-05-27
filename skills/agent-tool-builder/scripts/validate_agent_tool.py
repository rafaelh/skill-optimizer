#!/usr/bin/env python3
"""Validate that a Python script meets the agent tool interface contract.

Checks: argparse present, --format/--quiet flags, reachable exit codes 0/1/2,
no input() calls, no stdout/stderr mixing, PEP 723 block if non-stdlib imports
detected.

Usage:
    validate_agent_tool.py <script-path> [--json] [--exit-on-warn]

Exit codes:
    0   All checks pass
    1   One or more FAIL-level issues found
    2   Bad invocation (path missing, unreadable)
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
import textwrap

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD_INVOCATION = 2


@dataclass
class Finding:
    code: str
    severity: str  # "fail" | "warn" | "info"
    message: str
    line: int | None = None


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ast(src: str) -> ast.Module | None:
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_argparse(src: str) -> list[Finding]:
    if "ArgumentParser" not in src:
        return [
            Finding(
                "contract.missing-argparse",
                "fail",
                "No argparse.ArgumentParser found — agents cannot call --help or pass flags",
            )
        ]
    return []


def check_format_flag(src: str) -> list[Finding]:
    if "--format" not in src:
        return [
            Finding(
                "contract.missing-format-flag",
                "fail",
                "No --format flag — agents cannot request JSON output explicitly",
            )
        ]
    return []


def check_quiet_flag(src: str) -> list[Finding]:
    if "--quiet" not in src:
        return [
            Finding(
                "contract.missing-quiet-flag",
                "warn",
                "No --quiet flag — agents cannot suppress informational stderr",
            )
        ]
    return []


def check_exit_codes(src: str) -> list[Finding]:
    findings = []
    tree = _ast(src)
    if tree is None:
        return []

    # Build a map of name → literal int value for constants like EXIT_OK = 0
    const_map: dict[str, int] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            const_map[node.targets[0].id] = node.value.value

    exits_found: set[int] = set()
    for node in ast.walk(tree):
        # sys.exit(N) and exit(N) with literal or named constant
        if isinstance(node, ast.Call):
            func = node.func
            is_sys_exit = (
                isinstance(func, ast.Attribute)
                and func.attr == "exit"
                and isinstance(func.value, ast.Name)
                and func.value.id == "sys"
            )
            is_bare_exit = isinstance(func, ast.Name) and func.id == "exit"
            if (is_sys_exit or is_bare_exit) and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                    exits_found.add(arg.value)
                elif isinstance(arg, ast.Name) and arg.id in const_map:
                    exits_found.add(const_map[arg.id])
        # return N or return EXIT_* inside main() — idiomatic when sys.exit(main())
        elif isinstance(node, ast.Return) and node.value is not None:
            val = node.value
            if isinstance(val, ast.Constant) and isinstance(val.value, int):
                exits_found.add(val.value)
            elif isinstance(val, ast.Name) and val.id in const_map:
                exits_found.add(const_map[val.id])

    labels = {
        0: "success path missing",
        1: "user-error path missing",
        2: "system-error path missing",
    }
    findings = [
        Finding(f"contract.missing-exit-{n}", "warn", f"Exit code {n} not found — {labels[n]}")
        for n in (0, 1, 2)
        if n not in exits_found
    ]
    if 3 not in exits_found:
        findings.append(
            Finding(
                "contract.missing-exit-3",
                "warn",
                "Exit code 3 (not-found) not found — agents cannot distinguish "
                "'nothing matched' from 'error'",
            )
        )
    return findings


def check_no_interactive(src: str) -> list[Finding]:
    """Check for interactive prompts using AST — avoids false positives in docstrings/comments."""
    tree = _ast(src)
    if tree is None:
        return []

    def _call_name(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    return [
        Finding(
            "contract.interactive-prompt",
            "fail",
            f"Line {node.lineno}: interactive prompt detected — agents cannot respond",
            line=node.lineno,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) in {"input", "getpass"}
    ]


def check_stderr_for_errors(src: str) -> list[Finding]:
    # Heuristic: flag print(json.dumps(...error...)) on stdout — errors must go to stderr
    return [
        Finding(
            "contract.error-on-stdout",
            "warn",
            f"Line {i}: possible error JSON on stdout — errors must go to stderr",
            line=i,
        )
        for i, line in enumerate(src.splitlines(), 1)
        if (stripped := line.strip())
        and "json.dumps" in stripped
        and "error" in stripped.lower()
        and "file=sys.stderr" not in stripped
        and stripped.startswith("print(")
    ]


# sys.stdlib_module_names is exhaustive and ships with the interpreter (3.10+).
# We add __future__ explicitly because it's a pseudo-module not always listed.
_STDLIB_MODULES = set(sys.stdlib_module_names) | {"__future__"}

_PEP723_BLOCK_RE = re.compile(r"^# /// script\s*\n(.*?)^# ///\s*$", re.MULTILINE | re.DOTALL)


def check_pep723(src: str) -> list[Finding]:
    non_stdlib_modules: set[str] = set()
    for match in re.findall(r"^import (\w+)|^from (\w+)", src, re.MULTILINE):
        mod = match[0] or match[1]
        if mod and mod not in _STDLIB_MODULES:
            non_stdlib_modules.add(mod)
    stdlib_only = not non_stdlib_modules

    block_match = _PEP723_BLOCK_RE.search(src)
    findings: list[Finding] = []

    if not block_match:
        if not stdlib_only:
            findings.append(
                Finding(
                    "contract.missing-pep723",
                    "warn",
                    "Non-stdlib imports detected ("
                    + ", ".join(sorted(non_stdlib_modules))
                    + ") but no PEP 723 inline metadata block — add a '# /// script' "
                    "block so `uv run` resolves dependencies, or confirm this script "
                    "is part of a package whose pyproject.toml owns deps",
                )
            )
        return findings

    block = block_match.group(1)
    if not re.search(r"^#\s*requires-python\s*=", block, re.MULTILINE):
        findings.append(
            Finding(
                "contract.pep723-missing-requires-python",
                "warn",
                "PEP 723 block present but no 'requires-python' key — declare the "
                "minimum Python version your code actually uses (e.g. '>=3.11')",
            )
        )
    if not stdlib_only and not re.search(r"^#\s*dependencies\s*=", block, re.MULTILINE):
        findings.append(
            Finding(
                "contract.pep723-missing-dependencies",
                "warn",
                "PEP 723 block present but no 'dependencies' key, despite non-stdlib "
                "imports (" + ", ".join(sorted(non_stdlib_modules)) + ") — add them",
            )
        )
    return findings


def check_shebang(src: str) -> list[Finding]:
    if not src.startswith("#!/usr/bin/env python3"):
        return [
            Finding(
                "contract.missing-shebang",
                "info",
                "No '#!/usr/bin/env python3' shebang — script won't be directly executable",
            )
        ]
    return []


def check_docstring(src: str) -> list[Finding]:
    tree = _ast(src)
    if tree is None:
        return []
    if not (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
    ):
        return [
            Finding(
                "contract.missing-docstring",
                "info",
                "No module docstring — add one describing inputs, outputs, exit codes",
            )
        ]
    return []


def check_epilog(src: str) -> list[Finding]:
    """`--help` is the agent's reference; a flag list without examples leaves it guessing."""
    tree = _ast(src)
    if tree is None:
        return []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "ArgumentParser")
                or (isinstance(node.func, ast.Name) and node.func.id == "ArgumentParser")
            )
            and any(kw.arg == "epilog" for kw in node.keywords)
        ):
            return []
    return [
        Finding(
            "contract.missing-epilog",
            "warn",
            "ArgumentParser has no epilog= with usage examples — "
            "`--help` is the agent's primary reference; show 2-3 realistic invocations",
        )
    ]


def check_structured_stderr_errors(src: str) -> list[Finding]:
    """Tools that exit with user/system errors should emit structured JSON to stderr."""
    tree = _ast(src)
    if tree is None:
        return []

    has_nonzero_exit = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            is_exit = (isinstance(func, ast.Attribute) and func.attr == "exit") or (
                isinstance(func, ast.Name) and func.id == "exit"
            )
            if is_exit and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, int) and arg.value != 0:
                    has_nonzero_exit = True
                    break
                if isinstance(arg, ast.Name) and arg.id.startswith("EXIT_") and arg.id != "EXIT_OK":
                    has_nonzero_exit = True
                    break
        elif isinstance(node, ast.Return) and node.value is not None:
            val = node.value
            if isinstance(val, ast.Constant) and isinstance(val.value, int) and val.value != 0:
                has_nonzero_exit = True
                break
            if isinstance(val, ast.Name) and val.id.startswith("EXIT_") and val.id != "EXIT_OK":
                has_nonzero_exit = True
                break

    if not has_nonzero_exit:
        return []

    for line in src.splitlines():
        stripped = line.strip()
        if (
            "json.dumps" in stripped
            and "file=sys.stderr" in stripped
            and ("error" in stripped.lower() or "_emit_error" in stripped.lower())
        ):
            return []
    if "_emit_error" in src or "emit_error" in src:
        return []

    return [
        Finding(
            "contract.missing-structured-stderr",
            "warn",
            "Non-zero exit paths exist but no structured error JSON found on stderr — "
            'errors should be `print(json.dumps({"error": ..., "code": ..., "hint": ...}), '
            "file=sys.stderr)` so the agent gets a consistent shape",
        )
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    check_argparse,
    check_format_flag,
    check_quiet_flag,
    check_exit_codes,
    check_no_interactive,
    check_stderr_for_errors,
    check_pep723,
    check_shebang,
    check_docstring,
    check_epilog,
    check_structured_stderr_errors,
]


def validate(path: Path) -> list[Finding]:
    src = _src(path)
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(src))
    return findings


def _emit_text(path: Path, findings: list[Finding]) -> None:
    for f in findings:
        loc = f" (line {f.line})" if f.line else ""
        print(f"{f.severity.upper()}: [{f.code}]{loc} {f.message}")
    if not findings:
        print(f"OK: {path} passes all checks")


def _emit_json(path: Path, findings: list[Finding]) -> None:
    print(
        json.dumps(
            {
                "target": str(path),
                "findings": [asdict(f) for f in findings],
                "summary": {
                    "total": len(findings),
                    "fail": sum(1 for f in findings if f.severity == "fail"),
                    "warn": sum(1 for f in findings if f.severity == "warn"),
                },
            },
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0] if __doc__ else "",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              validate_agent_tool.py tool.py                  # text report to stdout
              validate_agent_tool.py tool.py --format json    # machine-readable JSON
              validate_agent_tool.py tool.py --exit-on-warn   # CI mode: warnings fail
        """),
    )
    parser.add_argument("script", help="Path to the Python script to validate")
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text). The validator's audience is humans by default; "
        "use --format json from other tools.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Alias for --format json (kept for back-compat).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational stderr (no effect on findings output).",
    )
    parser.add_argument(
        "--exit-on-warn", action="store_true", help="Exit 1 on warnings as well as failures"
    )
    args = parser.parse_args(argv)

    path = Path(args.script).expanduser().resolve()
    if not path.exists():
        print(f"error: path does not exist: {path}", file=sys.stderr)
        return EXIT_BAD_INVOCATION

    findings = validate(path)

    use_json = args.as_json or args.format == "json"
    if use_json:
        _emit_json(path, findings)
    else:
        _emit_text(path, findings)

    has_fail = any(f.severity == "fail" for f in findings)
    has_warn = any(f.severity == "warn" for f in findings)
    if has_fail:
        return EXIT_FAIL
    if args.exit_on_warn and has_warn:
        return EXIT_FAIL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
