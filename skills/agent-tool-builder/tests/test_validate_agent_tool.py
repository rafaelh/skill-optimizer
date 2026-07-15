"""Black-box subprocess tests for validate_agent_tool.py.

The interface under test is the CLI. Tests invoke the script via subprocess
against fixture content written into tmp_path and assert on returncode/stdout/stderr.
Do not patch internal functions.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_agent_tool.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Smoke: script self-validates without FAIL-level issues
# ---------------------------------------------------------------------------


def test_self_validates_clean() -> None:
    r = _run(str(SCRIPT), "--format", "json")
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    fails = [f for f in payload["findings"] if f["severity"] == "fail"]
    assert fails == [], f"validator should not fail itself: {fails}"


def test_help_exits_zero_with_examples() -> None:
    r = _run("--help")
    assert r.returncode == 0
    assert "examples:" in r.stdout.lower()


# ---------------------------------------------------------------------------
# Bad invocation
# ---------------------------------------------------------------------------


def test_missing_path_exits_2() -> None:
    r = _run("/definitely/does/not/exist.py")
    assert r.returncode == 2
    assert "does not exist" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Contract checks
# ---------------------------------------------------------------------------


def test_minimal_compliant_script_has_no_fails(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "ok_tool.py",
        '''\
        #!/usr/bin/env python3
        """A tool."""
        import argparse, json, sys

        EXIT_OK = 0
        EXIT_USER_ERROR = 1
        EXIT_SYSTEM_ERROR = 2
        EXIT_NOT_FOUND = 3

        def _emit_error(msg, code):
            print(json.dumps({"error": msg, "code": code}), file=sys.stderr)

        def main():
            p = argparse.ArgumentParser(epilog="examples:\\n  tool --format json")
            p.add_argument("--format", default="json")
            p.add_argument("--quiet", action="store_true")
            args = p.parse_args()
            if not args.format:
                _emit_error("missing", "BAD_ARG")
                return EXIT_USER_ERROR
            return EXIT_OK

        sys.exit(main())
        ''',
    )
    r = _run(str(fixture), "--format", "json")
    payload = json.loads(r.stdout)
    fails = [f for f in payload["findings"] if f["severity"] == "fail"]
    assert fails == [], f"unexpected fails: {fails}"


def test_missing_format_flag_fails(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "no_format.py",
        """\
        #!/usr/bin/env python3
        '''bad'''
        import argparse, sys
        p = argparse.ArgumentParser()
        p.add_argument('--id')
        sys.exit(0)
        """,
    )
    r = _run(str(fixture), "--format", "json")
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    codes = {f["code"] for f in payload["findings"]}
    assert "contract.missing-format-flag" in codes


def test_interactive_input_call_fails(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "interactive.py",
        """\
        #!/usr/bin/env python3
        '''bad'''
        import argparse, sys
        p = argparse.ArgumentParser()
        p.add_argument('--format', default='json')
        name = input("name? ")
        sys.exit(0)
        """,
    )
    r = _run(str(fixture), "--format", "json")
    assert r.returncode == 1, r.stdout
    codes = {f["code"] for f in json.loads(r.stdout)["findings"]}
    assert "contract.interactive-prompt" in codes


def test_missing_epilog_warns(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "no_epilog.py",
        """\
        #!/usr/bin/env python3
        '''tool'''
        import argparse, sys
        p = argparse.ArgumentParser()
        p.add_argument('--format', default='json')
        p.add_argument('--quiet', action='store_true')
        sys.exit(0)
        """,
    )
    r = _run(str(fixture), "--format", "json")
    codes = {f["code"] for f in json.loads(r.stdout)["findings"]}
    assert "contract.missing-epilog" in codes


def test_pep723_false_positive_on_stdlib(tmp_path: Path) -> None:
    """`cProfile`/`pstats` are stdlib — previously triggered a false positive."""
    fixture = _write(
        tmp_path,
        "stdlib_only.py",
        """\
        #!/usr/bin/env python3
        '''tool'''
        import argparse, sys, cProfile, pstats, base64, zlib, http
        p = argparse.ArgumentParser(epilog='examples: tool')
        p.add_argument('--format', default='json')
        p.add_argument('--quiet', action='store_true')
        sys.exit(0)
        """,
    )
    r = _run(str(fixture), "--format", "json")
    codes = {f["code"] for f in json.loads(r.stdout)["findings"]}
    assert "contract.missing-pep723" not in codes, "stdlib imports must not trigger PEP 723 warning"


def test_pep723_warns_on_real_nonstdlib(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "uses_httpx.py",
        """\
        #!/usr/bin/env python3
        '''tool'''
        import argparse, sys
        import httpx
        p = argparse.ArgumentParser(epilog='examples: tool')
        p.add_argument('--format', default='json')
        sys.exit(0)
        """,
    )
    r = _run(str(fixture), "--format", "json")
    codes = {f["code"] for f in json.loads(r.stdout)["findings"]}
    assert "contract.missing-pep723" in codes


def test_structured_stderr_required_when_nonzero_exit(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "bad_errors.py",
        """\
        #!/usr/bin/env python3
        '''tool'''
        import argparse, sys
        p = argparse.ArgumentParser(epilog='examples: tool')
        p.add_argument('--format', default='json')
        p.add_argument('--quiet', action='store_true')
        args = p.parse_args()
        if not args.format:
            print("missing format", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
        """,
    )
    r = _run(str(fixture), "--format", "json")
    codes = {f["code"] for f in json.loads(r.stdout)["findings"]}
    assert "contract.missing-structured-stderr" in codes


def test_text_output_is_default(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "ok.py",
        """\
        #!/usr/bin/env python3
        '''tool'''
        import argparse, sys
        p = argparse.ArgumentParser(epilog='examples: tool')
        p.add_argument('--format', default='json')
        p.add_argument('--quiet', action='store_true')
        sys.exit(0)
        """,
    )
    r = _run(str(fixture))
    assert r.returncode == 0
    assert not r.stdout.lstrip().startswith("{"), "default format should be text, not JSON"


def test_agent_tool_false_marker_skips(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "lib.py",
        """\
        \"\"\"Shared library module.\"\"\"
        # agent-tool: false

        def helper():
            return 42
        """,
    )
    r = _run(str(fixture), "--format", "json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["skipped"] is True
