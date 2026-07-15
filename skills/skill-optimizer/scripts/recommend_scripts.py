#!/usr/bin/env python3
"""Surface SKILL.md-level opportunities to extract bundled scripts.

Scans SKILL.md for procedures the agent would re-derive on every run (long
shell blocks) and recommends extracting them into `scripts/`. Per-script
quality checks (argparse, --format, --quiet, exit codes, PEP 723) are
delegated to the agent-tool-builder skill's `validate_agent_tool.py`.

Kinds:

    extract-procedure       a long bash block in SKILL.md should become a script
    skill-md-missing        the skill has no SKILL.md (recoverable warn)

Usage:
    recommend_scripts.py <skill-directory> [--json]

Default output is one opportunity per line. With --json, emits a structured
object on stdout. Echoed snippets are sanitized: ANSI escapes stripped,
control characters escaped, length capped.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

from skill_lib import sanitize_for_echo

LONG_BASH_LINES = 6  # blocks with this many or more non-empty lines flagged

_BASH_BLOCK_RE = re.compile(r"```(?:bash|sh|shell|zsh|fish)\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class Opportunity:
    kind: str
    where: str  # file or file:line-range
    title: str
    why: str
    suggestion: str
    severity: str  # "info" | "warn"


def recommend(skill_dir: Path) -> list[dict[str, Any]]:
    opps: list[Opportunity] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        opps.append(
            Opportunity(
                kind="recommend.skill-md.missing",
                where=str(skill_md),
                title="SKILL.md not found",
                why="Cannot scan a skill without a SKILL.md.",
                suggestion=(
                    "Create SKILL.md with frontmatter (name, description) "
                    "before running this script."
                ),
                severity="warn",
            )
        )
        return [asdict(o) for o in opps]

    body = skill_md.read_text(encoding="utf-8")
    _scan_bash_blocks(body, skill_md, opps)
    return [asdict(o) for o in opps]


def _scan_bash_blocks(body: str, skill_md: Path, opps: list[Opportunity]) -> None:
    for m in _BASH_BLOCK_RE.finditer(body):
        block = m.group(1)
        non_empty = [line for line in block.splitlines() if line.strip()]
        if len(non_empty) < LONG_BASH_LINES:
            continue
        start_line = body[: m.start()].count("\n") + 1
        end_line = body[: m.end()].count("\n") + 1
        snippet = sanitize_for_echo(non_empty[0], max_len=80)
        opps.append(
            Opportunity(
                kind="recommend.script.extract-procedure",
                where=f"{skill_md.name}:{start_line}-{end_line}",
                title=f"Long bash block ({len(non_empty)} lines)",
                why=(
                    "Multi-step shell procedures embedded in SKILL.md force the agent "
                    "to re-derive them on every run. The first command was: " + repr(snippet) + "."
                ),
                suggestion=(
                    "Extract into scripts/<name>.py with argparse, --format, and --quiet. "
                    "Replace the block in SKILL.md with a one-line invocation. "
                    "Then run agent-tool-builder/scripts/validate_agent_tool.py on the new script."
                ),
                severity="info",
            )
        )


def _emit_text(opps: list[dict[str, Any]]) -> None:
    if not opps:
        print("no opportunities found")
        return
    for o in opps:
        prefix = "WARN" if o["severity"] == "warn" else "INFO"
        print(f"{prefix}: [{o['kind']}] {o['where']} — {o['title']}")
        print(f"  why: {o['why']}")
        print(f"  suggestion: {o['suggestion']}")


def _emit_json(skill_dir: Path, opps: list[dict[str, Any]]) -> None:
    by_kind: Counter[str] = Counter(o["kind"] for o in opps)
    payload = {
        "skill_dir": str(skill_dir),
        "opportunities": opps,
        "summary": {
            "total": len(opps),
            "by_kind": dict(by_kind),
        },
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Surface SKILL.md-level opportunities to extract bundled scripts.",
        epilog="Examples:\n"
        "  recommend_scripts.py path/to/my-skill\n"
        "  recommend_scripts.py ./skill --json\n"
        "\nFor per-script quality checks, use:\n"
        "  agent-tool-builder/scripts/validate_agent_tool.py <script-path>\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("skill_dir", help="Path to the skill directory")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias for --format json."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress informational stderr.")
    args = parser.parse_args(argv)
    use_json = args.as_json or args.format == "json"

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    if not skill_dir.exists():
        print(f"recommend_scripts: path does not exist: {skill_dir}", file=sys.stderr)
        return 2
    if not skill_dir.is_dir():
        print(f"recommend_scripts: not a directory: {skill_dir}", file=sys.stderr)
        return 2

    opps = recommend(skill_dir)
    if use_json:
        _emit_json(skill_dir, opps)
    else:
        _emit_text(opps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
