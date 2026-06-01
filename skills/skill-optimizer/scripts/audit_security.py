#!/usr/bin/env python3
"""Audit a Claude skill against the OWASP Agentic Skills Top 10.

Static, high-signal security scan of a skill's SKILL.md, references, and
bundled scripts. Findings map to the OWASP Agentic Skills Top 10
(https://owasp.org/www-project-agentic-skills-top-10/) and its companion
skill development guide.

What it checks (statically detectable):

    AST01 Malicious Skills        hidden bidi/invisible unicode in instructions
    AST02 Supply Chain            curl|wget piped to a shell; unpinned PEP 723 deps
    AST03 Over-Privileged Skills  unrestricted Bash() in allowed-tools, broad globs
    AST04 Insecure Metadata       hardcoded secrets / credentials / private keys
    AST05 Unsafe Deserialization  pickle, yaml.load, eval/exec, marshal
    AST06 Weak Isolation          shell=True, os.system, destructive fs calls

AST07 (Update Drift), AST08 (Poor Scanning), AST09 (No Governance), and
AST10 (Cross-Platform Reuse) are process/governance risks that can't be
caught by a static scan — see references/security.md for the human checks.

Usage:
    audit_security.py <skill-directory> [--json] [--exit-on-warn]

Default output is one finding per line, prefixed with FAIL:/WARN:/INFO: and
a machine-readable code in brackets. With --json, emits a structured object.
Each finding carries its `ast` category id. Echoed snippets are sanitized.

Exit codes:
    0   no FAIL-level findings (and no WARN if --exit-on-warn)
    1   one or more FAIL-level findings
    2   bad invocation (path missing, not a directory, etc.)
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import io
import json
from pathlib import Path
import re
import sys
import token as token_mod
import tokenize
from typing import Any

from skill_lib import _DANGEROUS_UNICODE, parse_frontmatter, sanitize_for_echo

SEVERITIES = {"fail", "warn", "info"}

# --- AST04: hardcoded secrets ------------------------------------------------
# High-confidence: the token shape itself is the secret, so a hit is reported
# even if it contains a word like EXAMPLE. These target real secret material,
# not env references like os.environ["ANTHROPIC_API_KEY"], the correct form.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    (
        "private key block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ),
)

# Lower-confidence: a credential-shaped assignment to a quoted literal. Run the
# allowlist over the value to drop obvious placeholders and env-var renderings.
_CREDENTIAL_ASSIGN_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|passwd|password|client[_-]?secret)\b"
    r"\s*[:=]\s*"
    r"""(?P<q>['"])(?P<val>[^\s'"]{12,})(?P=q)"""
)

# Assigned values that are obviously not real secrets — placeholders, env
# lookups rendered as strings, format markers. Skip these to cut false hits.
_SECRET_ALLOWLIST_RE = re.compile(
    r"(?i)(your[_-]?|example|placeholder|xxx+|<[^>]+>|\$\{?[A-Z_]+\}?|changeme|redacted|"
    r"\.\.\.|fake|dummy|sample|test[_-]?(?:key|token|secret))"
)

# --- AST02: supply chain -----------------------------------------------------
# Require a URL scheme between the fetch and the pipe: a real fetch-and-run
# always names a remote artifact (curl https://… | sh), which keeps prose that
# merely describes the anti-pattern (curl … | sh) from matching.
_CURL_PIPE_SHELL_RE = re.compile(
    r"(?:curl|wget)\b[^\n|]*://[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
    re.IGNORECASE,
)
_PEP723_BLOCK_RE = re.compile(r"# /// script\s*\n(.*?)\n# ///", re.DOTALL)
_DEP_LINE_RE = re.compile(r"""['"]([A-Za-z0-9][A-Za-z0-9._\-]*)\s*([^'"]*)['"]""")

# --- AST05: unsafe deserialization / dynamic eval ----------------------------
_UNSAFE_DESERIALIZE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pickle.load", re.compile(r"\bpickle\.(?:load|loads)\s*\(")),
    ("marshal.load", re.compile(r"\bmarshal\.(?:load|loads)\s*\(")),
    ("yaml.load without SafeLoader", re.compile(r"\byaml\.load\s*\((?![^)]*Safe)")),
    ("eval()", re.compile(r"(?<![A-Za-z0-9_.])eval\s*\(")),
    ("exec()", re.compile(r"(?<![A-Za-z0-9_.])exec\s*\(")),
)

# --- AST06: weak isolation / injection ---------------------------------------
_SHELL_INJECTION: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("subprocess with shell=True", re.compile(r"shell\s*=\s*True")),
    ("os.system()", re.compile(r"\bos\.system\s*\(")),
    ("os.popen()", re.compile(r"\bos\.popen\s*\(")),
)
_DANGEROUS_FS_RE = re.compile(r"\b(?:shutil\.rmtree|os\.removedirs)\s*\(")


@dataclass
class Finding:
    severity: str  # "fail" | "warn" | "info"
    code: str
    ast: str  # OWASP Agentic Skills Top 10 id, e.g. "AST03"
    message: str
    where: str | None = None

    def to_line(self) -> str:
        prefix = {"fail": "FAIL", "warn": "WARN", "info": "INFO"}.get(
            self.severity, self.severity.upper()
        )
        loc = f" {self.where}" if self.where else ""
        return f"{prefix}: [{self.code}] ({self.ast}){loc} — {self.message}"


def _strip_code_spans(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", "", text)
    return text


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# Token types whose text is data, not executable constructs. Blanking them
# before the AST05/AST06 regexes means `eval(`, `shell=True`, `os.system(` etc.
# only match real code — not the same text quoted inside a docstring, a comment,
# or a pattern definition (e.g. this auditor's own rule table).
_STRINGY_TOKENS = frozenset(
    t
    for t in (
        token_mod.STRING,
        token_mod.COMMENT,
        getattr(token_mod, "FSTRING_START", -1),
        getattr(token_mod, "FSTRING_MIDDLE", -1),
        getattr(token_mod, "FSTRING_END", -1),
    )
    if t != -1
)


def _blank_strings_and_comments(content: str) -> str:
    """Return content with string/comment token text replaced by spaces.

    Offsets and line numbers are preserved (only non-newline chars are blanked),
    so findings still report accurate locations. Falls back to the raw content
    if the source can't be tokenized.
    """
    lines = content.splitlines(keepends=True)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(content).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return content
    chars = [list(line) for line in lines]
    for tok in toks:
        if tok.type not in _STRINGY_TOKENS:
            continue
        (sr, sc), (er, ec) = tok.start, tok.end
        for row in range(sr, er + 1):
            line = chars[row - 1]
            lo = sc if row == sr else 0
            hi = ec if row == er else len(line)
            for col in range(lo, min(hi, len(line))):
                if line[col] != "\n":
                    line[col] = " "
    return "".join("".join(line) for line in chars)


# Test fixtures intentionally embed mock secrets and dangerous-looking sample
# code; build artifacts aren't shipped. Skip both so real findings aren't buried.
_EXCLUDED_DIRS = frozenset({"tests", "__pycache__", ".pytest_cache", ".ruff_cache"})


def _is_excluded(path: Path, skill_dir: Path) -> bool:
    return any(part in _EXCLUDED_DIRS for part in path.relative_to(skill_dir).parts)


def audit(skill_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [
            Finding(
                "fail",
                "security.skill-md.missing",
                "AST08",
                f"SKILL.md not found at {sanitize_for_echo(skill_md)}",
            )
        ]

    skill_text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(skill_text)

    _check_allowed_tools(fm, findings)
    _check_hidden_unicode(body, skill_md, findings)

    py_files = sorted(
        p
        for p in (skill_dir / "scripts").rglob("*.py")
        if p.is_file() and not _is_excluded(p, skill_dir)
    )
    ref_files = sorted(
        p for p in (skill_dir / "references").rglob("*.md") if not _is_excluded(p, skill_dir)
    )
    text_files = [skill_md, *ref_files]

    # Secrets can hide in any shipped file. Fetch-and-run as an *instruction*
    # only matters in agent-readable text — inside a script it's a subprocess
    # string the shell-injection check already covers, so scanning .py for it
    # just trips on comments that document the pattern.
    for path in [*text_files, *py_files]:
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        content = path.read_text(encoding="utf-8", errors="replace")
        _check_secrets(content, rel, findings)
        if path.suffix != ".py":
            _check_curl_pipe_shell(content, rel, findings)

    for path in py_files:
        rel = path.relative_to(skill_dir)
        content = path.read_text(encoding="utf-8", errors="replace")
        # Code-construct checks run on source with strings/comments blanked, so
        # they match real calls rather than the same text quoted in a docstring.
        code = _blank_strings_and_comments(content)
        _check_unsafe_deserialization(code, rel, findings)
        _check_shell_injection(code, rel, findings)
        _check_dangerous_fs(code, rel, findings)
        # PEP 723 deps live inside a comment block, so scan the raw content.
        _check_pep723_pins(content, rel, findings)

    if not findings:
        findings.append(
            Finding(
                "info",
                "security.ok",
                "AST08",
                "no statically-detectable issues found. Process risks "
                "(AST07/09/10) still need the human checks in references/security.md.",
            )
        )
    return findings


def _check_allowed_tools(fm: dict[str, Any] | None, findings: list[Finding]) -> None:
    """AST03: over-privileged skills via unrestricted allowed-tools."""
    if not isinstance(fm, dict):
        return
    raw = fm.get("allowed-tools")
    if not raw or not isinstance(raw, str):
        return
    tokens = re.findall(r"[A-Za-z_]+(?:\([^)]*\))?", raw)
    for tok in tokens:
        name = tok.split("(", 1)[0]
        if name not in {"Bash", "Shell"}:
            continue
        if "(" not in tok:
            findings.append(
                Finding(
                    "warn",
                    "security.tools.unrestricted-bash",
                    "AST03",
                    f"allowed-tools grants {tok!r} with no command filter. Scope it, "
                    f"e.g. Bash(python3 *), so the skill can't run arbitrary shell.",
                    "SKILL.md",
                )
            )
        else:
            inner = tok[tok.index("(") + 1 : -1].strip()
            if inner in {"*", ":*", "* *"} or inner.startswith("*"):
                findings.append(
                    Finding(
                        "warn",
                        "security.tools.broad-bash-glob",
                        "AST03",
                        f"allowed-tools pattern {tok!r} is an unrestricted wildcard, "
                        f"equivalent to full shell access. Pin the command prefix.",
                        "SKILL.md",
                    )
                )


def _check_hidden_unicode(body: str, skill_md: Path, findings: list[Finding]) -> None:
    """AST01: malicious skills hiding instructions via invisible/bidi unicode."""
    seen: set[str] = set()
    for ch in body:
        if ch in _DANGEROUS_UNICODE and ch not in seen:
            seen.add(ch)
            findings.append(
                Finding(
                    "warn",
                    "security.body.hidden-unicode",
                    "AST01",
                    f"SKILL.md contains invisible/bidi-override character "
                    f"U+{ord(ch):04X}. Agent-readable instructions must be visible; "
                    f"hidden text is a prompt-injection vector (Trojan Source).",
                    "SKILL.md",
                )
            )


def _check_secrets(content: str, rel: Path, findings: list[Finding]) -> None:
    """AST04: hardcoded secrets / credentials in metadata or bundled files."""

    def report(label: str, m: re.Match[str]) -> None:
        findings.append(
            Finding(
                "fail",
                "security.secret.hardcoded",
                "AST04",
                f"possible {label} embedded: {sanitize_for_echo(m.group(0), 48)!r}. "
                f"Move secrets to environment variables; never ship them in a skill.",
                f"{rel}:{_line_of(content, m.start())}",
            )
        )

    for label, pat in _SECRET_PATTERNS:
        for m in pat.finditer(content):
            report(label, m)
    for m in _CREDENTIAL_ASSIGN_RE.finditer(content):
        if not _SECRET_ALLOWLIST_RE.search(m.group("val")):
            report("hardcoded credential assignment", m)


def _check_curl_pipe_shell(content: str, rel: Path, findings: list[Finding]) -> None:
    """AST02: supply-chain — fetching and executing remote code in one step."""
    findings.extend(
        Finding(
            "warn",
            "security.exec.curl-pipe-shell",
            "AST02",
            f"pipes a remote download straight into a shell "
            f"({sanitize_for_echo(m.group(0), 60)!r}). Pin and verify the artifact "
            f"(checksum/signature) before executing it.",
            f"{rel}:{_line_of(content, m.start())}",
        )
        for m in _CURL_PIPE_SHELL_RE.finditer(content)
    )


def _check_unsafe_deserialization(content: str, rel: Path, findings: list[Finding]) -> None:
    """AST05: unsafe deserialization and dynamic code execution."""
    findings.extend(
        Finding(
            "warn",
            "security.script.unsafe-deserialization",
            "AST05",
            f"{label} can execute attacker-controlled data. Use a safe parser "
            f"(json, yaml.safe_load) or validate input before deserializing.",
            f"{rel}:{_line_of(content, m.start())}",
        )
        for label, pat in _UNSAFE_DESERIALIZE
        for m in pat.finditer(content)
    )


def _check_shell_injection(code: str, rel: Path, findings: list[Finding]) -> None:
    """AST06: weak isolation — shell-injection-prone calls (code constructs)."""
    findings.extend(
        Finding(
            "warn",
            "security.script.shell-injection",
            "AST06",
            f"{label} invites command injection. Prefer subprocess with an "
            f"argument list and shell=False.",
            f"{rel}:{_line_of(code, m.start())}",
        )
        for label, pat in _SHELL_INJECTION
        for m in pat.finditer(code)
    )


def _check_dangerous_fs(code: str, rel: Path, findings: list[Finding]) -> None:
    """AST06: destructive filesystem calls (`shutil.rmtree`, `os.removedirs`).

    Runs on string-blanked source so it matches real calls, not the same names
    quoted in docs. Shell `rm -rf` is left to the shell-injection check, which
    already flags the os.system/shell=True call that would carry it.
    """
    findings.extend(
        Finding(
            "warn",
            "security.script.dangerous-fs",
            "AST06",
            f"destructive filesystem call "
            f"({sanitize_for_echo(m.group(0), 40)!r}). Confirm the target is "
            f"validated and inside the skill's working area; add --dry-run.",
            f"{rel}:{_line_of(code, m.start())}",
        )
        for m in _DANGEROUS_FS_RE.finditer(code)
    )


def _check_pep723_pins(content: str, rel: Path, findings: list[Finding]) -> None:
    """AST02: supply-chain — unpinned third-party dependencies."""
    for block in _PEP723_BLOCK_RE.finditer(content):
        inner = block.group(1)
        if "dependencies" not in inner:
            continue
        for dep in _DEP_LINE_RE.finditer(inner):
            name, spec = dep.group(1), dep.group(2).strip()
            if name in {"dependencies", "requires-python"}:
                continue
            if "==" not in spec:
                line = _line_of(content, block.start() + dep.start())
                findings.append(
                    Finding(
                        "warn",
                        "security.deps.unpinned",
                        "AST02",
                        f"dependency {name!r} is not pinned to an exact version "
                        f"({sanitize_for_echo(spec, 24)!r} lacks '=='). Pin it so a "
                        f"compromised upstream release can't be pulled in silently.",
                        f"{rel}:{line}",
                    )
                )


def _emit_text(findings: list[Finding]) -> None:
    for f in findings:
        print(f.to_line())


def _emit_json(skill_dir: Path, findings: list[Finding]) -> None:
    by_severity: Counter[str] = Counter(f.severity for f in findings if f.code != "security.ok")
    by_ast: Counter[str] = Counter(f.ast for f in findings if f.code != "security.ok")
    payload = {
        "skill_dir": str(skill_dir),
        "findings": [asdict(f) for f in findings],
        "summary": {
            "total": sum(by_severity.values()),
            "by_severity": dict(by_severity),
            "by_ast": dict(by_ast),
        },
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a Claude skill against the OWASP Agentic Skills Top 10.",
        epilog="Examples:\n"
        "  audit_security.py ~/.claude/skills/my-skill\n"
        "  audit_security.py ./skill --json\n"
        "  audit_security.py ./skill --exit-on-warn   # for CI / pre-commit\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("skill_dir", help="Path to the skill directory")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON on stdout.")
    parser.add_argument(
        "--exit-on-warn",
        action="store_true",
        help="Return non-zero exit if any WARN-level findings are present.",
    )
    args = parser.parse_args(argv)

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    if not skill_dir.exists():
        print(f"audit_security: path does not exist: {skill_dir}", file=sys.stderr)
        return 2
    if not skill_dir.is_dir():
        print(f"audit_security: not a directory: {skill_dir}", file=sys.stderr)
        return 2

    findings = audit(skill_dir)
    if args.as_json:
        _emit_json(skill_dir, findings)
    else:
        _emit_text(findings)

    fails = sum(1 for f in findings if f.severity == "fail")
    warns = sum(1 for f in findings if f.severity == "warn")
    if fails:
        return 1
    if args.exit_on_warn and warns:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
