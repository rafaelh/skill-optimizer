#!/usr/bin/env -S npx tsx
/**
 * Surface SKILL.md-level opportunities to extract bundled scripts.
 *
 * Scans SKILL.md for procedures the agent would re-derive on every run (long
 * shell blocks) and recommends extracting them into `scripts/`. Per-script
 * quality checks (parseArgs, --format, --quiet, exit codes, package.json
 * deps) are delegated to `validate_agent_tool.ts`.
 *
 * Kinds:
 *   extract-procedure       a long bash block in SKILL.md should become a script
 *   skill-md-missing        the skill has no SKILL.md (recoverable warn)
 *
 * Usage:
 *   recommend_scripts.ts <skill-directory> [--json]
 *
 * Default output is one opportunity per line. With --json, emits a
 * structured object on stdout. Echoed snippets are sanitized.
 *
 * Exit codes:
 *   0   ran successfully (with or without opportunities found)
 *   2   bad invocation (path missing, not a directory)
 *   3   SKILL.md missing (recoverable warn — opportunities list still emitted)
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { resolve, join } from "node:path";
import { parseArgs } from "node:util";
import { emitError, sanitizeForEcho } from "./skill_lib.js";

const LONG_BASH_LINES = 6; // blocks with this many or more non-empty lines flagged
// A fence opens/closes at the start of a line, optionally behind blockquote
// markers. Matching mid-line (or ignoring the `> ` prefix) makes a quoted block
// swallow everything up to the next unquoted fence.
const FENCE_RE = /^(?<prefix>[ \t]*(?:>[ \t]?)*)```(?<info>[^\s`]*)/;
const BASH_LANGS = new Set(["bash", "sh", "shell", "zsh", "fish"]);

export interface Opportunity {
  kind: string;
  where: string;
  title: string;
  why: string;
  suggestion: string;
  severity: "info" | "warn";
}

export function recommend(skillDir: string): Opportunity[] {
  const opps: Opportunity[] = [];
  const skillMd = join(skillDir, "SKILL.md");
  if (!existsSync(skillMd)) {
    opps.push({
      kind: "recommend.skill-md.missing",
      where: skillMd,
      title: "SKILL.md not found",
      why: "Cannot scan a skill without a SKILL.md.",
      suggestion:
        "Create SKILL.md with frontmatter (name, description) before running this script.",
      severity: "warn",
    });
    return opps;
  }

  const body = readFileSync(skillMd, "utf-8");
  scanBashBlocks(body, skillMd, opps);
  return opps;
}

/** Strip leading blockquote markers so quoted commands read like plain ones. */
function unquote(line: string): string {
  return line.replace(/^[ \t]*(?:>[ \t]?)*/, "");
}

function scanBashBlocks(body: string, skillMd: string, opps: Opportunity[]): void {
  const skillMdName = skillMd.split(/[/\\]/).pop()!;
  const lines = body.split("\n");

  let openPrefix: string | null = null;
  let openLine = 0;
  let collected: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const m = FENCE_RE.exec(lines[i]!);
    if (openPrefix === null) {
      // Only open on a bash-family fence; untagged and other-language fences
      // (including a bare closing fence with nothing open) are skipped.
      if (m && BASH_LANGS.has(m.groups!.info!.toLowerCase())) {
        openPrefix = m.groups!.prefix!.trim();
        openLine = i + 1;
        collected = [];
      }
      continue;
    }
    // Inside a block: it closes on a bare fence at the same quote level.
    if (m && m.groups!.info === "" && m.groups!.prefix!.trim() === openPrefix) {
      flagIfLong(collected, skillMdName, openLine, i + 1, opps);
      openPrefix = null;
      continue;
    }
    collected.push(unquote(lines[i]!));
  }
}

function flagIfLong(
  block: string[],
  skillMdName: string,
  startLine: number,
  endLine: number,
  opps: Opportunity[],
): void {
  const nonEmpty = block.filter((line) => line.trim() !== "");
  if (nonEmpty.length < LONG_BASH_LINES) return;
  const snippet = sanitizeForEcho(nonEmpty[0], 80);
  opps.push({
    kind: "recommend.script.extract-procedure",
    where: `${skillMdName}:${startLine}-${endLine}`,
    title: `Long bash block (${nonEmpty.length} lines)`,
    why:
      "Multi-step shell procedures embedded in SKILL.md force the agent to re-derive " +
      `them on every run. The first command was: '${snippet}'.`,
    suggestion:
      "Extract into scripts/<name>.ts with parseArgs, --format, and --quiet. Replace " +
      "the block in SKILL.md with a one-line invocation. Then run " +
      "validate_agent_tool.ts on the new script.",
    severity: "info",
  });
}

function emitText(opps: Opportunity[]): void {
  if (opps.length === 0) {
    console.log("no opportunities found");
    return;
  }
  for (const o of opps) {
    const prefix = o.severity === "warn" ? "WARN" : "INFO";
    console.log(`${prefix}: [${o.kind}] ${o.where} — ${o.title}`);
    console.log(`  why: ${o.why}`);
    console.log(`  suggestion: ${o.suggestion}`);
  }
}

function emitJson(skillDir: string, opps: Opportunity[]): void {
  const byKind: Record<string, number> = {};
  for (const o of opps) {
    byKind[o.kind] = (byKind[o.kind] ?? 0) + 1;
  }
  const payload = {
    skill_dir: skillDir,
    opportunities: opps,
    summary: { total: opps.length, by_kind: byKind },
  };
  console.log(JSON.stringify(payload, null, 2));
}

const HELP = `Surface SKILL.md-level opportunities to extract bundled scripts.

Usage:
  recommend_scripts.ts <skill-directory> [--json]

Examples:
  recommend_scripts.ts path/to/my-skill
  recommend_scripts.ts ./skill --json

For per-script quality checks, use:
  validate_agent_tool.ts <script-path>

Options:
  --format <json|text>   Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr
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
    emitError("recommend_scripts", `path does not exist: ${skillDir}`, {
      code: "recommend.input.not-found",
      hint: "Check the path and try again.",
    });
    return 2;
  }
  if (!statSync(skillDir).isDirectory()) {
    emitError("recommend_scripts", `not a directory: ${skillDir}`, {
      code: "recommend.input.not-dir",
      hint: "Argument must be a skill directory, not a file.",
    });
    return 2;
  }

  const opps = recommend(skillDir);
  if (useJson) {
    emitJson(skillDir, opps);
  } else {
    emitText(opps);
  }

  return opps.some((o) => o.kind === "recommend.skill-md.missing") ? 3 : 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
