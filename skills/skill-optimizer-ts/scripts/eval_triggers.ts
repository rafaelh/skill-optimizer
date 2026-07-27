#!/usr/bin/env -S npx tsx
/**
 * Run a trigger-rate eval against a labeled query set.
 *
 * Inputs a JSON array of `{query, should_trigger, [notes], [split]}` objects
 * (see assets/schemas/eval-queries.schema.json), invokes the configured
 * agent CLI (`--cli-bin`, default `claude`) with `-p --output-format json`
 * per query `--runs` times, and counts how often the named skill activates.
 * Computes per-query trigger rate plus train/validation pass rates so you
 * can iterate on the description without overfitting.
 *
 * Usage:
 *   eval_triggers.ts --queries QUERIES.json --skill-name <name>
 *   eval_triggers.ts --queries Q.json --skill-name s --runs 3 --train-split 0.6
 *   eval_triggers.ts ... --json
 *   eval_triggers.ts ... --dry-run            # validate inputs, run no calls
 *
 * Exit codes:
 *   0   eval ran successfully
 *   1   one or more queries failed (rate inverted from expectation)
 *   2   bad invocation (file missing, schema violation, CLI unavailable)
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { emitError, sanitizeForEcho } from "./skill_lib.js";

const DEFAULT_RUNS = 3;
const DEFAULT_TRAIN_SPLIT = 0.6;
const TRIGGER_THRESHOLD = 0.5;

export interface Query {
  query: string;
  should_trigger: boolean;
  notes?: string;
  split?: "train" | "validation";
}

export interface QueryResult {
  query: string;
  should_trigger: boolean;
  triggers: number;
  runs: number;
  rate: number;
  passed: boolean;
  split: "train" | "validation";
  code: "eval.query.no-trigger" | "eval.query.false-trigger" | "eval.query.passed";
  notes?: string;
}

export interface EvalSummary {
  skill_name: string;
  runs_per_query: number;
  train_split: number;
  by_query: QueryResult[];
  train_pass_rate: number;
  validation_pass_rate: number;
}

export type QueryRunner = (query: string, cliBin: string) => Promise<Record<string, unknown>>;

/**
 * Read and minimally validate the queries file.
 *
 * Schema enforcement is stricter than JSON.parse: each item must have
 * `query` (string) and `should_trigger` (boolean). `notes` and `split` are
 * optional.
 */
export function loadQueries(path: string): Query[] {
  const raw = readFileSync(path, "utf-8");
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (exc) {
    throw new Error(`queries file is not valid JSON: ${exc instanceof Error ? exc.message : exc}`);
  }
  if (!Array.isArray(data) || data.length === 0) {
    throw new Error("queries file must be a non-empty JSON array");
  }
  data.forEach((item: unknown, i: number) => {
    if (typeof item !== "object" || item === null) {
      throw new TypeError(`queries[${i}] must be an object`);
    }
    const obj = item as Record<string, unknown>;
    if (typeof obj.query !== "string" || !obj.query) {
      throw new TypeError(`queries[${i}].query must be a non-empty string`);
    }
    if (typeof obj.should_trigger !== "boolean") {
      throw new TypeError(`queries[${i}].should_trigger must be a boolean`);
    }
    if ("split" in obj && obj.split !== "train" && obj.split !== "validation") {
      throw new Error(`queries[${i}].split must be 'train' or 'validation' if set`);
    }
  });
  return data as Query[];
}

/**
 * Assign train/validation splits to queries lacking explicit ones.
 *
 * Mutates queries in place. Maintains proportional positive/negative
 * balance across splits — independently sample positives and negatives.
 */
export function assignSplits(queries: Query[], trainFraction: number): void {
  const positives = queries.filter((q) => q.split === undefined && q.should_trigger);
  const negatives = queries.filter((q) => q.split === undefined && !q.should_trigger);
  const trainPos = positives.length ? Math.max(1, Math.round(positives.length * trainFraction)) : 0;
  const trainNeg = negatives.length ? Math.max(1, Math.round(negatives.length * trainFraction)) : 0;
  positives.forEach((q, i) => {
    q.split = i < trainPos ? "train" : "validation";
  });
  negatives.forEach((q, i) => {
    q.split = i < trainNeg ? "train" : "validation";
  });
}

/** Invoke `<cli> -p query --output-format json` for a single query. */
async function defaultRunQuery(query: string, cliBin: string): Promise<Record<string, unknown>> {
  const result = spawnSync(cliBin, ["-p", query, "--output-format", "json"], {
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    throw new Error(`CLI exited ${result.status}: ${result.stderr}`);
  }
  try {
    return JSON.parse(result.stdout) as Record<string, unknown>;
  } catch {
    throw new Error(`CLI returned non-JSON output: '${sanitizeForEcho(result.stdout, 200)}'`);
  }
}

/** Inspect a CLI JSON response for a Skill tool use matching skillName. */
export function didSkillTrigger(cliResponse: Record<string, unknown>, skillName: string): boolean {
  const messages = cliResponse.messages;
  if (!Array.isArray(messages)) return false;
  for (const message of messages) {
    if (typeof message !== "object" || message === null) continue;
    const content = (message as Record<string, unknown>).content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (typeof block !== "object" || block === null) continue;
      const b = block as Record<string, unknown>;
      if (b.type !== "tool_use" || b.name !== "Skill") continue;
      const toolInput = b.input;
      if (
        typeof toolInput === "object" &&
        toolInput !== null &&
        (toolInput as Record<string, unknown>).skill === skillName
      ) {
        return true;
      }
    }
  }
  return false;
}

async function evaluateQuery(
  q: Query,
  opts: { skillName: string; runs: number; cliBin: string; dryRun: boolean; runner: QueryRunner },
): Promise<QueryResult> {
  let triggers = 0;
  if (!opts.dryRun) {
    for (let i = 0; i < opts.runs; i++) {
      const response = await opts.runner(q.query, opts.cliBin);
      if (didSkillTrigger(response, opts.skillName)) triggers++;
    }
  }
  const rate = opts.runs ? triggers / opts.runs : 0;
  const passed =
    (q.should_trigger && rate > TRIGGER_THRESHOLD) ||
    (!q.should_trigger && rate < TRIGGER_THRESHOLD);
  let code: QueryResult["code"];
  if (passed) {
    code = "eval.query.passed";
  } else if (q.should_trigger) {
    code = "eval.query.no-trigger";
  } else {
    code = "eval.query.false-trigger";
  }
  const result: QueryResult = {
    query: q.query,
    should_trigger: q.should_trigger,
    triggers,
    runs: opts.runs,
    rate,
    passed,
    split: q.split!,
    code,
  };
  if (q.notes !== undefined) result.notes = q.notes;
  return result;
}

function passRate(results: QueryResult[]): number {
  return results.length ? results.filter((r) => r.passed).length / results.length : 0;
}

export async function evaluate(
  queries: Query[],
  opts: {
    skillName: string;
    runs?: number;
    trainSplit?: number;
    cliBin?: string;
    dryRun?: boolean;
    runner?: QueryRunner;
  },
): Promise<EvalSummary> {
  const runs = opts.runs ?? DEFAULT_RUNS;
  const trainSplit = opts.trainSplit ?? DEFAULT_TRAIN_SPLIT;
  const cliBin = opts.cliBin ?? "claude";
  const dryRun = opts.dryRun ?? false;
  const runner = opts.runner ?? defaultRunQuery;

  assignSplits(queries, trainSplit);
  const byQuery: QueryResult[] = [];
  for (const q of queries) {
    byQuery.push(
      await evaluateQuery(q, { skillName: opts.skillName, runs, cliBin, dryRun, runner }),
    );
  }

  const trainResults = byQuery.filter((r) => r.split === "train");
  const valResults = byQuery.filter((r) => r.split === "validation");

  return {
    skill_name: opts.skillName,
    runs_per_query: runs,
    train_split: trainSplit,
    by_query: byQuery,
    train_pass_rate: passRate(trainResults),
    validation_pass_rate: passRate(valResults),
  };
}

function emitText(summary: EvalSummary): void {
  for (const r of summary.by_query) {
    const marker = r.passed ? "PASS" : "FAIL";
    const expected = r.should_trigger ? "trigger" : "no-trigger";
    const snippet = sanitizeForEcho(r.query, 80);
    console.log(
      `${marker}: [${r.code}] (${r.split}) expected=${expected} rate=${r.rate.toFixed(2)} ` +
        `(${r.triggers}/${r.runs}) — '${snippet}'`,
    );
  }
  console.log();
  console.log(`train pass rate:      ${summary.train_pass_rate.toFixed(2)}`);
  console.log(`validation pass rate: ${summary.validation_pass_rate.toFixed(2)}`);
}

function emitJson(summary: EvalSummary): void {
  const payload = {
    skill_name: summary.skill_name,
    runs_per_query: summary.runs_per_query,
    train_split: summary.train_split,
    by_query: summary.by_query,
    summary: {
      train: { pass_rate: summary.train_pass_rate },
      validation: { pass_rate: summary.validation_pass_rate },
    },
  };
  console.log(JSON.stringify(payload, null, 2));
}

const HELP = `Run a trigger-rate eval against a labeled query set.

Usage:
  eval_triggers.ts --queries QUERIES.json --skill-name <name>

Examples:
  eval_triggers.ts --queries queries.json --skill-name my-skill
  eval_triggers.ts --queries queries.json --skill-name my-skill --runs 5 --json
  eval_triggers.ts --queries queries.json --skill-name my-skill --dry-run

Options:
  --queries <path>       Path to queries JSON file, required
  --skill-name <name>    Frontmatter 'name' of the skill being evaluated, required
  --runs <n>              Invocations per query for stability (default: ${DEFAULT_RUNS})
  --train-split <n>      Fraction of queries assigned to train (default: ${DEFAULT_TRAIN_SPLIT})
  --cli-bin <bin>        Agent CLI binary (default: claude). Supports claude, copilot, codex.
  --dry-run              Validate inputs and assignments, but skip the actual CLI calls
  --format <json|text>   Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr
  --help                 Show this help message
`;

function commandExists(bin: string): boolean {
  const result = spawnSync(process.platform === "win32" ? "where" : "which", [bin], {
    encoding: "utf-8",
  });
  return result.status === 0;
}

export async function main(argv: string[]): Promise<number> {
  const { values } = parseArgs({
    args: argv,
    options: {
      queries: { type: "string" },
      "skill-name": { type: "string" },
      runs: { type: "string", default: String(DEFAULT_RUNS) },
      "train-split": { type: "string", default: String(DEFAULT_TRAIN_SPLIT) },
      "cli-bin": { type: "string", default: "claude" },
      "dry-run": { type: "boolean", default: false },
      format: { type: "string", default: "text" },
      json: { type: "boolean", default: false },
      quiet: { type: "boolean", default: false },
      help: { type: "boolean", default: false },
    },
  });

  if (values.help) {
    console.log(HELP);
    return 0;
  }

  const useJson = values.json || values.format === "json";

  if (!values.queries || !values["skill-name"]) {
    console.error(HELP);
    return 2;
  }

  const queriesPath = resolve(values.queries);
  if (!existsSync(queriesPath) || !statSync(queriesPath).isFile()) {
    emitError("eval_triggers", `queries file not found: ${queriesPath}`, {
      code: "eval.input.not-found",
      hint: "Check the --queries path.",
    });
    return 2;
  }

  if (!values["dry-run"] && !commandExists(values["cli-bin"]!)) {
    emitError("eval_triggers", `'${values["cli-bin"]}' not found on PATH.`, {
      code: "eval.input.cli-missing",
      hint: "Use --dry-run to validate inputs without invoking the CLI.",
    });
    return 2;
  }

  let queries: Query[];
  try {
    queries = loadQueries(queriesPath);
  } catch (exc) {
    emitError("eval_triggers", exc instanceof Error ? exc.message : String(exc), {
      code: "eval.input.invalid-queries",
    });
    return 2;
  }

  const summary = await evaluate(queries, {
    skillName: values["skill-name"]!,
    runs: Number(values.runs),
    trainSplit: Number(values["train-split"]),
    cliBin: values["cli-bin"]!,
    dryRun: values["dry-run"],
  });

  if (useJson) {
    emitJson(summary);
  } else {
    emitText(summary);
  }

  const failed = summary.by_query.filter((r) => !r.passed);
  return failed.length > 0 && !values["dry-run"] ? 1 : 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  main(process.argv.slice(2)).then((code) => process.exit(code));
}
