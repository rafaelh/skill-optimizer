#!/usr/bin/env -S npx tsx
/**
 * Detect description overlap between agent skills.
 *
 * Two modes, picked from the input shape:
 *
 * 1. Single skill vs siblings. Pass one skill directory plus `--against
 *    <parent-dir>`. Each sibling skill in the parent is compared to the
 *    focal skill; any pair above `--threshold` is flagged.
 *
 * 2. All-pairs. Pass a parent directory containing multiple skills. Every
 *    pair is compared.
 *
 * Algorithm: bag-of-words cosine similarity over description tokens
 * (lowercased, stop-words dropped, hyphens kept as part of compound terms).
 * Stable, deterministic, no LLM in the loop. The output isn't a verdict — a
 * high score is a flag worth investigating, not proof of misfire.
 *
 * Usage:
 *   detect_skill_overlap.ts <parent-dir>
 *   detect_skill_overlap.ts <skill-dir> --against <parent-dir>
 *   detect_skill_overlap.ts <parent-dir> --threshold 0.6 --json
 *
 * Exit codes:
 *   0   no overlap above threshold
 *   1   one or more overlapping pairs found (caller should review)
 *   2   bad invocation (path missing, no skills found)
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { resolve, join } from "node:path";
import { parseArgs } from "node:util";
import { emitError, parseFrontmatter, sanitizeForEcho } from "./skill_lib.js";

const DEFAULT_THRESHOLD = 0.5;
const TOP_SHARED_KEYWORDS = 8;
const MIN_KEYWORD_LEN = 3;

// Conservative English stopword list — short enough that we don't fight real
// domain words, broad enough to keep the cosine score from being dominated
// by noise like "the user wants" appearing in every description.
const STOPWORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "as",
  "at",
  "be",
  "but",
  "by",
  "can",
  "do",
  "does",
  "for",
  "from",
  "has",
  "have",
  "if",
  "in",
  "into",
  "is",
  "it",
  "its",
  "not",
  "of",
  "on",
  "or",
  "so",
  "than",
  "that",
  "the",
  "their",
  "them",
  "then",
  "there",
  "these",
  "they",
  "this",
  "those",
  "to",
  "up",
  "use",
  "user",
  "users",
  "want",
  "wants",
  "was",
  "we",
  "what",
  "when",
  "where",
  "which",
  "who",
  "why",
  "will",
  "with",
  "you",
  "your",
  // frequent "what skills do" boilerplate
  "skill",
  "skills",
  "claude",
  "trigger",
  "triggers",
  "even",
  "explicitly",
  "mention",
  "mentions",
  "asks",
  "ask",
]);

const TOKEN_RE = /[a-z0-9]+(?:[-/][a-z0-9]+)*/g;

export interface SkillEntry {
  name: string;
  path: string;
  description: string;
  tokens: string[];
}

export interface OverlapPair {
  a: string;
  b: string;
  similarity: number;
  shared_keywords: string[];
  code: "overlap.description.collision" | "overlap.trigger.shared-keyword";
}

export function tokenize(description: string): string[] {
  const matches = description.toLowerCase().match(TOKEN_RE) ?? [];
  return matches.filter((t) => !STOPWORDS.has(t) && t.length >= MIN_KEYWORD_LEN);
}

function counter(tokens: string[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const t of tokens) {
    counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return counts;
}

export function cosine(a: string[], b: string[]): number {
  if (a.length === 0 || b.length === 0) return 0;
  const countsA = counter(a);
  const countsB = counter(b);
  const shared = [...countsA.keys()].filter((k) => countsB.has(k));
  const dot = shared.reduce((sum, k) => sum + countsA.get(k)! * countsB.get(k)!, 0);
  const normA = Math.sqrt([...countsA.values()].reduce((s, v) => s + v * v, 0));
  const normB = Math.sqrt([...countsB.values()].reduce((s, v) => s + v * v, 0));
  return normA && normB ? dot / (normA * normB) : 0;
}

export function sharedKeywords(a: string[], b: string[], top = TOP_SHARED_KEYWORDS): string[] {
  const countsA = counter(a);
  const countsB = counter(b);
  const shared = [...countsA.keys()].filter((k) => countsB.has(k));
  // Ties break by codepoint (not localeCompare) so this ranking matches the
  // Python twin's `sorted(..., key=lambda k: (-count, k))` exactly.
  shared.sort((x, y) => {
    const byCount = countsB.get(y)! + countsA.get(y)! - (countsB.get(x)! + countsA.get(x)!);
    if (byCount !== 0) return byCount;
    return x < y ? -1 : x > y ? 1 : 0;
  });
  return shared.slice(0, top);
}

function loadSkill(skillDir: string): SkillEntry | null {
  const skillMd = join(skillDir, "SKILL.md");
  if (!existsSync(skillMd) || !statSync(skillMd).isFile()) return null;
  const text = readFileSync(skillMd, "utf-8");
  const [fm] = parseFrontmatter(text);
  if (fm === null || typeof fm !== "object") return null;
  const name = typeof fm.name === "string" && fm.name ? fm.name : skillDir.split(/[/\\]/).pop()!;
  const description = typeof fm.description === "string" ? fm.description : "";
  return { name, path: skillDir, description, tokens: tokenize(description) };
}

function discoverSkills(parent: string): SkillEntry[] {
  if (!existsSync(parent) || !statSync(parent).isDirectory()) return [];
  const children = readdirSync(parent).sort();
  const skills: SkillEntry[] = [];
  for (const child of children) {
    const childPath = join(parent, child);
    if (!statSync(childPath).isDirectory()) continue;
    const entry = loadSkill(childPath);
    if (entry !== null) skills.push(entry);
  }
  return skills;
}

function comparePairs(pairs: Array<[SkillEntry, SkillEntry]>, threshold: number): OverlapPair[] {
  const overlaps: OverlapPair[] = [];
  for (const [a, b] of pairs) {
    const sim = cosine(a.tokens, b.tokens);
    if (sim < threshold) continue;
    const shared = sharedKeywords(a.tokens, b.tokens);
    const code: OverlapPair["code"] =
      sim >= threshold + 0.1 || shared.length >= 4
        ? "overlap.description.collision"
        : "overlap.trigger.shared-keyword";
    overlaps.push({
      a: a.name,
      b: b.name,
      similarity: Math.round(sim * 10000) / 10000,
      shared_keywords: shared,
      code,
    });
  }
  overlaps.sort((x, y) => y.similarity - x.similarity);
  return overlaps;
}

export function detect(
  target: string,
  opts: { against?: string; threshold?: number } = {},
): [SkillEntry[], OverlapPair[]] {
  const threshold = opts.threshold ?? DEFAULT_THRESHOLD;
  const isSingleSkill = existsSync(join(target, "SKILL.md"));

  if (isSingleSkill) {
    const focal = loadSkill(target);
    if (focal === null) {
      throw new Error(`could not parse SKILL.md at ${target}`);
    }
    if (opts.against === undefined) {
      throw new Error("--against is required when target is a single skill directory");
    }
    const siblings = discoverSkills(opts.against).filter((s) => s.path !== focal.path);
    const pairs: Array<[SkillEntry, SkillEntry]> = siblings.map((s) => [focal, s]);
    return [[focal, ...siblings], comparePairs(pairs, threshold)];
  }

  const skills = discoverSkills(target);
  if (skills.length === 0) {
    throw new Error(`no skills with SKILL.md found under ${target}`);
  }
  const pairs: Array<[SkillEntry, SkillEntry]> = [];
  for (let i = 0; i < skills.length; i++) {
    for (let j = i + 1; j < skills.length; j++) {
      pairs.push([skills[i]!, skills[j]!]);
    }
  }
  return [skills, comparePairs(pairs, threshold)];
}

function emitText(skills: SkillEntry[], overlaps: OverlapPair[]): void {
  if (overlaps.length === 0) {
    console.log(`no overlapping pairs found across ${skills.length} skill(s)`);
    return;
  }
  for (const pair of overlaps) {
    const keywords = pair.shared_keywords.map((k) => sanitizeForEcho(k, 32)).join(", ");
    console.log(
      `WARN: [${pair.code}] ${pair.a} <-> ${pair.b}  similarity=${pair.similarity.toFixed(2)}  shared=[${keywords}]`,
    );
  }
}

function emitJson(skills: SkillEntry[], overlaps: OverlapPair[]): void {
  const payload = {
    skills: skills.map((s) => ({ name: s.name, path: s.path })),
    pairs: overlaps,
    summary: { skills_compared: skills.length, pairs_above_threshold: overlaps.length },
  };
  console.log(JSON.stringify(payload, null, 2));
}

const HELP = `Detect description overlap between agent skills.

Usage:
  detect_skill_overlap.ts <parent-dir>
  detect_skill_overlap.ts <skill-dir> --against <parent-dir> [--json]
  detect_skill_overlap.ts <parent-dir> --threshold 0.4

Examples:
  detect_skill_overlap.ts ~/.claude/skills/
  detect_skill_overlap.ts ./my-skill --against ~/.claude/skills/ --json
  detect_skill_overlap.ts ./skills --threshold 0.4

Options:
  --against <dir>        Parent directory of sibling skills (single-skill target only)
  --threshold <n>        Cosine similarity threshold, 0.0-1.0 (default: ${DEFAULT_THRESHOLD})
  --format <json|text>   Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr
  --exit-on-warn         Return non-zero exit if any pair is above the threshold
  --help                 Show this help message
`;

export function main(argv: string[]): number {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      against: { type: "string" },
      threshold: { type: "string", default: String(DEFAULT_THRESHOLD) },
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
  const targetArg = positionals[0];
  if (!targetArg) {
    console.error(HELP);
    return 2;
  }

  const target = resolve(targetArg);
  if (!existsSync(target)) {
    emitError("detect_skill_overlap", `target does not exist: ${target}`, {
      code: "overlap.input.not-found",
      hint: "Check the path and try again.",
    });
    return 2;
  }
  if (!statSync(target).isDirectory()) {
    emitError("detect_skill_overlap", `target is not a directory: ${target}`, {
      code: "overlap.input.not-dir",
      hint: "Argument must be a directory.",
    });
    return 2;
  }

  let against: string | undefined;
  if (values.against) {
    against = resolve(values.against);
    if (!existsSync(against) || !statSync(against).isDirectory()) {
      emitError("detect_skill_overlap", `--against is not a directory: ${against}`, {
        code: "overlap.input.not-dir",
        hint: "--against must point to an existing directory.",
      });
      return 2;
    }
  }

  let skills: SkillEntry[];
  let overlaps: OverlapPair[];
  try {
    [skills, overlaps] = detect(target, { against, threshold: Number(values.threshold) });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("no skills")) {
      emitError("detect_skill_overlap", msg, {
        code: "overlap.detect.not-found",
        hint: "Ensure the directory contains subdirectories with SKILL.md files.",
      });
      return 3;
    }
    emitError("detect_skill_overlap", msg, { code: "overlap.detect.error" });
    return 2;
  }

  if (useJson) {
    emitJson(skills, overlaps);
  } else {
    emitText(skills, overlaps);
  }

  // Findings are a successful result, not an invocation error — exit 1 is
  // reserved for bad args. Opt into a failing exit with --exit-on-warn, the
  // same way validate_skill.ts and audit_security.ts do.
  return values["exit-on-warn"] && overlaps.length > 0 ? 1 : 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
