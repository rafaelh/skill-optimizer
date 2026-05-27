"""Black-box subprocess tests for init_tool.py."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "init_tool.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_works() -> None:
    r = _run("--help")
    assert r.returncode == 0
    assert "examples:" in r.stdout.lower()


def test_scaffold_creates_tool(tmp_path: Path) -> None:
    target = tmp_path / "fetch_user.py"
    r = _run(str(target))
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["meta"]["created_count"] == 1
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "{{script_name}}" not in body
    assert "fetch_user" in body


def test_scaffold_with_tests(tmp_path: Path) -> None:
    target = tmp_path / "fetch_user.py"
    r = _run(str(target), "--with-tests")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    test_path = Path(payload["data"]["test_path"])
    assert test_path.exists()
    assert "subprocess" in test_path.read_text(encoding="utf-8")


def test_existing_file_skipped_without_force(tmp_path: Path) -> None:
    target = tmp_path / "tool.py"
    target.write_text("existing\n", encoding="utf-8")
    r = _run(str(target))
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["meta"]["created_count"] == 0
    assert payload["meta"]["skipped_count"] == 1
    assert target.read_text(encoding="utf-8") == "existing\n"


def test_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "tool.py"
    target.write_text("existing\n", encoding="utf-8")
    r = _run(str(target), "--force")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["meta"]["created_count"] == 1
    assert target.read_text(encoding="utf-8") != "existing\n"


def test_invalid_name_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "Bad-Name.py"
    r = _run(str(target))
    assert r.returncode == 1
    err = json.loads(r.stderr.splitlines()[-1])
    assert err["code"] == "INVALID_NAME"


def test_target_is_directory_exits_1(tmp_path: Path) -> None:
    r = _run(str(tmp_path))
    assert r.returncode == 1
    err = json.loads(r.stderr.splitlines()[-1])
    assert err["code"] == "TARGET_IS_DIR"
