import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { count } from "./count_tokens.js";
import { runCli } from "./test-helpers.js";

let root: string;
let savedApiKey: string | undefined;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
  savedApiKey = process.env.ANTHROPIC_API_KEY;
  delete process.env.ANTHROPIC_API_KEY;
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
  if (savedApiKey === undefined) {
    delete process.env.ANTHROPIC_API_KEY;
  } else {
    process.env.ANTHROPIC_API_KEY = savedApiKey;
  }
});

describe("count", () => {
  it("falls back to the heuristic when no API key is set", async () => {
    const result = await count("hello world");
    expect(result.exact).toBe(false);
    expect(result.method).toBe("heuristic");
    expect(result.chars_per_token).toBe(3.5);
    expect(result.tokens).toBe(Math.max(1, Math.floor("hello world".length / 3.5)));
  });

  it("never returns fewer than 1 token, even for empty input", async () => {
    const result = await count("");
    expect(result.tokens).toBe(1);
  });

  it("uses the SDK caller and returns an exact count when an API key is set", async () => {
    process.env.ANTHROPIC_API_KEY = "test-key";
    const result = await count("hello world", "claude-sonnet-4-6", async () => 42);
    expect(result).toMatchObject({
      tokens: 42,
      method: "anthropic-sdk",
      exact: true,
      model: "claude-sonnet-4-6",
    });
  });

  it("falls back to the heuristic with a reason when the SDK caller throws", async () => {
    process.env.ANTHROPIC_API_KEY = "test-key";
    const result = await count("hello world", "claude-sonnet-4-6", async () => {
      throw new Error("network down");
    });
    expect(result.exact).toBe(false);
    expect(result.method).toBe("heuristic");
    expect(result.fallback_reason).toBe("network down");
  });
});

describe("count_tokens CLI", () => {
  it("--help exits 0", () => {
    const result = runCli("count_tokens.ts", ["--help"]);
    expect(result.status).toBe(0);
  });

  it("counts tokens in a file", () => {
    const file = join(root, "sample.txt");
    writeFileSync(file, "hello world");
    const result = runCli("count_tokens.ts", [file], { env: {} });
    expect(result.status).toBe(0);
    expect(result.stdout.toLowerCase()).toContain("tokens");
  });

  it("counts tokens from stdin via '-'", () => {
    const result = runCli("count_tokens.ts", ["-"], { input: "hello world" });
    expect(result.status).toBe(0);
    expect(result.stdout.toLowerCase()).toContain("tokens");
  });

  it("--json emits a structured payload with target and tokens", () => {
    const file = join(root, "sample.txt");
    writeFileSync(file, "hello world");
    const result = runCli("count_tokens.ts", [file, "--json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data).toHaveProperty("target");
    expect(data).toHaveProperty("tokens");
    expect(data.exact).toBe(false);
  });

  it("exits 2 for a missing file", () => {
    const result = runCli("count_tokens.ts", [join(root, "does-not-exist.txt")]);
    expect(result.status).toBe(2);
  });
});
