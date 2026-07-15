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

    def test_scripts_dir_is_ignored(self, skill: SkillFactory) -> None:
        # Per-script quality is delegated to validate_agent_tool.py; this
        # script should not produce any per-script findings even when scripts/
        # contains a deficient file.
        scripts = {"doit.py": "import sys\nprint(sys.argv[1])\n"}
        d = skill(scripts=scripts)
        opps = recommend(d)
        assert all(o["kind"] != "recommend.script.missing-argparse" for o in opps)
        assert all(o["kind"] != "recommend.script.missing-json" for o in opps)
        assert all(o["kind"] != "recommend.script.missing-pep723" for o in opps)


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
        body = "# Demo\n\n```bash\n" + "\n".join(f"echo {i}" for i in range(8)) + "\n```\n"
        d = skill(body=body)
        result = self._run(str(d), "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "opportunities" in data
        assert "summary" in data
        for o in data["opportunities"]:
            assert {"kind", "where", "title", "why", "suggestion", "severity"} <= set(o.keys())

    def test_format_json_flag(self, skill: SkillFactory) -> None:
        result = self._run(str(skill()), "--format", "json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "opportunities" in data

    def test_quiet_flag_accepted(self, skill: SkillFactory) -> None:
        result = self._run(str(skill()), "--quiet")
        assert result.returncode == 0
