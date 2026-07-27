// agent-tool: false
/**
 * Test-only helpers for the vitest suite. Not an agent tool — it has no CLI,
 * and validate_agent_tool.ts skips it on the marker above.
 */

import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url));
const TSX_BIN = join(SCRIPTS_DIR, "..", "node_modules", ".bin", "tsx");

export interface CliResult {
  status: number;
  stdout: string;
  stderr: string;
}

/** Run a bundled script the same way an agent would: `npx tsx <script> <args>`. */
export function runCli(
  scriptName: string,
  args: string[],
  opts: { input?: string; env?: Record<string, string> } = {},
): CliResult {
  const scriptPath = join(SCRIPTS_DIR, scriptName);
  const result = spawnSync(TSX_BIN, [scriptPath, ...args], {
    encoding: "utf-8",
    input: opts.input,
    env: opts.env ? { ...process.env, ...opts.env } : process.env,
  });
  return {
    status: result.status ?? -1,
    stdout: result.stdout,
    stderr: result.stderr,
  };
}
