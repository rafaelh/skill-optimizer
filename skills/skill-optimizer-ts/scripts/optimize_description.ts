#!/usr/bin/env -S npx tsx
/**
 * Iterate skill descriptions toward higher validation pass rate.
 *
 * Wraps the manual loop documented in references/evaluation.md:
 *
 *   1. Evaluate the current description on train + validation.
 *   2. For each round, generate N candidate revisions via the agent CLI,
 *      each one targeted at the train-set failures.
 *   3. Evaluate every candidate.
 *   4. Repeat for --rounds rounds, keeping the candidates from each round
 *      as a pool.
 *   5. Pick the candidate with the highest validation pass rate.
 *
 * Default is propose-only: emit JSON of every candidate and its scores;
 * user copies the winner into SKILL.md. With --apply, the winner is written
 * to SKILL.md and the prior file is saved as SKILL.md.bak.
 *
 * Usage:
 *   optimize_description.ts <skill-dir> --queries Q.json
 *   optimize_description.ts <skill-dir> --queries Q.json --apply
 *   optimize_description.ts <skill-dir> --queries Q.json --rounds 3 --candidates 3 --runs 3
 *   optimize_description.ts <skill-dir> --queries Q.json --json
 *
 * Exit codes:
 *   0   ran successfully (with or without applying)
 *   1   no candidate beat the baseline (caller may still inspect output)
 *   2   bad invocation (missing files, CLI unavailable)
 */

import { copyFileSync, existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve, join } from "node:path";
import { parseArgs } from "node:util";
import { evaluate, loadQueries, type Query, type QueryRunner } from "./eval_triggers.js";
import { emitError, parseFrontmatter, sanitizeForEcho } from "./skill_lib.js";

const DEFAULT_ROUNDS = 3;
const DEFAULT_CANDIDATES = 3;
const DEFAULT_RUNS = 3;
const DEFAULT_TRAIN_SPLIT = 0.6;
const MAX_DESCRIPTION = 1024;

export interface Candidate {
  description: string;
  round: number; // 0 == baseline
  train_pass_rate: number;
  validation_pass_rate: number;
  failures_addressed: string[];
}

export interface OptimizationResult {
  skill_dir: string;
  skill_name: string;
  baseline: Candidate;
  candidates: Candidate[];
  winner: Candidate;
  applied: boolean;
  rounds: number;
  runs_per_query: number;
}

export type CandidateGenerator = (
  currentDescription: string,
  failures: Array<{ query: string; should_trigger: boolean; rate: number }>,
  n: number,
  cliBin: string,
) => Promise<string[]>;

function readSkillMd(skillDir: string): { fm: Record<string, unknown>; skillMdPath: string } {
  const skillMdPath = join(skillDir, "SKILL.md");
  if (!existsSync(skillMdPath)) {
    throw new Error(`SKILL.md not found at ${skillMdPath}`);
  }
  const text = readFileSync(skillMdPath, "utf-8");
  const [fm] = parseFrontmatter(text);
  if (fm === null || typeof fm !== "object") {
    throw new TypeError(`SKILL.md at ${skillMdPath} has no parseable frontmatter`);
  }
  return { fm, skillMdPath };
}

/**
 * Replace the `description:` line(s) in frontmatter with newDescription.
 * Handles both single-line scalar form and multi-line folded (`>`) form.
 */
export function replaceDescription(skillMdText: string, newDescription: string): string {
  const lines = skillMdText.split(/(?<=\n)/);
  if (lines.length === 0 || !lines[0]!.startsWith("---")) {
    throw new Error("SKILL.md has no frontmatter fence");
  }
  const closing = findClosingFenceIndex(lines);
  if (closing === -1) {
    throw new Error("SKILL.md frontmatter has no closing '---' fence");
  }

  const out: string[] = [lines[0]!];
  let i = 1;
  let replaced = false;
  while (i < closing) {
    const line = lines[i]!;
    if (line.startsWith("description:") && !replaced) {
      out.push(`description: ${newDescription}\n`);
      i += 1;
      while (i < closing && (isIndentedContinuation(lines[i]!) || lines[i]!.trim() === "")) {
        i += 1;
      }
      replaced = true;
    } else {
      out.push(line);
      i += 1;
    }
  }
  if (!replaced) {
    out.push(`description: ${newDescription}\n`);
  }
  out.push(...lines.slice(closing));
  return out.join("");
}

function isIndentedContinuation(line: string): boolean {
  return line.startsWith("  ") || line.startsWith("\t");
}

/** Search for the closing `---` fence line, returning its index or -1 if absent. */
function findClosingFenceIndex(lines: string[]): number {
  for (let i = 1; i < lines.length; i++) {
    if (lines[i]!.replace(/[\r\n]+$/, "") === "---") return i;
  }
  return -1;
}

async function evaluateCandidate(
  skillDir: string,
  description: string,
  queries: Query[],
  opts: {
    skillName: string;
    runs: number;
    trainSplit: number;
    cliBin: string;
    runner?: QueryRunner;
  },
) {
  const skillMdPath = join(skillDir, "SKILL.md");
  const original = readFileSync(skillMdPath, "utf-8");
  try {
    const newText = replaceDescription(original, description);
    writeFileSync(skillMdPath, newText, "utf-8");
    // evaluate() assigns splits in-place; copy per call so candidates start
    // with the same original (unassigned) splits.
    const copiedQueries = queries.map((q) => ({ ...q }));
    return await evaluate(copiedQueries, {
      skillName: opts.skillName,
      runs: opts.runs,
      trainSplit: opts.trainSplit,
      cliBin: opts.cliBin,
      runner: opts.runner,
    });
  } finally {
    writeFileSync(skillMdPath, original, "utf-8");
  }
}

async function defaultCandidateGenerator(
  currentDescription: string,
  failures: Array<{ query: string; should_trigger: boolean; rate: number }>,
  n: number,
  cliBin: string,
): Promise<string[]> {
  if (failures.length === 0) return [];
  const failureLines = failures
    .map(
      (f) =>
        `- ${sanitizeForEcho(f.query, 200)} (should_trigger=${f.should_trigger}, actual_rate=${f.rate.toFixed(2)})`,
    )
    .join("\n");
  const prompt =
    "You are tuning an Agent Skill description. The description is the ONLY field the " +
    "agent uses to decide whether to activate the skill. " +
    `It must stay under ${MAX_DESCRIPTION} characters and use imperative phrasing ` +
    "('Use this skill when...' not 'This skill does...').\n\n" +
    "The current description:\n" +
    `<<<\n${currentDescription}\n>>>\n\n` +
    "Failed against these queries:\n" +
    `${failureLines}\n\n` +
    `Write ${n} candidate revisions, separated by '===' on its own line. Each revision ` +
    `must address the failures while staying under ${MAX_DESCRIPTION} chars. Output ONLY ` +
    "the candidates separated by '==='. No preamble, no numbering.";

  const result = spawnSync(cliBin, ["-p", prompt], { encoding: "utf-8" });
  if (result.status !== 0) {
    throw new Error(`CLI exited ${result.status}: ${result.stderr}`);
  }
  const raw = result.stdout.trim();
  const candidates = raw
    .split("===")
    .map((c) => c.trim())
    .filter((c) => c.length > 0);
  return candidates.slice(0, n).map((c) => c.slice(0, MAX_DESCRIPTION));
}

export async function optimize(
  skillDir: string,
  queriesPath: string,
  opts: {
    rounds?: number;
    candidatesPerRound?: number;
    runs?: number;
    trainSplit?: number;
    cliBin?: string;
    apply?: boolean;
    runner?: QueryRunner;
    candidateGenerator?: CandidateGenerator;
  } = {},
): Promise<OptimizationResult> {
  const rounds = opts.rounds ?? DEFAULT_ROUNDS;
  const candidatesPerRound = opts.candidatesPerRound ?? DEFAULT_CANDIDATES;
  const runs = opts.runs ?? DEFAULT_RUNS;
  const trainSplit = opts.trainSplit ?? DEFAULT_TRAIN_SPLIT;
  const cliBin = opts.cliBin ?? "claude";
  const apply = opts.apply ?? false;
  const generateCandidates = opts.candidateGenerator ?? defaultCandidateGenerator;

  const { fm } = readSkillMd(skillDir);
  const skillName =
    typeof fm.name === "string" && fm.name ? fm.name : skillDir.split(/[/\\]/).pop()!;
  const currentDescription = typeof fm.description === "string" ? fm.description : "";
  const queries = loadQueries(queriesPath);
  const evalOpts = { skillName, runs, trainSplit, cliBin, runner: opts.runner };

  const baselineSummary = await evaluateCandidate(skillDir, currentDescription, queries, evalOpts);
  const baseline: Candidate = {
    description: currentDescription,
    round: 0,
    train_pass_rate: baselineSummary.train_pass_rate,
    validation_pass_rate: baselineSummary.validation_pass_rate,
    failures_addressed: [],
  };

  const candidates: Candidate[] = [baseline];
  let lastSummary = baselineSummary;
  let lastDescription = currentDescription;

  for (let roundNum = 1; roundNum <= rounds; roundNum++) {
    const trainFailures = lastSummary.by_query.filter((r) => r.split === "train" && !r.passed);
    if (trainFailures.length === 0) break; // nothing left to improve

    const candidateTexts = await generateCandidates(
      lastDescription,
      trainFailures.map((f) => ({
        query: f.query,
        should_trigger: f.should_trigger,
        rate: f.rate,
      })),
      candidatesPerRound,
      cliBin,
    );

    for (const candText of candidateTexts) {
      const candSummary = await evaluateCandidate(skillDir, candText, queries, evalOpts);
      candidates.push({
        description: candText,
        round: roundNum,
        train_pass_rate: candSummary.train_pass_rate,
        validation_pass_rate: candSummary.validation_pass_rate,
        failures_addressed: trainFailures.map((f) => f.query),
      });
    }

    const roundCandidates = candidates.filter((c) => c.round === roundNum);
    if (roundCandidates.length > 0) {
      const best = roundCandidates.reduce((a, b) =>
        b.validation_pass_rate > a.validation_pass_rate ? b : a,
      );
      lastDescription = best.description;
      lastSummary = await evaluateCandidate(skillDir, best.description, queries, evalOpts);
    }
  }

  const winner = candidates.reduce((a, b) => {
    if (b.validation_pass_rate !== a.validation_pass_rate) {
      return b.validation_pass_rate > a.validation_pass_rate ? b : a;
    }
    return b.train_pass_rate > a.train_pass_rate ? b : a;
  });

  let applied = false;
  if (apply && winner.description !== baseline.description) {
    const skillMdPath = join(skillDir, "SKILL.md");
    const backup = skillMdPath.replace(/\.md$/, ".md.bak");
    copyFileSync(skillMdPath, backup);
    const original = readFileSync(skillMdPath, "utf-8");
    writeFileSync(skillMdPath, replaceDescription(original, winner.description), "utf-8");
    applied = true;
  }

  return {
    skill_dir: skillDir,
    skill_name: skillName,
    baseline,
    candidates,
    winner,
    applied,
    rounds,
    runs_per_query: runs,
  };
}

function emitText(result: OptimizationResult): void {
  console.log(`Skill: ${result.skill_name}`);
  console.log(
    `Baseline: train=${result.baseline.train_pass_rate.toFixed(2)} ` +
      `validation=${result.baseline.validation_pass_rate.toFixed(2)}`,
  );
  for (const c of result.candidates) {
    if (c === result.baseline) continue;
    const marker = c === result.winner ? "*" : " ";
    const snippet = sanitizeForEcho(c.description, 80);
    console.log(
      `${marker} round=${c.round} train=${c.train_pass_rate.toFixed(2)} ` +
        `validation=${c.validation_pass_rate.toFixed(2)} — '${snippet}'`,
    );
  }
  if (result.applied) {
    console.log(`\nApplied winner to ${result.skill_dir}/SKILL.md (backup at SKILL.md.bak)`);
  } else if (result.winner !== result.baseline) {
    console.log("\n(propose-only — pass --apply to write the winner to SKILL.md)");
  } else {
    console.log("\nNo candidate beat the baseline — leaving SKILL.md unchanged.");
  }
}

function emitJson(result: OptimizationResult): void {
  console.log(JSON.stringify(result, null, 2));
}

const HELP = `Iterate skill descriptions toward higher validation pass rate.

Usage:
  optimize_description.ts <skill-dir> --queries Q.json

Examples:
  optimize_description.ts ./my-skill --queries queries.json
  optimize_description.ts ./my-skill --queries q.json --rounds 5 --apply
  optimize_description.ts ./my-skill --queries q.json --json

Options:
  --queries <path>       Path to eval queries JSON, required
  --rounds <n>            Optimization rounds (default: ${DEFAULT_ROUNDS})
  --candidates <n>        Candidates generated per round (default: ${DEFAULT_CANDIDATES})
  --runs <n>              Invocations per query (default: ${DEFAULT_RUNS})
  --train-split <n>      Fraction of queries assigned to train (default: ${DEFAULT_TRAIN_SPLIT})
  --cli-bin <bin>        Agent CLI binary (default: claude). Supports claude, copilot, codex.
  --apply                Write the winning description to SKILL.md (creates .bak)
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
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      queries: { type: "string" },
      rounds: { type: "string", default: String(DEFAULT_ROUNDS) },
      candidates: { type: "string", default: String(DEFAULT_CANDIDATES) },
      runs: { type: "string", default: String(DEFAULT_RUNS) },
      "train-split": { type: "string", default: String(DEFAULT_TRAIN_SPLIT) },
      "cli-bin": { type: "string", default: "claude" },
      apply: { type: "boolean", default: false },
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
  const skillDirArg = positionals[0];
  if (!skillDirArg || !values.queries) {
    console.error(HELP);
    return 2;
  }

  const skillDir = resolve(skillDirArg);
  if (!existsSync(skillDir) || !statSync(skillDir).isDirectory()) {
    emitError("optimize_description", `not a directory: ${skillDir}`, {
      code: "optimize.input.not-dir",
      hint: "First argument must be a skill directory.",
    });
    return 2;
  }
  const queriesPath = resolve(values.queries);
  if (!existsSync(queriesPath) || !statSync(queriesPath).isFile()) {
    emitError("optimize_description", `queries file not found: ${queriesPath}`, {
      code: "optimize.input.not-found",
      hint: "Check the --queries path.",
    });
    return 2;
  }
  if (!commandExists(values["cli-bin"]!)) {
    emitError("optimize_description", `'${values["cli-bin"]}' not found on PATH.`, {
      code: "optimize.input.cli-missing",
      hint: "Install the CLI or specify --cli-bin.",
    });
    return 2;
  }

  let result: OptimizationResult;
  try {
    result = await optimize(skillDir, queriesPath, {
      rounds: Number(values.rounds),
      candidatesPerRound: Number(values.candidates),
      runs: Number(values.runs),
      trainSplit: Number(values["train-split"]),
      cliBin: values["cli-bin"]!,
      apply: values.apply,
    });
  } catch (exc) {
    emitError("optimize_description", exc instanceof Error ? exc.message : String(exc), {
      code: "optimize.run.error",
    });
    return 2;
  }

  if (useJson) {
    emitJson(result);
  } else {
    emitText(result);
  }

  return result.winner !== result.baseline ? 0 : 1;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  main(process.argv.slice(2)).then((code) => process.exit(code));
}
