import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { recommend, type Opportunity } from "./recommend_scripts.js";
import { runCli } from "./test-helpers.js";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

const DESC =
  "Use this skill when the user wants to test the recommend_scripts logic. " +
  "Trigger when this fixture is used.";

function makeSkill(
  opts: {
    name?: string;
    body?: string;
    scripts?: Record<string, string>;
  } = {},
): string {
  const name = opts.name ?? "demo";
  const body = opts.body ?? "# Demo\n";
  const d = join(root, name);
  mkdirSync(d);
  writeFileSync(join(d, "SKILL.md"), `---\nname: ${name}\ndescription: ${DESC}\n---\n${body}`);
  if (opts.scripts) {
    const scriptsDir = join(d, "scripts");
    mkdirSync(scriptsDir, { recursive: true });
    for (const [fname, contents] of Object.entries(opts.scripts)) {
      writeFileSync(join(scriptsDir, fname), contents);
    }
  }
  return d;
}

function kinds(opps: Opportunity[]): Set<string> {
  return new Set(opps.map((o) => o.kind));
}

describe("recommend extract-procedure", () => {
  it("flags a long bash block", () => {
    const body =
      "# Demo\n\n```bash\n" +
      Array.from({ length: 8 }, (_, i) => `echo step ${i}`).join("\n") +
      "\n```\n";
    const opps = recommend(makeSkill({ body }));
    expect(kinds(opps).has("recommend.script.extract-procedure")).toBe(true);
  });

  it("does not flag a short bash block", () => {
    const body = "# Demo\n\n```bash\necho one\necho two\n```\n";
    const opps = recommend(makeSkill({ body }));
    expect(kinds(opps).has("recommend.script.extract-procedure")).toBe(false);
  });

  it("assesses multiple blocks independently", () => {
    const long = Array.from({ length: 8 }, (_, i) => `echo ${i}`).join("\n");
    const body = `# Demo\n\n\`\`\`bash\necho short\n\`\`\`\n\n\`\`\`bash\n${long}\n\`\`\`\n`;
    const opps = recommend(makeSkill({ body })).filter(
      (o) => o.kind === "recommend.script.extract-procedure",
    );
    expect(opps).toHaveLength(1);
  });

  it("closes a blockquoted fence at its own quote level", () => {
    // Several short quoted blocks must stay separate. Ignoring the '> ' prefix
    // makes the first one swallow the rest and report one giant procedure.
    const quoted = ["> ```bash", "> echo one", "> ```"].join("\n");
    const body = `# Demo\n\n${quoted}\n\n${quoted}\n\n${quoted}\n`;
    const opps = recommend(makeSkill({ body })).filter(
      (o) => o.kind === "recommend.script.extract-procedure",
    );
    expect(opps).toHaveLength(0);
  });

  it("flags a long blockquoted bash block", () => {
    const inner = Array.from({ length: 8 }, (_, i) => `> echo step ${i}`).join("\n");
    const body = `# Demo\n\n> \`\`\`bash\n${inner}\n> \`\`\`\n`;
    const opps = recommend(makeSkill({ body })).filter(
      (o) => o.kind === "recommend.script.extract-procedure",
    );
    expect(opps).toHaveLength(1);
  });
});

describe("recommend extra fence languages", () => {
  it("flags a shell-tagged block", () => {
    const body =
      "# Demo\n\n```shell\n" +
      Array.from({ length: 8 }, (_, i) => `echo ${i}`).join("\n") +
      "\n```\n";
    const opps = recommend(makeSkill({ body })).filter(
      (o) => o.kind === "recommend.script.extract-procedure",
    );
    expect(opps).toHaveLength(1);
  });

  it("flags a zsh-tagged block", () => {
    const body =
      "# Demo\n\n```zsh\n" +
      Array.from({ length: 8 }, (_, i) => `echo ${i}`).join("\n") +
      "\n```\n";
    const opps = recommend(makeSkill({ body })).filter(
      (o) => o.kind === "recommend.script.extract-procedure",
    );
    expect(opps).toHaveLength(1);
  });
});

describe("recommend robustness", () => {
  it("returns a meta warning when SKILL.md is missing", () => {
    const d = join(root, "demo");
    mkdirSync(d);
    const opps = recommend(d);
    expect(kinds(opps).has("recommend.skill-md.missing")).toBe(true);
  });

  it("does not crash when there is no scripts/ directory", () => {
    const opps = recommend(makeSkill());
    expect(Array.isArray(opps)).toBe(true);
  });

  it("does not produce per-script findings for scripts/ contents", () => {
    const d = makeSkill({ scripts: { "doit.ts": "console.log(process.argv[2]);\n" } });
    const opps = recommend(d);
    expect(opps.every((o) => o.kind !== "recommend.script.missing-argparse")).toBe(true);
    expect(opps.every((o) => o.kind !== "recommend.script.missing-json")).toBe(true);
    expect(opps.every((o) => o.kind !== "recommend.script.missing-pep723")).toBe(true);
  });
});

describe("recommend_scripts CLI", () => {
  it("--help exits 0", () => {
    const result = runCli("recommend_scripts.ts", ["--help"]);
    expect(result.status).toBe(0);
  });

  it("default text output exits 0", () => {
    const result = runCli("recommend_scripts.ts", [makeSkill()]);
    expect(result.status).toBe(0);
  });

  it("--json emits opportunities and summary with required fields", () => {
    const body =
      "# Demo\n\n```bash\n" +
      Array.from({ length: 8 }, (_, i) => `echo ${i}`).join("\n") +
      "\n```\n";
    const result = runCli("recommend_scripts.ts", [makeSkill({ body }), "--json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data).toHaveProperty("opportunities");
    expect(data).toHaveProperty("summary");
    for (const o of data.opportunities) {
      for (const key of ["kind", "where", "title", "why", "suggestion", "severity"]) {
        expect(o).toHaveProperty(key);
      }
    }
  });

  it("--format json behaves like --json", () => {
    const result = runCli("recommend_scripts.ts", [makeSkill(), "--format", "json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data).toHaveProperty("opportunities");
  });

  it("--quiet is accepted", () => {
    const result = runCli("recommend_scripts.ts", [makeSkill(), "--quiet"]);
    expect(result.status).toBe(0);
  });
});
