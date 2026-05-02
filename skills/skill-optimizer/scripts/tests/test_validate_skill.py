import json
import subprocess
import sys
from pathlib import Path

import pytest
from validate_skill import validate

SCRIPT = Path(__file__).resolve().parent.parent / "validate_skill.py"


@pytest.fixture
def skill(tmp_path: Path):
    """Build a minimal valid skill at tmp_path / <name>."""

    def _make(
        name: str = "demo",
        description: str | None = None,
        body: str = "# Demo\n",
        extra_frontmatter: str = "",
    ) -> Path:
        if description is None:
            description = (
                "Use this skill when the user wants to validate or audit a "
                "Claude skill's SKILL.md file. Trigger when the user mentions "
                "skill optimization, skill validation, or skill activation issues."
            )
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        fm = f"name: {name}\ndescription: {description}\n{extra_frontmatter}"
        (skill_dir / "SKILL.md").write_text(f"---\n{fm}---\n{body}")
        return skill_dir

    return _make


class TestValidate:
    def test_passing_skill_has_no_failures(self, skill):
        issues = validate(skill())
        assert all(i.severity != "fail" for i in issues), [i.message for i in issues]

    def test_missing_skill_md(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        issues = validate(empty)
        codes = {i.code for i in issues}
        assert "skill-md-missing" in codes
        assert any(i.severity == "fail" for i in issues)

    def test_missing_frontmatter(self, tmp_path):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "SKILL.md").write_text("# Just a heading\nno frontmatter\n")
        issues = validate(d)
        assert any(i.code == "frontmatter-missing" for i in issues)

    def test_missing_name(self, skill):
        d = skill(name="x", extra_frontmatter="")
        # Force missing name by rewriting:
        text = (d / "SKILL.md").read_text().replace("name: x\n", "")
        (d / "SKILL.md").write_text(text)
        issues = validate(d)
        assert any(i.code == "name-missing" for i in issues)

    def test_name_mismatches_directory(self, skill):
        d = skill(name="actual")
        text = (d / "SKILL.md").read_text().replace("name: actual", "name: different")
        (d / "SKILL.md").write_text(text)
        issues = validate(d)
        assert any(i.code == "name-mismatch-dir" for i in issues)

    def test_name_bad_format(self, skill):
        d = skill(name="demo")
        text = (d / "SKILL.md").read_text().replace("name: demo", "name: Demo--Bad")
        (d / "SKILL.md").write_text(text)
        issues = validate(d)
        assert any(i.code == "name-bad-format" for i in issues)

    def test_description_too_long(self, skill):
        d = skill(description="x" * 1100)
        issues = validate(d)
        assert any(i.code == "description-too-long" for i in issues)

    def test_description_too_short(self, skill):
        d = skill(description="short")
        issues = validate(d)
        assert any(i.code == "description-too-short" for i in issues)

    def test_unknown_frontmatter_key(self, skill):
        d = skill(extra_frontmatter="bogus: value\n")
        issues = validate(d)
        assert any(i.code == "unknown-frontmatter-key" for i in issues)

    def test_broken_reference(self, skill):
        body = "# Body\n\nSee [missing](references/does-not-exist.md).\n"
        d = skill(body=body)
        issues = validate(d)
        assert any(i.code == "broken-reference" for i in issues)

    def test_placeholder_script_path_not_flagged(self, skill):
        body = "# Body\n\nExtract into `scripts/<name>.py` to keep things tidy.\n"
        d = skill(body=body)
        issues = validate(d)
        assert all(i.code != "broken-script-reference" for i in issues)

    def test_reference_outside_skill_dir_not_probed(self, skill):
        # Crafted markdown linking to a path outside the skill — must NOT be
        # flagged or probed via filesystem.
        body = "# Body\n\nSee [oops](../../../../etc/passwd.md).\n"
        d = skill(body=body)
        issues = validate(d)
        assert all(i.code != "broken-reference" for i in issues)

    def test_absolute_path_reference_skipped(self, skill):
        body = "# Body\n\nSee [absolute](/etc/hosts.md).\n"
        d = skill(body=body)
        issues = validate(d)
        assert all(i.code != "broken-reference" for i in issues)

    def test_utf8_skill_md_handled(self, skill):
        # Non-ASCII characters must round-trip cleanly.
        body = "# Demo\n\nUnicode: café 日本語 🎉\n"
        d = skill(body=body)
        issues = validate(d)
        assert all(i.severity != "fail" for i in issues)


class TestSanitization:
    def test_echoed_name_strips_ansi_and_control_chars(self, skill, capsys):
        # A SKILL.md with ANSI escapes in the name should not propagate them.
        d = skill(name="x")
        text = (d / "SKILL.md").read_text().replace("name: x", "name: \x1b[31mevil\x1b[0m")
        (d / "SKILL.md").write_text(text)
        issues = validate(d)
        # Find an issue mentioning the name, ensure no ANSI in its message.
        for issue in issues:
            assert "\x1b" not in issue.message


class TestCli:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8"
        )

    def test_help_works(self):
        result = self._run("--help")
        assert result.returncode == 0
        assert "validate" in result.stdout.lower() or "skill" in result.stdout.lower()

    def test_default_text_output_passing(self, skill):
        result = self._run(str(skill()))
        assert result.returncode == 0
        assert "fail" in result.stdout.lower()

    def test_default_text_output_failing(self, tmp_path):
        empty = tmp_path / "x"
        empty.mkdir()
        result = self._run(str(empty))
        assert result.returncode == 1

    def test_json_output_passing(self, skill):
        result = self._run(str(skill()), "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["summary"]["fail"] == 0
        assert data["summary"]["ok"] is True
        assert isinstance(data["issues"], list)

    def test_json_output_failing(self, tmp_path):
        empty = tmp_path / "x"
        empty.mkdir()
        result = self._run(str(empty), "--json")
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["summary"]["fail"] >= 1
        assert data["summary"]["ok"] is False
        assert any(i["code"] == "skill-md-missing" for i in data["issues"])

    def test_json_each_issue_has_required_fields(self, tmp_path):
        empty = tmp_path / "x"
        empty.mkdir()
        result = self._run(str(empty), "--json")
        data = json.loads(result.stdout)
        for issue in data["issues"]:
            assert {"severity", "code", "message"} <= set(issue.keys())
            assert issue["severity"] in ("fail", "warn")

    def test_not_a_directory_exits_2(self, tmp_path):
        not_dir = tmp_path / "file.md"
        not_dir.write_text("hi")
        result = self._run(str(not_dir))
        assert result.returncode == 2
