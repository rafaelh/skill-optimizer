"""Black-box subprocess tests for perf_check.py."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "perf_check.py"


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


def test_help_exits_zero() -> None:
    r = _run("--help")
    assert r.returncode == 0
    assert "examples:" in r.stdout.lower()


def test_no_args_exits_1() -> None:
    r = _run()
    assert r.returncode == 1


def test_missing_path_emits_structured_error() -> None:
    r = _run("/nowhere/missing.py", "--format", "json")
    assert r.returncode == 1
    err = json.loads(r.stderr.splitlines()[-1])
    assert err["code"] == "PATH_NOT_FOUND"


def test_clean_file_returns_no_issues(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "clean.py",
        """\
        def main():
            return 0
        """,
    )
    r = _run(str(fixture), "--format", "json", "--quiet")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["meta"]["issues_high"] == 0
    assert payload["meta"]["files_analyzed"] == 1


def test_string_concat_loop_flagged(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "concat.py",
        """\
        def build():
            out = ""
            for i in range(100):
                out += "x"
            return out
        """,
    )
    r = _run(str(fixture), "--format", "json", "--quiet")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    cats = {issue["category"] for issue in payload["static"]}
    assert "string-concat-loop" in cats


def test_directory_walks_recursively(tmp_path: Path) -> None:
    """The previous version crashed with IsADirectoryError on directory input."""
    sub = tmp_path / "nested"
    sub.mkdir()
    _write(
        sub,
        "a.py",
        """\
        def f():
            out = ""
            for i in range(3):
                out += "y"
        """,
    )
    _write(tmp_path, "b.py", "def g(): pass\n")
    r = _run(str(tmp_path), "--format", "json", "--quiet")
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["meta"]["files_analyzed"] == 2
    cats = {issue["category"] for issue in payload["static"]}
    assert "string-concat-loop" in cats


def test_empty_directory_exits_3(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(str(empty), "--format", "json", "--quiet")
    assert r.returncode == 3


def test_membership_seq_flagged(tmp_path: Path) -> None:
    fixture = _write(
        tmp_path,
        "member.py",
        """\
        def f(x):
            for _ in range(10):
                if x in ["a", "b", "c"]:
                    return True
            return False
        """,
    )
    r = _run(str(fixture), "--format", "json", "--quiet")
    payload = json.loads(r.stdout)
    cats = {issue["category"] for issue in payload["static"]}
    assert "membership-seq" in cats
