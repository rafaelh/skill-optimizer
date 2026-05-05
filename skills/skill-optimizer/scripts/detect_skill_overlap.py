#!/usr/bin/env python3
"""Detect description overlap between Claude skills.

Two modes, picked from the input shape:

1. **Single skill vs siblings.** Pass one skill directory plus `--against
   <parent-dir>`. Each sibling skill in the parent is compared to the focal
   skill; any pair above `--threshold` is flagged.

2. **All-pairs.** Pass a parent directory containing multiple skills. Every
   pair is compared.

Algorithm: bag-of-words cosine similarity over description tokens
(lowercased, stop-words dropped, hyphens kept as part of compound terms).
Stable, deterministic, no LLM in the loop. The output isn't a verdict — a
high score is a flag worth investigating, not proof of misfire.

Usage:
    detect_skill_overlap.py <parent-dir>
    detect_skill_overlap.py <skill-dir> --against <parent-dir>
    detect_skill_overlap.py <parent-dir> --threshold 0.6 --json

Exit codes:
    0   no overlap above threshold
    1   one or more overlapping pairs found (caller should review)
    2   bad invocation (path missing, no skills found)
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import itertools
import json
import math
from pathlib import Path
import re
import sys

from skill_lib import parse_frontmatter, sanitize_for_echo

DEFAULT_THRESHOLD = 0.5
TOP_SHARED_KEYWORDS = 8
MIN_KEYWORD_LEN = 3

# Conservative English stopword list — short enough that we don't fight
# real domain words, broad enough to keep the cosine score from being
# dominated by noise like "the user wants" appearing in every description.
_STOPWORDS = frozenset(
    {
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
        # frequent "what skills do" boilerplate
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
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")


@dataclass
class SkillEntry:
    name: str
    path: str
    description: str
    tokens: tuple[str, ...]


@dataclass
class OverlapPair:
    a: str
    b: str
    similarity: float
    shared_keywords: list[str]
    code: str  # overlap.description.collision | overlap.trigger.shared-keyword


def tokenize(description: str) -> tuple[str, ...]:
    return tuple(
        t
        for t in _TOKEN_RE.findall(description.lower())
        if t not in _STOPWORDS and len(t) >= MIN_KEYWORD_LEN
    )


def cosine(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    if not a or not b:
        return 0.0
    ca, cb = Counter(a), Counter(b)
    shared = set(ca) & set(cb)
    dot = sum(ca[k] * cb[k] for k in shared)
    norm_a = math.sqrt(sum(v * v for v in ca.values()))
    norm_b = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def shared_keywords(
    a: tuple[str, ...],
    b: tuple[str, ...],
    top: int = TOP_SHARED_KEYWORDS,
) -> list[str]:
    ca, cb = Counter(a), Counter(b)
    shared = set(ca) & set(cb)
    # Rank by combined frequency: keywords used heavily in both descriptions
    # are the most useful signal for what the agent will see as a collision.
    ranked = sorted(shared, key=lambda k: ca[k] + cb[k], reverse=True)
    return ranked[:top]


def load_skill(skill_dir: Path) -> SkillEntry | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(encoding="utf-8")
    fm, _body = parse_frontmatter(text)
    if not isinstance(fm, dict):
        return None
    name = fm.get("name") or skill_dir.name
    description = fm.get("description") or ""
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    return SkillEntry(
        name=name,
        path=str(skill_dir),
        description=description,
        tokens=tokenize(description),
    )


def discover_skills(parent: Path) -> list[SkillEntry]:
    """Return all loadable skills directly under `parent`."""
    skills: list[SkillEntry] = []
    if not parent.is_dir():
        return skills
    for child in sorted(parent.iterdir()):
        if not child.is_dir():
            continue
        entry = load_skill(child)
        if entry is not None:
            skills.append(entry)
    return skills


def compare_pairs(
    pairs: list[tuple[SkillEntry, SkillEntry]],
    *,
    threshold: float,
) -> list[OverlapPair]:
    overlaps: list[OverlapPair] = []
    for a, b in pairs:
        sim = cosine(a.tokens, b.tokens)
        if sim < threshold:
            continue
        shared = shared_keywords(a.tokens, b.tokens)
        code = (
            "overlap.description.collision"
            if sim >= threshold + 0.1 or len(shared) >= 4
            else "overlap.trigger.shared-keyword"
        )
        overlaps.append(
            OverlapPair(
                a=a.name,
                b=b.name,
                similarity=round(sim, 4),
                shared_keywords=shared,
                code=code,
            )
        )
    overlaps.sort(key=lambda p: p.similarity, reverse=True)
    return overlaps


def detect(
    target: Path,
    *,
    against: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[SkillEntry], list[OverlapPair]]:
    """Dispatch on the input shape.

    - target is a single skill (has SKILL.md) + against is provided → mode 1
    - target is a parent directory → mode 2 (all-pairs)
    """
    is_single_skill = (target / "SKILL.md").is_file()
    if is_single_skill:
        focal = load_skill(target)
        if focal is None:
            raise ValueError(f"could not parse SKILL.md at {target}")
        if against is None:
            raise ValueError("--against is required when target is a single skill directory")
        siblings = [s for s in discover_skills(against) if s.path != focal.path]
        pairs = [(focal, s) for s in siblings]
        return [focal, *siblings], compare_pairs(pairs, threshold=threshold)

    skills = discover_skills(target)
    if not skills:
        raise ValueError(f"no skills with SKILL.md found under {target}")
    pairs = list(itertools.combinations(skills, 2))
    return skills, compare_pairs(pairs, threshold=threshold)


def _emit_text(skills: list[SkillEntry], overlaps: list[OverlapPair]) -> None:
    if not overlaps:
        print(f"no overlapping pairs found across {len(skills)} skill(s)")
        return
    for o in overlaps:
        keywords = ", ".join(sanitize_for_echo(k, max_len=32) for k in o.shared_keywords)
        print(
            f"WARN: [{o.code}] {o.a} <-> {o.b}  similarity={o.similarity:.2f}  shared=[{keywords}]"
        )


def _emit_json(skills: list[SkillEntry], overlaps: list[OverlapPair]) -> None:
    payload = {
        "skills": [{"name": s.name, "path": s.path} for s in skills],
        "pairs": [asdict(o) for o in overlaps],
        "summary": {
            "skills_compared": len(skills),
            "pairs_above_threshold": len(overlaps),
        },
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect description overlap between Claude skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        help=(
            "Either a parent directory containing skills, "
            "or a single skill directory (with --against)"
        ),
    )
    parser.add_argument(
        "--against",
        help="Parent directory of sibling skills (only with single-skill target).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity threshold (0.0-1.0, default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON on stdout.",
    )
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(f"detect_skill_overlap: target does not exist: {target}", file=sys.stderr)
        return 2
    if not target.is_dir():
        print(f"detect_skill_overlap: target is not a directory: {target}", file=sys.stderr)
        return 2

    against = None
    if args.against:
        against = Path(args.against).expanduser().resolve()
        if not against.is_dir():
            print(f"detect_skill_overlap: --against is not a directory: {against}", file=sys.stderr)
            return 2

    try:
        skills, overlaps = detect(target, against=against, threshold=args.threshold)
    except ValueError as exc:
        print(f"detect_skill_overlap: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        _emit_json(skills, overlaps)
    else:
        _emit_text(skills, overlaps)

    return 1 if overlaps else 0


if __name__ == "__main__":
    sys.exit(main())
