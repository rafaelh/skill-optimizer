import json
from pathlib import Path
import subprocess
import sys

from analyze_skill import analyze
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "analyze_skill.py"


@pytest.fixture
def skill(tmp_path: Path):
    def _make(name: str = "demo", description: str | None = None, body: str = "# Demo\n\nbody\n"):
        if description is None:
            description = (
                "Use this skill when the user wants to validate or audit a "
                "Claude skill's SKILL.md file. Trigger when the user mentions "
                "skill optimization, skill validation, or skill activation issues."
            )
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}")
        return d

    return _make


class TestAnalyze:
    def test_clean_skill_has_only_info_stats(self, skill):
        issues = analyze(skill())
        warns = [i for i in issues if i.severity == "warn"]
        assert warns == [], [i.message for i in warns]

    def test_declarative_description(self, skill):
        d = skill(
            description="This skill processes CSV files quickly and reliably across many cases."
        )
        issues = analyze(d)
        assert any(i.code == "declarative-description" for i in issues)

    def test_description_missing_when(self, skill):
        d = skill(
            description="Helps process CSV files quickly across many cases for various users."
        )
        issues = analyze(d)
        assert any(i.code == "description-no-when" for i in issues)

    def test_description_too_short_quality(self, skill):
        d = skill(description="Use this skill when handling CSVs.")
        issues = analyze(d)
        assert any(i.code == "description-too-short-quality" for i in issues)

    def test_body_over_500_lines(self, skill):
        body = "# Heading\n\n" + ("filler line " * 5 + "\n") * 510
        d = skill(body=body)
        issues = analyze(d)
        assert any(i.code == "body-too-many-lines" for i in issues)

    def test_body_too_many_tokens(self, skill):
        body = "x " * 12000
        d = skill(body=body)
        issues = analyze(d)
        assert any(i.code == "body-too-many-tokens" for i in issues)

    def test_generic_filler(self, skill):
        body = "# Demo\n\nFollow best practices and handle errors appropriately.\n"
        d = skill(body=body)
        issues = analyze(d)
        assert any(i.code == "generic-filler" for i in issues)

    def test_reference_without_load_trigger(self, skill):
        body = "# Demo\n\nSee [details](references/details.md).\n" + "padding\n" * 100
        d = skill(body=body)
        issues = analyze(d)
        assert any(i.code == "reference-no-load-trigger" for i in issues)

    def test_reference_with_load_trigger_no_warning(self, skill):
        body = (
            "# Demo\n\nRead [details](references/details.md) when the user "
            "asks for advanced behaviour.\n" + "padding\n" * 100
        )
        d = skill(body=body)
        issues = analyze(d)
        assert all(i.code != "reference-no-load-trigger" for i in issues)

    def test_stats_present(self, skill):
        issues = analyze(skill())
        stats = [i for i in issues if i.code == "stats"]
        assert len(stats) == 1


class TestSanitization:
    def test_no_ansi_in_messages(self, skill):
        # Description with ANSI should not bleed into output messages.
        body = "# Demo\n\n\x1b[31mevil\x1b[0m content\n"
        d = skill(body=body)
        issues = analyze(d)
        for issue in issues:
            assert "\x1b" not in issue.message


class TestCli:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_help_works(self):
        result = self._run("--help")
        assert result.returncode == 0

    def test_default_text_output(self, skill):
        result = self._run(str(skill()))
        assert result.returncode == 0
        assert "INFO:" in result.stdout or "stats" in result.stdout.lower()

    def test_json_output(self, skill):
        result = self._run(str(skill()), "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "issues" in data
        assert "stats" in data
        assert "body_lines" in data["stats"]
        assert "approx_tokens" in data["stats"]

    def test_json_issue_has_required_fields(self, skill):
        d = skill(description="This skill handles CSVs.")  # triggers declarative
        result = self._run(str(d), "--json")
        data = json.loads(result.stdout)
        for issue in data["issues"]:
            assert {"severity", "code", "message"} <= set(issue.keys())

    def test_exit_on_warn_flag(self, skill):
        d = skill(description="This skill handles CSVs.")  # warns
        ok_default = self._run(str(d))
        assert ok_default.returncode == 0
        warn_strict = self._run(str(d), "--exit-on-warn")
        assert warn_strict.returncode == 1
