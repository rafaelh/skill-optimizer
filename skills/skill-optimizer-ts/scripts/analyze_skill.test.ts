import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { analyze } from "./analyze_skill.js";
import { runCli } from "./test-helpers.js";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

const DEFAULT_DESCRIPTION =
  "Use this skill when the user wants to validate or audit a agent skill's SKILL.md file. " +
  "Trigger when the user mentions skill optimization, skill validation, or skill activation issues.";

interface SkillOpts {
  name?: string;
  description?: string;
  body?: string;
}

function makeSkill(opts: SkillOpts = {}): string {
  const name = opts.name ?? "demo";
  const description = opts.description ?? DEFAULT_DESCRIPTION;
  const body = opts.body ?? "# Demo\n\nbody\n";
  const d = join(root, name);
  mkdirSync(d);
  writeFileSync(
    join(d, "SKILL.md"),
    `---\nname: ${name}\ndescription: ${description}\n---\n${body}`,
  );
  return d;
}

/** Issue codes reported for a synthetic skill built from `opts`. */
function codesFor(opts: SkillOpts = {}): string[] {
  return analyze(makeSkill(opts)).issues.map((i) => i.code);
}

describe("analyze", () => {
  it("reports no warnings for a clean skill", () => {
    const { issues } = analyze(makeSkill());
    expect(issues.filter((i) => i.severity === "warn")).toEqual([]);
  });

  it("flags a declarative description opening", () => {
    const description = "This skill processes CSV files quickly and reliably across many cases.";
    expect(codesFor({ description })).toContain("analyze.description.declarative");
  });

  it("flags a description missing a trigger context", () => {
    const description = "Helps process CSV files quickly across many cases for various users.";
    expect(codesFor({ description })).toContain("analyze.description.no-trigger");
  });

  it("flags a thin description", () => {
    const description = "Use this skill when handling CSVs.";
    expect(codesFor({ description })).toContain("analyze.description.thin");
  });

  it("flags a body over 500 lines", () => {
    const body = "# Heading\n\n" + "filler line filler line \n".repeat(510);
    expect(codesFor({ body })).toContain("analyze.body.lines-over-limit");
  });

  it("flags a body with too many approximate tokens", () => {
    const body = "x ".repeat(12000);
    expect(codesFor({ body })).toContain("analyze.body.tokens-over-limit");
  });

  it("flags generic filler phrases", () => {
    const body = "# Demo\n\nFollow best practices and handle errors appropriately.\n";
    expect(codesFor({ body })).toContain("analyze.body.generic-filler");
  });

  it("flags a reference mentioned without a load trigger", () => {
    const body = "# Demo\n\nSee [details](references/details.md).\n" + "padding\n".repeat(100);
    expect(codesFor({ body })).toContain("analyze.reference.no-trigger");
  });

  it("does not flag a reference that has a load trigger nearby", () => {
    const body =
      "# Demo\n\nRead [details](references/details.md) when the user asks for advanced behaviour.\n" +
      "padding\n".repeat(100);
    expect(codesFor({ body })).not.toContain("analyze.reference.no-trigger");
  });

  it("measures the body and the description", () => {
    const { stats } = analyze(makeSkill({ body: "# Demo\n\n## One\n\n## Two\n" }));
    expect(stats).toEqual({
      body_lines: 6,
      approx_tokens: 5, // 23 body chars / 4
      h2_sections: 2,
      description_chars: DEFAULT_DESCRIPTION.length,
    });
  });
});

describe("analyze sanitization", () => {
  it("never leaks ANSI escapes into issue messages", () => {
    const body = "# Demo\n\n\x1b[31mevil\x1b[0m content\n";
    const { issues } = analyze(makeSkill({ body }));
    for (const issue of issues) {
      expect(issue.message).not.toContain("\x1b");
    }
  });
});

describe("analyze_skill CLI", () => {
  it("--help exits 0", () => {
    const result = runCli("analyze_skill.ts", ["--help"]);
    expect(result.status).toBe(0);
  });

  it("default text output includes INFO or stats", () => {
    const result = runCli("analyze_skill.ts", [makeSkill()]);
    expect(result.status).toBe(0);
    expect(result.stdout.includes("INFO:") || result.stdout.toLowerCase().includes("stats")).toBe(
      true,
    );
  });

  it("--json includes issues and stats with body_lines/approx_tokens", () => {
    const result = runCli("analyze_skill.ts", [makeSkill(), "--json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data).toHaveProperty("issues");
    expect(data).toHaveProperty("stats");
    expect(data.stats).toHaveProperty("body_lines");
    expect(data.stats).toHaveProperty("approx_tokens");
  });

  it("--json issues have severity/code/message", () => {
    const d = makeSkill({ description: "This skill handles CSVs." });
    const result = runCli("analyze_skill.ts", [d, "--json"]);
    const data = JSON.parse(result.stdout);
    for (const issue of data.issues) {
      expect(issue).toHaveProperty("severity");
      expect(issue).toHaveProperty("code");
      expect(issue).toHaveProperty("message");
    }
  });

  it("--exit-on-warn turns warnings into a non-zero exit", () => {
    const d = makeSkill({ description: "This skill handles CSVs." });
    const okDefault = runCli("analyze_skill.ts", [d]);
    expect(okDefault.status).toBe(0);
    const warnStrict = runCli("analyze_skill.ts", [d, "--exit-on-warn"]);
    expect(warnStrict.status).toBe(1);
  });

  it("--format json behaves like --json", () => {
    const result = runCli("analyze_skill.ts", [makeSkill(), "--format", "json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data).toHaveProperty("issues");
    expect(data).toHaveProperty("stats");
  });

  it("--quiet is accepted alongside --format json", () => {
    const result = runCli("analyze_skill.ts", [makeSkill(), "--quiet", "--format", "json"]);
    expect(result.status).toBe(0);
  });
});
