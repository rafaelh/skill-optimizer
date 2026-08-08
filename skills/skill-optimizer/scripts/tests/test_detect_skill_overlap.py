from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "detect_skill_overlap.py"

SkillFactory = Callable[[str, str], Path]

OVERLAPPING = (
    "Audit, optimize, and validate agent skills whose bundled scripts are Python. "
    "Use when the user wants to review a SKILL.md, check frontmatter, or audit a skills directory."
)


@pytest.fixture
def skill(tmp_path: Path) -> SkillFactory:
    def _make(name: str, description: str) -> Path:
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
            encoding="utf-8",
        )
        return d

    return _make


def run_cli(*args: str, seed: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if seed is not None:
        env["PYTHONHASHSEED"] = seed
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )


class TestCli:
    def test_help_works(self) -> None:
        assert run_cli("--help").returncode == 0

    def test_no_overlap_exits_zero(self, skill: SkillFactory, tmp_path: Path) -> None:
        skill("pdf-tool", "Use this skill when the user wants to rotate PDF documents.")
        skill("csv-tool", "Use this skill when the user wants to parse CSV files.")
        result = run_cli(str(tmp_path), "--json")
        assert result.returncode == 0
        assert json.loads(result.stdout)["summary"]["pairs_above_threshold"] == 0

    def test_findings_alone_do_not_fail(self, skill: SkillFactory, tmp_path: Path) -> None:
        """Overlap is a successful result; exit 1 is reserved for bad invocation."""
        skill("skill-a", OVERLAPPING)
        skill("skill-b", OVERLAPPING)
        result = run_cli(str(tmp_path), "--json")
        assert result.returncode == 0
        assert json.loads(result.stdout)["summary"]["pairs_above_threshold"] >= 1

    def test_exit_on_warn_opts_into_failure(self, skill: SkillFactory, tmp_path: Path) -> None:
        skill("skill-a", OVERLAPPING)
        skill("skill-b", OVERLAPPING)
        assert run_cli(str(tmp_path), "--json", "--exit-on-warn").returncode == 1

    def test_exit_on_warn_stays_zero_without_findings(
        self, skill: SkillFactory, tmp_path: Path
    ) -> None:
        skill("pdf-tool", "Use this skill when the user wants to rotate PDF documents.")
        skill("csv-tool", "Use this skill when the user wants to parse CSV files.")
        assert run_cli(str(tmp_path), "--json", "--exit-on-warn").returncode == 0

    def test_missing_target_exits_two(self, tmp_path: Path) -> None:
        assert run_cli(str(tmp_path / "nope"), "--json").returncode == 2

    def test_empty_parent_exits_three(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert run_cli(str(empty), "--json").returncode == 3


class TestDeterminism:
    def test_shared_keywords_stable_across_hash_seeds(
        self, skill: SkillFactory, tmp_path: Path
    ) -> None:
        """Ties used to break on set iteration order, so identical input gave
        different keywords per run. PYTHONHASHSEED forces that variation."""
        skill("skill-a", OVERLAPPING)
        skill("skill-b", OVERLAPPING)
        results = {
            json.dumps(
                json.loads(run_cli(str(tmp_path), "--json", seed=seed).stdout)["pairs"][0][
                    "shared_keywords"
                ]
            )
            for seed in ("0", "1", "42", "12345")
        }
        assert len(results) == 1
