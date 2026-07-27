#!/usr/bin/env -S npx tsx
/**
 * Count tokens in a file or stdin (Anthropic tokenizer when possible).
 *
 * Two-tier strategy:
 *
 * 1. If `@anthropic-ai/sdk` is installed AND `ANTHROPIC_API_KEY` is set, call
 *    `client.beta.messages.countTokens` for an exact count. This API is free
 *    — it doesn't burn output tokens.
 *
 * 2. Otherwise fall back to a heuristic (`len / 3.5`), which is closer to
 *    typical LLM chars-per-token ratios than the older `len / 4` rule of
 *    thumb.
 *
 * The script never blocks on the slow path: graceful fallback means the
 * agent always gets *some* answer, with `exact: false` so it knows to
 * interpret it as an estimate.
 *
 * Usage:
 *   count_tokens.ts <file>           # exact or heuristic depending on env
 *   count_tokens.ts - < text         # read stdin
 *   count_tokens.ts <file> --json    # machine-readable output
 *   count_tokens.ts <file> --model claude-sonnet-4-6
 *
 * Exit codes:
 *   0   counted successfully
 *   2   bad invocation (path missing, unreadable file)
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { emitError } from "./skill_lib.js";

const DEFAULT_MODEL = "claude-sonnet-4-6";
const HEURISTIC_CHARS_PER_TOKEN = 3.5;

export interface CountResult {
  tokens: number;
  method: "anthropic-sdk" | "heuristic";
  exact: boolean;
  model?: string;
  chars_per_token?: number;
  fallback_reason?: string;
}

export type SdkCaller = (text: string, model: string) => Promise<number>;

async function defaultSdkCaller(text: string, model: string): Promise<number> {
  // Lazy import — if the SDK isn't available, the heuristic path doesn't pay
  // the import cost.
  const { default: Anthropic } = await import("@anthropic-ai/sdk");
  const client = new Anthropic();
  const response = await client.beta.messages.countTokens({
    model,
    messages: [{ role: "user", content: text }],
  });
  return response.input_tokens;
}

export async function count(
  text: string,
  model: string = DEFAULT_MODEL,
  sdkCaller: SdkCaller = defaultSdkCaller,
): Promise<CountResult> {
  if (process.env.ANTHROPIC_API_KEY) {
    try {
      const tokens = await sdkCaller(text, model);
      return { tokens, method: "anthropic-sdk", exact: true, model };
    } catch (err) {
      // The SDK either isn't installed or the call failed (network, bad
      // key, throttling). Surface the reason but still answer with the
      // heuristic so the caller isn't blocked.
      const reason = err instanceof Error ? err.message : String(err);
      return countHeuristic(text, reason);
    }
  }
  return countHeuristic(text);
}

function countHeuristic(text: string, fallbackReason?: string): CountResult {
  const tokens = Math.max(1, Math.floor(text.length / HEURISTIC_CHARS_PER_TOKEN));
  const result: CountResult = {
    tokens,
    method: "heuristic",
    exact: false,
    chars_per_token: HEURISTIC_CHARS_PER_TOKEN,
  };
  if (fallbackReason !== undefined) {
    result.fallback_reason = fallbackReason;
  }
  return result;
}

function readStdin(): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    const chunks: Buffer[] = [];
    process.stdin.on("data", (chunk: Buffer) => chunks.push(chunk));
    process.stdin.on("end", () => resolvePromise(Buffer.concat(chunks).toString("utf-8")));
    process.stdin.on("error", reject);
  });
}

async function readInput(target: string): Promise<string> {
  if (target === "-") {
    return readStdin();
  }
  const path = resolve(target);
  if (!existsSync(path)) {
    throw new Error(`path does not exist: ${path}`);
  }
  if (statSync(path).isDirectory()) {
    throw new Error(`not a file: ${path}`);
  }
  return readFileSync(path, "utf-8");
}

function emitText(result: CountResult, quiet: boolean): void {
  const exactness = result.exact ? "exact" : "estimate";
  const suffix = result.model ? ` (model=${result.model})` : "";
  console.log(`${result.tokens} tokens — ${result.method} (${exactness})${suffix}`);
  if (result.fallback_reason !== undefined && !quiet) {
    console.error(`  fallback: ${result.fallback_reason}`);
  }
}

function emitJson(target: string, result: CountResult): void {
  console.log(JSON.stringify({ target, ...result }, null, 2));
}

const HELP = `Count tokens in a file or stdin (Anthropic tokenizer when available).

Usage:
  count_tokens.ts <file>
  count_tokens.ts - < text
  count_tokens.ts <file> --json
  count_tokens.ts <file> --model claude-sonnet-4-6

Examples:
  count_tokens.ts path/to/SKILL.md
  count_tokens.ts path/to/SKILL.md --json
  echo 'hello' | count_tokens.ts - --model claude-sonnet-4-6

Options:
  --model <id>           Anthropic model id for the SDK call (default: ${DEFAULT_MODEL})
  --format <json|text>   Output format (default: text)
  --json                 Alias for --format json
  --quiet                Suppress informational stderr
  --help                 Show this help message
`;

export async function main(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      model: { type: "string", default: DEFAULT_MODEL },
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
  const target = positionals[0];
  if (!target) {
    console.error(HELP);
    return 2;
  }

  let text: string;
  try {
    text = await readInput(target);
  } catch (err) {
    emitError("count_tokens", err instanceof Error ? err.message : String(err), {
      code: "count.input.not-found",
      hint: "Provide a valid file path or '-' for stdin.",
    });
    return 2;
  }

  const result = await count(text, values.model);

  if (useJson) {
    emitJson(target, result);
  } else {
    emitText(result, values.quiet);
  }
  return 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  main(process.argv.slice(2)).then((code) => process.exit(code));
}
