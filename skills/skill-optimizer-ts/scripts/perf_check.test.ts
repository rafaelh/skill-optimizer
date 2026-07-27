import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runCli } from "./test-helpers.js";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function write(name: string, body: string): string {
  const p = join(root, name);
  writeFileSync(p, body, "utf-8");
  return p;
}

function categoriesOf(payload: { static: { category: string }[] }): string[] {
  return payload.static.map((i) => i.category);
}

describe("perf_check CLI", () => {
  it("--help exits 0 with usage examples", () => {
    const result = runCli("perf_check.ts", ["--help"]);
    expect(result.status).toBe(0);
    expect(result.stdout.toLowerCase()).toContain("examples");
  });

  it("exits 2 with no arguments", () => {
    const result = runCli("perf_check.ts", []);
    expect(result.status).toBe(2);
  });

  it("emits a structured PATH_NOT_FOUND error for a missing path", () => {
    const result = runCli("perf_check.ts", ["/nowhere/missing.ts", "--format", "json"]);
    expect(result.status).toBe(2);
    const lastLine = result.stderr.trim().split("\n").pop()!;
    const err = JSON.parse(lastLine);
    expect(err.code).toBe("PATH_NOT_FOUND");
  });

  it("returns no issues for a clean file", () => {
    const fixture = write(
      "clean.ts",
      `function main(): number {
  return 0;
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    expect(result.status).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.meta.issues_high).toBe(0);
    expect(payload.meta.files_analyzed).toBe(1);
  });

  it("flags string concatenation in a loop", () => {
    const fixture = write(
      "concat.ts",
      `function build(): string {
  let out = "";
  for (let i = 0; i < 100; i++) {
    out += "x";
  }
  return out;
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    expect(result.status).toBe(0);
    const payload = JSON.parse(result.stdout);
    const cats = categoriesOf(payload);
    expect(cats).toContain("string-concat-loop");
  });

  it("flags new RegExp(literal) recompiled inside a loop", () => {
    const fixture = write(
      "regex.ts",
      `function scan(items: string[]): void {
  for (const item of items) {
    const re = new RegExp("^\\\\d+$");
    re.test(item);
  }
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    const payload = JSON.parse(result.stdout);
    const cats = categoriesOf(payload);
    expect(cats).toContain("regex-recompile");
  });

  it("flags array membership tests (.includes on a literal) inside a loop", () => {
    const fixture = write(
      "member.ts",
      `function f(x: string): boolean {
  for (let i = 0; i < 10; i++) {
    if (["a", "b", "c"].includes(x)) return true;
  }
  return false;
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    const payload = JSON.parse(result.stdout);
    const cats = categoriesOf(payload);
    expect(cats).toContain("membership-seq");
  });

  it("flags readFileSync re-reading the same path every iteration", () => {
    const fixture = write(
      "readloop.ts",
      `import { readFileSync } from "node:fs";
function h(items: string[], config: string): void {
  for (const it of items) {
    const rules = readFileSync(config, "utf-8");
    apply(it, rules);
  }
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    const payload = JSON.parse(result.stdout);
    const cats = categoriesOf(payload);
    expect(cats).toContain("read-in-loop");
  });

  it("does not flag readFileSync when the path varies per iteration", () => {
    const fixture = write(
      "readvary.ts",
      `import { readFileSync } from "node:fs";
function h(files: string[]): void {
  for (const f of files) {
    readFileSync(f, "utf-8");
  }
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    const payload = JSON.parse(result.stdout);
    const cats = categoriesOf(payload);
    expect(cats).not.toContain("read-in-loop");
  });

  it("does not flag work in a loop header, which runs once", () => {
    const fixture = write(
      "loopheader.ts",
      `import { readdirSync } from "node:fs";
function h(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir).sort()) {
    out.push(entry);
  }
  return out;
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    const payload = JSON.parse(result.stdout);
    const cats = categoriesOf(payload);
    expect(cats).not.toContain("sort-in-loop");
  });

  it("flags .sort() called inside a loop", () => {
    const fixture = write(
      "sortloop.ts",
      `function k(arr: number[][]): void {
  for (const sub of arr) {
    sub.sort();
  }
}
`,
    );
    const result = runCli("perf_check.ts", [fixture, "--format", "json", "--quiet"]);
    const payload = JSON.parse(result.stdout);
    const cats = categoriesOf(payload);
    expect(cats).toContain("sort-in-loop");
  });

  it("walks a directory recursively and aggregates file counts", () => {
    const nested = join(root, "nested");
    mkdirSync(nested);
    writeFileSync(
      join(nested, "a.ts"),
      `function f(): void {
  let out = "";
  for (let i = 0; i < 3; i++) {
    out += "y";
  }
}
`,
    );
    writeFileSync(join(root, "b.ts"), "function g(): void {}\n");
    const result = runCli("perf_check.ts", [root, "--format", "json", "--quiet"]);
    expect(result.status).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.meta.files_analyzed).toBe(2);
    const cats = categoriesOf(payload);
    expect(cats).toContain("string-concat-loop");
  });

  it("exits 3 for a directory with no matching files", () => {
    const empty = join(root, "empty");
    mkdirSync(empty);
    const result = runCli("perf_check.ts", [empty, "--format", "json", "--quiet"]);
    expect(result.status).toBe(3);
  });
});
