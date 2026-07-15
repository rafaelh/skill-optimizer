#!/usr/bin/env python3
"""Iterate skill descriptions toward higher validation pass rate.

Wraps the manual loop documented in references/evaluation.md:

  1. Evaluate the current description on train + validation.
  2. For each round, generate N candidate revisions via the agent CLI,
     each one targeted at the train-set failures.
  3. Evaluate every candidate.
  4. Repeat for `--rounds` rounds, keeping the candidates from each round
     as a pool.
  5. Pick the candidate with the highest validation pass rate.

Default is propose-only: emit JSON of every candidate and its scores; user
copies the winner into SKILL.md. With `--apply`, the winner is written to
SKILL.md and the prior file is saved as SKILL.md.bak.

Usage:
    optimize_description.py <skill-dir> --queries Q.json
    optimize_description.py <skill-dir> --queries Q.json --apply
    optimize_description.py <skill-dir> --queries Q.json --rounds 3 --candidates 3 --runs 3
    optimize_description.py <skill-dir> --queries Q.json --json

Exit codes:
    0   ran successfully (with or without applying)
    1   no candidate beat the baseline (caller may still inspect output)
    2   bad invocation (missing files, CLI unavailable)
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

from eval_triggers import EvalSummary, evaluate, load_queries
from skill_lib import emit_error, parse_frontmatter, sanitize_for_echo

DEFAULT_ROUNDS = 3
DEFAULT_CANDIDATES = 3
DEFAULT_RUNS = 3
DEFAULT_TRAIN_SPLIT = 0.6
MAX_DESCRIPTION = 1024


@dataclass
class Candidate:
    description: str
    round: int  # 0 == baseline
    train_pass_rate: float
    validation_pass_rate: float
    failures_addressed: list[str] = field(default_factory=list)


@dataclass
class OptimizationResult:
    skill_dir: str
    skill_name: str
    baseline: Candidate
    candidates: list[Candidate]
    winner: Candidate
    applied: bool
    rounds: int
    runs_per_query: int


def _read_skill_md(skill_dir: Path) -> tuple[str, dict[str, Any], str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md not found at {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if not isinstance(fm, dict):
        raise TypeError(f"SKILL.md at {skill_md} has no parseable frontmatter")
    return text, fm, body


def _evaluate_candidate(
    skill_dir: Path,
    description: str,
    queries: list[dict[str, Any]],
    *,
    skill_name: str,
    runs: int,
    train_split: float,
    cli_bin: str,
) -> EvalSummary:
    """Write the candidate to SKILL.md, run the eval, restore."""
    skill_md = skill_dir / "SKILL.md"
    original = skill_md.read_text(encoding="utf-8")
    try:
        new_text = _replace_description(original, description)
        skill_md.write_text(new_text, encoding="utf-8")
        # queries get split assignments in-place by evaluate(); copy
        # per call so candidates start with the same original splits.
        return evaluate(
            [dict(q) for q in queries],
            skill_name=skill_name,
            runs=runs,
            train_split=train_split,
            cli_bin=cli_bin,
        )
    finally:
        skill_md.write_text(original, encoding="utf-8")


def _replace_description(skill_md_text: str, new_description: str) -> str:
    """Replace the `description:` line(s) in frontmatter with `new_description`.

    Handles both single-line scalar form and multi-line folded (`>`) form.
    """
    lines = skill_md_text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("---"):
        raise ValueError("SKILL.md has no frontmatter fence")
    # Find the closing fence.
    closing = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
    if closing is None:
        raise ValueError("SKILL.md frontmatter has no closing '---' fence")

    out: list[str] = [lines[0]]
    i = 1
    replaced = False
    while i < closing:
        line = lines[i]
        if line.startswith("description:") and not replaced:
            # Replace this line and any continuation lines (folded scalar).
            out.append(f"description: {new_description}\n")
            i += 1
            # Skip continuation lines (indented) for folded scalar form.
            while i < closing and (lines[i].startswith(("  ", "\t")) or not lines[i].strip()):
                i += 1
            replaced = True
        else:
            out.append(line)
            i += 1
    if not replaced:
        # No description in frontmatter — insert one before the closing fence.
        out.append(f"description: {new_description}\n")
    out.extend(lines[closing:])
    return "".join(out)


def _generate_candidates(
    current_description: str,
    failures: list[dict[str, Any]],
    n: int,
    *,
    cli_bin: str,
) -> list[str]:
    """Use the agent CLI to generate N candidate revisions addressing failures."""
    if not failures:
        return []
    failure_lines = "\n".join(
        f"- {sanitize_for_echo(f['query'], 200)} "
        f"(should_trigger={f['should_trigger']}, actual_rate={f['rate']:.2f})"
        for f in failures
    )
    prompt = (
        "You are tuning an Agent Skill description. The description is the "
        "ONLY field the agent uses to decide whether to activate the skill. "
        f"It must stay under {MAX_DESCRIPTION} characters and use imperative phrasing "
        "('Use this skill when...' not 'This skill does...').\n\n"
        "The current description:\n"
        f"<<<\n{current_description}\n>>>\n\n"
        "Failed against these queries:\n"
        f"{failure_lines}\n\n"
        f"Write {n} candidate revisions, separated by '===' on its own line. "
        "Each revision must address the failures while staying under "
        f"{MAX_DESCRIPTION} chars. Output ONLY the candidates separated by '==='. "
        "No preamble, no numbering."
    )
    result = subprocess.run(
        [cli_bin, "-p", prompt],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = result.stdout.strip()
    candidates = [c.strip() for c in raw.split("===") if c.strip()]
    # Clamp candidate length to the spec maximum.
    return [c[:MAX_DESCRIPTION] for c in candidates[:n]]


def optimize(
    skill_dir: Path,
    queries_path: Path,
    *,
    rounds: int = DEFAULT_ROUNDS,
    candidates_per_round: int = DEFAULT_CANDIDATES,
    runs: int = DEFAULT_RUNS,
    train_split: float = DEFAULT_TRAIN_SPLIT,
    cli_bin: str = "claude",
    apply: bool = False,
) -> OptimizationResult:
    _, fm, _ = _read_skill_md(skill_dir)
    skill_name = fm.get("name") or skill_dir.name
    if not isinstance(skill_name, str):
        raise TypeError("frontmatter 'name' is not a string")
    current_description = fm.get("description") or ""
    if not isinstance(current_description, str):
        raise TypeError("frontmatter 'description' is not a string")
    queries = load_queries(queries_path)

    # Baseline: evaluate the current description once.
    baseline_summary = _evaluate_candidate(
        skill_dir,
        current_description,
        queries,
        skill_name=skill_name,
        runs=runs,
        train_split=train_split,
        cli_bin=cli_bin,
    )
    baseline = Candidate(
        description=current_description,
        round=0,
        train_pass_rate=baseline_summary.train_pass_rate,
        validation_pass_rate=baseline_summary.validation_pass_rate,
    )

    candidates: list[Candidate] = [baseline]
    last_summary = baseline_summary
    last_description = current_description

    for round_num in range(1, rounds + 1):
        train_failures = [
            asdict(r) for r in last_summary.by_query if r.split == "train" and not r.passed
        ]
        if not train_failures:
            break  # nothing left to improve
        candidate_texts = _generate_candidates(
            last_description,
            train_failures,
            candidates_per_round,
            cli_bin=cli_bin,
        )
        for cand_text in candidate_texts:
            cand_summary = _evaluate_candidate(
                skill_dir,
                cand_text,
                queries,
                skill_name=skill_name,
                runs=runs,
                train_split=train_split,
                cli_bin=cli_bin,
            )
            candidates.append(
                Candidate(
                    description=cand_text,
                    round=round_num,
                    train_pass_rate=cand_summary.train_pass_rate,
                    validation_pass_rate=cand_summary.validation_pass_rate,
                    failures_addressed=[f["query"] for f in train_failures],
                )
            )
        # Pick the best candidate from this round to seed the next round.
        round_candidates = [c for c in candidates if c.round == round_num]
        if round_candidates:
            best = max(round_candidates, key=lambda c: c.validation_pass_rate)
            last_description = best.description
            last_summary = _evaluate_candidate(
                skill_dir,
                best.description,
                queries,
                skill_name=skill_name,
                runs=runs,
                train_split=train_split,
                cli_bin=cli_bin,
            )

    winner = max(candidates, key=lambda c: (c.validation_pass_rate, c.train_pass_rate))
    applied = False
    if apply and winner.description != baseline.description:
        skill_md = skill_dir / "SKILL.md"
        backup = skill_md.with_suffix(".md.bak")
        shutil.copy2(skill_md, backup)
        original = skill_md.read_text(encoding="utf-8")
        skill_md.write_text(_replace_description(original, winner.description), encoding="utf-8")
        applied = True

    return OptimizationResult(
        skill_dir=str(skill_dir),
        skill_name=skill_name,
        baseline=baseline,
        candidates=candidates,
        winner=winner,
        applied=applied,
        rounds=rounds,
        runs_per_query=runs,
    )


def _emit_text(result: OptimizationResult) -> None:
    print(f"Skill: {result.skill_name}")
    print(
        f"Baseline: train={result.baseline.train_pass_rate:.2f} "
        f"validation={result.baseline.validation_pass_rate:.2f}"
    )
    for c in result.candidates:
        if c is result.baseline:
            continue
        marker = "★" if c is result.winner else " "
        snippet = sanitize_for_echo(c.description, max_len=80)
        print(
            f"{marker} round={c.round} "
            f"train={c.train_pass_rate:.2f} validation={c.validation_pass_rate:.2f} "
            f"— {snippet!r}"
        )
    if result.applied:
        print(f"\nApplied winner to {result.skill_dir}/SKILL.md (backup at SKILL.md.bak)")
    elif result.winner is not result.baseline:
        print("\n(propose-only — pass --apply to write the winner to SKILL.md)")
    else:
        print("\nNo candidate beat the baseline — leaving SKILL.md unchanged.")


def _emit_json(result: OptimizationResult) -> None:
    payload = {
        "skill_dir": result.skill_dir,
        "skill_name": result.skill_name,
        "baseline": asdict(result.baseline),
        "candidates": [asdict(c) for c in result.candidates],
        "winner": asdict(result.winner),
        "applied": result.applied,
        "rounds": result.rounds,
        "runs_per_query": result.runs_per_query,
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Iterate skill descriptions toward higher validation pass rate.",
        epilog="Examples:\n"
        "  optimize_description.py ./my-skill --queries queries.json\n"
        "  optimize_description.py ./my-skill --queries q.json --rounds 5 --apply\n"
        "  optimize_description.py ./my-skill --queries q.json --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("skill_dir", help="Path to the skill directory")
    parser.add_argument("--queries", required=True, help="Path to eval queries JSON")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--train-split", type=float, default=DEFAULT_TRAIN_SPLIT)
    parser.add_argument(
        "--cli-bin",
        default="claude",
        help="Path to the agent CLI binary (default: 'claude'). Supports claude, copilot, codex.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the winning description to SKILL.md (creates .bak)",
    )
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Alias for --format json."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress informational stderr.")
    args = parser.parse_args(argv)
    use_json = args.as_json or args.format == "json"

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    if not skill_dir.is_dir():
        emit_error(
            "optimize_description", f"not a directory: {skill_dir}",
            code="optimize.input.not-dir",
            hint="First argument must be a skill directory.",
        )
        return 2
    queries_path = Path(args.queries).expanduser().resolve()
    if not queries_path.is_file():
        emit_error(
            "optimize_description",
            f"queries file not found: {queries_path}",
            code="optimize.input.not-found",
            hint="Check the --queries path.",
        )
        return 2
    if shutil.which(args.cli_bin) is None:
        emit_error(
            "optimize_description",
            f"'{args.cli_bin}' not found on PATH.",
            code="optimize.input.cli-missing",
            hint="Install the CLI or specify --cli-bin.",
        )
        return 2

    try:
        result = optimize(
            skill_dir,
            queries_path,
            rounds=args.rounds,
            candidates_per_round=args.candidates,
            runs=args.runs,
            train_split=args.train_split,
            cli_bin=args.cli_bin,
            apply=args.apply,
        )
    except (FileNotFoundError, ValueError, TypeError) as exc:
        emit_error("optimize_description", str(exc), code="optimize.run.error")
        return 2

    if use_json:
        _emit_json(result)
    else:
        _emit_text(result)

    return 0 if result.winner is not result.baseline else 1


if __name__ == "__main__":
    sys.exit(main())
