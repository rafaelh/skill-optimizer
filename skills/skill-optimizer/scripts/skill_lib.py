"""Shared helpers for skill-optimizer scripts.

Imported by validate_skill.py, analyze_skill.py, and recommend_scripts.py.
Stdlib only.
"""

from __future__ import annotations

import re
from typing import Any

# Terminal escape sequences (ESC-initiated). Catches the common forms;
# any stray ESC byte the regex misses still gets escaped to `\x1b` by the
# per-char fallback in sanitize_for_echo, so this is about output cleanliness
# rather than security.
_ANSI_RE = re.compile(
    r"\x1b"
    r"(?:"
    r"\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"  # CSI
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC (BEL or ST terminator)
    r"|[PX^_][^\x1b]*\x1b\\"  # DCS / SOS / PM / APC
    r"|[NO]"  # SS2 / SS3
    r"|[\x40-\x5f]"  # other single-byte escapes
    r")"
)

# Unicode codepoints that aren't visible but can manipulate or hide content.
# Escaped (not stripped) so they remain inspectable in the sanitized output.
_DANGEROUS_UNICODE = frozenset(
    {
        # Bidi overrides + isolates (Trojan Source, RLO file-name attacks).
        "‪",
        "‫",
        "‬",
        "‭",
        "‮",
        "⁦",
        "⁧",
        "⁨",
        "⁩",
        # Invisible width / spacing. ZWJ (U+200D) is intentionally NOT here —
        # it's load-bearing inside emoji sequences.
        "\u200b",
        "‌",
        "⁠",
        "﻿",
    }
)

_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_NESTED_RE = re.compile(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$")
_FOLDED_MARKERS = (">", "|", ">-", "|-", ">+", "|+")
_FENCE_END_RE = re.compile(r"\r?\n---(?:\r?\n|\Z)")


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return (frontmatter_dict, body) or (None, full_text) if no frontmatter.

    Handles a small subset of YAML sufficient for SKILL.md frontmatter:
    scalar key/value, single-level nesting, folded (>) and block (|) scalars.
    Quoted scalars have their quotes stripped.

    Strict on fence shape: the opening must be `---` followed by a newline,
    and the closing must be `---` on its own line (or at end of file). A
    body line beginning with `--- something` is body, not a closing fence.
    """
    if not text.startswith("---"):
        return None, text
    if len(text) < 4 or text[3] not in ("\n", "\r"):
        # Opening fence must be on a line by itself.
        return None, text
    match = _FENCE_END_RE.search(text, 3)
    if not match:
        return None, text
    fm_text = text[3 : match.start()].strip("\r\n")
    body = text[match.end() :]
    return _parse_simple_yaml(fm_text), body


def _collect_indented(lines: list[str], start: int) -> tuple[list[str], int]:
    """Return (non-empty indented lines, next index) from *start*."""
    collected: list[str] = []
    i = start
    while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
        if lines[i].strip():
            collected.append(lines[i])
        i += 1
    return collected, i


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = _KEY_RE.match(line)
        if not m:
            i += 1
            continue
        key, value = m.group(1), m.group(2).rstrip()
        if value in _FOLDED_MARKERS:
            indented, i = _collect_indented(lines, i + 1)
            sep = " " if value.startswith(">") else "\n"
            result[key] = sep.join(ln.strip() for ln in indented)
            continue
        if value == "":
            nested: dict[str, str] = {}
            indented, i = _collect_indented(lines, i + 1)
            for ln in indented:
                nm = _NESTED_RE.match(ln)
                if nm:
                    nested[nm.group(1)] = _strip_quotes(nm.group(2).strip())
            result[key] = nested or ""
            continue
        result[key] = _strip_quotes(value)
        i += 1
    return result


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and (
        (value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")
    ):
        return value[1:-1]
    return value


def sanitize_for_echo(value: Any, max_len: int = 200) -> str:
    """Make value safe to print or include in JSON output.

    Untrusted SKILL.md content may contain ANSI escapes, control characters,
    or unusual unicode that could confuse a downstream agent or terminal.

    This pass strips terminal escape sequences (CSI, OSC, DCS, single-byte
    ESC), escapes C0 control characters as `\\xNN`, and escapes a small set
    of invisible / bidi-override Unicode codepoints (incl. U+202E "RLO" used
    in Trojan Source attacks) as `\\uXXXX`. Newline and tab pass through.
    ZWJ (U+200D) passes through to keep emoji sequences intact. Length is
    capped at max_len.

    Stdlib only. Not designed to be cryptographic. Don't pipe adversarial
    input to a destination where stricter sanitization matters.
    """
    if not isinstance(value, str):
        value = str(value)
    value = _ANSI_RE.sub("", value)
    cleaned: list[str] = []
    for ch in value:
        cp = ord(ch)
        if ch in {"\n", "\t"}:
            cleaned.append(ch)
        elif cp < 0x20 or cp == 0x7F:
            cleaned.append(f"\\x{cp:02x}")
        elif ch in _DANGEROUS_UNICODE:
            cleaned.append(f"\\u{cp:04x}")
        else:
            cleaned.append(ch)
    out = "".join(cleaned)
    if len(out) > max_len:
        out = out[: max_len - 1] + "…"
    return out
