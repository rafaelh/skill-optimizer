#!/usr/bin/env -S npx tsx
/**
 * Audit an agent skill against the OWASP Agentic Skills Top 10.
 *
 * Static, high-signal security scan of a skill's SKILL.md, references, and
 * bundled TypeScript scripts. Findings map to the OWASP Agentic Skills Top
 * 10 (https://owasp.org/www-project-agentic-skills-top-10/) and its
 * companion skill development guide.
 *
 * What it checks (statically detectable):
 *
 *   AST01 Malicious Skills        hidden bidi/invisible unicode in instructions;
 *                                 prose that countermands the operator, conceals
 *                                 activity from the user, or directs exfiltration
 *   AST02 Supply Chain            curl|wget piped to a shell; unpinned package.json deps
 *   AST03 Over-Privileged Skills  unrestricted Bash() in allowed-tools, broad globs
 *   AST04 Insecure Metadata       hardcoded secrets / credentials / private keys
 *   AST05 Unsafe Deserialization  eval, new Function, vm.runInThisContext/runInNewContext
 *   AST06 Weak Isolation          child_process.exec(Sync), {shell:true}, destructive fs calls
 *
 * AST07 (Update Drift), AST08 (Poor Scanning), AST09 (No Governance), and
 * AST10 (Cross-Platform Reuse) are process/governance risks that can't be
 * caught by a static scan — see references/security.md for the human checks.
 *
 * Usage:
 *   audit_security.ts <skill-directory> [--json] [--exit-on-warn]
 *
 * Exit codes:
 *   0   no FAIL-level findings (and no WARN if --exit-on-warn)
 *   1   one or more FAIL-level findings
 *   2   bad invocation (path missing, not a directory, etc.)
 */

import { existsSync, readFileSync, statSync, readdirSync } from "node:fs";
import { resolve, join, relative } from "node:path";
import { parseArgs } from "node:util";
import ts from "typescript";
import {
  DANGEROUS_UNICODE,
  emitError,
  parseFrontmatter,
  sanitizeForEcho,
  type Frontmatter,
} from "./skill_lib.js";

// --- AST04: hardcoded secrets ------------------------------------------------
// High-confidence: the token shape itself is the secret, so a hit is reported
// even if it contains a word like EXAMPLE. These target real secret material,
// not env references like process.env.ANTHROPIC_API_KEY, the correct form.
const SECRET_PATTERNS: Array<[string, RegExp]> = [
  ["AWS access key id", /\bAKIA[0-9A-Z]{16}\b/g],
  ["GitHub token", /\bgh[pousr]_[A-Za-z0-9]{36,}\b/g],
  ["Slack token", /\bxox[baprs]-[0-9A-Za-z-]{10,}\b/g],
  ["Google API key", /\bAIza[0-9A-Za-z_-]{35}\b/g],
  ["private key block", /-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----/g],
];

// Lower-confidence: a credential-shaped assignment to a quoted literal. Run
// the allowlist over the value to drop obvious placeholders and env-var
// renderings.
const CREDENTIAL_ASSIGN_RE =
  /\b(?:api[_-]?key|secret|token|passwd|password|client[_-]?secret)\b\s*[:=]\s*(['"])([^\s'"]{12,})\1/gi;

const SECRET_ALLOWLIST_RE =
  /(your[_-]?|example|placeholder|xxx+|<[^>]+>|\$\{?[A-Z_]+\}?|changeme|redacted|\.\.\.|fake|dummy|sample|test[_-]?(?:key|token|secret))/i;

// --- AST01: prompt injection in agent-readable prose -------------------------
// A skill's prose *is* the agent's instructions, so a directive that countermands
// the operator, hides activity from the user, or ships credentials off-box is an
// attack with no script involved. Matching runs over prose only — code spans are
// blanked first, so a security doc that exhibits a payload as code (the way this
// repo's own references do) doesn't trip the check that hunts for it.
const INJECTION_PATTERNS: Array<[string, string, RegExp]> = [
  [
    "security.body.instruction-override",
    "countermands the operator's instructions",
    /\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(?:previous|prior|earlier|preceding|above|all)\b[^.\n]{0,40}\b(?:instruction|prompt|rule|direction|guideline|constraint)s?\b/gi,
  ],
  [
    "security.body.prompt-replacement",
    "presents itself as a replacement system prompt",
    /\b(?:new|updated|revised|real|actual)\s+(?:system\s+)?(?:prompt|instructions)\s*:/gi,
  ],
  [
    // `user` must not be followed by `to <verb>`: "don't tell the user to run X"
    // withholds an instruction, which is ordinary guidance. Concealment is
    // "don't tell the user that/about …".
    "security.body.conceal-from-user",
    "tells the agent to hide its activity from the user",
    /\b(?:do not|don't|never|avoid)\b[^.\n]{0,30}\b(?:tell|inform|notify|mention|reveal|disclose|show|alert)\b[^.\n]{0,30}\buser\b(?!\s+to\s)|\bwithout\s+(?:telling|informing|notifying|alerting)\s+(?:the\s+)?user\b(?!\s+to\s)/gi,
  ],
];

// Exfiltration needs both halves in one sentence — a sink verb *and* something
// worth stealing. Either alone is ordinary prose ("send the request", "your API key").
const EXFIL_SINK_RE = /\b(?:exfiltrat\w+|upload|post|send|transmit|forward|email|leak)\w*\b/i;
const EXFIL_TARGET_RE =
  /(?:[~$]?\/?\.(?:ssh|aws|netrc|npmrc|env)\b)|\bid_(?:rsa|ed25519|ecdsa)\b|\bprivate[ _-]key\b|\bcredentials?\b|\bapi[ _-]?keys?\b|\baccess[ _-]?tokens?\b/i;
// "Never ship a secret … a committed key is a leaked key" is advice, not an
// attack. Judged per sentence: a negation anywhere in the clause disarms it, and
// a sink verb can't pair with a target from an unrelated sentence.
const NEGATION_RE =
  /\b(?:not|never|avoid|refuse|forbid|prohibit|don't|doesn't|shouldn't|mustn't)\b/i;
// Split on terminators followed by whitespace, so `SKILL.md` and `0.5` survive.
const SENTENCE_SPLIT_RE = /(?<=[.!?])\s+|\n/g;

// --- AST02: supply chain -----------------------------------------------------
// Require a URL scheme between the fetch and the pipe: a real fetch-and-run
// always names a remote artifact (curl https://… | sh), which keeps prose
// that merely describes the anti-pattern (curl … | sh) from matching.
const CURL_PIPE_SHELL_RE = /(?:curl|wget)\b[^\n|]*:\/\/[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b/gi;

// --- AST05: unsafe deserialization / dynamic eval ----------------------------
const UNSAFE_DESERIALIZE: Array<[string, RegExp]> = [
  ["eval()", /(?<![A-Za-z0-9_.])eval\s*\(/g],
  ["new Function()", /\bnew\s+Function\s*\(/g],
  ["vm.runInThisContext()", /\bvm\.runInThisContext\s*\(/g],
  ["vm.runInNewContext()", /\bvm\.runInNewContext\s*\(/g],
];

// --- AST06: weak isolation / injection ---------------------------------------
const SHELL_INJECTION: Array<[string, RegExp]> = [
  [
    "child_process.exec()/execSync() (always spawns a shell)",
    // Require the child_process qualifier, or a bare (unqualified) call —
    // never a property access like `pattern.exec(str)`, which is the
    // extremely common RegExp.prototype.exec() and unrelated to shelling out.
    /(?:\bchild_process\.exec(?:Sync)?\s*\(|(?<!\.)\bexec(?:Sync)?\s*\()/g,
  ],
  ["{ shell: true } option", /shell\s*:\s*true/g],
];
const DANGEROUS_FS_RE =
  /\b(?:fs\.)?(?:rmSync|rm)\s*\([^)]*recursive\s*:\s*true[^)]*\)|\brimraf\s*\(/g;

export interface Finding {
  severity: "fail" | "warn" | "info";
  code: string;
  ast: string;
  message: string;
  where?: string;
}

function findingLine(f: Finding): string {
  const loc = f.where ? ` ${f.where}` : "";
  return `${f.severity.toUpperCase()}: [${f.code}] (${f.ast})${loc} — ${f.message}`;
}

function lineOf(text: string, offset: number): number {
  return text.slice(0, offset).split("\n").length;
}

const BLANK_LITERAL_KINDS = new Set<ts.SyntaxKind>([
  ts.SyntaxKind.StringLiteral,
  ts.SyntaxKind.NoSubstitutionTemplateLiteral,
  ts.SyntaxKind.TemplateHead,
  ts.SyntaxKind.TemplateMiddle,
  ts.SyntaxKind.TemplateTail,
  ts.SyntaxKind.RegularExpressionLiteral,
]);

/**
 * Return content with string/template-literal/regex-literal/comment text
 * replaced by spaces. Offsets and line numbers are preserved (only
 * non-newline chars are blanked), so findings still report accurate
 * locations. This is the TS analog of Python's tokenize-based blanking: it
 * means `eval(`, `shell: true`, etc. only match real code — not the same
 * text quoted inside a string, a comment, or a pattern definition (e.g. this
 * auditor's own rule table).
 *
 * Uses the full parser (ts.createSourceFile) rather than the raw scanner:
 * the raw scanner requires manually re-scanning `}` as a template
 * continuation token on every substitution boundary, and skipping that
 * corrupts tokenization for the rest of the file. Walking the parsed AST
 * sidesteps that entirely — template boundaries are already resolved.
 */
function blankStringsAndComments(content: string): string {
  const chars = content.split("");
  const blankRange = (start: number, end: number): void => {
    for (let i = start; i < end; i++) {
      if (chars[i] !== "\n") chars[i] = " ";
    }
  };

  let sourceFile: ts.SourceFile;
  try {
    sourceFile = ts.createSourceFile(
      "input.ts",
      content,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    );
  } catch {
    return content;
  }

  // Blanking is idempotent, so nodes that share a leading comment (a statement
  // and its first token, say) can each blank it without harm.
  const blankLeadingComments = (node: ts.Node): void => {
    for (const range of ts.getLeadingCommentRanges(content, node.getFullStart()) ?? []) {
      blankRange(range.pos, range.end);
    }
  };

  const visit = (node: ts.Node): void => {
    blankLeadingComments(node);
    if (BLANK_LITERAL_KINDS.has(node.kind)) {
      blankRange(node.getStart(sourceFile), node.getEnd());
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  blankLeadingComments(sourceFile.endOfFileToken); // trailing comment at EOF

  return chars.join("");
}

// Test fixtures intentionally embed mock secrets and dangerous-looking sample
// code; build artifacts aren't shipped. `*.test.ts` is this project's
// colocated-test convention (no separate tests/ subdirectory), so it's
// excluded the same way Python excludes its `tests/` directory.
const EXCLUDED_DIRS = new Set(["tests", "node_modules", ".git", "dist"]);

function isExcluded(entry: string): boolean {
  return EXCLUDED_DIRS.has(entry) || entry.endsWith(".test.ts");
}

function walk(dir: string, predicate: (p: string) => boolean): string[] {
  if (!existsSync(dir)) return [];
  const results: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (isExcluded(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      results.push(...walk(full, predicate));
    } else if (predicate(full)) {
      results.push(full);
    }
  }
  return results.sort();
}

export function audit(skillDir: string): Finding[] {
  const findings: Finding[] = [];
  const skillMd = join(skillDir, "SKILL.md");
  if (!existsSync(skillMd)) {
    return [
      {
        severity: "fail",
        code: "security.skill-md.missing",
        ast: "AST08",
        message: `SKILL.md not found at ${sanitizeForEcho(skillMd)}`,
      },
    ];
  }

  const skillText = readFileSync(skillMd, "utf-8");
  const [fm, body] = parseFrontmatter(skillText);

  checkAllowedTools(fm, findings);
  checkHiddenUnicode(body, findings);

  const scriptFiles = walk(join(skillDir, "scripts"), (p) => p.endsWith(".ts"));
  const refFiles = walk(join(skillDir, "references"), (p) => p.endsWith(".md"));
  const packageJsonFiles = walk(skillDir, (p) => p.endsWith("package.json"));
  const textFiles = [skillMd, ...refFiles];

  // Secrets can hide in any shipped file. Fetch-and-run as an *instruction*
  // only matters in agent-readable text — inside a script it's a subprocess
  // string the shell-injection check already covers, so scanning .ts for it
  // just trips on comments that document the pattern.
  for (const path of textFiles) {
    const content = readFileSync(path, "utf-8");
    const rel = relative(skillDir, path);
    checkSecrets(content, rel, findings);
    checkCurlPipeShell(content, rel, findings);
    checkPromptInjection(content, rel, findings);
  }

  for (const path of scriptFiles) {
    const content = readFileSync(path, "utf-8");
    const rel = relative(skillDir, path);
    checkSecrets(content, rel, findings);
    // Code-construct checks run on source with strings/comments blanked, so
    // they match real calls rather than the same text quoted in a comment.
    const code = blankStringsAndComments(content);
    checkUnsafeDeserialization(code, rel, findings);
    checkShellInjection(code, rel, findings);
    checkDangerousFs(code, rel, findings);
  }

  for (const path of packageJsonFiles) {
    const content = readFileSync(path, "utf-8");
    const rel = relative(skillDir, path);
    checkUnpinnedDeps(content, rel, findings);
  }

  if (findings.length === 0) {
    findings.push({
      severity: "info",
      code: "security.ok",
      ast: "AST08",
      message:
        "no statically-detectable issues found. Process risks (AST07/09/10) still need " +
        "the human checks in references/security.md.",
    });
  }
  return findings;
}

function checkAllowedTools(fm: Frontmatter | null, findings: Finding[]): void {
  if (!fm) return;
  const raw = fm["allowed-tools"];
  if (!raw || typeof raw !== "string") return;
  const tokens = raw.match(/[A-Za-z_]+(?:\([^)]*\))?/g) ?? [];
  for (const tok of tokens) {
    const name = tok.split("(")[0]!;
    if (name !== "Bash" && name !== "Shell") continue;
    if (!tok.includes("(")) {
      findings.push({
        severity: "warn",
        code: "security.tools.unrestricted-bash",
        ast: "AST03",
        message:
          `allowed-tools grants '${tok}' with no command filter. Scope it, e.g. ` +
          "Bash(npx tsx *), so the skill can't run arbitrary shell.",
        where: "SKILL.md",
      });
    } else {
      const inner = tok.slice(tok.indexOf("(") + 1, -1).trim();
      if (inner.startsWith("*") || inner === ":*") {
        findings.push({
          severity: "warn",
          code: "security.tools.broad-bash-glob",
          ast: "AST03",
          message:
            `allowed-tools pattern '${tok}' is an unrestricted wildcard, equivalent to ` +
            "full shell access. Pin the command prefix.",
          where: "SKILL.md",
        });
      }
    }
  }
}

function checkHiddenUnicode(body: string, findings: Finding[]): void {
  const seen = new Set<string>();
  for (const ch of body) {
    if (DANGEROUS_UNICODE.has(ch) && !seen.has(ch)) {
      seen.add(ch);
      const codepoint = ch.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0");
      findings.push({
        severity: "warn",
        code: "security.body.hidden-unicode",
        ast: "AST01",
        message:
          `SKILL.md contains invisible/bidi-override character U+${codepoint}. Agent-readable ` +
          "instructions must be visible; hidden text is a prompt-injection vector (Trojan Source).",
        where: "SKILL.md",
      });
    }
  }
}

function checkSecrets(content: string, rel: string, findings: Finding[]): void {
  const report = (label: string, index: number, matchText: string): void => {
    findings.push({
      severity: "fail",
      code: "security.secret.hardcoded",
      ast: "AST04",
      message:
        `possible ${label} embedded: '${sanitizeForEcho(matchText, 48)}'. Move secrets to ` +
        "environment variables; never ship them in a skill.",
      where: `${rel}:${lineOf(content, index)}`,
    });
  };
  for (const [label, pat] of SECRET_PATTERNS) {
    for (const m of content.matchAll(pat)) {
      report(label, m.index!, m[0]);
    }
  }
  for (const m of content.matchAll(CREDENTIAL_ASSIGN_RE)) {
    const value = m[2]!;
    if (!SECRET_ALLOWLIST_RE.test(value)) {
      report("hardcoded credential assignment", m.index!, m[0]);
    }
  }
}

function checkCurlPipeShell(content: string, rel: string, findings: Finding[]): void {
  for (const m of content.matchAll(CURL_PIPE_SHELL_RE)) {
    findings.push({
      severity: "warn",
      code: "security.exec.curl-pipe-shell",
      ast: "AST02",
      message:
        `pipes a remote download straight into a shell ('${sanitizeForEcho(m[0], 60)}'). Pin ` +
        "and verify the artifact (checksum/signature) before executing it.",
      where: `${rel}:${lineOf(content, m.index!)}`,
    });
  }
}

/** Blank fenced blocks and inline code, preserving length and newlines so
 *  reported line numbers still point at the original file. */
function blankCodeSpans(text: string): string {
  const blank = (m: string) => [...m].map((c) => (c === "\n" ? "\n" : " ")).join("");
  return text.replace(/```[\s\S]*?```/g, blank).replace(/`[^`\n]+`/g, blank);
}

/** (offset, sentence) pairs, so a sentence-scoped scan can still report a file position. */
function iterSentences(text: string): Array<[number, string]> {
  const spans: Array<[number, string]> = [];
  let start = 0;
  for (const m of text.matchAll(SENTENCE_SPLIT_RE)) {
    spans.push([start, text.slice(start, m.index!)]);
    start = m.index! + m[0].length;
  }
  spans.push([start, text.slice(start)]);
  return spans;
}

/** AST01: malicious instructions written as plain prose. */
function checkPromptInjection(content: string, rel: string, findings: Finding[]): void {
  const prose = blankCodeSpans(content);

  for (const [code, what, pattern] of INJECTION_PATTERNS) {
    for (const m of prose.matchAll(pattern)) {
      findings.push({
        severity: "warn",
        code,
        ast: "AST01",
        message:
          `agent-readable text ${what} ('${sanitizeForEcho(m[0], 80)}'). A skill's prose ` +
          "becomes the agent's instructions — this is a prompt-injection vector.",
        where: `${rel}:${lineOf(content, m.index!)}`,
      });
    }
  }

  for (const [offset, sentence] of iterSentences(prose)) {
    const target = EXFIL_TARGET_RE.exec(sentence);
    const sink = EXFIL_SINK_RE.exec(sentence);
    if (!target || !sink || NEGATION_RE.test(sentence)) continue;
    findings.push({
      severity: "warn",
      code: "security.body.exfiltration",
      ast: "AST01",
      message:
        `agent-readable text directs '${sink[0]}' of '${target[0]}'. Skills must not ` +
        "instruct the agent to move credentials or private keys off the user's machine.",
      where: `${rel}:${lineOf(content, offset)}`,
    });
  }
}

function checkUnsafeDeserialization(code: string, rel: string, findings: Finding[]): void {
  for (const [label, pat] of UNSAFE_DESERIALIZE) {
    for (const m of code.matchAll(pat)) {
      findings.push({
        severity: "warn",
        code: "security.script.unsafe-deserialization",
        ast: "AST05",
        message:
          `${label} can execute attacker-controlled data. Use a safe parser (JSON.parse, a ` +
          "schema validator) or validate input before evaluating it.",
        where: `${rel}:${lineOf(code, m.index!)}`,
      });
    }
  }
}

function checkShellInjection(code: string, rel: string, findings: Finding[]): void {
  for (const [label, pat] of SHELL_INJECTION) {
    for (const m of code.matchAll(pat)) {
      findings.push({
        severity: "warn",
        code: "security.script.shell-injection",
        ast: "AST06",
        message: `${label} invites command injection. Prefer execFile/spawn with an argument array and shell:false.`,
        where: `${rel}:${lineOf(code, m.index!)}`,
      });
    }
  }
}

function checkDangerousFs(code: string, rel: string, findings: Finding[]): void {
  for (const m of code.matchAll(DANGEROUS_FS_RE)) {
    findings.push({
      severity: "warn",
      code: "security.script.dangerous-fs",
      ast: "AST06",
      message: `destructive filesystem call ('${sanitizeForEcho(m[0], 40)}'). Confirm the target is validated and inside the skill's working area; add --dry-run.`,
      where: `${rel}:${lineOf(code, m.index!)}`,
    });
  }
}

function isPinned(spec: string): boolean {
  return /^\d+\.\d+\.\d+(-[\w.]+)?$/.test(spec.trim());
}

function checkUnpinnedDeps(content: string, rel: string, findings: Finding[]): void {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(content);
  } catch {
    return;
  }
  const depFields = ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"];
  for (const field of depFields) {
    const deps = parsed[field];
    if (!deps || typeof deps !== "object") continue;
    for (const [name, spec] of Object.entries(deps as Record<string, unknown>)) {
      if (typeof spec !== "string") continue;
      if (!isPinned(spec)) {
        findings.push({
          severity: "warn",
          code: "security.deps.unpinned",
          ast: "AST02",
          message:
            `dependency '${name}' is not pinned to an exact version ('${sanitizeForEcho(spec, 24)}' ` +
            "is a range). Pin it so a compromised upstream release can't be pulled in silently.",
          where: rel,
        });
      }
    }
  }
}

function emitText(findings: Finding[]): void {
  for (const f of findings) {
    console.log(findingLine(f));
  }
}

function emitJson(skillDir: string, findings: Finding[]): void {
  const reportable = findings.filter((f) => f.code !== "security.ok");
  const bySeverity: Record<string, number> = {};
  const byAst: Record<string, number> = {};
  for (const f of reportable) {
    bySeverity[f.severity] = (bySeverity[f.severity] ?? 0) + 1;
    byAst[f.ast] = (byAst[f.ast] ?? 0) + 1;
  }
  const payload = {
    skill_dir: skillDir,
    findings,
    summary: {
      total: reportable.length,
      by_severity: bySeverity,
      by_ast: byAst,
    },
  };
  console.log(JSON.stringify(payload, null, 2));
}

const HELP = `Audit an agent skill against the OWASP Agentic Skills Top 10.

Usage:
  audit_security.ts <skill-directory> [--json] [--exit-on-warn]

Examples:
  audit_security.ts path/to/my-skill
  audit_security.ts ./skill --json
  audit_security.ts ./skill --exit-on-warn   # for CI / pre-commit

Options:
  --format <json|text>  Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr
  --exit-on-warn         Return non-zero exit if any WARN-level findings are present
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
    emitError("audit_security", `path does not exist: ${skillDir}`, {
      code: "security.input.not-found",
      hint: "Check the path and try again.",
    });
    return 2;
  }
  if (!statSync(skillDir).isDirectory()) {
    emitError("audit_security", `not a directory: ${skillDir}`, {
      code: "security.input.not-dir",
      hint: "Argument must be a skill directory, not a file.",
    });
    return 2;
  }

  const findings = audit(skillDir);
  if (useJson) {
    emitJson(skillDir, findings);
  } else {
    emitText(findings);
  }

  const fails = findings.filter((f) => f.severity === "fail").length;
  const warns = findings.filter((f) => f.severity === "warn").length;
  if (fails) return 1;
  if (values["exit-on-warn"] && warns) return 1;
  return 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
