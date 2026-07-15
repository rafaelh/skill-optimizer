#!/usr/bin/env python3
"""Scaffold a new agent skill from the bundled templates.

Creates a directory layout that already passes validate_skill.py and
analyze_skill.py: a frontmatter-complete SKILL.md, an example script with
argparse + --json + PEP 723, empty references/ and assets/ with README
stubs, and a tests/ directory wired up to run.

Usage:
    init_skill.py <parent-dir> --name <slug> --description <text>
    init_skill.py <parent-dir> --name <slug> --description <text> --minimal
    init_skill.py <parent-dir> --name <slug> --description <text> --json

The skill is created at <parent-dir>/<name>/. The script refuses to
overwrite an existing directory unless --force is set.

Exit codes:
    0   skill created
    1   destination already exists (without --force) or invalid name
    2   bad invocation (missing parent dir, unwritable path)
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys

from skill_lib import sanitize_for_echo

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MIN_DESCRIPTION_WARN = 60

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"
SKILL_TEMPLATE = TEMPLATES_DIR / "SKILL.md.template"
SCRIPT_TEMPLATE = TEMPLATES_DIR / "script.py.template"


@dataclass
class CreatedFile:
    path: str
    kind: str  # "skill-md" | "script" | "reference" | "asset" | "test" | "dir"


def init_skill(
    parent_dir: Path,
    name: str,
    description: str,
    *,
    minimal: bool = False,
    force: bool = False,
) -> tuple[Path, list[CreatedFile]]:
    """Create the skill, return (skill_dir, list-of-created-files)."""
    _validate_name(name)
    _validate_description(description)

    skill_dir = parent_dir / name
    if skill_dir.exists() and not force:
        raise FileExistsError(f"destination already exists: {skill_dir}")
    # With --force we still don't blindly delete; we just write into the
    # existing directory and let the user resolve conflicts in git.

    skill_dir.mkdir(parents=True, exist_ok=True)
    created: list[CreatedFile] = [CreatedFile(str(skill_dir), "dir")]

    # SKILL.md is always created.
    skill_md_text = _render_skill_md(name=name, description=description)
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(skill_md_text, encoding="utf-8")
    created.append(CreatedFile(str(skill_md_path), "skill-md"))

    if minimal:
        return skill_dir, created

    # Full layout: scripts/example.py + references/ + assets/ + tests/.
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    created.append(CreatedFile(str(scripts_dir), "dir"))

    example_script_path = scripts_dir / "example.py"
    example_script_path.write_text(_render_example_script(), encoding="utf-8")
    created.append(CreatedFile(str(example_script_path), "script"))

    references_dir = skill_dir / "references"
    references_dir.mkdir(exist_ok=True)
    (references_dir / "README.md").write_text(_REFERENCES_README, encoding="utf-8")
    created.append(CreatedFile(str(references_dir / "README.md"), "reference"))

    assets_dir = skill_dir / "assets"
    assets_dir.mkdir(exist_ok=True)
    (assets_dir / "README.md").write_text(_ASSETS_README, encoding="utf-8")
    created.append(CreatedFile(str(assets_dir / "README.md"), "asset"))

    tests_dir = scripts_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    (tests_dir / "test_example.py").write_text(_TEST_EXAMPLE, encoding="utf-8")
    created.append(CreatedFile(str(tests_dir / "conftest.py"), "test"))
    created.append(CreatedFile(str(tests_dir / "test_example.py"), "test"))

    return skill_dir, created


def _validate_name(name: str) -> None:
    if len(name) > MAX_NAME:
        raise ValueError(f"name is {len(name)} chars (max {MAX_NAME})")
    if not NAME_RE.match(name):
        raise ValueError(
            f"name must be lowercase a-z/0-9 with single hyphens, no leading/"
            f"trailing/consecutive hyphens. Got: {sanitize_for_echo(name)!r}"
        )


def _validate_description(description: str) -> None:
    if not description:
        raise ValueError("description is required")
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(f"description is {len(description)} chars (max {MAX_DESCRIPTION})")


def _render_skill_md(*, name: str, description: str) -> str:
    template = SKILL_TEMPLATE.read_text(encoding="utf-8")
    display_name = " ".join(part.capitalize() for part in name.split("-"))
    return (
        template.replace("{{name}}", name)
        .replace("{{description}}", description)
        .replace("{{display_name}}", display_name)
    )


def _render_example_script() -> str:
    template = SCRIPT_TEMPLATE.read_text(encoding="utf-8")
    return template.replace("{{script_name}}", "example")


_REFERENCES_README = """# references/

Place reference documents here that should be loaded only on-demand by the
agent. Examples: schemas, edge-case catalogs, lookup tables, long
explanations of *why* a procedure exists.

Critical rule: every reference file mentioned in SKILL.md must have a load
trigger nearby. Phrases like "see references/foo.md" are dead weight —
the agent won't follow them. Phrases like "Read references/foo.md when the
API returns a non-200 status" actually get loaded.
"""

_ASSETS_README = """# assets/

Templates, schemas, lookup tables, sample data — anything that scripts read
or generate but the agent shouldn't need to load directly. Subdirectories
are common:

- assets/templates/  — text/json/yaml templates
- assets/schemas/    — JSON Schemas for validating inputs
"""

_CONFTEST = """from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
"""

_TEST_EXAMPLE = '''"""Smoke test for example.py.

Replace with real tests as you flesh out the script.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parent.parent / "example.py"


def test_help_works() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
'''


def _emit_text(skill_dir: Path, created: list[CreatedFile]) -> None:
    print(f"Created skill at {skill_dir}")
    for entry in created:
        if entry.kind == "dir":
            continue
        print(f"  + {entry.path}")


def _emit_json(skill_dir: Path, created: list[CreatedFile]) -> None:
    payload = {
        "skill_dir": str(skill_dir),
        "created": [asdict(c) for c in created],
        "summary": {"files": sum(1 for c in created if c.kind != "dir")},
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold a new agent skill from the bundled templates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("parent_dir", help="Parent directory under which <name>/ will be created")
    parser.add_argument("--name", required=True, help="Skill identifier (lowercase + hyphens)")
    parser.add_argument(
        "--description",
        required=True,
        help="Frontmatter description — what the skill does and when to use it.",
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Create only SKILL.md (skip scripts/, references/, assets/, tests/).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow writing into an existing destination directory.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON on stdout.",
    )
    args = parser.parse_args(argv)

    parent = Path(args.parent_dir).expanduser().resolve()
    if not parent.exists():
        print(f"init_skill: parent does not exist: {parent}", file=sys.stderr)
        return 2
    if not parent.is_dir():
        print(f"init_skill: parent is not a directory: {parent}", file=sys.stderr)
        return 2

    try:
        skill_dir, created = init_skill(
            parent,
            args.name,
            args.description,
            minimal=args.minimal,
            force=args.force,
        )
    except FileExistsError as exc:
        print(f"init_skill: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"init_skill: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        _emit_json(skill_dir, created)
    else:
        _emit_text(skill_dir, created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
