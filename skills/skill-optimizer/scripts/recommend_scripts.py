#!/usr/bin/env python3
"""Surface opportunities to add or improve bundled scripts in a target skill.

Heuristics, not certainties. Each opportunity has a stable `kind` so a caller
can filter or batch fixes:

    extract-procedure       a long bash block in SKILL.md should become a script
    missing-argparse        a bundled script doesn't use argparse / lacks --help
    add-json-output         a bundled script doesn't expose --json
    add-pep723-metadata     a script imports non-stdlib without inline deps
    skill-md-missing        the skill has no SKILL.md (recoverable warn)

Usage:
    recommend_scripts.py <skill-directory> [--json]

Default output is one opportunity per line. With --json, emits a structured
object on stdout. Echoed snippets are sanitized: ANSI escapes stripped,
control characters escaped, length capped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from skill_lib import sanitize_for_echo

LONG_BASH_LINES = 6  # blocks with this many or more non-empty lines flagged
SCRIPT_EXTS = (".py",)
STDLIB = frozenset(sys.stdlib_module_names)
SKIP_DIRS_IN_SCRIPTS = frozenset({"tests", "__pycache__", ".pytest_cache"})

_BASH_BLOCK_RE = re.compile(r"```(?:bash|sh|shell|zsh|fish)\s*\n(.*?)\n```", re.DOTALL)
_PEP723_RE = re.compile(r"^# ///\s*script", re.MULTILINE)
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z0-9_.]+)", re.MULTILINE)


@dataclass
class Opportunity:
    kind: str
    where: str  # file or file:line-range
    title: str
    why: str
    suggestion: str
    severity: str  # "info" | "warn"


def recommend(skill_dir: Path) -> list[dict]:
    skill_dir = Path(skill_dir)
    opps: list[Opportunity] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        opps.append(
            Opportunity(
                kind="skill-md-missing",
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
    _scan_scripts_dir(skill_dir, opps)
    return [asdict(o) for o in opps]


def _scan_bash_blocks(body: str, skill_md: Path, opps: list[Opportunity]) -> None:
    line_starts = [0]
    for i, ch in enumerate(body):
        if ch == "\n":
            line_starts.append(i + 1)

    def line_of(offset: int) -> int:
        # Binary search would be faster but bodies are small.
        for i in range(len(line_starts) - 1, -1, -1):
            if line_starts[i] <= offset:
                return i + 1
        return 1

    for m in _BASH_BLOCK_RE.finditer(body):
        block = m.group(1)
        non_empty = [line for line in block.splitlines() if line.strip()]
        if len(non_empty) < LONG_BASH_LINES:
            continue
        start_line = line_of(m.start())
        end_line = line_of(m.end())
        snippet = sanitize_for_echo(non_empty[0], max_len=80)
        opps.append(
            Opportunity(
                kind="extract-procedure",
                where=f"{skill_md.name}:{start_line}-{end_line}",
                title=f"Long bash block ({len(non_empty)} lines)",
                why=(
                    "Multi-step shell procedures embedded in SKILL.md force the agent "
                    "to re-derive them on every run. The first command was: " + repr(snippet) + "."
                ),
                suggestion=(
                    "Extract into scripts/<name>.py with argparse, --help, and --json. "
                    "Replace the block in SKILL.md with a one-line invocation."
                ),
                severity="info",
            )
        )


def _iter_skill_scripts(skill_dir: Path) -> Iterable[Path]:
    scripts = skill_dir / "scripts"
    if not scripts.is_dir():
        return
    scripts_resolved = scripts.resolve()
    for path in scripts.rglob("*"):
        if path.is_dir():
            continue
        if path.suffix not in SCRIPT_EXTS:
            continue
        # Skip symlinks pointing outside scripts/ — refuse to read unrelated files.
        if path.is_symlink():
            try:
                path.resolve().relative_to(scripts_resolved)
            except ValueError:
                continue
        if any(part in SKIP_DIRS_IN_SCRIPTS for part in path.relative_to(scripts).parts):
            continue
        if path.name.startswith("_"):
            # convention: _shared.py / _lib.py modules, not entry points
            continue
        yield path


def _is_entry_point(text: str) -> bool:
    """Heuristic: this script is invoked directly (not just imported as a library).

    Pure libraries don't read sys.argv or call parse_args(). Either of those
    signals — plus the canonical `if __name__` guard — is enough to treat
    the file as an entry point.
    """
    return "if __name__" in text or "sys.argv" in text or "parse_args()" in text


def _scan_scripts_dir(skill_dir: Path, opps: list[Opportunity]) -> None:
    scripts_dir = skill_dir / "scripts"
    local_modules: set[str] = set()
    if scripts_dir.is_dir():
        for path in scripts_dir.iterdir():
            if path.is_file() and path.suffix == ".py":
                local_modules.add(path.stem)
    for script in _iter_skill_scripts(skill_dir):
        text = script.read_text(encoding="utf-8")
        rel = _posix_str(script.relative_to(skill_dir))
        _check_argparse(text, rel, opps)
        _check_json_output(text, rel, opps)
        _check_pep723(text, rel, local_modules, opps)


def _posix_str(path: Path | str) -> str:
    """Render a Path with forward slashes regardless of OS, for stable output."""
    return str(path).replace("\\", "/")


def _check_argparse(text: str, rel: str, opps: list[Opportunity]) -> None:
    has_argparse_import = re.search(r"\bimport\s+argparse\b", text) or re.search(
        r"\bfrom\s+argparse\b", text
    )
    has_argument_parser = "ArgumentParser" in text
    if has_argparse_import and has_argument_parser:
        return
    if "if __name__" not in text and "sys.argv" not in text:
        # Not an entry-point script; might be a library. Skip.
        return
    opps.append(
        Opportunity(
            kind="missing-argparse",
            where=rel,
            title="Script lacks argparse",
            why=(
                "Without argparse the script has no --help output, which is the "
                "primary way an agent learns the script's interface."
            ),
            suggestion=(
                "Use argparse.ArgumentParser(description=...) with positional + "
                "optional flags, and let --help be implicit."
            ),
            severity="warn",
        )
    )


def _check_json_output(text: str, rel: str, opps: list[Opportunity]) -> None:
    if "'--json'" in text or '"--json"' in text:
        return
    # If the script already produces JSON anywhere (e.g. via a subcommand),
    # treat it as JSON-aware and don't flag.
    if "json.dumps" in text:
        return
    if "ArgumentParser" not in text and "if __name__" not in text:
        return
    opps.append(
        Opportunity(
            kind="add-json-output",
            where=rel,
            title="Script does not expose --json",
            why=(
                "Structured output is easier for agents to consume than free-form "
                "text and is the convention in this skill."
            ),
            suggestion=(
                "Add a --json flag. Default to text for human use; emit "
                "json.dumps(payload, indent=2) on stdout when set."
            ),
            severity="info",
        )
    )


def _check_pep723(text: str, rel: str, local_modules: set[str], opps: list[Opportunity]) -> None:
    # PEP 723 is for scripts run directly (uv run script.py). Library files
    # don't need it; their callers do.
    if not _is_entry_point(text):
        return
    if _PEP723_RE.search(text):
        return
    imported = {m.split(".")[0] for m in _IMPORT_RE.findall(text)}
    non_stdlib = imported - STDLIB - {"skill_lib"} - local_modules
    if not non_stdlib:
        return
    opps.append(
        Opportunity(
            kind="add-pep723-metadata",
            where=rel,
            title="Non-stdlib imports without PEP 723 inline metadata",
            why=(
                f"Script imports non-stdlib modules ({sorted(non_stdlib)!r}) but "
                f"declares no dependencies. Agents running it via uv run will fail."
            ),
            suggestion=(
                "Add a `# /// script ... # ///` block at the top declaring "
                "dependencies (PEP 723), so `uv run scripts/<name>.py` resolves "
                "them automatically."
            ),
            severity="warn",
        )
    )


def _emit_text(opps: list[dict]) -> None:
    if not opps:
        print("no opportunities found")
        return
    for o in opps:
        prefix = "WARN" if o["severity"] == "warn" else "INFO"
        print(f"{prefix}: [{o['kind']}] {o['where']} — {o['title']}")
        print(f"  why: {o['why']}")
        print(f"  suggestion: {o['suggestion']}")


def _emit_json(skill_dir: Path, opps: list[dict]) -> None:
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
        description="Surface opportunities to add or improve bundled scripts in a Claude skill.",
        epilog="Examples:\n"
        "  recommend_scripts.py ~/.claude/skills/my-skill\n"
        "  recommend_scripts.py ./skill --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("skill_dir", help="Path to the skill directory")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON on stdout.")
    args = parser.parse_args(argv)

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    if not skill_dir.exists():
        print(f"recommend_scripts: path does not exist: {skill_dir}", file=sys.stderr)
        return 2
    if not skill_dir.is_dir():
        print(f"recommend_scripts: not a directory: {skill_dir}", file=sys.stderr)
        return 2

    opps = recommend(skill_dir)
    if args.as_json:
        _emit_json(skill_dir, opps)
    else:
        _emit_text(opps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
