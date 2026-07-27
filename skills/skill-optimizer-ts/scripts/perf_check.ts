#!/usr/bin/env -S npx tsx
/**
 * Static TypeScript/JavaScript performance analysis for agent tools.
 *
 * Walks .ts files (or directories, recursively) and flags common per-call
 * performance anti-patterns: string concatenation in loops, regex
 * recompilation, linear-scan array membership tests, readFileSync inside a
 * loop, and sort/reverse inside a loop.
 *
 * Usage:
 *   perf_check.ts <path> [path ...]                # static analysis
 *   perf_check.ts <path> --format json             # machine-readable
 *
 * Exit codes:
 *   0   Analysis ran (regardless of findings)
 *   2   Bad invocation — missing args, paths not found, unreadable input
 *   3   Analysis ran; no TypeScript files matched the inputs
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import ts from "typescript";
import { emitError } from "./skill_lib.js";

const EXIT_OK = 0;
// Skill-wide contract (assets/templates/script.ts.template): 2 covers anything
// that stopped the tool running — bad args, missing paths, unreadable input.
const EXIT_BAD_INVOCATION = 2;
const EXIT_NOT_FOUND = 3;

export interface Issue {
  file: string;
  line: number;
  severity: "HIGH" | "MEDIUM" | "LOW";
  category: string;
  message: string;
  fix: string;
}

const LOOP_KINDS = new Set<ts.SyntaxKind>([
  ts.SyntaxKind.ForStatement,
  ts.SyntaxKind.ForOfStatement,
  ts.SyntaxKind.ForInStatement,
  ts.SyntaxKind.WhileStatement,
  ts.SyntaxKind.DoStatement,
]);

function lineOf(sourceFile: ts.SourceFile, pos: number): number {
  return sourceFile.getLineAndCharacterOfPosition(pos).line + 1;
}

/**
 * Depth to walk a child with. Entering a loop does not by itself mean the child
 * repeats: `for (const x of readdirSync(d).sort())` evaluates the iterated
 * expression exactly once, and a `for` initializer runs once. Only the parts
 * that actually re-execute get depth + 1.
 */
function childDepth(parent: ts.Node, child: ts.Node, depth: number): number {
  if (!LOOP_KINDS.has(parent.kind)) return depth;
  const body = (parent as ts.IterationStatement).statement;
  if (child === body) return depth + 1;
  if (ts.isForOfStatement(parent) || ts.isForInStatement(parent)) {
    // .expression is evaluated once, before iteration begins.
    return child === parent.expression ? depth : depth + 1;
  }
  if (ts.isForStatement(parent)) {
    // .initializer runs once; .condition and .incrementor run every iteration.
    return child === parent.initializer ? depth : depth + 1;
  }
  // while / do-while: the condition is re-evaluated every iteration.
  return depth + 1;
}

/** Names bound by a loop — its own declarations plus anything declared in its body. */
function loopBoundNames(loop: ts.Node): Set<string> {
  const names = new Set<string>();
  const addBinding = (name: ts.BindingName): void => {
    if (ts.isIdentifier(name)) {
      names.add(name.text);
      return;
    }
    for (const el of name.elements) {
      if (ts.isBindingElement(el)) addBinding(el.name);
    }
  };
  const visit = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node)) addBinding(node.name);
    ts.forEachChild(node, visit);
  };
  visit(loop);
  return names;
}

/** True if the expression reads any of `names` — i.e. it varies per iteration. */
function referencesAny(node: ts.Node | undefined, names: Set<string>): boolean {
  if (!node) return false;
  if (ts.isIdentifier(node) && names.has(node.text)) return true;
  let found = false;
  ts.forEachChild(node, (child) => {
    if (!found && referencesAny(child, names)) found = true;
  });
  return found;
}

function walkAst(
  node: ts.Node,
  sourceFile: ts.SourceFile,
  depth: number,
  fileName: string,
  issues: Issue[],
  loopBound: Set<string> = new Set(),
): void {
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusEqualsToken) {
    const rhs = node.right;
    const isStringLiteral = ts.isStringLiteralLike(rhs);
    const isTemplate = ts.isTemplateExpression(rhs);
    if (depth > 0 && (isStringLiteral || isTemplate)) {
      issues.push({
        file: fileName,
        line: lineOf(sourceFile, node.getStart(sourceFile)),
        severity: "HIGH",
        category: "string-concat-loop",
        message: `${isTemplate ? "Template literal" : "String"} += inside a loop — O(n²) copies`,
        fix: "Collect into an array, then array.join('') after the loop",
      });
    }
  }

  if (
    ts.isNewExpression(node) &&
    ts.isIdentifier(node.expression) &&
    node.expression.text === "RegExp"
  ) {
    const firstArg = node.arguments?.[0];
    if (depth > 0 && firstArg && ts.isStringLiteralLike(firstArg)) {
      issues.push({
        file: fileName,
        line: lineOf(sourceFile, node.getStart(sourceFile)),
        severity: "MEDIUM",
        category: "regex-recompile",
        message: "new RegExp(stringLiteral) in loop — pattern recompiled each iteration",
        fix: "Hoist the RegExp construction above the loop",
      });
    }
  }

  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
    const callee = node.expression;
    const methodName = callee.name.text;

    if (
      (methodName === "includes" || methodName === "indexOf") &&
      ts.isArrayLiteralExpression(callee.expression) &&
      callee.expression.elements.length > 1
    ) {
      issues.push({
        file: fileName,
        line: lineOf(sourceFile, node.getStart(sourceFile)),
        severity: depth > 0 ? "HIGH" : "MEDIUM",
        category: "membership-seq",
        message: `'.${methodName}()' on an array literal — O(n) scan each time`,
        fix: "Use a Set literal for O(1) membership tests",
      });
    }

    if (depth > 0 && (methodName === "sort" || methodName === "reverse")) {
      issues.push({
        file: fileName,
        line: lineOf(sourceFile, node.getStart(sourceFile)),
        severity: "MEDIUM",
        category: "sort-in-loop",
        message: `.${methodName}() called inside a loop — O(n log n) work every iteration`,
        fix: "Sort once after the loop, or maintain order incrementally",
      });
    }
  }

  // Reading a *different* file each iteration is the correct way to walk a file
  // set — only a path that cannot vary means the same read is being repeated.
  if (
    depth > 0 &&
    isReadFileSyncCall(node) &&
    !referencesAny((node as ts.CallExpression).arguments[0], loopBound)
  ) {
    issues.push({
      file: fileName,
      line: lineOf(sourceFile, node.getStart(sourceFile)),
      severity: "MEDIUM",
      category: "read-in-loop",
      message: "readFileSync() called inside a loop with a path that does not vary",
      fix: "Read the file once before the loop",
    });
  }

  const nextBound = LOOP_KINDS.has(node.kind)
    ? new Set([...loopBound, ...loopBoundNames(node)])
    : loopBound;
  ts.forEachChild(node, (child) =>
    walkAst(child, sourceFile, childDepth(node, child, depth), fileName, issues, nextBound),
  );
}

function isReadFileSyncCall(node: ts.Node): boolean {
  if (!ts.isCallExpression(node)) return false;
  const callee = node.expression;
  if (ts.isIdentifier(callee)) return callee.text === "readFileSync";
  if (ts.isPropertyAccessExpression(callee)) return callee.name.text === "readFileSync";
  return false;
}

export function analyze(filePath: string): Issue[] {
  const content = readFileSync(filePath, "utf-8");
  const sourceFile = ts.createSourceFile(
    filePath,
    content,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const issues: Issue[] = [];
  walkAst(sourceFile, sourceFile, 0, filePath, issues);
  return issues;
}

function resolveInputs(paths: string[]): { files: string[]; missing: string[] } {
  const files: string[] = [];
  const missing: string[] = [];
  for (const raw of paths) {
    const p = resolve(raw);
    if (!existsSync(p)) {
      missing.push(raw);
      continue;
    }
    if (statSync(p).isDirectory()) {
      files.push(...walkDir(p));
    } else {
      files.push(p);
    }
  }
  return { files, missing };
}

function walkDir(dir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir).sort()) {
    if (entry === "node_modules") continue;
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      results.push(...walkDir(full));
    } else if (full.endsWith(".ts")) {
      results.push(full);
    }
  }
  return results;
}

function log(msg: string, quiet: boolean): void {
  if (!quiet) process.stderr.write(msg + "\n");
}

function printIssues(issues: Issue[]): void {
  if (issues.length === 0) {
    console.log("  No issues found.\n");
    return;
  }
  const order: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };
  const sorted = [...issues].sort(
    (a, b) => order[a.severity]! - order[b.severity]! || a.line - b.line,
  );
  const counts = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  for (const issue of sorted) {
    counts[issue.severity]++;
    console.log(`  ${issue.severity.padEnd(6)}  ${issue.file}:${issue.line}  [${issue.category}]`);
    console.log(`         ${issue.message}`);
    console.log(`         Fix: ${issue.fix}\n`);
  }
  console.log(`  Summary: ${counts.HIGH} high  ${counts.MEDIUM} medium  ${counts.LOW} low\n`);
}

const HELP = `Detect TypeScript/JavaScript performance anti-patterns (static analysis).

Usage:
  perf_check.ts <path> [path ...]

Examples:
  perf_check.ts app.ts                            # static analysis of one file
  perf_check.ts src/tools/                        # walk a directory recursively
  perf_check.ts app.ts --format json              # machine-readable output
  perf_check.ts app.ts --quiet                    # suppress informational stderr

Options:
  --format <json|text>   Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr (file counts, etc.)
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
    return EXIT_OK;
  }

  if (positionals.length === 0) {
    console.log(HELP);
    return EXIT_BAD_INVOCATION;
  }

  const useJson = values.json || values.format === "json";

  let files: string[];
  let missing: string[];
  try {
    ({ files, missing } = resolveInputs(positionals));
  } catch (exc) {
    emitError(
      "perf_check",
      `Unexpected error resolving inputs: ${exc instanceof Error ? exc.message : String(exc)}`,
      { code: "RESOLVE_FAILED", hint: "Check that paths exist and are readable" },
    );
    return EXIT_BAD_INVOCATION;
  }

  if (missing.length > 0) {
    emitError("perf_check", `Path(s) not found: ${missing.join(", ")}`, {
      code: "PATH_NOT_FOUND",
      hint: "Pass an existing file or directory; directories are walked recursively",
    });
    return EXIT_BAD_INVOCATION;
  }

  if (files.length === 0) {
    log("No TypeScript files matched the given paths.", values.quiet);
    return EXIT_NOT_FOUND;
  }

  log(`Analyzing ${files.length} file(s)...`, values.quiet);

  try {
    const allIssues: Issue[] = [];
    for (const file of files) {
      allIssues.push(...analyze(file));
    }

    if (useJson) {
      const payload = {
        static: allIssues,
        meta: {
          files_analyzed: files.length,
          issues_total: allIssues.length,
          issues_high: allIssues.filter((i) => i.severity === "HIGH").length,
        },
      };
      console.log(JSON.stringify(payload, null, 2));
      return EXIT_OK;
    }

    console.log(`\nStatic Analysis (${files.length} file(s))`);
    console.log("─".repeat(72));
    printIssues(allIssues);
    return EXIT_OK;
  } catch (exc) {
    emitError(
      "perf_check",
      exc instanceof Error ? `${exc.constructor.name}: ${exc.message}` : String(exc),
      {
        code: "UNEXPECTED_ERROR",
        hint: "Re-run with the file directly to isolate the failing input",
      },
    );
    return EXIT_BAD_INVOCATION;
  }
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
