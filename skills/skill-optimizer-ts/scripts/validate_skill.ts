#!/usr/bin/env -S npx tsx
/**
 * Validate an agent skill against the Agent Skills specification.
 *
 * Usage:
 *   validate_skill.ts <skill-directory> [--json] [--exit-on-warn]
 *
 * Default output is one issue per line, prefixed with FAIL: or WARN: and a
 * machine-readable code in brackets. With --json, emits a structured object
 * on stdout.
 *
 * Exit codes:
 *   0   no FAIL-level issues (and no WARN if --exit-on-warn)
 *   1   one or more FAIL-level issues
 *   2   bad invocation (path missing, not a directory, etc.)
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { resolve, relative, join } from "node:path";
import { parseArgs } from "node:util";
import { emitError, parseFrontmatter, sanitizeForEcho, type Frontmatter } from "./skill_lib.js";

const NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const MD_LINK_RE = /\]\(([^)]+\.md)\)/g;
const SCRIPT_REF_RE = /`(scripts\/[^`\s]+\.(?:py|sh|js|ts))`/g;
const MAX_NAME = 64;
const MAX_DESCRIPTION = 1024;
const MIN_DESCRIPTION_WARN = 60;
const MAX_COMPATIBILITY = 500;

const ALLOWED_KEYS = new Set([
  // agentskills.io base spec
  "name",
  "description",
  "license",
  "compatibility",
  "metadata",
  "allowed-tools",
  // Platform extensions (Claude Code, Copilot, Codex, VS Code)
  "when_to_use",
  "argument-hint",
  "arguments",
  "disable-model-invocation",
  "user-invocable",
  "model",
  "effort",
  "context",
  "agent",
  "hooks",
  "paths",
  "shell",
]);

export interface Issue {
  severity: "fail" | "warn";
  code: string;
  message: string;
  field?: string;
}

function issueLine(issue: Issue): string {
  const prefix = issue.severity === "fail" ? "FAIL" : "WARN";
  return `${prefix}: [${issue.code}] ${issue.message}`;
}

export function validate(skillDir: string): Issue[] {
  const issues: Issue[] = [];
  const skillMd = join(skillDir, "SKILL.md");
  if (!existsSync(skillMd)) {
    return [
      {
        severity: "fail",
        code: "validate.skill-md.missing",
        message: `SKILL.md not found at ${sanitizeForEcho(skillMd)}`,
      },
    ];
  }

  const text = readFileSync(skillMd, "utf-8");
  const [fm, body] = parseFrontmatter(text);
  if (fm === null) {
    return [
      {
        severity: "fail",
        code: "validate.frontmatter.missing",
        message: "SKILL.md is missing YAML frontmatter (must start with '---')",
      },
    ];
  }

  checkName(fm, skillDir, issues);
  checkDescription(fm, issues);
  checkCompatibility(fm, issues);
  checkUnknownKeys(fm, issues);
  checkReferences(skillDir, body, issues);
  return issues;
}

function checkName(fm: Frontmatter, skillDir: string, issues: Issue[]): void {
  const name = typeof fm.name === "string" ? fm.name : "";
  if (!name) {
    issues.push({
      severity: "fail",
      code: "validate.name.missing",
      message: "'name' field is required",
      field: "name",
    });
    return;
  }
  const safe = sanitizeForEcho(name, 80);
  if (name.length > MAX_NAME) {
    issues.push({
      severity: "fail",
      code: "validate.name.too-long",
      message: `'name' is ${name.length} chars (max ${MAX_NAME})`,
      field: "name",
    });
  }
  if (!NAME_RE.test(name)) {
    issues.push({
      severity: "fail",
      code: "validate.name.bad-format",
      message:
        "'name' must be lowercase a-z/0-9 with single hyphens, no leading/trailing/" +
        `consecutive hyphens. Got: '${safe}'`,
      field: "name",
    });
  }
  const dirName = skillDir.split(/[/\\]/).filter(Boolean).pop() ?? "";
  if (name !== dirName) {
    issues.push({
      severity: "fail",
      code: "validate.name.dir-mismatch",
      message: `'name' ('${safe}') must match parent directory ('${sanitizeForEcho(dirName)}')`,
      field: "name",
    });
  }
}

function checkDescription(fm: Frontmatter, issues: Issue[]): void {
  const desc = typeof fm.description === "string" ? fm.description : "";
  if (!desc) {
    issues.push({
      severity: "fail",
      code: "validate.description.missing",
      message: "'description' is required and must be non-empty",
      field: "description",
    });
    return;
  }
  if (desc.length > MAX_DESCRIPTION) {
    issues.push({
      severity: "fail",
      code: "validate.description.too-long",
      message: `'description' is ${desc.length} chars (max ${MAX_DESCRIPTION})`,
      field: "description",
    });
  }
  if (desc.length < MIN_DESCRIPTION_WARN) {
    issues.push({
      severity: "warn",
      code: "validate.description.too-short",
      message:
        `'description' is only ${desc.length} chars — likely too short to convey ` +
        "when to trigger",
      field: "description",
    });
  }
}

function checkCompatibility(fm: Frontmatter, issues: Issue[]): void {
  const compat = fm.compatibility;
  if (typeof compat === "string" && compat && compat.length > MAX_COMPATIBILITY) {
    issues.push({
      severity: "fail",
      code: "validate.compatibility.too-long",
      message: `'compatibility' is ${compat.length} chars (max ${MAX_COMPATIBILITY})`,
      field: "compatibility",
    });
  }
}

const ALLOWED_KEYS_LIST = [...ALLOWED_KEYS].sort().join(", ");

function checkUnknownKeys(fm: Frontmatter, issues: Issue[]): void {
  const unknown = Object.keys(fm)
    .filter((k) => !ALLOWED_KEYS.has(k))
    .sort();
  for (const key of unknown) {
    const safe = sanitizeForEcho(key, 64);
    issues.push({
      severity: "warn",
      code: "validate.frontmatter.unknown-key",
      message: `unknown frontmatter key '${safe}' (spec defines: ${ALLOWED_KEYS_LIST})`,
      field: key,
    });
  }
}

function withinSkill(skillRoot: string, target: string): boolean {
  const rel = relative(skillRoot, target);
  return rel === "" || (!rel.startsWith("..") && !rel.startsWith(`..${"/"}`) && rel !== "..");
}

function checkRefsExist(
  skillDir: string,
  skillRoot: string,
  refs: string[],
  code: string,
  label: string,
  issues: Issue[],
): void {
  for (const ref of refs) {
    // Skip template placeholder paths like `references/<topic>.md`.
    if (/[<>${}]/.test(ref)) continue;
    const target = resolve(skillDir, ref);
    if (!withinSkill(skillRoot, target)) continue;
    if (!existsSync(target)) {
      issues.push({
        severity: "warn",
        code,
        message: `referenced ${label} does not exist: ${sanitizeForEcho(ref)}`,
      });
    }
  }
}

function checkReferences(skillDir: string, body: string, issues: Issue[]): void {
  const skillRoot = resolve(skillDir);

  const mdRefs = [...body.matchAll(MD_LINK_RE)]
    .map((m) => m[1]!)
    .filter((ref) => !/^(https?:\/\/|#|\/)/.test(ref));
  checkRefsExist(skillDir, skillRoot, mdRefs, "validate.reference.broken", "file", issues);

  const scriptRefs = [...body.matchAll(SCRIPT_REF_RE)].map((m) => m[1]!);
  checkRefsExist(skillDir, skillRoot, scriptRefs, "validate.script.broken", "script", issues);
}

interface Summary {
  fail: number;
  warn: number;
  ok: boolean;
}

function summarize(issues: Issue[]): Summary {
  const fail = issues.filter((i) => i.severity === "fail").length;
  const warn = issues.filter((i) => i.severity === "warn").length;
  return { fail, warn, ok: fail === 0 };
}

function emitText(issues: Issue[], summary: Summary): void {
  for (const issue of issues) {
    console.log(issueLine(issue));
  }
  console.log(`\n${summary.fail} fail(s), ${summary.warn} warning(s)`);
}

function emitJson(skillDir: string, issues: Issue[], summary: Summary): void {
  console.log(JSON.stringify({ skill_dir: skillDir, issues, summary }, null, 2));
}

const HELP = `Validate an agent skill against the Agent Skills specification.

Usage:
  validate_skill.ts <skill-directory> [--json] [--exit-on-warn]

Examples:
  validate_skill.ts path/to/my-skill
  validate_skill.ts ./skill --json
  validate_skill.ts ./skill --exit-on-warn

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

  const skillDir = resolve(skillDirArg.replace(/^~/, process.env.HOME ?? "~"));
  if (!existsSync(skillDir)) {
    emitError("validate_skill", `path does not exist: ${skillDir}`, {
      code: "validate.input.not-found",
      hint: "Check the path and try again.",
    });
    return 2;
  }
  if (!statSync(skillDir).isDirectory()) {
    emitError("validate_skill", `not a directory: ${skillDir}`, {
      code: "validate.input.not-dir",
      hint: "Argument must be a skill directory, not a file.",
    });
    return 2;
  }

  const issues = validate(skillDir);
  const summary = summarize(issues);
  if (useJson) {
    emitJson(skillDir, issues, summary);
  } else {
    emitText(issues, summary);
  }

  if (summary.fail) return 1;
  if (values["exit-on-warn"] && summary.warn) return 1;
  return 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
