from collections.abc import Callable
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from recommend_scripts import recommend

SCRIPT = Path(__file__).resolve().parent.parent / "recommend_scripts.py"

SkillFactory = Callable[..., Path]


@pytest.fixture
def skill(tmp_path: Path) -> SkillFactory:
    def _make(
        name: str = "demo",
        body: str = "# Demo\n",
        scripts: dict[str, str] | None = None,
    ) -> Path:
        d = tmp_path / name
        d.mkdir()
        desc = (
            "Use this skill when the user wants to test the recommend_scripts "
            "logic. Trigger when this fixture is used."
        )
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}")
        if scripts:
            scripts_dir = d / "scripts"
            scripts_dir.mkdir(exist_ok=True)
            for fname, contents in scripts.items():
                (scripts_dir / fname).write_text(contents)
        return d

    return _make


def kinds(opportunities: list[dict[str, Any]]) -> set[str]:
    return {o["kind"] for o in opportunities}


class TestExtractProcedure:
    def test_long_bash_block_flagged(self, skill: SkillFactory) -> None:
        body = "# Demo\n\n```bash\n" + "\n".join([f"echo step {i}" for i in range(8)]) + "\n```\n"
        d = skill(body=body)
        opps = recommend(d)
        assert "recommend.script.extract-procedure" in kinds(opps)

    def test_short_bash_block_not_flagged(self, skill: SkillFactory) -> None:
        body = "# Demo\n\n```bash\necho one\necho two\n```\n"
        d = skill(body=body)
        opps = recommend(d)
        assert "recommend.script.extract-procedure" not in kinds(opps)

    def test_multiple_blocks_each_assessed(self, skill: SkillFactory) -> None:
        long = "\n".join([f"echo {i}" for i in range(8)])
        body = f"# Demo\n\n```bash\necho short\n```\n\n```bash\n{long}\n```\n"
        d = skill(body=body)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.extract-procedure"]
        assert len(opps) == 1


class TestArgparseAndHelp:
    def test_script_without_argparse_flagged(self, skill: SkillFactory) -> None:
        scripts = {"do_thing.py": ("import sys\nprint(sys.argv[1])\n")}
        d = skill(scripts=scripts)
        opps = recommend(d)
        assert "recommend.script.missing-argparse" in kinds(opps)

    def test_script_with_argparse_not_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "do_thing.py": (
                "import argparse\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('x')\n"
                "p.parse_args()\n"
            )
        }
        d = skill(scripts=scripts)
        opps = recommend(d)
        assert all(
            not (o["kind"] == "recommend.script.missing-argparse" and "do_thing.py" in o["where"])
            for o in opps
        )


class TestJsonOutput:
    def test_script_without_json_output_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "doit.py": (
                "import argparse\np = argparse.ArgumentParser()\np.parse_args()\nprint('hello')\n"
            )
        }
        d = skill(scripts=scripts)
        opps = recommend(d)
        assert "recommend.script.missing-json" in kinds(opps)

    def test_script_with_json_flag_not_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "doit.py": (
                "import argparse, json\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--json', action='store_true')\n"
                "p.parse_args()\n"
                "print(json.dumps({}))\n"
            )
        }
        d = skill(scripts=scripts)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.missing-json"]
        assert opps == []


class TestPep723:
    def test_non_stdlib_import_without_pep723_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "fetch.py": (
                "import argparse, json\n"
                "import requests\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--json', action='store_true')\n"
                "p.parse_args()\n"
                "print(json.dumps({}))\n"
            )
        }
        d = skill(scripts=scripts)
        opps = recommend(d)
        assert "recommend.script.missing-pep723" in kinds(opps)

    def test_library_file_not_flagged(self, skill: SkillFactory) -> None:
        # No __main__ block, no sys.argv: this is a library, not an entry
        # point. PEP 723 belongs on the caller, not here.
        scripts = {
            "lib.py": ("import requests\ndef fetch(url):\n    return requests.get(url).text\n"),
        }
        d = skill(scripts=scripts)
        opps = [
            o
            for o in recommend(d)
            if o["kind"] == "recommend.script.missing-pep723" and "lib.py" in o["where"]
        ]
        assert opps == []

    def test_non_stdlib_import_with_pep723_not_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "fetch.py": (
                "# /// script\n"
                '# dependencies = ["requests"]\n'
                "# ///\n"
                "import argparse, json\n"
                "import requests\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--json', action='store_true')\n"
                "p.parse_args()\n"
                "print(json.dumps({}))\n"
            )
        }
        d = skill(scripts=scripts)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.missing-pep723"]
        assert opps == []

    def test_local_module_imports_are_not_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "helper.py": "def f(): return 1\n",
            "main.py": (
                "import argparse, json\n"
                "from helper import f\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--json', action='store_true')\n"
                "p.parse_args()\n"
                "print(json.dumps({'r': f()}))\n"
            ),
        }
        d = skill(scripts=scripts)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.missing-pep723"]
        assert opps == []

    def test_script_with_json_dumps_but_no_flag_not_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "doit.py": (
                "import argparse, json\n"
                "p = argparse.ArgumentParser()\n"
                "sub = p.add_subparsers(dest='cmd')\n"
                "sub.add_parser('show')\n"
                "args = p.parse_args()\n"
                "if args.cmd == 'show':\n"
                "    print(json.dumps({'x': 1}))\n"
            )
        }
        d = skill(scripts=scripts)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.missing-json"]
        assert opps == []

    def test_stdlib_only_script_not_flagged(self, skill: SkillFactory) -> None:
        scripts = {
            "doit.py": (
                "import argparse, json, sys, re\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--json', action='store_true')\n"
                "p.parse_args()\n"
                "print(json.dumps({}))\n"
            )
        }
        d = skill(scripts=scripts)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.missing-pep723"]
        assert opps == []


class TestExtraFenceLanguages:
    def test_shell_block_flagged(self, skill: SkillFactory) -> None:
        body = "# Demo\n\n```shell\n" + "\n".join(f"echo {i}" for i in range(8)) + "\n```\n"
        d = skill(body=body)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.extract-procedure"]
        assert len(opps) == 1

    def test_zsh_block_flagged(self, skill: SkillFactory) -> None:
        body = "# Demo\n\n```zsh\n" + "\n".join(f"echo {i}" for i in range(8)) + "\n```\n"
        d = skill(body=body)
        opps = [o for o in recommend(d) if o["kind"] == "recommend.script.extract-procedure"]
        assert len(opps) == 1


class TestSymlinkSafety:
    def test_external_symlink_in_scripts_skipped(self, skill: SkillFactory, tmp_path: Path) -> None:
        scripts = {
            "real.py": (
                "import argparse, json\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--json', action='store_true')\n"
                "p.parse_args()\n"
                "print(json.dumps({}))\n"
            )
        }
        d = skill(scripts=scripts)
        # Plant an external file and a symlink under scripts/ pointing to it.
        outside = tmp_path / "outside.py"
        outside.write_text("import psycopg2\nprint('should not be read')\n")
        try:
            (d / "scripts" / "external_link.py").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable on this platform: {exc}")
        opps = recommend(d)
        # The external link should not contribute opportunities.
        assert all("external_link.py" not in o["where"] for o in opps)


class TestRobustness:
    def test_skill_md_missing_returns_meta_warning(self, tmp_path: Path) -> None:
        d = tmp_path / "demo"
        d.mkdir()
        opps = recommend(d)
        assert "recommend.skill-md.missing" in kinds(opps)

    def test_no_scripts_dir_does_not_crash(self, skill: SkillFactory) -> None:
        d = skill()
        opps = recommend(d)
        assert isinstance(opps, list)

    def test_does_not_recurse_into_tests(self, skill: SkillFactory) -> None:
        scripts = {
            "doit.py": (
                "import argparse, json\np=argparse.ArgumentParser()\n"
                "p.add_argument('--json',action='store_true');p.parse_args()\n"
                "print(json.dumps({}))\n"
            )
        }
        d = skill(scripts=scripts)
        # Add a junk file under scripts/tests/ that would otherwise look like
        # a deficient script.
        tests_dir = d / "scripts" / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_doit.py").write_text("def test_x(): pass\n")
        opps = recommend(d)
        assert all("tests/" not in o.get("where", "") for o in opps)


class TestCli:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_help_works(self) -> None:
        result = self._run("--help")
        assert result.returncode == 0

    def test_default_text_output(self, skill: SkillFactory) -> None:
        result = self._run(str(skill()))
        assert result.returncode == 0

    def test_json_output(self, skill: SkillFactory) -> None:
        scripts = {"x.py": "print(1)\n"}
        d = skill(scripts=scripts)
        result = self._run(str(d), "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "opportunities" in data
        assert "summary" in data
        for o in data["opportunities"]:
            assert {"kind", "where", "title", "why", "suggestion", "severity"} <= set(o.keys())
