#!/usr/bin/env -S npx tsx
/**
 * Validate that a TypeScript script meets the agent tool interface contract.
 *
 * Checks: parseArgs (or another arg parser) present, --format/--quiet flags,
 * reachable exit codes 0/1/2/3, no interactive prompts, no stdout/stderr
 * mixing, non-builtin imports declared in a reachable package.json.
 *
 * Usage:
 *   validate_agent_tool.ts <script-path> [--json] [--exit-on-warn]
 *
 * Exit codes:
 *   0   All checks pass
 *   1   One or more FAIL-level issues found
 *   2   Bad invocation (path missing, unreadable)
 */

import { existsSync, readFileSync } from "node:fs";
import { builtinModules } from "node:module";
import { dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import ts from "typescript";

const EXIT_OK = 0;
const EXIT_FAIL = 1;
const EXIT_BAD_INVOCATION = 2;

export interface Finding {
  code: string;
  severity: "fail" | "warn" | "info";
  message: string;
  line?: number;
}

function ast(src: string): ts.SourceFile {
  return ts.createSourceFile("tool.ts", src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
}

function lineOf(sourceFile: ts.SourceFile, pos: number): number {
  return sourceFile.getLineAndCharacterOfPosition(pos).line + 1;
}

function forEachNode(node: ts.Node, visit: (n: ts.Node) => void): void {
  visit(node);
  ts.forEachChild(node, (child) => forEachNode(child, visit));
}

/**
 * Resolve the possible numeric exit-code values of an expression: a literal,
 * a named EXIT_* constant, or either branch of a ternary (idiomatic for
 * `return cond ? 1 : 0`). Returns all statically-derivable values.
 */
function resolveExitValues(
  arg: ts.Expression | undefined,
  constMap: Map<string, number>,
): number[] {
  if (!arg) return [];
  if (ts.isNumericLiteral(arg)) return [Number(arg.text)];
  if (ts.isIdentifier(arg)) {
    const val = constMap.get(arg.text);
    return val !== undefined ? [val] : [];
  }
  if (ts.isConditionalExpression(arg)) {
    return [
      ...resolveExitValues(arg.whenTrue, constMap),
      ...resolveExitValues(arg.whenFalse, constMap),
    ];
  }
  if (ts.isParenthesizedExpression(arg)) {
    return resolveExitValues(arg.expression, constMap);
  }
  return [];
}

/** Map every `const NAME = <numeric literal>` declaration in the file, for resolving EXIT_* constants. */
function buildNumericConstMap(sourceFile: ts.SourceFile): Map<string, number> {
  const constMap = new Map<string, number>();
  forEachNode(sourceFile, (node) => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      ts.isNumericLiteral(node.initializer)
    ) {
      constMap.set(node.name.text, Number(node.initializer.text));
    }
  });
  return constMap;
}

function isProcessExitCall(node: ts.Node): node is ts.CallExpression {
  return (
    ts.isCallExpression(node) &&
    ts.isPropertyAccessExpression(node.expression) &&
    ts.isIdentifier(node.expression.expression) &&
    node.expression.expression.text === "process" &&
    node.expression.name.text === "exit"
  );
}

/** Every statically-derivable exit-code value reachable via `process.exit(...)` or `return ...`. */
function collectExitValues(sourceFile: ts.SourceFile, constMap: Map<string, number>): number[] {
  const values: number[] = [];
  forEachNode(sourceFile, (node) => {
    if (isProcessExitCall(node)) {
      values.push(...resolveExitValues(node.arguments[0], constMap));
    } else if (ts.isReturnStatement(node) && node.expression) {
      values.push(...resolveExitValues(node.expression, constMap));
    }
  });
  return values;
}

// ---------------------------------------------------------------------------
// Individual checks
// ---------------------------------------------------------------------------

function checkArgParser(src: string): Finding[] {
  if (
    !/\bparseArgs\s*\(/.test(src) &&
    !/\bcommander\b/i.test(src) &&
    !src.includes("new Command(")
  ) {
    return [
      {
        code: "contract.missing-argparse",
        severity: "fail",
        message: "No parseArgs()/commander call found — agents cannot call --help or pass flags",
      },
    ];
  }
  return [];
}

function checkFormatFlag(src: string): Finding[] {
  if (!src.includes("--format")) {
    return [
      {
        code: "contract.missing-format-flag",
        severity: "fail",
        message: "No --format flag — agents cannot request JSON output explicitly",
      },
    ];
  }
  return [];
}

function checkQuietFlag(src: string): Finding[] {
  if (!src.includes("--quiet")) {
    return [
      {
        code: "contract.missing-quiet-flag",
        severity: "warn",
        message: "No --quiet flag — agents cannot suppress informational stderr",
      },
    ];
  }
  return [];
}

function checkExitCodes(src: string): Finding[] {
  const sourceFile = ast(src);
  const constMap = buildNumericConstMap(sourceFile);
  const exitsFound = new Set(collectExitValues(sourceFile, constMap));

  // Contract (assets/templates/script.ts.template): 0 success, 1 issues found,
  // 2 bad invocation, 3 nothing matched. Every tool needs a success path and a
  // bad-invocation path. Codes 1 and 3 only mean something for tools that can
  // report findings or return an empty result — a converter or a counter has
  // neither, so flagging their absence as a warning is noise, not signal.
  const CHECKS: { code: number; severity: Finding["severity"]; message: string }[] = [
    { code: 0, severity: "warn", message: "Exit code 0 not found — success path missing" },
    {
      code: 2,
      severity: "warn",
      message: "Exit code 2 not found — bad-invocation path missing (unparseable args, bad path)",
    },
    {
      code: 1,
      severity: "info",
      message:
        "No exit code 1 — if this tool reports findings, return 1 when any are found so " +
        "agents can branch on the result without parsing output",
    },
    {
      code: 3,
      severity: "info",
      message:
        "No exit code 3 — if this tool can legitimately return zero results, return 3 so " +
        "agents can distinguish 'nothing matched' from 'error'",
    },
  ];
  return CHECKS.filter((c) => !exitsFound.has(c.code)).map(({ code, severity, message }) => ({
    code: `contract.missing-exit-${code}`,
    severity,
    message,
  }));
}

function checkNoInteractive(src: string): Finding[] {
  const sourceFile = ast(src);
  const findings: Finding[] = [];
  forEachNode(sourceFile, (node) => {
    if (!ts.isCallExpression(node)) return;
    const callee = node.expression;
    const isQuestionCall = ts.isPropertyAccessExpression(callee) && callee.name.text === "question";
    const isBarePromptCall = ts.isIdentifier(callee) && callee.text === "prompt";
    if (isQuestionCall || isBarePromptCall) {
      findings.push({
        code: "contract.interactive-prompt",
        severity: "fail",
        message: `Line ${lineOf(sourceFile, node.getStart(sourceFile))}: interactive prompt detected — agents cannot respond`,
        line: lineOf(sourceFile, node.getStart(sourceFile)),
      });
    }
  });
  return findings;
}

function checkStderrForErrors(src: string): Finding[] {
  const findings: Finding[] = [];
  const lines = src.split("\n");
  lines.forEach((line, i) => {
    const stripped = line.trim();
    if (
      stripped.startsWith("console.log(") &&
      stripped.includes("JSON.stringify") &&
      /error/i.test(stripped) &&
      !stripped.includes("process.stderr")
    ) {
      findings.push({
        code: "contract.error-on-stdout",
        severity: "warn",
        message: `Line ${i + 1}: possible error JSON on stdout — errors must go to stderr`,
        line: i + 1,
      });
    }
  });
  return findings;
}

const NODE_BUILTINS = new Set(builtinModules);

function isBuiltinModule(spec: string): boolean {
  const bare = spec.startsWith("node:") ? spec.slice(5) : spec;
  return spec.startsWith("node:") || NODE_BUILTINS.has(bare);
}

function extractImportedModules(src: string): string[] {
  const modules = new Set<string>();
  const importRe = /(?:import\s+(?:[\w*${},\s]+\s+from\s+)?|require\()\s*['"]([^'"]+)['"]/g;
  for (const m of src.matchAll(importRe)) {
    const spec = m[1]!;
    if (spec.startsWith(".") || spec.startsWith("/")) continue;
    modules.add(spec);
  }
  return [...modules];
}

function findNearestPackageJson(startDir: string): string | null {
  let dir = resolve(startDir);
  for (let i = 0; i < 20; i++) {
    const candidate = join(dir, "package.json");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

function packageName(spec: string): string {
  // Scoped packages (@scope/pkg/subpath) keep the scope+name; unscoped
  // packages (pkg/subpath) keep only the leading segment.
  const parts = spec.split("/");
  if (spec.startsWith("@")) return parts.slice(0, 2).join("/");
  return parts[0]!;
}

function checkPackageDeps(src: string, scriptPath: string): Finding[] {
  const nonBuiltin = extractImportedModules(src)
    .map(packageName)
    .filter((m) => !isBuiltinModule(m));
  if (nonBuiltin.length === 0) return [];

  const pkgPath = findNearestPackageJson(dirname(resolve(scriptPath)));
  if (!pkgPath) {
    return [
      {
        code: "contract.missing-package-json",
        severity: "warn",
        message:
          `Non-builtin imports detected (${[...new Set(nonBuiltin)].sort().join(", ")}) but no ` +
          "reachable package.json — add one declaring these as dependencies so `npm ci` " +
          "resolves them.",
      },
    ];
  }

  let declared = new Set<string>();
  try {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8")) as Record<string, unknown>;
    for (const field of [
      "dependencies",
      "devDependencies",
      "optionalDependencies",
      "peerDependencies",
    ]) {
      const deps = pkg[field];
      if (deps && typeof deps === "object") {
        declared = new Set([...declared, ...Object.keys(deps)]);
      }
    }
  } catch {
    // Malformed package.json — treat as if nothing is declared.
  }

  const missing = [...new Set(nonBuiltin)].filter((m) => !declared.has(m)).sort();
  if (missing.length === 0) return [];
  return [
    {
      code: "contract.missing-package-dep",
      severity: "warn",
      message:
        `package.json at ${pkgPath} does not declare: ${missing.join(", ")} — add them as ` +
        "dependencies so `npm ci` resolves what this script imports.",
    },
  ];
}

function checkShebang(src: string): Finding[] {
  if (!src.startsWith("#!/usr/bin/env -S npx tsx") && !src.startsWith("#!/usr/bin/env node")) {
    return [
      {
        code: "contract.missing-shebang",
        severity: "info",
        message: "No '#!/usr/bin/env -S npx tsx' shebang — script won't be directly executable",
      },
    ];
  }
  return [];
}

function checkDocComment(src: string): Finding[] {
  const withoutShebang = src.replace(/^#!.*\n/, "");
  if (!/^\s*\/\*\*/.test(withoutShebang)) {
    return [
      {
        code: "contract.missing-docstring",
        severity: "info",
        message: "No leading /** doc comment — add one describing inputs, outputs, exit codes",
      },
    ];
  }
  return [];
}

function checkEpilog(src: string): Finding[] {
  if (/examples?:/i.test(src)) return [];
  return [
    {
      code: "contract.missing-epilog",
      severity: "warn",
      message:
        "No 'Examples:' text found in help output — `--help` is the agent's primary " +
        "reference; show 2-3 realistic invocations",
    },
  ];
}

function checkStructuredStderrErrors(src: string): Finding[] {
  const sourceFile = ast(src);
  const constMap = buildNumericConstMap(sourceFile);
  const hasNonZeroExit = collectExitValues(sourceFile, constMap).some((v) => v !== 0);

  if (!hasNonZeroExit) return [];

  if (src.includes("emitError(") || src.includes("emit_error(")) return [];
  for (const line of src.split("\n")) {
    const stripped = line.trim();
    if (
      stripped.includes("JSON.stringify") &&
      (stripped.includes("process.stderr") || stripped.includes("console.error")) &&
      /error/i.test(stripped)
    ) {
      return [];
    }
  }

  return [
    {
      code: "contract.missing-structured-stderr",
      severity: "warn",
      message:
        "Non-zero exit paths exist but no structured error JSON found on stderr — errors " +
        'should be `process.stderr.write(JSON.stringify({error, code, hint}) + "\\n")` so the ' +
        "agent gets a consistent shape",
    },
  ];
}

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

const ALL_CHECKS: Array<(src: string) => Finding[]> = [
  checkArgParser,
  checkFormatFlag,
  checkQuietFlag,
  checkExitCodes,
  checkNoInteractive,
  checkStderrForErrors,
  checkShebang,
  checkDocComment,
  checkEpilog,
  checkStructuredStderrErrors,
];

export function validate(scriptPath: string): Finding[] {
  const src = readFileSync(scriptPath, "utf-8");
  const findings: Finding[] = [];
  for (const check of ALL_CHECKS) {
    findings.push(...check(src));
  }
  findings.push(...checkPackageDeps(src, scriptPath));
  return findings;
}

function emitText(path: string, findings: Finding[]): void {
  for (const f of findings) {
    const loc = f.line ? ` (line ${f.line})` : "";
    console.log(`${f.severity.toUpperCase()}: [${f.code}]${loc} ${f.message}`);
  }
  if (findings.length === 0) {
    console.log(`OK: ${path} passes all checks`);
  }
}

function emitJson(path: string, findings: Finding[]): void {
  console.log(
    JSON.stringify(
      {
        target: path,
        findings,
        summary: {
          total: findings.length,
          fail: findings.filter((f) => f.severity === "fail").length,
          warn: findings.filter((f) => f.severity === "warn").length,
        },
      },
      null,
      2,
    ),
  );
}

const HELP = `Validate that a TypeScript script meets the agent tool interface contract.

Usage:
  validate_agent_tool.ts <script-path> [--format json|text] [--exit-on-warn]

Examples:
  validate_agent_tool.ts tool.ts                  # text report to stdout
  validate_agent_tool.ts tool.ts --format json    # machine-readable JSON
  validate_agent_tool.ts tool.ts --exit-on-warn   # CI mode: warnings fail

Options:
  --format <json|text>  Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr (no effect on findings output)
  --exit-on-warn         Exit 1 on warnings as well as failures
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
    return EXIT_OK;
  }

  const scriptArg = positionals[0];
  if (!scriptArg) {
    console.error(HELP);
    return EXIT_BAD_INVOCATION;
  }

  const path = resolve(scriptArg);
  if (!existsSync(path)) {
    console.error(`error: path does not exist: ${path}`);
    return EXIT_BAD_INVOCATION;
  }

  const useJson = values.json || values.format === "json";
  const src = readFileSync(path, "utf-8");
  const head = src.split("\n").slice(0, 10).join("\n");
  if (head.includes("agent-tool: false")) {
    if (useJson) {
      console.log(JSON.stringify({ target: path, skipped: true }, null, 2));
    } else if (!values.quiet) {
      console.log(`SKIP: ${path} (marked '// agent-tool: false')`);
    }
    return EXIT_OK;
  }

  const findings = validate(path);
  if (useJson) {
    emitJson(path, findings);
  } else {
    emitText(path, findings);
  }

  const hasFail = findings.some((f) => f.severity === "fail");
  const hasWarn = findings.some((f) => f.severity === "warn");
  if (hasFail) return EXIT_FAIL;
  if (values["exit-on-warn"] && hasWarn) return EXIT_FAIL;
  return EXIT_OK;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
