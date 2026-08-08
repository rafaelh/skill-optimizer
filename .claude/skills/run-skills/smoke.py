#!/usr/bin/env python3
"""Smoke-drive every agent tool this marketplace ships.

The "app" here is the bundled scripts under skills/*/scripts/. This launches
each one for real and asserts the interface contract holds, then dogfoods the
repo's own validators against its own skills and checks the two manifests
agree with what is on disk.

Run from anywhere; paths resolve relative to the repo root.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
TS_DIR = REPO / "skills" / "skill-optimizer-ts"
TSX = TS_DIR / "node_modules" / ".bin" / "tsx"

# Not agent tools: shared library modules and test helpers the validators skip.
NOT_TOOLS = {"skill_lib.py", "skill_lib.ts", "test-helpers.ts"}

TIMEOUT = 120


Check = dict[str, object]


@dataclass
class Result:
    checks: list[Check] = field(default_factory=list[Check])

    def add(self, name: str, ok: bool, detail: str = "", **extra: object) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail, **extra})

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c["ok"]]


def emit_error(code: str, message: str, hint: str) -> None:
    """Structured error on stderr, per the interface contract this repo enforces."""
    print(json.dumps({"error": message, "code": code, "hint": hint}), file=sys.stderr)


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, cwd=cwd or REPO, capture_output=True, text=True, timeout=TIMEOUT, check=False
    )
    return proc.returncode, proc.stdout, proc.stderr


def python_tools() -> list[Path]:
    return sorted(
        p
        for p in REPO.glob("skills/*/scripts/*.py")
        if p.name not in NOT_TOOLS and "tests" not in p.parts
    )


def ts_tools() -> list[Path]:
    return sorted(
        p
        for p in TS_DIR.glob("scripts/*.ts")
        if p.name not in NOT_TOOLS and not p.name.endswith(".test.ts")
    )


def skill_dirs() -> list[Path]:
    return sorted(p for p in (REPO / "skills").iterdir() if (p / "SKILL.md").is_file())


def check_toolchain(res: Result) -> bool:
    """Fail fast: the rest of the sweep is noise without these."""
    ok = True
    for name, cmd in (("uv", ["uv", "--version"]), ("node", ["node", "--version"])):
        if shutil.which(cmd[0]) is None:
            res.add(f"toolchain/{name}", False, f"{name} not on PATH")
            ok = False
            continue
        _, out, _ = run(cmd)
        res.add(f"toolchain/{name}", True, out.strip())
    if not TSX.is_file():
        res.add("toolchain/tsx", False, f"missing {TSX} — run `npm ci` in {TS_DIR}")
        ok = False
    else:
        res.add("toolchain/tsx", True, str(TSX.relative_to(REPO)))
    return ok


def check_help(res: Result, tools: list[Path], runner: list[str], label: str) -> None:
    """Every agent tool must answer --help with exit 0 and a usage line."""
    for tool in tools:
        rel = tool.relative_to(REPO).as_posix()
        code, out, err = run([*runner, str(tool), "--help"])
        text = out + err
        ok = code == 0 and ("usage" in text.lower() or "Usage" in text)
        res.add(f"{label}/help/{rel}", ok, f"exit={code}", exit_code=code)


def check_json_stdout(res: Result, tools: list[Path], runner: list[str], label: str) -> None:
    """JSON mode must put parseable JSON on stdout with nothing else mixed in."""
    target = (REPO / "skills" / "architecture").as_posix()
    # Each tool takes a different positional; these are the ones that accept a skill dir.
    accepts_skill_dir = {"validate_skill", "analyze_skill", "audit_security", "recommend_scripts"}
    for tool in tools:
        if tool.stem not in accepts_skill_dir:
            continue
        rel = tool.relative_to(REPO).as_posix()
        code, out, _ = run([*runner, str(tool), target, "--json"])
        try:
            payload = json.loads(out)
            ok = isinstance(payload, dict)
            detail = f"exit={code} keys={sorted(payload)[:4]}" if ok else "not a JSON object"
        except json.JSONDecodeError as exc:
            ok, detail = False, f"stdout is not JSON: {exc}"
        res.add(f"{label}/json/{rel}", ok, detail, exit_code=code)


def _summary_of(out: str) -> dict[str, object] | None:
    """Extract the `summary` object from a tool's --json output, or None if unparseable."""
    try:
        return json.loads(out)["summary"]
    except json.JSONDecodeError, KeyError:
        return None


def check_self_audit(res: Result, runner: list[str], validator: Path, label: str) -> None:
    """The marketplace must pass its own spec validator.

    Held to fail=0 *and* warn=0: every shipped skill is clean today, so any warn
    is drift. The validator's own `ok` flag is laxer — it tolerates warnings.
    """
    for skill in skill_dirs():
        code, out, _ = run([*runner, str(validator), str(skill), "--json", "--quiet"])
        summary = _summary_of(out)
        if summary is None:
            res.add(f"{label}/self-audit/{skill.name}", False, f"unparseable output (exit={code})")
            continue
        res.add(
            f"{label}/self-audit/{skill.name}",
            summary.get("fail") == 0 and summary.get("warn") == 0,
            f"fail={summary.get('fail')} warn={summary.get('warn')}",
        )


def check_parity(res: Result) -> None:
    """CLAUDE.md requires the Python and TypeScript twins to behave identically."""
    py = REPO / "skills" / "skill-optimizer" / "scripts" / "validate_skill.py"
    ts = TS_DIR / "scripts" / "validate_skill.ts"
    for skill in skill_dirs():
        _, a, _ = run([sys.executable, str(py), str(skill), "--json", "--quiet"])
        _, b, _ = run([str(TSX), str(ts), str(skill), "--json"])
        sa, sb = _summary_of(a), _summary_of(b)
        if sa is None or sb is None:
            res.add(f"parity/{skill.name}", False, "one side produced unparseable JSON")
            continue
        res.add(f"parity/{skill.name}", sa == sb, f"py={sa} ts={sb}")


def check_manifests(res: Result) -> None:
    """Adding or renaming a skill means editing two manifests; catch the half-done edit."""
    marketplace = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    listed = {p["source"].removeprefix("./skills/"): p for p in marketplace["plugins"]}
    on_disk = {p.name for p in skill_dirs()}

    stray_manifest = sorted(set(listed) - on_disk)
    stray_dir = sorted(on_disk - set(listed))
    res.add(
        "manifests/marketplace-covers-disk",
        not stray_manifest and not stray_dir,
        f"only-in-marketplace={stray_manifest} only-on-disk={stray_dir}",
    )
    for skill in skill_dirs():
        plugin = skill / ".claude-plugin" / "plugin.json"
        if not plugin.is_file():
            res.add(
                f"manifests/plugin-json/{skill.name}", False, "missing .claude-plugin/plugin.json"
            )
            continue
        name = json.loads(plugin.read_text()).get("name")
        res.add(f"manifests/plugin-json/{skill.name}", name == skill.name, f"name={name!r}")


def check_tests(res: Result) -> None:
    code, out, _ = run([sys.executable, "-m", "pytest", "-q"])
    summary = out.strip().splitlines()[-1] if out.strip() else f"exit={code}"
    res.add("tests/pytest", code == 0, summary)
    code, out, err = run(["npm", "test", "--silent"], cwd=TS_DIR)
    tail = [ln for ln in (out + err).splitlines() if "Tests" in ln or "failed" in ln]
    res.add("tests/vitest", code == 0, tail[-1].strip() if tail else f"exit={code}")


def check_language(
    res: Result,
    label: str,
    tools: list[Path],
    runner: list[str],
    validator: Path,
    lang: str,
    glob: str,
) -> int | None:
    """Run the --help/--json/self-audit group for one language. Returns a fatal
    exit code if no tools were found, else None to continue the sweep."""
    if not tools:
        emit_error(
            "smoke.tools.none-found",
            f"no {lang} agent tools found under {glob}",
            "check that the repo is intact and you are pointing at the right checkout",
        )
        return 3
    check_help(res, tools, runner, label)
    check_json_stdout(res, tools, runner, label)
    check_self_audit(res, runner, validator, label)
    return None


def emit(res: Result, fmt: str, elapsed: float) -> None:
    """Render the report. Must honour `fmt` on every exit path — an agent
    running --json parses stdout, so leaking the text table there breaks it."""
    if fmt == "json":
        summary = {
            "total": len(res.checks),
            "failed": len(res.failed),
            "ok": not res.failed,
            "elapsed_s": round(elapsed, 2),
        }
        print(json.dumps({"checks": res.checks, "summary": summary}, indent=2))
        return
    for c in res.checks:
        mark = "PASS" if c["ok"] else "FAIL"
        print(f"  [{mark}] {c['check']}  {c['detail']}")
    total, bad = len(res.checks), len(res.failed)
    print(f"\n{total - bad}/{total} checks passed in {elapsed:.1f}s")
    if bad:
        print(f"\n{bad} failing:")
        for c in res.failed:
            print(f"  - {c['check']}: {c['detail']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-drive every agent tool in this marketplace.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  smoke.py                       # full sweep, no test suites\n"
            "  smoke.py --tests               # also run pytest + vitest\n"
            "  smoke.py --only python --json  # Python tools only, machine-readable\n"
        ),
    )
    parser.add_argument(
        "--only",
        choices=["python", "ts", "parity", "manifests"],
        help="Restrict the sweep to one group (default: all groups).",
    )
    parser.add_argument(
        "--tests", action="store_true", help="Also run the pytest and vitest suites."
    )
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    parser.add_argument("--json", action="store_true", help="Alias for --format json.")
    parser.add_argument("--quiet", action="store_true", help="Suppress informational stderr.")
    args = parser.parse_args()

    fmt = "json" if args.json else args.format
    res = Result()
    started = time.monotonic()

    if not args.quiet:
        print(f"repo: {REPO}", file=sys.stderr)

    if not check_toolchain(res):
        # Everything downstream shells out to these; reporting 40 cascading
        # failures would bury the one that matters.
        emit(res, fmt, time.monotonic() - started)
        emit_error(
            "smoke.toolchain.missing",
            "required toolchain is incomplete",
            f"run `uv sync` at {REPO} and `npm ci` in {TS_DIR}",
        )
        return 2

    groups = {args.only} if args.only else {"python", "ts", "parity", "manifests"}
    py_runner = [sys.executable]
    ts_runner = [str(TSX)]
    py_validator = REPO / "skills" / "skill-optimizer" / "scripts" / "validate_skill.py"

    if "python" in groups:
        code = check_language(
            res, "python", python_tools(), py_runner, py_validator, "Python", "skills/*/scripts/"
        )
        if code is not None:
            return code
    if "ts" in groups:
        code = check_language(
            res,
            "ts",
            ts_tools(),
            ts_runner,
            TS_DIR / "scripts" / "validate_skill.ts",
            "TypeScript",
            "skills/skill-optimizer-ts/scripts/",
        )
        if code is not None:
            return code
    if "parity" in groups:
        check_parity(res)
    if "manifests" in groups:
        check_manifests(res)
    if args.tests:
        check_tests(res)

    emit(res, fmt, time.monotonic() - started)

    if res.failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
