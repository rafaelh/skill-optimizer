#!/usr/bin/env python3
"""Validate a Claude skill against the Agent Skills specification.

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

from skill_lib import parse_frontmatter, sanitize_for_echo

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MIN_DESCRIPTION_WARN = 60
MAX_COMPATIBILITY = 500
ALLOWED_KEYS = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
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
                "skill-md-missing",
                f"SKILL.md not found at {sanitize_for_echo(skill_md)}",
            )
        ]

    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if fm is None:
        return [
            Issue(
                "fail",
                "frontmatter-missing",
                "SKILL.md is missing YAML frontmatter (must start with '---')",
            )
        ]

    _check_name(fm, skill_dir, issues)
    _check_description(fm, issues)
    _check_compatibility(fm, issues)
    _check_unknown_keys(fm, issues)
    _check_references(skill_dir, body, issues)
    return issues


def _check_name(fm: dict, skill_dir: Path, issues: list[Issue]) -> None:
    name = fm.get("name", "")
    if not name:
        issues.append(Issue("fail", "name-missing", "'name' field is required", "name"))
        return
    safe = sanitize_for_echo(name, max_len=80)
    if len(name) > MAX_NAME:
        issues.append(
            Issue(
                "fail",
                "name-too-long",
                f"'name' is {len(name)} chars (max {MAX_NAME})",
                "name",
            )
        )
    if not NAME_RE.match(name):
        issues.append(
            Issue(
                "fail",
                "name-bad-format",
                f"'name' must be lowercase a-z/0-9 with single hyphens, no leading/"
                f"trailing/consecutive hyphens. Got: {safe!r}",
                "name",
            )
        )
    if name != skill_dir.name:
        issues.append(
            Issue(
                "fail",
                "name-mismatch-dir",
                f"'name' ({safe!r}) must match parent directory "
                f"({sanitize_for_echo(skill_dir.name)!r})",
                "name",
            )
        )


def _check_description(fm: dict, issues: list[Issue]) -> None:
    desc = fm.get("description", "")
    if not desc:
        issues.append(
            Issue(
                "fail",
                "description-missing",
                "'description' is required and must be non-empty",
                "description",
            )
        )
        return
    if len(desc) > MAX_DESCRIPTION:
        issues.append(
            Issue(
                "fail",
                "description-too-long",
                f"'description' is {len(desc)} chars (max {MAX_DESCRIPTION})",
                "description",
            )
        )
    if len(desc) < MIN_DESCRIPTION_WARN:
        issues.append(
            Issue(
                "warn",
                "description-too-short",
                f"'description' is only {len(desc)} chars — likely too short to "
                f"convey when to trigger",
                "description",
            )
        )


def _check_compatibility(fm: dict, issues: list[Issue]) -> None:
    compat = fm.get("compatibility")
    if isinstance(compat, str) and compat and len(compat) > MAX_COMPATIBILITY:
        issues.append(
            Issue(
                "fail",
                "compatibility-too-long",
                f"'compatibility' is {len(compat)} chars (max {MAX_COMPATIBILITY})",
                "compatibility",
            )
        )


def _check_unknown_keys(fm: dict, issues: list[Issue]) -> None:
    unknown = set(fm.keys()) - ALLOWED_KEYS
    for key in sorted(unknown):
        safe = sanitize_for_echo(key, max_len=64)
        issues.append(
            Issue(
                "warn",
                "unknown-frontmatter-key",
                f"unknown frontmatter key {safe!r} (spec defines: "
                f"{', '.join(sorted(ALLOWED_KEYS))})",
                key,
            )
        )


def _check_references(skill_dir: Path, body: str, issues: list[Issue]) -> None:
    skill_root = skill_dir.resolve()

    def _within_skill(target: Path) -> bool:
        try:
            target.resolve().relative_to(skill_root)
        except ValueError:
            return False
        return True

    for ref in re.findall(r"\]\(([^)]+\.md)\)", body):
        if ref.startswith(("http://", "https://", "#", "/")):
            continue
        target = (skill_dir / ref).resolve()
        # Refuse to probe paths that escape the skill directory.
        if not _within_skill(target):
            continue
        if not target.exists():
            issues.append(
                Issue(
                    "warn",
                    "broken-reference",
                    f"referenced file does not exist: {sanitize_for_echo(ref)}",
                )
            )

    for script_match in re.finditer(r"`(scripts/[^`\s]+\.(?:py|sh|js|ts))`", body):
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
                    "broken-script-reference",
                    f"referenced script does not exist: {sanitize_for_echo(ref)}",
                )
            )


def _summary(issues: list[Issue]) -> dict:
    fails = sum(1 for i in issues if i.severity == "fail")
    warns = sum(1 for i in issues if i.severity == "warn")
    return {"fail": fails, "warn": warns, "ok": fails == 0}


def _emit_text(skill_dir: Path, issues: list[Issue]) -> None:
    for issue in issues:
        print(issue.to_line())
    s = _summary(issues)
    print(f"\n{s['fail']} fail(s), {s['warn']} warning(s)")


def _emit_json(skill_dir: Path, issues: list[Issue]) -> None:
    payload = {
        "skill_dir": str(skill_dir),
        "issues": [asdict(i) for i in issues],
        "summary": _summary(issues),
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a Claude skill against the Agent Skills specification.",
        epilog="Examples:\n"
        "  validate_skill.py ~/.claude/skills/my-skill\n"
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
    if args.as_json:
        _emit_json(skill_dir, issues)
    else:
        _emit_text(skill_dir, issues)

    fails = sum(1 for i in issues if i.severity == "fail")
    warns = sum(1 for i in issues if i.severity == "warn")
    if fails:
        return 1
    if args.exit_on_warn and warns:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
