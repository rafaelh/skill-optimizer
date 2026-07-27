#!/usr/bin/env -S npx tsx
/**
 * Analyze an agent skill for content anti-patterns.
 *
 * Heuristic checks: declarative description openings, missing trigger
 * contexts, body too long for progressive disclosure, generic filler
 * phrases, references introduced without a load trigger, mega-skill
 * structure.
 *
 * Usage:
 *   analyze_skill.ts <skill-directory> [--json] [--exit-on-warn]
 *
 * Exit codes:
 *   0   no FAIL-level issues (and no WARN if --exit-on-warn)
 *   1   one or more FAIL-level issues
 *   2   bad invocation (path missing, not a directory, etc.)
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { resolve, join } from "node:path";
import { parseArgs } from "node:util";
import { emitError, parseFrontmatter, sanitizeForEcho } from "./skill_lib.js";

const GENERIC_PHRASES = [
  /\bhandle errors appropriately\b/i,
  /\bfollow best practices\b/i,
  /\bas needed\b/i,
  /\bif applicable\b/i,
  /\bas appropriate\b/i,
  /\busing standard practices\b/i,
  /\bproperly\b(?! formatted| escaped)/i,
];

const DECLARATIVE_STARTS = [
  "this skill ",
  "this command ",
  "a skill that ",
  "helps with ",
  "helps the ",
];

const REFERENCE_TRIGGER_WORDS = ["when ", "if ", "before ", "after ", "during ", "read ", "load "];

const MAX_BODY_LINES = 500;
const MAX_BODY_TOKENS = 5000;
const MIN_DESCRIPTION_QUALITY = 100;
const LARGE_SKILL_TOKENS = 1500;
const MEGA_SKILL_H2_COUNT = 12;

const REFERENCE_LINK_RE = /\[([^\]]+)\]\((references\/[^)]+\.md|assets\/[^)]+|scripts\/[^)]+)\)/g;
const GOTCHAS_HEADER_RE = /^#+\s*Gotchas?\b/im;

export interface Issue {
  severity: "fail" | "warn" | "info";
  code: string;
  message: string;
  field?: string;
}

/** Size measurements the checks are derived from; also reported for their own sake. */
export interface Stats {
  body_lines: number;
  approx_tokens: number;
  h2_sections: number;
  description_chars: number;
}

export interface Analysis {
  issues: Issue[];
  /** null when analysis stopped early — an unreadable skill has nothing to measure. */
  stats: Stats | null;
}

function issueLine(issue: Issue): string {
  const prefix = { fail: "FAIL", warn: "WARN", info: "INFO" }[issue.severity];
  return `${prefix}: [${issue.code}] ${issue.message}`;
}

function statsLine(stats: Stats): string {
  return issueLine({
    severity: "info",
    code: "analyze.stats",
    message:
      `body = ${stats.body_lines} lines, ~${stats.approx_tokens} tokens, ` +
      `${stats.h2_sections} H2 sections; description = ${stats.description_chars} chars.`,
  });
}

function stripCodeSpans(text: string): string {
  return text.replace(/```[\s\S]*?```/g, "").replace(/`[^`\n]+`/g, "");
}

function unreadable(code: string, message: string): Analysis {
  return { issues: [{ severity: "fail", code, message }], stats: null };
}

export function analyze(skillDir: string): Analysis {
  const skillMd = join(skillDir, "SKILL.md");
  if (!existsSync(skillMd)) {
    return unreadable(
      "analyze.skill-md.missing",
      `SKILL.md not found at ${sanitizeForEcho(skillMd)}`,
    );
  }
  const text = readFileSync(skillMd, "utf-8");
  const [fm, body] = parseFrontmatter(text);
  if (fm === null) {
    return unreadable("analyze.frontmatter.missing", "SKILL.md is missing YAML frontmatter");
  }
  const desc = typeof fm.description === "string" ? fm.description : "";
  const stats = measure(body, desc);

  const issues: Issue[] = [];
  checkDescription(desc, issues);
  checkBodySize(stats, issues);
  checkGenericFiller(stripCodeSpans(body), issues);
  checkGotchas(body, stats.approx_tokens, issues);
  checkReferenceLoadTriggers(body, issues);
  checkMegaSkill(stats.h2_sections, issues);
  return { issues, stats };
}

function measure(body: string, desc: string): Stats {
  const lines = body.split("\n");
  return {
    body_lines: lines.length,
    approx_tokens: Math.max(1, Math.floor(body.length / 4)),
    h2_sections: lines.filter((line) => line.startsWith("## ")).length,
    description_chars: desc.length,
  };
}

function checkDescription(desc: string, issues: Issue[]): void {
  if (!desc) return;
  const safe = sanitizeForEcho(desc, 120);
  const descLow = desc.toLowerCase();
  if (DECLARATIVE_STARTS.some((s) => descLow.startsWith(s))) {
    issues.push({
      severity: "warn",
      code: "analyze.description.declarative",
      message:
        `description starts declaratively ('${safe}'). Prefer imperative form: ` +
        "'Use this skill when...' or '<Verb> ...'.",
      field: "description",
    });
  }
  const hasWhen = descLow.includes("when ") || descLow.includes("use this skill");
  if (!hasWhen) {
    issues.push({
      severity: "warn",
      code: "analyze.description.no-trigger",
      message:
        "description does not say *when* to use the skill. Add explicit trigger " +
        "contexts ('Use when the user wants to ...').",
      field: "description",
    });
  }
  if (desc.length < MIN_DESCRIPTION_QUALITY) {
    issues.push({
      severity: "warn",
      code: "analyze.description.thin",
      message:
        `description is only ${desc.length} chars. Effective descriptions list ` +
        "multiple trigger contexts and usually run 200-600 chars.",
      field: "description",
    });
  }
}

function checkBodySize(stats: Stats, issues: Issue[]): void {
  if (stats.body_lines > MAX_BODY_LINES) {
    issues.push({
      severity: "warn",
      code: "analyze.body.lines-over-limit",
      message:
        `SKILL.md body is ${stats.body_lines} lines (>${MAX_BODY_LINES}). Move detail to ` +
        "references/, scripts/, or assets/.",
    });
  }
  if (stats.approx_tokens > MAX_BODY_TOKENS) {
    issues.push({
      severity: "warn",
      code: "analyze.body.tokens-over-limit",
      message: `SKILL.md body is ~${stats.approx_tokens} tokens (>${MAX_BODY_TOKENS}). Apply progressive disclosure.`,
    });
  }
}

function checkGenericFiller(prose: string, issues: Issue[]): void {
  for (const pat of GENERIC_PHRASES) {
    const m = pat.exec(prose);
    if (m) {
      const safe = sanitizeForEcho(m[0]);
      issues.push({
        severity: "warn",
        code: "analyze.body.generic-filler",
        message: `generic filler detected: '${safe}'. Replace with concrete gotchas or specific procedures.`,
      });
    }
  }
}

function checkGotchas(body: string, approxTokens: number, issues: Issue[]): void {
  if (approxTokens > LARGE_SKILL_TOKENS && !GOTCHAS_HEADER_RE.test(body)) {
    issues.push({
      severity: "info",
      code: "analyze.body.gotchas-missing",
      message:
        "no 'Gotchas' section. Consider adding one for non-obvious environment-specific " +
        "facts the agent would otherwise miss.",
    });
  }
}

function checkReferenceLoadTriggers(body: string, issues: Issue[]): void {
  for (const m of body.matchAll(REFERENCE_LINK_RE)) {
    const target = m[2]!;
    const start = Math.max(0, m.index! - 220);
    const end = Math.min(body.length, m.index! + m[0].length + 220);
    const ctx = body.slice(start, end).toLowerCase();
    if (!REFERENCE_TRIGGER_WORDS.some((t) => ctx.includes(t))) {
      issues.push({
        severity: "warn",
        code: "analyze.reference.no-trigger",
        message:
          `reference '${sanitizeForEcho(target)}' mentioned without a load trigger. ` +
          "Tell the agent *when* to load it (e.g., 'Read X when Y').",
      });
    }
  }
}

function checkMegaSkill(h2Count: number, issues: Issue[]): void {
  if (h2Count > MEGA_SKILL_H2_COUNT) {
    issues.push({
      severity: "info",
      code: "analyze.body.mega-skill",
      message: `${h2Count} H2 sections — possible mega-skill. Consider splitting into focused skills.`,
    });
  }
}

function emitText(analysis: Analysis): void {
  for (const issue of analysis.issues) {
    console.log(issueLine(issue));
  }
  if (analysis.stats) {
    console.log(statsLine(analysis.stats));
  }
}

function emitJson(skillDir: string, analysis: Analysis): void {
  const payload = {
    skill_dir: skillDir,
    issues: analysis.issues,
    stats: analysis.stats ?? {},
  };
  console.log(JSON.stringify(payload, null, 2));
}

const HELP = `Analyze an agent skill for content anti-patterns.

Usage:
  analyze_skill.ts <skill-directory> [--json] [--exit-on-warn]

Examples:
  analyze_skill.ts path/to/my-skill
  analyze_skill.ts ./skill --json

Options:
  --format <json|text>  Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr
  --exit-on-warn         Return non-zero exit if any WARN-level issues are found
  --help                 Show this help message
`;

export function main(argv: string[]): number {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      format: { type: "string", default: "text" },
      json: { type: "boolean", default: false },
      quiet: { type: "boolean", default: false },
      "exit-on-warn": { type: "boolean", default: false },
      help: { type: "boolean", default: false },
    },
  });

  if (values.help) {
    console.log(HELP);
    return 0;
  }

  const useJson = values.json || values.format === "json";
  const skillDirArg = positionals[0];
  if (!skillDirArg) {
    console.error(HELP);
    return 2;
  }

  const skillDir = resolve(skillDirArg);
  if (!existsSync(skillDir)) {
    emitError("analyze_skill", `path does not exist: ${skillDir}`, {
      code: "analyze.input.not-found",
      hint: "Check the path and try again.",
    });
    return 2;
  }
  if (!statSync(skillDir).isDirectory()) {
    emitError("analyze_skill", `not a directory: ${skillDir}`, {
      code: "analyze.input.not-dir",
      hint: "Argument must be a skill directory, not a file.",
    });
    return 2;
  }

  const analysis = analyze(skillDir);
  if (useJson) {
    emitJson(skillDir, analysis);
  } else {
    emitText(analysis);
  }

  const { issues } = analysis;
  if (issues.some((i) => i.severity === "fail")) return 1;
  if (values["exit-on-warn"] && issues.some((i) => i.severity === "warn")) return 1;
  return 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
