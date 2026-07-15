#!/usr/bin/env python3
"""Run a trigger-rate eval against a labeled query set.

Inputs a JSON array of `{query, should_trigger, [notes], [split]}` objects
(see assets/schemas/eval-queries.schema.json), invokes the configured agent
CLI (`--cli-bin`, default `claude`) with `-p --output-format json` per query
`--runs` times, and counts how often the named
skill activates. Computes per-query trigger rate plus train/validation pass
rates so you can iterate on the description without overfitting.

Usage:
    eval_triggers.py --queries QUERIES.json --skill-name <name>
    eval_triggers.py --queries Q.json --skill-name s --runs 3 --train-split 0.6
    eval_triggers.py ... --json
    eval_triggers.py ... --dry-run            # validate inputs, run no calls

Exit codes:
    0   eval ran successfully
    1   one or more queries failed (rate inverted from expectation)
    2   bad invocation (file missing, schema violation, CLI unavailable)
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from skill_lib import sanitize_for_echo

DEFAULT_RUNS = 3
DEFAULT_TRAIN_SPLIT = 0.6
TRIGGER_THRESHOLD = 0.5


@dataclass
class QueryResult:
    query: str
    should_trigger: bool
    triggers: int
    runs: int
    rate: float
    passed: bool
    split: str  # "train" | "validation"
    code: str  # eval.query.no-trigger | eval.query.false-trigger | eval.query.passed
    notes: str | None = None


@dataclass
class EvalSummary:
    skill_name: str
    runs_per_query: int
    train_split: float
    by_query: list[QueryResult] = field(default_factory=list)
    train_pass_rate: float = 0.0
    validation_pass_rate: float = 0.0


def load_queries(path: Path) -> list[dict[str, Any]]:
    """Read and minimally validate the queries file.

    Schema enforcement is stricter than `json.load`: each item must have
    `query` (str) and `should_trigger` (bool). `notes` and `split` are
    optional.
    """
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"queries file is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("queries file must be a non-empty JSON array")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise TypeError(f"queries[{i}] must be an object")
        if not isinstance(item.get("query"), str) or not item["query"]:
            raise TypeError(f"queries[{i}].query must be a non-empty string")
        if not isinstance(item.get("should_trigger"), bool):
            raise TypeError(f"queries[{i}].should_trigger must be a boolean")
        if "split" in item and item["split"] not in ("train", "validation"):
            raise ValueError(f"queries[{i}].split must be 'train' or 'validation' if set")
    return data


def assign_splits(queries: list[dict[str, Any]], train_fraction: float) -> None:
    """Assign train/validation splits to queries lacking explicit ones.

    Mutates queries in place. Maintains proportional positive/negative
    balance across splits — independently sample positives and negatives.
    """
    positives = [q for q in queries if q.get("split") is None and q["should_trigger"]]
    negatives = [q for q in queries if q.get("split") is None and not q["should_trigger"]]
    train_pos = max(1, round(len(positives) * train_fraction)) if positives else 0
    train_neg = max(1, round(len(negatives) * train_fraction)) if negatives else 0
    for i, q in enumerate(positives):
        q["split"] = "train" if i < train_pos else "validation"
    for i, q in enumerate(negatives):
        q["split"] = "train" if i < train_neg else "validation"


def run_query(query: str, *, cli_bin: str = "claude") -> dict[str, Any]:
    """Invoke `<cli> -p --output-format json` for a single query.

    Returns the parsed JSON response. Raises CalledProcessError if the CLI
    fails or RuntimeError if the output isn't JSON.
    """
    result = subprocess.run(
        [cli_bin, "-p", query, "--output-format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        return json.loads(result.stdout)  # type: ignore[no-any-return]
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"CLI returned non-JSON output: {sanitize_for_echo(result.stdout, 200)!r}"
        ) from exc


def did_skill_trigger(cli_response: dict[str, Any], skill_name: str) -> bool:
    """Inspect a CLI JSON response for a Skill tool use matching skill_name."""
    messages = cli_response.get("messages") or []
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") != "Skill":
                continue
            tool_input = block.get("input") or {}
            if isinstance(tool_input, dict) and tool_input.get("skill") == skill_name:
                return True
    return False


def _evaluate_query(
    q: dict[str, Any],
    *,
    skill_name: str,
    runs: int,
    cli_bin: str,
    dry_run: bool,
) -> QueryResult:
    """Run a single query against the CLI and classify the result."""
    triggers = 0
    if not dry_run:
        triggers = sum(
            did_skill_trigger(run_query(q["query"], cli_bin=cli_bin), skill_name)
            for _ in range(runs)
        )
    rate = triggers / runs if runs else 0.0
    passed = (q["should_trigger"] and rate > TRIGGER_THRESHOLD) or (
        not q["should_trigger"] and rate < TRIGGER_THRESHOLD
    )
    if passed:
        code = "eval.query.passed"
    elif q["should_trigger"]:
        code = "eval.query.no-trigger"
    else:
        code = "eval.query.false-trigger"
    return QueryResult(
        query=q["query"],
        should_trigger=q["should_trigger"],
        triggers=triggers,
        runs=runs,
        rate=rate,
        passed=passed,
        split=q["split"],
        code=code,
        notes=q.get("notes"),
    )


def _pass_rate(results: list[QueryResult]) -> float:
    """Compute the fraction of results that passed."""
    return sum(r.passed for r in results) / len(results) if results else 0.0


def evaluate(
    queries: list[dict[str, Any]],
    *,
    skill_name: str,
    runs: int = DEFAULT_RUNS,
    train_split: float = DEFAULT_TRAIN_SPLIT,
    cli_bin: str = "claude",
    dry_run: bool = False,
) -> EvalSummary:
    assign_splits(queries, train_split)
    summary = EvalSummary(skill_name=skill_name, runs_per_query=runs, train_split=train_split)

    for q in queries:
        summary.by_query.append(
            _evaluate_query(q, skill_name=skill_name, runs=runs, cli_bin=cli_bin, dry_run=dry_run)
        )

    train_results = [r for r in summary.by_query if r.split == "train"]
    val_results = [r for r in summary.by_query if r.split == "validation"]
    summary.train_pass_rate = _pass_rate(train_results)
    summary.validation_pass_rate = _pass_rate(val_results)
    return summary


def _emit_text(summary: EvalSummary) -> None:
    for r in summary.by_query:
        marker = "PASS" if r.passed else "FAIL"
        expected = "trigger" if r.should_trigger else "no-trigger"
        snippet = sanitize_for_echo(r.query, max_len=80)
        print(
            f"{marker}: [{r.code}] ({r.split}) expected={expected} "
            f"rate={r.rate:.2f} ({r.triggers}/{r.runs}) — {snippet!r}"
        )
    print()
    print(f"train pass rate:      {summary.train_pass_rate:.2f}")
    print(f"validation pass rate: {summary.validation_pass_rate:.2f}")


def _emit_json(summary: EvalSummary) -> None:
    payload = {
        "skill_name": summary.skill_name,
        "runs_per_query": summary.runs_per_query,
        "train_split": summary.train_split,
        "by_query": [asdict(r) for r in summary.by_query],
        "summary": {
            "train": {"pass_rate": summary.train_pass_rate},
            "validation": {"pass_rate": summary.validation_pass_rate},
        },
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a trigger-rate eval against a labeled query set.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--queries",
        required=True,
        help="Path to queries JSON file (see assets/schemas/eval-queries.schema.json)",
    )
    parser.add_argument(
        "--skill-name",
        required=True,
        help="Frontmatter `name` of the skill being evaluated.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Invocations per query for stability (default: {DEFAULT_RUNS}).",
    )
    parser.add_argument(
        "--train-split",
        type=float,
        default=DEFAULT_TRAIN_SPLIT,
        help=f"Fraction of queries assigned to train (default: {DEFAULT_TRAIN_SPLIT}).",
    )
    parser.add_argument(
        "--cli-bin",
        default="claude",
        help=(
            "Path to the agent CLI binary (default: 'claude' on PATH)."
            " Supports claude, copilot, codex."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and assignments, but skip the actual CLI calls.",
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

    queries_path = Path(args.queries).expanduser().resolve()
    if not queries_path.is_file():
        print(f"eval_triggers: queries file not found: {queries_path}", file=sys.stderr)
        return 2

    if not args.dry_run and shutil.which(args.cli_bin) is None:
        print(
            f"eval_triggers: '{args.cli_bin}' not found on PATH. "
            f"Use --dry-run to validate inputs without invoking the CLI.",
            file=sys.stderr,
        )
        return 2

    try:
        queries = load_queries(queries_path)
    except ValueError as exc:
        print(f"eval_triggers: {exc}", file=sys.stderr)
        return 2

    summary = evaluate(
        queries,
        skill_name=args.skill_name,
        runs=args.runs,
        train_split=args.train_split,
        cli_bin=args.cli_bin,
        dry_run=args.dry_run,
    )

    if use_json:
        _emit_json(summary)
    else:
        _emit_text(summary)

    failed = [r for r in summary.by_query if not r.passed]
    return 1 if failed and not args.dry_run else 0


if __name__ == "__main__":
    sys.exit(main())
