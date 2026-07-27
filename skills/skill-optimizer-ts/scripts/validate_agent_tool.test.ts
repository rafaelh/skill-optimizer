import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { runCli } from "./test-helpers.js";

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url));

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "skill-optimizer-ts-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function write(name: string, body: string): string {
  const p = join(root, name);
  writeFileSync(p, body, "utf-8");
  return p;
}

describe("validate_agent_tool self-validation", () => {
  it("finds no FAIL-level issues in this project's own analyze_skill.ts", () => {
    const target = join(SCRIPTS_DIR, "analyze_skill.ts");
    const result = runCli("validate_agent_tool.ts", [target, "--format", "json"]);
    const payload = JSON.parse(result.stdout);
    const fails = payload.findings.filter((f: { severity: string }) => f.severity === "fail");
    expect(fails).toEqual([]);
  });

  it("--help exits 0 and includes usage examples", () => {
    const result = runCli("validate_agent_tool.ts", ["--help"]);
    expect(result.status).toBe(0);
    expect(result.stdout.toLowerCase()).toContain("examples");
  });
});

describe("validate_agent_tool bad invocation", () => {
  it("exits 2 for a missing script path", () => {
    const result = runCli("validate_agent_tool.ts", ["/definitely/does/not/exist.ts"]);
    expect(result.status).toBe(2);
    expect(result.stderr.toLowerCase()).toContain("does not exist");
  });
});

const COMPLIANT_FIXTURE = `#!/usr/bin/env -S npx tsx
/**
 * A tool.
 */
import { parseArgs } from "node:util";

const EXIT_OK = 0;
const EXIT_USER_ERROR = 1;

function emitError(msg: string, code: string): void {
  process.stderr.write(JSON.stringify({ error: msg, code }) + "\\n");
}

const HELP = \`A tool.

Examples:
  tool --format json
\`;

function main(argv: string[]): number {
  const { values } = parseArgs({
    args: argv,
    options: {
      format: { type: "string", default: "json" },
      quiet: { type: "boolean", default: false },
      help: { type: "boolean", default: false },
    },
  });
  if (values.help) {
    console.log(HELP);
    return EXIT_OK;
  }
  if (!values.format) {
    emitError("missing", "BAD_ARG");
    return EXIT_USER_ERROR;
  }
  return EXIT_OK;
}

process.exit(main(process.argv.slice(2)));
`;

describe("validate_agent_tool contract checks", () => {
  it("reports no fails for a minimal compliant script", () => {
    const fixture = write("ok_tool.ts", COMPLIANT_FIXTURE);
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    const payload = JSON.parse(result.stdout);
    const fails = payload.findings.filter((f: { severity: string }) => f.severity === "fail");
    expect(fails).toEqual([]);
  });

  it("fails a script with no --format flag", () => {
    const fixture = write(
      "no_format.ts",
      `#!/usr/bin/env -S npx tsx
/** bad */
import { parseArgs } from "node:util";
parseArgs({ args: process.argv.slice(2), options: { id: { type: "string" } } });
process.exit(0);
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    expect(result.status).toBe(1);
    const codes = JSON.parse(result.stdout).findings.map((f: { code: string }) => f.code);
    expect(codes).toContain("contract.missing-format-flag");
  });

  it("fails a script with an interactive prompt", () => {
    const fixture = write(
      "interactive.ts",
      `#!/usr/bin/env -S npx tsx
/** bad */
import * as readline from "node:readline/promises";
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const name = await rl.question("name? ");
console.log("--format --quiet");
process.exit(0);
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    expect(result.status).toBe(1);
    const codes = JSON.parse(result.stdout).findings.map((f: { code: string }) => f.code);
    expect(codes).toContain("contract.interactive-prompt");
  });

  it("warns when usage examples are missing from --help output", () => {
    const fixture = write(
      "no_examples.ts",
      `#!/usr/bin/env -S npx tsx
/** tool */
import { parseArgs } from "node:util";
parseArgs({ args: process.argv.slice(2), options: { format: { type: "string" }, quiet: { type: "boolean" } } });
process.exit(0);
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    const codes = JSON.parse(result.stdout).findings.map((f: { code: string }) => f.code);
    expect(codes).toContain("contract.missing-epilog");
  });

  it("does not warn about package deps for builtin-only imports", () => {
    const fixture = write(
      "stdlib_only.ts",
      `#!/usr/bin/env -S npx tsx
/** tool - examples: tool --format json */
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
parseArgs({ args: process.argv.slice(2), options: { format: { type: "string" }, quiet: { type: "boolean" } } });
void readFileSync;
process.exit(0);
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    const codes = JSON.parse(result.stdout).findings.map((f: { code: string }) => f.code);
    expect(codes).not.toContain("contract.missing-package-json");
    expect(codes).not.toContain("contract.missing-package-dep");
  });

  it("warns when a real non-builtin import has no reachable package.json declaring it", () => {
    const fixture = write(
      "uses_chalk.ts",
      `#!/usr/bin/env -S npx tsx
/** tool - examples: tool --format json */
import chalk from "chalk";
import { parseArgs } from "node:util";
parseArgs({ args: process.argv.slice(2), options: { format: { type: "string" }, quiet: { type: "boolean" } } });
void chalk;
process.exit(0);
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    const codes = JSON.parse(result.stdout).findings.map((f: { code: string }) => f.code);
    expect(codes.some((c: string) => c.startsWith("contract.missing-package"))).toBe(true);
  });

  it("recognizes exit codes returned via a ternary expression", () => {
    const fixture = write(
      "ternary_exit.ts",
      `#!/usr/bin/env -S npx tsx
/** tool - examples: tool --format json */
import { parseArgs } from "node:util";

function emitError(msg: string, code: string): void {
  process.stderr.write(JSON.stringify({ error: msg, code }) + "\\n");
}

function main(argv: string[]): number {
  const { values } = parseArgs({ args: argv, options: { format: { type: "string" }, quiet: { type: "boolean" } } });
  if (!values.format) emitError("bad", "X");
  return values.format ? 0 : 1;
}
process.exit(main(process.argv.slice(2)));
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    const codes = JSON.parse(result.stdout).findings.map((f: { code: string }) => f.code);
    expect(codes).not.toContain("contract.missing-exit-0");
    expect(codes).not.toContain("contract.missing-exit-1");
  });

  it("warns when a non-zero exit path lacks structured stderr", () => {
    const fixture = write(
      "bad_errors.ts",
      `#!/usr/bin/env -S npx tsx
/** tool - examples: tool --format json */
import { parseArgs } from "node:util";
const { values } = parseArgs({ args: process.argv.slice(2), options: { format: { type: "string" }, quiet: { type: "boolean" } } });
if (!values.format) {
  console.error("missing format");
  process.exit(1);
}
process.exit(0);
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    const codes = JSON.parse(result.stdout).findings.map((f: { code: string }) => f.code);
    expect(codes).toContain("contract.missing-structured-stderr");
  });

  it("defaults to text output, not JSON", () => {
    const fixture = write("ok.ts", COMPLIANT_FIXTURE);
    const result = runCli("validate_agent_tool.ts", [fixture]);
    expect(result.status).toBe(0);
    expect(result.stdout.trimStart().startsWith("{")).toBe(false);
  });

  it("skips a file marked '// agent-tool: false'", () => {
    const fixture = write(
      "lib.ts",
      `// agent-tool: false
export function helper(): number {
  return 42;
}
`,
    );
    const result = runCli("validate_agent_tool.ts", [fixture, "--format", "json"]);
    expect(result.status).toBe(0);
    const data = JSON.parse(result.stdout);
    expect(data.skipped).toBe(true);
  });
});
