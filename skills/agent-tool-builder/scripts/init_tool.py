#!/usr/bin/env python3
"""init_tool.py — Scaffold a new agent-callable Python tool from the bundled template.

Writes the agent_tool.py.template to <target-path> with `{{script_name}}`
substituted, makes it executable, and emits a structured JSON summary so the
calling agent knows what landed where. Optionally also writes a paired
subprocess test under <target-dir>/tests/.

Usage:
    init_tool.py <target-path> [--name NAME] [--with-tests] [--force]

Exit codes:
    0   Scaffold written (or no-op when target exists and --force omitted, see below)
    1   User/invocation error — bad path, target exists without --force, malformed name
    2   System/infrastructure error — template missing, write failed
    3   Not used (no "not found" case)
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import stat
import sys
import textwrap

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_SYSTEM_ERROR = 2

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = SKILL_DIR / "assets" / "templates" / "agent_tool.py.template"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class Result:
    tool_path: str
    test_path: str | None
    created: list[str]
    skipped: list[str]


def _emit_error(error: str, code: str, hint: str = "") -> None:
    payload: dict[str, str] = {"error": error, "code": code}
    if hint:
        payload["hint"] = hint
    print(json.dumps(payload), file=sys.stderr)


def _derive_name(target: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    stem = target.stem
    return stem


def _render(template: str, script_name: str) -> str:
    return template.replace("{{script_name}}", script_name)


def _test_body(script_name: str, tool_path: Path) -> str:
    return textwrap.dedent(f'''\
        """Black-box subprocess tests for {script_name}.

        These tests invoke the script via subprocess and assert on returncode/stdout/stderr.
        Do not patch internal functions — the interface under test is the CLI.
        """

        from __future__ import annotations

        import json
        import subprocess
        import sys
        from pathlib import Path

        TOOL = Path(__file__).resolve().parent.parent / "{tool_path.name}"


        def _run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(TOOL), *args],
                capture_output=True,
                text=True,
            )


        def test_help_works():
            r = _run("--help")
            assert r.returncode == 0
            assert "usage" in r.stdout.lower()


        def test_validation_error_exits_1():
            # NotImplementedError path returns exit 2; missing/invalid flag combo should be 1.
            r = _run("--limit", "0")
            assert r.returncode == 1, r.stderr
            payload = json.loads(r.stderr.strip().splitlines()[-1])
            assert payload["code"] == "INVALID_LIMIT"
    ''')


def scaffold(target: Path, name: str, with_tests: bool, force: bool) -> Result:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"template missing: {TEMPLATE_PATH}")

    created: list[str] = []
    skipped: list[str] = []

    if target.exists() and not force:
        skipped.append(str(target))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = _render(TEMPLATE_PATH.read_text(encoding="utf-8"), name)
        target.write_text(body, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        created.append(str(target))

    test_path: Path | None = None
    if with_tests:
        test_dir = target.parent / "tests"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_path = test_dir / f"test_{name}.py"
        if test_path.exists() and not force:
            skipped.append(str(test_path))
        else:
            test_path.write_text(_test_body(name, target), encoding="utf-8")
            created.append(str(test_path))

    return Result(
        tool_path=str(target),
        test_path=str(test_path) if test_path else None,
        created=created,
        skipped=skipped,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="init_tool.py",
        description=__doc__.split("\n", 1)[0] if __doc__ else "",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              init_tool.py src/tools/fetch_user.py
              init_tool.py src/tools/fetch_user.py --name fetch_user --with-tests
              init_tool.py /tmp/scratch.py --force        # overwrite existing
        """),
    )
    parser.add_argument(
        "target",
        help="Path where the new tool script will be written (e.g. src/tools/fetch_user.py)",
    )
    parser.add_argument(
        "--name",
        help="Logical script name used to substitute {{script_name}} in the template "
        "(default: target file stem)",
    )
    parser.add_argument(
        "--with-tests",
        action="store_true",
        help="Also write a paired subprocess test under <target-dir>/tests/test_<name>.py",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files (default: skip, no error)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Output format (default: json — designed to be agent-callable)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational stderr (no effect on result payload).",
    )
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser()
    name = _derive_name(target, args.name)

    if not _NAME_RE.match(name):
        _emit_error(
            f"Invalid script name: {name!r}",
            "INVALID_NAME",
            hint="Use lowercase letters, digits, and underscores; must start with a letter",
        )
        return EXIT_USER_ERROR

    if target.is_dir():
        _emit_error(
            f"Target is a directory: {target}",
            "TARGET_IS_DIR",
            hint="Pass a file path ending in .py",
        )
        return EXIT_USER_ERROR

    try:
        result = scaffold(target, name, args.with_tests, args.force)
    except FileNotFoundError as exc:
        _emit_error(str(exc), "TEMPLATE_MISSING", hint=f"Expected at: {TEMPLATE_PATH}")
        return EXIT_SYSTEM_ERROR
    except OSError as exc:
        _emit_error(
            f"Write failed: {exc}",
            "WRITE_FAILED",
            hint="Check directory permissions and disk space",
        )
        return EXIT_SYSTEM_ERROR

    payload = {
        "data": asdict(result),
        "meta": {
            "created_count": len(result.created),
            "skipped_count": len(result.skipped),
            "name": name,
        },
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        if result.created:
            print("created:")
            for path in result.created:
                print(f"  {path}")
        if result.skipped:
            print("skipped (already exists; pass --force to overwrite):")
            for path in result.skipped:
                print(f"  {path}")

    if not args.quiet and not result.created and result.skipped:
        print(
            "info: no files written — all targets already exist. Pass --force to overwrite.",
            file=sys.stderr,
        )

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
