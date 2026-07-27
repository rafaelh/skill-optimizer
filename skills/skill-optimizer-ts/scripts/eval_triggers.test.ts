import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadQueries,
  assignSplits,
  didSkillTrigger,
  evaluate,
  type Query,
} from "./eval_triggers.js";
import { runCli } from "./test-helpers.js";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function writeQueries(items: unknown): string {
  const p = join(root, "queries.json");
  writeFileSync(p, JSON.stringify(items), "utf-8");
  return p;
}

describe("loadQueries", () => {
  it("parses a valid queries file", () => {
    const p = writeQueries([{ query: "help me with X", should_trigger: true }]);
    const queries = loadQueries(p);
    expect(queries).toHaveLength(1);
    expect(queries[0]!.query).toBe("help me with X");
  });

  it("rejects a non-array payload", () => {
    const p = writeQueries({ not: "an array" });
    expect(() => loadQueries(p)).toThrow();
  });

  it("rejects an empty array", () => {
    const p = writeQueries([]);
    expect(() => loadQueries(p)).toThrow();
  });

  it("rejects an item missing 'query'", () => {
    const p = writeQueries([{ should_trigger: true }]);
    expect(() => loadQueries(p)).toThrow();
  });

  it("rejects an item whose should_trigger isn't boolean", () => {
    const p = writeQueries([{ query: "x", should_trigger: "yes" }]);
    expect(() => loadQueries(p)).toThrow();
  });

  it("rejects an invalid split value", () => {
    const p = writeQueries([{ query: "x", should_trigger: true, split: "test" }]);
    expect(() => loadQueries(p)).toThrow();
  });

  it("rejects malformed JSON", () => {
    const p = join(root, "bad.json");
    writeFileSync(p, "{not json", "utf-8");
    expect(() => loadQueries(p)).toThrow();
  });
});

describe("assignSplits", () => {
  it("assigns proportional train/validation splits independently for positives and negatives", () => {
    const queries: Query[] = [
      { query: "p1", should_trigger: true },
      { query: "p2", should_trigger: true },
      { query: "p3", should_trigger: true },
      { query: "n1", should_trigger: false },
      { query: "n2", should_trigger: false },
    ];
    assignSplits(queries, 0.6);
    const positives = queries.filter((q) => q.should_trigger);
    const negatives = queries.filter((q) => !q.should_trigger);
    expect(positives.filter((q) => q.split === "train")).toHaveLength(2);
    expect(negatives.filter((q) => q.split === "train")).toHaveLength(1);
  });

  it("leaves an explicit split untouched", () => {
    const queries: Query[] = [{ query: "p1", should_trigger: true, split: "validation" }];
    assignSplits(queries, 0.6);
    expect(queries[0]!.split).toBe("validation");
  });
});

describe("didSkillTrigger", () => {
  const responseWith = (skill: string): Record<string, unknown> => ({
    messages: [
      {
        content: [{ type: "tool_use", name: "Skill", input: { skill } }],
      },
    ],
  });

  it("is true when a matching Skill tool_use block is present", () => {
    expect(didSkillTrigger(responseWith("my-skill"), "my-skill")).toBe(true);
  });

  it("is false when the skill name does not match", () => {
    expect(didSkillTrigger(responseWith("other-skill"), "my-skill")).toBe(false);
  });

  it("is false when there are no messages", () => {
    expect(didSkillTrigger({}, "my-skill")).toBe(false);
  });

  it("is false when the tool_use block is not a Skill invocation", () => {
    const response = {
      messages: [{ content: [{ type: "tool_use", name: "Bash", input: {} }] }],
    };
    expect(didSkillTrigger(response, "my-skill")).toBe(false);
  });
});

describe("evaluate with an injected query runner", () => {
  it("counts triggers, computes rates, and classifies pass/fail codes", async () => {
    const queries: Query[] = [
      { query: "trigger me", should_trigger: true },
      { query: "do not trigger", should_trigger: false },
    ];
    const runner = async (query: string): Promise<Record<string, unknown>> => ({
      messages: [
        {
          content: [
            {
              type: "tool_use",
              name: "Skill",
              input: { skill: query === "trigger me" ? "target-skill" : "unrelated" },
            },
          ],
        },
      ],
    });
    const summary = await evaluate(queries, {
      skillName: "target-skill",
      runs: 2,
      trainSplit: 0.6,
      runner,
    });
    const triggerResult = summary.by_query.find((r) => r.query === "trigger me")!;
    expect(triggerResult.triggers).toBe(2);
    expect(triggerResult.rate).toBe(1);
    expect(triggerResult.passed).toBe(true);
    expect(triggerResult.code).toBe("eval.query.passed");

    const noTriggerResult = summary.by_query.find((r) => r.query === "do not trigger")!;
    expect(noTriggerResult.triggers).toBe(0);
    expect(noTriggerResult.passed).toBe(true);
  });

  it("marks a should_trigger query that never fires as eval.query.no-trigger", async () => {
    const queries: Query[] = [{ query: "trigger me", should_trigger: true }];
    const runner = async (): Promise<Record<string, unknown>> => ({});
    const summary = await evaluate(queries, { skillName: "x", runs: 2, trainSplit: 0.6, runner });
    expect(summary.by_query[0]!.code).toBe("eval.query.no-trigger");
    expect(summary.by_query[0]!.passed).toBe(false);
  });
});

describe("eval_triggers CLI", () => {
  it("--help exits 0", () => {
    expect(runCli("eval_triggers.ts", ["--help"]).status).toBe(0);
  });

  it("exits 2 when the queries file is missing", () => {
    const result = runCli("eval_triggers.ts", [
      "--queries",
      join(root, "nope.json"),
      "--skill-name",
      "x",
      "--dry-run",
    ]);
    expect(result.status).toBe(2);
  });

  it("exits 2 for invalid queries JSON", () => {
    const p = writeQueries([{ should_trigger: true }]);
    const result = runCli("eval_triggers.ts", ["--queries", p, "--skill-name", "x", "--dry-run"]);
    expect(result.status).toBe(2);
  });

  it("exits 2 when the CLI binary is missing and --dry-run is not set", () => {
    const p = writeQueries([{ query: "x", should_trigger: true }]);
    const result = runCli("eval_triggers.ts", [
      "--queries",
      p,
      "--skill-name",
      "x",
      "--cli-bin",
      "definitely-not-a-real-cli-xyz",
    ]);
    expect(result.status).toBe(2);
  });

  it("--dry-run succeeds even with a nonexistent CLI binary", () => {
    const p = writeQueries([
      { query: "a", should_trigger: true },
      { query: "b", should_trigger: false },
    ]);
    const result = runCli("eval_triggers.ts", [
      "--queries",
      p,
      "--skill-name",
      "x",
      "--cli-bin",
      "definitely-not-a-real-cli-xyz",
      "--dry-run",
      "--json",
    ]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(Array.isArray(data.by_query)).toBe(true);
    expect(data.summary).toHaveProperty("train");
    expect(data.summary).toHaveProperty("validation");
  });
});
