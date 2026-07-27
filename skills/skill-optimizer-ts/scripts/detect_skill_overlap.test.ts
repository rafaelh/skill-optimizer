import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { tokenize, cosine, sharedKeywords, detect } from "./detect_skill_overlap.js";
import { runCli } from "./test-helpers.js";

let root: string;

const OVERLAPPING_DESCRIPTION =
  "Use this skill when the user wants to audit or optimize an agent skill SKILL.md file.";

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function makeSkillDir(parent: string, name: string, description: string): string {
  const dir = join(parent, name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    join(dir, "SKILL.md"),
    `---\nname: ${name}\ndescription: ${description}\n---\nbody\n`,
  );
  return dir;
}

describe("tokenize", () => {
  it("lowercases, drops stopwords, and keeps hyphenated compounds", () => {
    const tokens = tokenize("Use this skill when the user wants PDF-processing help.");
    expect(tokens).toContain("pdf-processing");
    expect(tokens).not.toContain("the");
    expect(tokens).not.toContain("use");
  });

  it("drops tokens shorter than the minimum keyword length", () => {
    const tokens = tokenize("a an at do it");
    expect(tokens).toEqual([]);
  });
});

describe("cosine", () => {
  it("is 0 for disjoint token sets", () => {
    expect(cosine(["pdf", "rotate"], ["csv", "parse"])).toBe(0);
  });

  it("is 1 for identical token sets", () => {
    expect(cosine(["pdf", "rotate"], ["pdf", "rotate"])).toBeCloseTo(1, 10);
  });

  it("is 0 when either side is empty", () => {
    expect(cosine([], ["pdf"])).toBe(0);
    expect(cosine(["pdf"], [])).toBe(0);
  });
});

describe("sharedKeywords", () => {
  it("ranks by combined frequency, most shared first", () => {
    const a = ["pdf", "pdf", "rotate", "merge"];
    const b = ["pdf", "rotate", "rotate"];
    const ranked = sharedKeywords(a, b, 2);
    expect(ranked[0]).toBe("pdf");
  });
});

describe("detect", () => {
  it("finds no overlap between unrelated skill descriptions", () => {
    const parent = root;
    makeSkillDir(
      parent,
      "pdf-tool",
      "Use this skill when the user wants to rotate or merge PDF documents.",
    );
    makeSkillDir(
      parent,
      "csv-tool",
      "Use this skill when the user wants to parse or transform CSV spreadsheets.",
    );
    const [skills, overlaps] = detect(parent, { threshold: 0.5 });
    expect(skills).toHaveLength(2);
    expect(overlaps).toEqual([]);
  });

  it("flags near-identical descriptions above the threshold", () => {
    const parent = root;
    makeSkillDir(parent, "skill-a", OVERLAPPING_DESCRIPTION);
    makeSkillDir(parent, "skill-b", OVERLAPPING_DESCRIPTION);
    const [, overlaps] = detect(parent, { threshold: 0.5 });
    expect(overlaps).toHaveLength(1);
    expect(overlaps[0]!.similarity).toBeGreaterThanOrEqual(0.5);
    expect(overlaps[0]!.shared_keywords.length).toBeGreaterThan(0);
  });

  it("in single-skill mode, compares only the focal skill against its siblings", () => {
    const siblingsParent = join(root, "siblings");
    mkdirSync(siblingsParent);
    makeSkillDir(siblingsParent, "other-skill", OVERLAPPING_DESCRIPTION);
    const focal = makeSkillDir(root, "focal-skill", OVERLAPPING_DESCRIPTION);
    const [skills, overlaps] = detect(focal, { against: siblingsParent, threshold: 0.5 });
    expect(skills.some((s) => s.name === "focal-skill")).toBe(true);
    expect(skills.some((s) => s.name === "other-skill")).toBe(true);
    expect(overlaps).toHaveLength(1);
  });

  it("throws when target has no siblings with SKILL.md", () => {
    const empty = join(root, "empty-parent");
    mkdirSync(empty);
    expect(() => detect(empty, { threshold: 0.5 })).toThrow(/no skills/);
  });
});

describe("detect_skill_overlap CLI", () => {
  it("--help exits 0", () => {
    const result = runCli("detect_skill_overlap.ts", ["--help"]);
    expect(result.status).toBe(0);
  });

  it("exits 0 with no overlap for unrelated descriptions", () => {
    makeSkillDir(root, "pdf-tool", "Use this skill when the user wants to rotate PDF documents.");
    makeSkillDir(root, "csv-tool", "Use this skill when the user wants to parse CSV files.");
    const result = runCli("detect_skill_overlap.ts", [root]);
    expect(result.status).toBe(0);
  });

  it("exits 1 and reports JSON pairs above threshold", () => {
    makeSkillDir(root, "skill-a", OVERLAPPING_DESCRIPTION);
    makeSkillDir(root, "skill-b", OVERLAPPING_DESCRIPTION);
    const result = runCli("detect_skill_overlap.ts", [root, "--json"]);
    expect(result.status).toBe(1);
    const data = JSON.parse(result.stdout);
    expect(data.pairs.length).toBeGreaterThanOrEqual(1);
    expect(data.pairs[0]).toHaveProperty("shared_keywords");
  });

  it("exits 2 when the target path does not exist", () => {
    const result = runCli("detect_skill_overlap.ts", [join(root, "nope")]);
    expect(result.status).toBe(2);
  });
});
