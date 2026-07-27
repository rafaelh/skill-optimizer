import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { validate } from "./validate_skill.js";
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

function makeSkill(
  opts: {
    name?: string;
    description?: string;
    body?: string;
    extraFrontmatter?: string;
  } = {},
): string {
  const name = opts.name ?? "demo";
  const description = opts.description ?? DEFAULT_DESCRIPTION;
  const body = opts.body ?? "# Demo\n";
  const extraFrontmatter = opts.extraFrontmatter ?? "";
  const skillDir = join(root, name);
  mkdirSync(skillDir);
  const fm = `name: ${name}\ndescription: ${description}\n${extraFrontmatter}`;
  writeFileSync(join(skillDir, "SKILL.md"), `---\n${fm}---\n${body}`);
  return skillDir;
}

describe("validate", () => {
  it("reports no failures for a passing skill", () => {
    const issues = validate(makeSkill());
    expect(issues.filter((i) => i.severity === "fail")).toEqual([]);
  });

  it("flags a missing SKILL.md", () => {
    const empty = join(root, "empty");
    mkdirSync(empty);
    const issues = validate(empty);
    expect(issues.some((i) => i.code === "validate.skill-md.missing")).toBe(true);
    expect(issues.some((i) => i.severity === "fail")).toBe(true);
  });

  it("flags missing frontmatter", () => {
    const d = join(root, "bad");
    mkdirSync(d);
    writeFileSync(join(d, "SKILL.md"), "# Just a heading\nno frontmatter\n");
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.frontmatter.missing")).toBe(true);
  });

  it("flags a missing name field", () => {
    const d = makeSkill({ name: "x" });
    const text = readFileSync(join(d, "SKILL.md"), "utf-8").replace("name: x\n", "");
    writeFileSync(join(d, "SKILL.md"), text);
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.name.missing")).toBe(true);
  });

  it("flags name/directory mismatch", () => {
    const d = makeSkill({ name: "actual" });
    const text = readFileSync(join(d, "SKILL.md"), "utf-8").replace(
      "name: actual",
      "name: different",
    );
    writeFileSync(join(d, "SKILL.md"), text);
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.name.dir-mismatch")).toBe(true);
  });

  it("flags a badly formatted name", () => {
    const d = makeSkill({ name: "demo" });
    const text = readFileSync(join(d, "SKILL.md"), "utf-8").replace(
      "name: demo",
      "name: Demo--Bad",
    );
    writeFileSync(join(d, "SKILL.md"), text);
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.name.bad-format")).toBe(true);
  });

  it("flags a description that is too long", () => {
    const d = makeSkill({ description: "x".repeat(1100) });
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.description.too-long")).toBe(true);
  });

  it("flags a description that is too short", () => {
    const d = makeSkill({ description: "short" });
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.description.too-short")).toBe(true);
  });

  it("flags an unknown frontmatter key", () => {
    const d = makeSkill({ extraFrontmatter: "bogus: value\n" });
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.frontmatter.unknown-key")).toBe(true);
  });

  it("flags a broken markdown reference", () => {
    const d = makeSkill({ body: "# Body\n\nSee [missing](references/does-not-exist.md).\n" });
    const issues = validate(d);
    expect(issues.some((i) => i.code === "validate.reference.broken")).toBe(true);
  });

  it("does not flag a placeholder script path", () => {
    const d = makeSkill({
      body: "# Body\n\nExtract into `scripts/<name>.ts` to keep things tidy.\n",
    });
    const issues = validate(d);
    expect(issues.every((i) => i.code !== "validate.script.broken")).toBe(true);
  });

  it("does not probe references that escape the skill directory", () => {
    const d = makeSkill({ body: "# Body\n\nSee [oops](../../../../etc/passwd.md).\n" });
    const issues = validate(d);
    expect(issues.every((i) => i.code !== "validate.reference.broken")).toBe(true);
  });

  it("skips absolute path references", () => {
    const d = makeSkill({ body: "# Body\n\nSee [absolute](/etc/hosts.md).\n" });
    const issues = validate(d);
    expect(issues.every((i) => i.code !== "validate.reference.broken")).toBe(true);
  });

  it("handles UTF-8 content cleanly", () => {
    const d = makeSkill({ body: "# Demo\n\nUnicode: café 日本語 🎉\n" });
    const issues = validate(d);
    expect(issues.every((i) => i.severity !== "fail")).toBe(true);
  });

  it("sanitizes ANSI escapes out of echoed issue messages", () => {
    const d = makeSkill({ name: "x" });
    const text = readFileSync(join(d, "SKILL.md"), "utf-8").replace(
      "name: x",
      "name: \x1b[31mevil\x1b[0m",
    );
    writeFileSync(join(d, "SKILL.md"), text);
    const issues = validate(d);
    for (const issue of issues) {
      expect(issue.message).not.toContain("\x1b");
    }
  });
});

describe("validate_skill CLI", () => {
  it("--help exits 0 and mentions the tool's purpose", () => {
    const result = runCli("validate_skill.ts", ["--help"]);
    expect(result.status).toBe(0);
    expect(result.stdout.toLowerCase()).toMatch(/validate|skill/);
  });

  it("exits 0 with text output for a passing skill", () => {
    const dir = makeSkill();
    const result = runCli("validate_skill.ts", [dir]);
    expect(result.status).toBe(0);
    expect(result.stdout.toLowerCase()).toContain("fail");
  });

  it("exits 1 for a failing skill", () => {
    const empty = join(root, "x");
    mkdirSync(empty);
    const result = runCli("validate_skill.ts", [empty]);
    expect(result.status).toBe(1);
  });

  it("--json emits parseable JSON with summary.ok true for a passing skill", () => {
    const dir = makeSkill();
    const result = runCli("validate_skill.ts", [dir, "--json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data.summary.fail).toBe(0);
    expect(data.summary.ok).toBe(true);
    expect(Array.isArray(data.issues)).toBe(true);
  });

  it("--json reports failures with the missing SKILL.md code", () => {
    const empty = join(root, "x");
    mkdirSync(empty);
    const result = runCli("validate_skill.ts", [empty, "--json"]);
    expect(result.status).toBe(1);
    const data = JSON.parse(result.stdout);
    expect(data.summary.fail).toBeGreaterThanOrEqual(1);
    expect(data.summary.ok).toBe(false);
    expect(data.issues.some((i: { code: string }) => i.code === "validate.skill-md.missing")).toBe(
      true,
    );
  });

  it("every JSON issue has severity/code/message", () => {
    const empty = join(root, "x");
    mkdirSync(empty);
    const result = runCli("validate_skill.ts", [empty, "--json"]);
    const data = JSON.parse(result.stdout);
    for (const issue of data.issues) {
      expect(issue).toHaveProperty("severity");
      expect(issue).toHaveProperty("code");
      expect(issue).toHaveProperty("message");
      expect(["fail", "warn"]).toContain(issue.severity);
    }
  });

  it("exits 2 when the path is not a directory", () => {
    const notDir = join(root, "file.md");
    writeFileSync(notDir, "hi");
    const result = runCli("validate_skill.ts", [notDir]);
    expect(result.status).toBe(2);
  });

  it("--format json behaves like --json", () => {
    const dir = makeSkill();
    const result = runCli("validate_skill.ts", [dir, "--format", "json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data.summary.ok).toBe(true);
  });

  it("--quiet still surfaces errors on stderr", () => {
    const notDir = join(root, "file.md");
    writeFileSync(notDir, "hi");
    const result = runCli("validate_skill.ts", [notDir, "--quiet"]);
    expect(result.status).toBe(2);
    expect(result.stderr).not.toBe("");
  });
});
