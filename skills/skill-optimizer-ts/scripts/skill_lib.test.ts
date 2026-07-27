import { describe, it, expect, vi } from "vitest";
import { parseFrontmatter, sanitizeForEcho, emitError } from "./skill_lib.js";

describe("parseFrontmatter", () => {
  it("parses simple scalar key/value pairs", () => {
    const text = "---\nname: foo\ndescription: bar\n---\nbody\n";
    const [fm, body] = parseFrontmatter(text);
    expect(fm).toEqual({ name: "foo", description: "bar" });
    expect(body).toBe("body\n");
  });

  it("returns null frontmatter and full text when no frontmatter present", () => {
    const text = "no frontmatter here\n";
    const [fm, body] = parseFrontmatter(text);
    expect(fm).toBeNull();
    expect(body).toBe(text);
  });

  it("returns null when the fence is never closed", () => {
    const text = "---\nname: foo\nbody without closing\n";
    const [fm, body] = parseFrontmatter(text);
    expect(fm).toBeNull();
    expect(body).toBe(text);
  });

  it("strips quotes from quoted scalar values", () => {
    const text = "---\nname: \"quoted\"\ndescription: 'single'\n---\n";
    const [fm] = parseFrontmatter(text);
    expect(fm).toEqual({ name: "quoted", description: "single" });
  });

  it("joins folded (>) scalars with spaces", () => {
    const text = "---\ndescription: >\n  line one\n  line two\n---\n";
    const [fm] = parseFrontmatter(text);
    expect(fm).toEqual({ description: "line one line two" });
  });

  it("joins block (|) scalars with newlines", () => {
    const text = "---\ndescription: |\n  line one\n  line two\n---\n";
    const [fm] = parseFrontmatter(text);
    expect(fm).toEqual({ description: "line one\nline two" });
  });

  it("parses a single level of nested mapping", () => {
    const text = "---\nmetadata:\n  author: rafe\n  version: 1\n---\n";
    const [fm] = parseFrontmatter(text);
    expect(fm).toEqual({ metadata: { author: "rafe", version: "1" } });
  });
});

describe("parseFrontmatter strict fence handling", () => {
  it("does not treat a closing-fence-with-trailing-text as a fence", () => {
    const text = "---\nname: x\n--- this is body, not a fence\n---\nactual body\n";
    const [fm, body] = parseFrontmatter(text);
    expect(fm).toEqual({ name: "x" });
    expect(body).toBe("actual body\n");
  });

  it("rejects an opening fence with trailing text", () => {
    const text = "--- foo\nname: x\n---\nbody\n";
    const [fm, body] = parseFrontmatter(text);
    expect(fm).toBeNull();
    expect(body).toBe(text);
  });

  it("handles CRLF line endings", () => {
    const text = "---\r\nname: x\r\n---\r\nbody\r\n";
    const [fm, body] = parseFrontmatter(text);
    expect(fm).toEqual({ name: "x" });
    expect(body).toContain("body");
  });

  it("handles a closing fence at end of file with no trailing newline", () => {
    const text = "---\nname: x\n---";
    const [fm, body] = parseFrontmatter(text);
    expect(fm).toEqual({ name: "x" });
    expect(body).toBe("");
  });
});

describe("sanitizeForEcho", () => {
  it("leaves plain text unchanged", () => {
    expect(sanitizeForEcho("hello world")).toBe("hello world");
  });

  it("strips ANSI escape sequences", () => {
    expect(sanitizeForEcho("\x1b[31mred\x1b[0m text")).toBe("red text");
  });

  it("escapes control characters", () => {
    expect(sanitizeForEcho("bell\x07inside")).toContain("\\x07");
  });

  it("preserves tabs", () => {
    expect(sanitizeForEcho("col1\tcol2")).toBe("col1\tcol2");
  });

  it("truncates long input to max_len", () => {
    const out = sanitizeForEcho("x".repeat(500), 50);
    expect(out.length).toBeLessThanOrEqual(50);
  });

  it("ends truncated output with an ellipsis marker", () => {
    const out = sanitizeForEcho("x".repeat(500), 50);
    expect(out.endsWith("…") || out.endsWith("...")).toBe(true);
  });

  it("stringifies non-string input", () => {
    expect(sanitizeForEcho(42)).toBe("42");
    expect(sanitizeForEcho(null)).toBe("null");
  });

  it("escapes NUL and similar control bytes", () => {
    const out = sanitizeForEcho("before\x00after");
    expect(out).not.toContain("\x00");
    expect(out).toContain("\\x00");
  });

  it("escapes bidi override RLO (Trojan Source)", () => {
    const out = sanitizeForEcho("evil‮txt.exe");
    expect(out).not.toContain("‮");
    expect(out).toContain("\\u202e");
  });

  it("escapes all bidi overrides and isolates", () => {
    for (const cp of [0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069]) {
      const ch = String.fromCodePoint(cp);
      const out = sanitizeForEcho(`x${ch}y`);
      expect(out).not.toContain(ch);
      expect(out).toContain(`\\u${cp.toString(16).padStart(4, "0")}`);
    }
  });

  it("escapes invisible-width characters", () => {
    for (const cp of [0x200b, 0x200c, 0x2060, 0xfeff]) {
      const ch = String.fromCodePoint(cp);
      const out = sanitizeForEcho(`x${ch}y`);
      expect(out).not.toContain(ch);
    }
  });

  it("preserves ZWJ so emoji sequences stay intact", () => {
    const family = "\u{1F468}‍\u{1F469}‍\u{1F467}";
    const out = sanitizeForEcho(family);
    expect(out).toContain("‍");
  });

  it("strips OSC escape sequences", () => {
    const text = "before\x1b]0;malicious-title\x07after";
    const out = sanitizeForEcho(text);
    expect(out).not.toContain("\x1b");
    expect(out).not.toContain("\x07");
    expect(out).toContain("before");
    expect(out).toContain("after");
  });

  it("strips single-byte ESC sequences", () => {
    const out = sanitizeForEcho("x\x1bMy");
    expect(out).not.toContain("\x1b");
  });

  it("preserves non-ASCII letters", () => {
    const text = "café 日本語";
    expect(sanitizeForEcho(text)).toBe(text);
  });
});

describe("emitError", () => {
  it("writes a JSON payload with error/code/hint to stderr", () => {
    const spy = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    emitError("myscript", "boom", { code: "my.error.code", hint: "try again" });
    expect(spy).toHaveBeenCalledTimes(1);
    const written = spy.mock.calls[0]![0] as string;
    expect(JSON.parse(written)).toEqual({
      error: "boom",
      code: "my.error.code",
      hint: "try again",
    });
    spy.mockRestore();
  });

  it("omits code/hint when not provided", () => {
    const spy = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    emitError("myscript", "boom");
    const written = spy.mock.calls[0]![0] as string;
    expect(JSON.parse(written)).toEqual({ error: "boom" });
    spy.mockRestore();
  });
});
