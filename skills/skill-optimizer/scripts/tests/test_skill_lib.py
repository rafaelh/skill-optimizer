from skill_lib import (
    parse_frontmatter,
    sanitize_for_echo,
)


class TestParseFrontmatter:
    def test_simple(self) -> None:
        text = "---\nname: foo\ndescription: bar\n---\nbody\n"
        fm, body = parse_frontmatter(text)
        assert fm == {"name": "foo", "description": "bar"}
        assert body == "body\n"

    def test_no_frontmatter(self) -> None:
        text = "no frontmatter here\n"
        fm, body = parse_frontmatter(text)
        assert fm is None
        assert body == text

    def test_unclosed_frontmatter(self) -> None:
        text = "---\nname: foo\nbody without closing\n"
        fm, body = parse_frontmatter(text)
        assert fm is None
        assert body == text

    def test_quoted_values_stripped(self) -> None:
        text = "---\nname: \"quoted\"\ndescription: 'single'\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm == {"name": "quoted", "description": "single"}

    def test_folded_scalar(self) -> None:
        text = "---\ndescription: >\n  line one\n  line two\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm == {"description": "line one line two"}

    def test_block_scalar(self) -> None:
        text = "---\ndescription: |\n  line one\n  line two\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm == {"description": "line one\nline two"}

    def test_nested_mapping(self) -> None:
        text = "---\nmetadata:\n  author: rafe\n  version: 1\n---\n"
        fm, _ = parse_frontmatter(text)
        assert fm == {"metadata": {"author": "rafe", "version": "1"}}


class TestParseFrontmatterStrictFence:
    def test_closing_fence_with_trailing_text_not_matched(self) -> None:
        text = "---\nname: x\n--- this is body, not a fence\n---\nactual body\n"
        fm, body = parse_frontmatter(text)
        # The first `---` followed by junk is NOT a closing fence; the next
        # `---` on its own line is.
        assert fm == {"name": "x"}
        assert body == "actual body\n"

    def test_opening_fence_with_trailing_text_rejected(self) -> None:
        text = "--- foo\nname: x\n---\nbody\n"
        fm, body = parse_frontmatter(text)
        assert fm is None
        assert body == text

    def test_handles_crlf_line_endings(self) -> None:
        text = "---\r\nname: x\r\n---\r\nbody\r\n"
        fm, body = parse_frontmatter(text)
        assert fm == {"name": "x"}
        assert "body" in body

    def test_closing_fence_at_eof(self) -> None:
        text = "---\nname: x\n---"
        fm, body = parse_frontmatter(text)
        assert fm == {"name": "x"}
        assert body == ""


class TestSanitizeForEcho:
    def test_plain_text_unchanged(self) -> None:
        assert sanitize_for_echo("hello world") == "hello world"

    def test_strips_ansi_escapes(self) -> None:
        assert sanitize_for_echo("\x1b[31mred\x1b[0m text") == "red text"

    def test_escapes_control_characters(self) -> None:
        assert "\\x07" in sanitize_for_echo("bell\x07inside")

    def test_preserves_tab(self) -> None:
        assert sanitize_for_echo("col1\tcol2") == "col1\tcol2"

    def test_truncates_long_input(self) -> None:
        out = sanitize_for_echo("x" * 500, max_len=50)
        assert len(out) <= 50

    def test_truncation_marker(self) -> None:
        out = sanitize_for_echo("x" * 500, max_len=50)
        assert out.endswith(("…", "..."))

    def test_handles_non_string(self) -> None:
        assert sanitize_for_echo(42) == "42"
        assert sanitize_for_echo(None) == "None"

    def test_strips_prompt_injection_marker_examples(self) -> None:
        # We don't redact <system>, but we do escape NUL and similar.
        text = "before\x00after"
        out = sanitize_for_echo(text)
        assert "\x00" not in out
        assert "\\x00" in out

    def test_escapes_bidi_override_rlo(self) -> None:
        # Trojan-source style: RLO between visible text reverses rendering.
        text = "evil‮txt.exe"
        out = sanitize_for_echo(text)
        assert "‮" not in out
        assert "\\u202e" in out

    def test_escapes_all_bidi_overrides_and_isolates(self) -> None:
        for cp in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069):
            ch = chr(cp)
            out = sanitize_for_echo(f"x{ch}y")
            assert ch not in out
            assert f"\\u{cp:04x}" in out

    def test_escapes_invisible_width_chars(self) -> None:
        for cp in (0x200B, 0x200C, 0x2060, 0xFEFF):
            ch = chr(cp)
            out = sanitize_for_echo(f"x{ch}y")
            assert ch not in out

    def test_preserves_zwj_for_emoji(self) -> None:
        # U+200D (ZWJ) is load-bearing inside emoji sequences.
        family = "👨‍👩‍👧"
        out = sanitize_for_echo(family)
        assert "‍" in out

    def test_strips_osc_escape_sequence(self) -> None:
        # OSC: ESC ] 0 ; <title> BEL — used to set terminal title.
        text = "before\x1b]0;malicious-title\x07after"
        out = sanitize_for_echo(text)
        assert "\x1b" not in out
        assert "\x07" not in out
        assert "before" in out and "after" in out

    def test_strips_single_byte_esc_sequence(self) -> None:
        # ESC M = reverse line feed.
        text = "x\x1bMy"
        out = sanitize_for_echo(text)
        assert "\x1b" not in out

    def test_preserves_non_ascii_letters(self) -> None:
        text = "café 日本語"
        out = sanitize_for_echo(text)
        assert out == text
