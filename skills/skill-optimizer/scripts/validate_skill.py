#!/usr/bin/env python3
"""Validate an agent skill against the Agent Skills specification.

Usage:
    validate_skill.py <skill-directory> [--json] [--exit-on-warn]

Default output is one issue per line, prefixed with FAIL: or WARN: and a
machine-readable code in brackets — backwards compatible with prior behavior.
With --json, emits a structured object on stdout.

Exit codes:
    0   no FAIL-level issues (and no WARN if --exit-on-warn)
    1   one or more FAIL-level issues
    2   bad invocation (path missing, not a directory, etc.)
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

from skill_lib import parse_frontmatter, sanitize_for_echo

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MD_LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")
_SCRIPT_REF_RE = re.compile(r"`(scripts/[^`\s]+\.(?:py|sh|js|ts))`")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MIN_DESCRIPTION_WARN = 60
MAX_COMPATIBILITY = 500
ALLOWED_KEYS = frozenset(
    {
        # agentskills.io base spec
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
        # Platform extensions (Claude Code, Copilot, Codex, VS Code)
        "when_to_use",
        "argument-hint",
        "arguments",
        "disable-model-invocation",
        "user-invocable",
        "model",
        "effort",
        "context",
        "agent",
        "hooks",
        "paths",
        "shell",
    }
)


@dataclass
class Issue:
    severity: str  # "fail" or "warn"
    code: str
    message: str
    field: str | None = None

    def to_line(self) -> str:
        prefix = "FAIL" if self.severity == "fail" else "WARN"
        return f"{prefix}: [{self.code}] {self.message}"


def validate(skill_dir: Path) -> list[Issue]:
    issues: list[Issue] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [
            Issue(
                "fail",
                "validate.skill-md.missing",
                f"SKILL.md not found at {sanitize_for_echo(skill_md)}",
            )
        ]

    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if fm is None:
        return [
            Issue(
                "fail",
                "validate.frontmatter.missing",
                "SKILL.md is missing YAML frontmatter (must start with '---')",
            )
        ]

    _check_name(fm, skill_dir, issues)
    _check_description(fm, issues)
    _check_compatibility(fm, issues)
    _check_unknown_keys(fm, issues)
    _check_references(skill_dir, body, issues)
    return issues


def _check_name(fm: dict[str, Any], skill_dir: Path, issues: list[Issue]) -> None:
    name = fm.get("name", "")
    if not name:
        issues.append(Issue("fail", "validate.name.missing", "'name' field is required", "name"))
        return
    safe = sanitize_for_echo(name, max_len=80)
    if len(name) > MAX_NAME:
        issues.append(
            Issue(
                "fail",
                "validate.name.too-long",
                f"'name' is {len(name)} chars (max {MAX_NAME})",
                "name",
            )
        )
    if not NAME_RE.match(name):
        issues.append(
            Issue(
                "fail",
                "validate.name.bad-format",
                f"'name' must be lowercase a-z/0-9 with single hyphens, no leading/"
                f"trailing/consecutive hyphens. Got: {safe!r}",
                "name",
            )
        )
    if name != skill_dir.name:
        issues.append(
            Issue(
                "fail",
                "validate.name.dir-mismatch",
                f"'name' ({safe!r}) must match parent directory "
                f"({sanitize_for_echo(skill_dir.name)!r})",
                "name",
            )
        )


def _check_description(fm: dict[str, Any], issues: list[Issue]) -> None:
    desc = fm.get("description", "")
    if not desc:
        issues.append(
            Issue(
                "fail",
                "validate.description.missing",
                "'description' is required and must be non-empty",
                "description",
            )
        )
        return
    if len(desc) > MAX_DESCRIPTION:
        issues.append(
            Issue(
                "fail",
                "validate.description.too-long",
                f"'description' is {len(desc)} chars (max {MAX_DESCRIPTION})",
                "description",
            )
        )
    if len(desc) < MIN_DESCRIPTION_WARN:
        issues.append(
            Issue(
                "warn",
                "validate.description.too-short",
                f"'description' is only {len(desc)} chars — likely too short to "
                f"convey when to trigger",
                "description",
            )
        )


def _check_compatibility(fm: dict[str, Any], issues: list[Issue]) -> None:
    compat = fm.get("compatibility")
    if isinstance(compat, str) and compat and len(compat) > MAX_COMPATIBILITY:
        issues.append(
            Issue(
                "fail",
                "validate.compatibility.too-long",
                f"'compatibility' is {len(compat)} chars (max {MAX_COMPATIBILITY})",
                "compatibility",
            )
        )


def _check_unknown_keys(fm: dict[str, Any], issues: list[Issue]) -> None:
    unknown = set(fm.keys()) - ALLOWED_KEYS
    for key in sorted(unknown):
        safe = sanitize_for_echo(key, max_len=64)
        issues.append(
            Issue(
                "warn",
                "validate.frontmatter.unknown-key",
                f"unknown frontmatter key {safe!r} (spec defines: "
                f"{', '.join(sorted(ALLOWED_KEYS))})",
                key,
            )
        )


def _check_references(skill_dir: Path, body: str, issues: list[Issue]) -> None:
    skill_root = skill_dir.resolve()

    def _within_skill(target: Path) -> bool:
        try:
            target.relative_to(skill_root)
        except ValueError:
            return False
        return True

    for ref in _MD_LINK_RE.findall(body):
        if ref.startswith(("http://", "https://", "#", "/")):
            continue
        # Skip template placeholder paths like `references/<topic>.md`.
        if any(c in ref for c in "<>${}"):
            continue
        target = (skill_dir / ref).resolve()
        # Refuse to probe paths that escape the skill directory.
        if not _within_skill(target):
            continue
        if not target.exists():
            issues.append(
                Issue(
                    "warn",
                    "validate.reference.broken",
                    f"referenced file does not exist: {sanitize_for_echo(ref)}",
                )
            )

    for script_match in _SCRIPT_REF_RE.finditer(body):
        ref = script_match.group(1)
        # Skip placeholder paths like `scripts/<name>.py` or `scripts/${X}.py`.
        if any(c in ref for c in "<>${}"):
            continue
        target = (skill_dir / ref).resolve()
        if not _within_skill(target):
            continue
        if not target.exists():
            issues.append(
                Issue(
                    "warn",
                    "validate.script.broken",
                    f"referenced script does not exist: {sanitize_for_echo(ref)}",
                )
            )


def _summary(issues: list[Issue]) -> dict[str, Any]:
    fails = sum(1 for i in issues if i.severity == "fail")
    warns = sum(1 for i in issues if i.severity == "warn")
    return {"fail": fails, "warn": warns, "ok": fails == 0}


def _emit_text(issues: list[Issue], summary: dict[str, Any]) -> None:
    for issue in issues:
        print(issue.to_line())
    print(f"\n{summary['fail']} fail(s), {summary['warn']} warning(s)")


def _emit_json(skill_dir: Path, issues: list[Issue], summary: dict[str, Any]) -> None:
    payload = {
        "skill_dir": str(skill_dir),
        "issues": [asdict(i) for i in issues],
        "summary": summary,
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an agent skill against the Agent Skills specification.",
        epilog="Examples:\n"
        "  validate_skill.py path/to/my-skill\n"
        "  validate_skill.py ./skill --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("skill_dir", help="Path to the skill directory")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON on stdout.")
    parser.add_argument(
        "--exit-on-warn",
        action="store_true",
        help="Return non-zero exit if any WARN-level issues are found.",
    )
    args = parser.parse_args(argv)

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    if not skill_dir.exists():
        print(f"validate_skill: path does not exist: {skill_dir}", file=sys.stderr)
        return 2
    if not skill_dir.is_dir():
        print(f"validate_skill: not a directory: {skill_dir}", file=sys.stderr)
        return 2

    issues = validate(skill_dir)
    summary = _summary(issues)
    if args.as_json:
        _emit_json(skill_dir, issues, summary)
    else:
        _emit_text(issues, summary)

    if summary["fail"]:
        return 1
    if args.exit_on_warn and summary["warn"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
