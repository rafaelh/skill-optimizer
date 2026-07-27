import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { replaceDescription, optimize } from "./optimize_description.js";
import { runCli } from "./test-helpers.js";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("replaceDescription", () => {
  it("replaces a single-line description", () => {
    const text = "---\nname: demo\ndescription: old text\n---\nbody\n";
    const out = replaceDescription(text, "new text");
    expect(out).toContain("description: new text\n");
    expect(out).not.toContain("old text");
    expect(out).toContain("body\n");
  });

  it("replaces a folded-scalar (multi-line) description", () => {
    const text = "---\nname: demo\ndescription: >\n  line one\n  line two\n---\nbody\n";
    const out = replaceDescription(text, "new text");
    expect(out).toContain("description: new text\n");
    expect(out).not.toContain("line one");
    expect(out).toContain("body\n");
  });

  it("inserts a description when none exists", () => {
    const text = "---\nname: demo\n---\nbody\n";
    const out = replaceDescription(text, "new text");
    expect(out).toContain("description: new text\n");
  });

  it("throws when there is no opening frontmatter fence", () => {
    expect(() => replaceDescription("no frontmatter here\n", "x")).toThrow();
  });

  it("throws when the frontmatter has no closing fence", () => {
    expect(() => replaceDescription("---\nname: demo\n", "x")).toThrow();
  });
});

function writeSkill(name: string, description: string, body = "# Demo\n"): string {
  const d = join(root, name);
  mkdirSync(d, { recursive: true });
  writeFileSync(
    join(d, "SKILL.md"),
    `---\nname: ${name}\ndescription: ${description}\n---\n${body}`,
  );
  return d;
}

function writeQueries(items: unknown): string {
  const p = join(root, "queries.json");
  writeFileSync(p, JSON.stringify(items), "utf-8");
  return p;
}

describe("optimize", () => {
  it("picks the baseline as the winner when no candidate beats it", async () => {
    const skillDir = writeSkill("demo", "old description");
    const queriesPath = writeQueries([{ query: "q1", should_trigger: true }]);
    const result = await optimize(skillDir, queriesPath, {
      rounds: 2,
      candidatesPerRound: 2,
      runs: 1,
      runner: async () => ({}), // never triggers -> baseline always fails, no generator called
      candidateGenerator: async () => [],
    });
    expect(result.winner).toBe(result.baseline);
    expect(result.applied).toBe(false);
    // The original file must be restored (not left with a probe description).
    const finalText = readFileSync(join(skillDir, "SKILL.md"), "utf-8");
    expect(finalText).toContain("description: old description");
  });

  // A fake CLI runner that inspects which description is currently written to
  // SKILL.md (evaluateCandidate() writes each candidate before evaluating and
  // restores the original after), returning a real Skill tool_use response
  // shaped exactly as didSkillTrigger() expects.
  function makeDiskAwareRunner(skillDir: string, skillName: string, triggerOn: string) {
    return async (): Promise<Record<string, unknown>> => {
      const text = readFileSync(join(skillDir, "SKILL.md"), "utf-8");
      if (!text.includes(triggerOn)) return {};
      return {
        messages: [{ content: [{ type: "tool_use", name: "Skill", input: { skill: skillName } }] }],
      };
    };
  }

  it("selects a generated candidate that outperforms the baseline", async () => {
    const skillDir = writeSkill("demo", "bad description");
    const queriesPath = writeQueries([{ query: "q1", should_trigger: true }]);
    const result = await optimize(skillDir, queriesPath, {
      rounds: 1,
      candidatesPerRound: 1,
      runs: 1,
      runner: makeDiskAwareRunner(skillDir, "demo", "great description"),
      candidateGenerator: async () => ["great description"],
    });
    expect(result.winner.description).toBe("great description");
    expect(result.winner).not.toBe(result.baseline);
  });

  it("writes the winner to SKILL.md and creates a .bak when apply is set", async () => {
    const skillDir = writeSkill("demo", "bad description");
    const queriesPath = writeQueries([{ query: "q1", should_trigger: true }]);
    const result = await optimize(skillDir, queriesPath, {
      rounds: 1,
      candidatesPerRound: 1,
      runs: 1,
      runner: makeDiskAwareRunner(skillDir, "demo", "great description"),
      candidateGenerator: async () => ["great description"],
      apply: true,
    });
    expect(result.applied).toBe(true);
    expect(existsSync(join(skillDir, "SKILL.md.bak"))).toBe(true);
    const finalText = readFileSync(join(skillDir, "SKILL.md"), "utf-8");
    expect(finalText).toContain("description: great description");
  });

  it("throws when SKILL.md is missing", async () => {
    const empty = join(root, "empty");
    mkdirSync(empty);
    const queriesPath = writeQueries([{ query: "q1", should_trigger: true }]);
    await expect(
      optimize(empty, queriesPath, {
        runner: async () => ({}),
        candidateGenerator: async () => [],
      }),
    ).rejects.toThrow();
  });
});

describe("optimize_description CLI", () => {
  it("--help exits 0", () => {
    expect(runCli("optimize_description.ts", ["--help"]).status).toBe(0);
  });

  it("exits 2 when the skill directory does not exist", () => {
    const queriesPath = writeQueries([{ query: "q1", should_trigger: true }]);
    const result = runCli("optimize_description.ts", [
      join(root, "nope"),
      "--queries",
      queriesPath,
    ]);
    expect(result.status).toBe(2);
  });

  it("exits 2 when the queries file does not exist", () => {
    const skillDir = writeSkill("demo", "old description");
    const result = runCli("optimize_description.ts", [
      skillDir,
      "--queries",
      join(root, "nope.json"),
    ]);
    expect(result.status).toBe(2);
  });

  it("exits 2 when the configured CLI binary is not on PATH", () => {
    const skillDir = writeSkill("demo", "old description");
    const queriesPath = writeQueries([{ query: "q1", should_trigger: true }]);
    const result = runCli("optimize_description.ts", [
      skillDir,
      "--queries",
      queriesPath,
      "--cli-bin",
      "definitely-not-a-real-cli-xyz",
    ]);
    expect(result.status).toBe(2);
  });
});
