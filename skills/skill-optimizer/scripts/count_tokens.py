#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "anthropic>=0.40.0",
# ]
# ///
"""Count tokens in a file or stdin (Anthropic tokenizer when possible).

Two-tier strategy:

1. If the `anthropic` SDK is importable AND `ANTHROPIC_API_KEY` is set, call
   `client.beta.messages.count_tokens` for an exact count. This API is free —
   it doesn't burn output tokens.

2. Otherwise fall back to a heuristic (`len(text) / 3.5`), which is closer to
   typical LLM chars-per-token ratios than the older `len // 4` rule of thumb.

The script never blocks on the slow path: graceful fallback means the agent
always gets *some* answer, with `exact: false` so it knows to interpret it as
an estimate.

Usage:
    count_tokens.py <file>           # exact or heuristic depending on env
    count_tokens.py - < text         # read stdin
    count_tokens.py <file> --json    # machine-readable output
    count_tokens.py <file> --model claude-sonnet-4-6   # pick model for SDK call

Exit codes:
    0   counted successfully
    2   bad invocation (path missing, unreadable file)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

DEFAULT_MODEL = "claude-sonnet-4-6"
HEURISTIC_CHARS_PER_TOKEN = 3.5


def emit_error(
    script: str, message: str, *, code: str | None = None, hint: str | None = None
) -> None:
    """Emit structured error JSON to stderr."""
    payload: dict[str, str] = {"error": message}
    if code:
        payload["code"] = code
    if hint:
        payload["hint"] = hint
    print(json.dumps(payload), file=sys.stderr)


def count(text: str, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Return {tokens, method, exact, model?}."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _count_via_sdk(text, model)
        except Exception as exc:
            # Anthropic SDK either isn't installed or the call failed (network,
            # bad key, throttling). Surface the reason in JSON output but still
            # answer with the heuristic so the caller isn't blocked.
            return _count_heuristic(text, fallback_reason=str(exc))
    return _count_heuristic(text)


def _count_via_sdk(text: str, model: str) -> dict[str, Any]:
    # Lazy import — if the SDK isn't available, the heuristic path doesn't
    # need to pay the import cost.
    from anthropic import Anthropic  # type: ignore[import-not-found]

    client = Anthropic()
    response = client.beta.messages.count_tokens(
        model=model,
        messages=[{"role": "user", "content": text}],
    )
    return {
        "tokens": int(response.input_tokens),
        "method": "anthropic-sdk",
        "exact": True,
        "model": model,
    }


def _count_heuristic(text: str, fallback_reason: str | None = None) -> dict[str, Any]:
    tokens = max(1, int(len(text) / HEURISTIC_CHARS_PER_TOKEN))
    payload: dict[str, Any] = {
        "tokens": tokens,
        "method": "heuristic",
        "exact": False,
        "chars_per_token": HEURISTIC_CHARS_PER_TOKEN,
    }
    if fallback_reason is not None:
        payload["fallback_reason"] = fallback_reason
    return payload


def _read_input(target: str) -> str:
    if target == "-":
        return sys.stdin.read()
    path = Path(target).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"path does not exist: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"not a file: {path}")
    return path.read_text(encoding="utf-8")


def _emit_text(result: dict[str, Any], *, quiet: bool = False) -> None:
    method = result["method"]
    exact = "exact" if result["exact"] else "estimate"
    suffix = f" (model={result['model']})" if "model" in result else ""
    print(f"{result['tokens']} tokens — {method} ({exact}){suffix}")
    if "fallback_reason" in result and not quiet:
        print(f"  fallback: {result['fallback_reason']}", file=sys.stderr)


def _emit_json(target: str, result: dict[str, Any]) -> None:
    payload = {"target": target, **result}
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count tokens in a file or stdin (Anthropic tokenizer when available).",
        epilog="Examples:\n"
        "  count_tokens.py path/to/SKILL.md\n"
        "  count_tokens.py path/to/SKILL.md --json\n"
        "  echo 'hello' | count_tokens.py - --model claude-sonnet-4-6\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", help="Path to a UTF-8 text file, or '-' for stdin")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model id for the SDK call (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Alias for --format json.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress informational stderr.")
    args = parser.parse_args(argv)
    use_json = args.as_json or args.format == "json"

    try:
        text = _read_input(args.target)
    except (FileNotFoundError, IsADirectoryError) as exc:
        emit_error(
            "count_tokens",
            str(exc),
            code="count.input.not-found",
            hint="Provide a valid file path or '-' for stdin.",
        )
        return 2

    result = count(text, model=args.model)

    if use_json:
        _emit_json(args.target, result)
    else:
        _emit_text(result, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
