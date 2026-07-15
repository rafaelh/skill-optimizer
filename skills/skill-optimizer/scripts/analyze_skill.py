#!/usr/bin/env python3
"""Analyze an agent skill for content anti-patterns.

Heuristic checks: declarative description openings, missing trigger contexts,
body too long for progressive disclosure, generic filler phrases, references
introduced without a load trigger, mega-skill structure.

Usage:
    analyze_skill.py <skill-directory> [--json] [--exit-on-warn]

Default output is one issue per line, prefixed with WARN:/INFO: and a
machine-readable code in brackets. With --json, emits a structured object.

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

from skill_lib import emit_error, parse_frontmatter, sanitize_for_echo

GENERIC_PHRASES = (
    r"\bhandle errors appropriately\b",
    r"\bfollow best practices\b",
    r"\bas needed\b",
    r"\bif applicable\b",
    r"\bas appropriate\b",
    r"\busing standard practices\b",
    r"\bproperly\b(?! formatted| escaped)",
)

DECLARATIVE_STARTS = (
    "this skill ",
    "this command ",
    "a skill that ",
    "helps with ",
    "helps the ",
)

REFERENCE_TRIGGER_WORDS = ("when ", "if ", "before ", "after ", "during ", "read ", "load ")

MAX_BODY_LINES = 500
MAX_BODY_TOKENS = 5000
MIN_DESCRIPTION_QUALITY = 100
LARGE_SKILL_TOKENS = 1500
MEGA_SKILL_H2_COUNT = 12

_REFERENCE_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((references/[^)]+\.md|assets/[^)]+|scripts/[^)]+)\)"
)
_GOTCHAS_HEADER_RE = re.compile(r"^#+\s*Gotchas?\b", re.MULTILINE | re.IGNORECASE)


@dataclass
class Issue:
    severity: str  # "fail" | "warn" | "info"
    code: str
    message: str
    field: str | None = None

    def to_line(self) -> str:
        prefix = {"fail": "FAIL", "warn": "WARN", "info": "INFO"}.get(
            self.severity, self.severity.upper()
        )
        return f"{prefix}: [{self.code}] {self.message}"


def _strip_code_spans(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", "", text)
    return text


def analyze(skill_dir: Path) -> list[Issue]:
    issues: list[Issue] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [
            Issue(
                "fail",
                "analyze.skill-md.missing",
                f"SKILL.md not found at {sanitize_for_echo(skill_md)}",
            )
        ]
    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if fm is None:
        return [
            Issue(
                "fail",
                "analyze.frontmatter.missing",
                "SKILL.md is missing YAML frontmatter",
            )
        ]
    desc = fm.get("description", "") if isinstance(fm, dict) else ""
    prose = _strip_code_spans(body)

    _check_description(desc, issues)
    body_lines, approx_tokens, h2s = _check_body_size(body, issues)
    _check_generic_filler(prose, issues)
    _check_gotchas(approx_tokens, body, issues)
    _check_reference_load_triggers(body, issues)
    _check_mega_skill(h2s, issues)

    issues.append(
        Issue(
            "info",
            "analyze.stats",
            f"body = {body_lines} lines, ~{approx_tokens} tokens, {len(h2s)} "
            f"H2 sections; description = {len(desc)} chars.",
        )
    )
    return issues


def _check_description(desc: str, issues: list[Issue]) -> None:
    if not desc:
        return
    safe = sanitize_for_echo(desc, max_len=120)
    desc_low = desc.lower()
    if any(desc_low.startswith(s) for s in DECLARATIVE_STARTS):
        issues.append(
            Issue(
                "warn",
                "analyze.description.declarative",
                f"description starts declaratively ({safe!r}). Prefer imperative "
                f"form: 'Use this skill when...' or '<Verb> ...'.",
                "description",
            )
        )
    has_when = "when " in desc_low or "use this skill" in desc_low
    if not has_when:
        issues.append(
            Issue(
                "warn",
                "analyze.description.no-trigger",
                "description does not say *when* to use the skill. Add explicit "
                "trigger contexts ('Use when the user wants to ...').",
                "description",
            )
        )
    if 0 < len(desc) < MIN_DESCRIPTION_QUALITY:
        issues.append(
            Issue(
                "warn",
                "analyze.description.thin",
                f"description is only {len(desc)} chars. Effective descriptions "
                f"list multiple trigger contexts and usually run 200-600 chars.",
                "description",
            )
        )


def _check_body_size(body: str, issues: list[Issue]) -> tuple[int, int, list[str]]:
    body_lines = body.splitlines()
    line_count = len(body_lines)
    approx_tokens = max(1, len(body) // 4)
    h2s = [line for line in body_lines if line.startswith("## ")]
    if line_count > MAX_BODY_LINES:
        issues.append(
            Issue(
                "warn",
                "analyze.body.lines-over-limit",
                f"SKILL.md body is {line_count} lines (>{MAX_BODY_LINES}). Move "
                f"detail to references/, scripts/, or assets/.",
            )
        )
    if approx_tokens > MAX_BODY_TOKENS:
        issues.append(
            Issue(
                "warn",
                "analyze.body.tokens-over-limit",
                f"SKILL.md body is ~{approx_tokens} tokens (>{MAX_BODY_TOKENS}). "
                f"Apply progressive disclosure.",
            )
        )
    return line_count, approx_tokens, h2s


def _check_generic_filler(prose: str, issues: list[Issue]) -> None:
    for pat in GENERIC_PHRASES:
        m = re.search(pat, prose, re.IGNORECASE)
        if m:
            safe = sanitize_for_echo(m.group(0))
            issues.append(
                Issue(
                    "warn",
                    "analyze.body.generic-filler",
                    f"generic filler detected: {safe!r}. Replace with concrete "
                    f"gotchas or specific procedures.",
                )
            )


def _check_gotchas(approx_tokens: int, body: str, issues: list[Issue]) -> None:
    if approx_tokens > LARGE_SKILL_TOKENS and not _GOTCHAS_HEADER_RE.search(body):
        issues.append(
            Issue(
                "info",
                "analyze.body.gotchas-missing",
                "no 'Gotchas' section. Consider adding one for non-obvious "
                "environment-specific facts the agent would otherwise miss.",
            )
        )


def _check_reference_load_triggers(body: str, issues: list[Issue]) -> None:
    for ref_match in _REFERENCE_LINK_RE.finditer(body):
        target = ref_match.group(2)
        start = max(0, ref_match.start() - 220)
        end = min(len(body), ref_match.end() + 220)
        ctx = body[start:end].lower()
        if not any(t in ctx for t in REFERENCE_TRIGGER_WORDS):
            issues.append(
                Issue(
                    "warn",
                    "analyze.reference.no-trigger",
                    f"reference {sanitize_for_echo(target)!r} mentioned without "
                    f"a load trigger. Tell the agent *when* to load it (e.g., "
                    f"'Read X when Y').",
                )
            )


def _check_mega_skill(h2s: list[str], issues: list[Issue]) -> None:
    if len(h2s) > MEGA_SKILL_H2_COUNT:
        issues.append(
            Issue(
                "info",
                "analyze.body.mega-skill",
                f"{len(h2s)} H2 sections — possible mega-skill. Consider splitting "
                f"into focused skills.",
            )
        )


def _emit_text(skill_dir: Path, issues: list[Issue]) -> None:
    for issue in issues:
        print(issue.to_line())


def _emit_json(skill_dir: Path, issues: list[Issue]) -> None:
    stats_issue = next((i for i in issues if i.code == "analyze.stats"), None)
    stats: dict[str, int] = {}
    if stats_issue:
        m = re.search(
            r"body = (\d+) lines, ~(\d+) tokens, (\d+) H2 sections; description = (\d+) chars",
            stats_issue.message,
        )
        if m:
            stats = {
                "body_lines": int(m.group(1)),
                "approx_tokens": int(m.group(2)),
                "h2_sections": int(m.group(3)),
                "description_chars": int(m.group(4)),
            }
    payload = {
        "skill_dir": str(skill_dir),
        "issues": [asdict(i) for i in issues if i.code != "analyze.stats"],
        "stats": stats,
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze an agent skill for content anti-patterns.",
        epilog="Examples:\n"
        "  analyze_skill.py path/to/my-skill\n"
        "  analyze_skill.py ./skill --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("skill_dir", help="Path to the skill directory")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias for --format json."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress informational stderr.")
    parser.add_argument(
        "--exit-on-warn",
        action="store_true",
        help="Return non-zero exit if any WARN-level issues are found.",
    )
    args = parser.parse_args(argv)
    use_json = args.as_json or args.format == "json"

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    if not skill_dir.exists():
        emit_error(
            "analyze_skill", f"path does not exist: {skill_dir}",
            code="analyze.input.not-found", hint="Check the path and try again.",
        )
        return 2
    if not skill_dir.is_dir():
        emit_error(
            "analyze_skill", f"not a directory: {skill_dir}",
            code="analyze.input.not-dir",
            hint="Argument must be a skill directory, not a file.",
        )
        return 2

    issues = analyze(skill_dir)
    if use_json:
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
