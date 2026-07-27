// agent-tool: false
/**
 * Shared helpers for skill-optimizer-ts scripts.
 *
 * Imported by validate_skill.ts, analyze_skill.ts, recommend_scripts.ts, and others.
 * Node built-ins only.
 */

export type FrontmatterValue = string | Record<string, string>;
export type Frontmatter = Record<string, FrontmatterValue>;

// Terminal escape sequences (ESC-initiated). Catches the common forms; any
// stray ESC byte the regex misses still gets escaped to `\x1b` by the
// per-char fallback below, so this is about output cleanliness rather than
// security.
const ANSI_RE = new RegExp(
  "\\x1b(?:" +
    "\\[[\\x30-\\x3f]*[\\x20-\\x2f]*[\\x40-\\x7e]" + // CSI
    "|\\][^\\x07\\x1b]*(?:\\x07|\\x1b\\\\)" + // OSC (BEL or ST terminator)
    "|[PX^_][^\\x1b]*\\x1b\\\\" + // DCS / SOS / PM / APC
    "|[NO]" + // SS2 / SS3
    "|[\\x40-\\x5f]" + // other single-byte escapes
    ")",
  "g",
);

// Unicode codepoints that aren't visible but can manipulate or hide content.
// Escaped (not stripped) so they remain inspectable in the sanitized output.
export const DANGEROUS_UNICODE = new Set([
  // Bidi overrides + isolates (Trojan Source, RLO file-name attacks).
  "‪",
  "‫",
  "‬",
  "‭",
  "‮",
  "⁦",
  "⁧",
  "⁨",
  "⁩",
  // Invisible width / spacing. ZWJ (U+200D) is intentionally NOT here — it's
  // load-bearing inside emoji sequences.
  "​",
  "‌",
  "⁠",
  "﻿",
]);

const KEY_RE = /^([A-Za-z0-9_-]+):\s*(.*)$/;
const NESTED_RE = /^\s+([A-Za-z0-9_-]+):\s*(.*)$/;
const FOLDED_MARKERS = new Set([">", "|", ">-", "|-", ">+", "|+"]);
const FENCE_END_RE = /\r?\n---(?:\r?\n|$)/;

/**
 * Return [frontmatterObject, body] or [null, fullText] if no frontmatter.
 *
 * Handles a small subset of YAML sufficient for SKILL.md frontmatter: scalar
 * key/value, single-level nesting, folded (>) and block (|) scalars. Quoted
 * scalars have their quotes stripped.
 *
 * Strict on fence shape: the opening must be `---` followed by a newline, and
 * the closing must be `---` on its own line (or at end of file). A body line
 * beginning with `--- something` is body, not a closing fence.
 */
export function parseFrontmatter(text: string): [Frontmatter | null, string] {
  if (!text.startsWith("---")) {
    return [null, text];
  }
  if (text.length < 4 || (text[3] !== "\n" && text[3] !== "\r")) {
    // Opening fence must be on a line by itself.
    return [null, text];
  }
  const match = findFenceEnd(text, 3);
  if (!match) {
    return [null, text];
  }
  const fmText = text.slice(3, match.start).replace(/^[\r\n]+|[\r\n]+$/g, "");
  const body = text.slice(match.end);
  return [parseSimpleYaml(fmText), body];
}

/** Search for the closing `---` fence starting at `from`, in original-text coordinates. */
function findFenceEnd(text: string, from: number): { start: number; end: number } | null {
  const sub = text.slice(from);
  const m = FENCE_END_RE.exec(sub);
  if (!m) return null;
  return { start: from + m.index, end: from + m.index + m[0].length };
}

function collectIndented(lines: string[], start: number): [string[], number] {
  const collected: string[] = [];
  let i = start;
  while (i < lines.length && (lines[i]!.startsWith("  ") || lines[i]!.trim() === "")) {
    if (lines[i]!.trim() !== "") {
      collected.push(lines[i]!);
    }
    i += 1;
  }
  return [collected, i];
}

function parseSimpleYaml(text: string): Frontmatter {
  const result: Frontmatter = {};
  const lines = text.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (line.trim() === "" || line.trimStart().startsWith("#")) {
      i += 1;
      continue;
    }
    const m = KEY_RE.exec(line);
    if (!m) {
      i += 1;
      continue;
    }
    const key = m[1]!;
    const value = m[2]!.trimEnd();
    if (FOLDED_MARKERS.has(value)) {
      const [indented, next] = collectIndented(lines, i + 1);
      i = next;
      const sep = value.startsWith(">") ? " " : "\n";
      result[key] = indented.map((ln) => ln.trim()).join(sep);
      continue;
    }
    if (value === "") {
      const nested: Record<string, string> = {};
      const [indented, next] = collectIndented(lines, i + 1);
      i = next;
      for (const ln of indented) {
        const nm = NESTED_RE.exec(ln);
        if (nm) {
          nested[nm[1]!] = stripQuotes(nm[2]!.trim());
        }
      }
      result[key] = Object.keys(nested).length > 0 ? nested : "";
      continue;
    }
    result[key] = stripQuotes(value);
    i += 1;
  }
  return result;
}

function stripQuotes(value: string): string {
  if (
    value.length >= 2 &&
    ((value[0] === '"' && value[value.length - 1] === '"') ||
      (value[0] === "'" && value[value.length - 1] === "'"))
  ) {
    return value.slice(1, -1);
  }
  return value;
}

/**
 * Make value safe to print or include in JSON output.
 *
 * Untrusted SKILL.md content may contain ANSI escapes, control characters, or
 * unusual unicode that could confuse a downstream agent or terminal.
 *
 * This pass strips terminal escape sequences (CSI, OSC, DCS, single-byte
 * ESC), escapes C0 control characters as `\xNN`, and escapes a small set of
 * invisible / bidi-override Unicode codepoints (incl. U+202E "RLO" used in
 * Trojan Source attacks) as `\uXXXX`. Newline and tab pass through. ZWJ
 * (U+200D) passes through to keep emoji sequences intact. Length is capped
 * at maxLen.
 *
 * Not designed to be cryptographic. Don't pipe adversarial input to a
 * destination where stricter sanitization matters.
 */
export function sanitizeForEcho(value: unknown, maxLen = 200): string {
  let str = typeof value === "string" ? value : String(value);
  str = str.replace(ANSI_RE, "");
  const cleaned: string[] = [];
  for (const ch of str) {
    const cp = ch.codePointAt(0)!;
    if (ch === "\n" || ch === "\t") {
      cleaned.push(ch);
    } else if (cp < 0x20 || cp === 0x7f) {
      cleaned.push(`\\x${cp.toString(16).padStart(2, "0")}`);
    } else if (DANGEROUS_UNICODE.has(ch)) {
      cleaned.push(`\\u${cp.toString(16).padStart(4, "0")}`);
    } else {
      cleaned.push(ch);
    }
  }
  let out = cleaned.join("");
  if (out.length > maxLen) {
    out = out.slice(0, maxLen - 1) + "…";
  }
  return out;
}

/**
 * Emit structured error JSON to stderr.
 *
 * Provides a consistent shape for agent consumption:
 * {"error": <message>, "code": <dotted-code>, "hint": <recovery suggestion>}
 */
export function emitError(
  _script: string,
  message: string,
  opts: { code?: string; hint?: string } = {},
): void {
  const payload: Record<string, string> = { error: sanitizeForEcho(message, 500) };
  if (opts.code) payload.code = opts.code;
  if (opts.hint) payload.hint = opts.hint;
  process.stderr.write(JSON.stringify(payload) + "\n");
}
