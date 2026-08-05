# Audit reviewer prompts

Load this at Mode 2 step 2, once the context brief exists. These are the three
subagent prompts, ready to dispatch in parallel.

Give every reviewer: the tool path, its tests if any, and the context brief.
Give them nothing else in common — the value comes from them not sharing a
lens. Do not pass one reviewer's output to another.

---

## Reviewer A — contract audit (scripts + references)

> Audit `<tool-path>` against this skill's rules. Run:
>
> ```bash
> python3 "${CLAUDE_SKILL_DIR}/scripts/validate_agent_tool.py" <tool-path> --format json
> python3 "${CLAUDE_SKILL_DIR}/scripts/perf_check.py" <tool-path> --format json
> ```
>
> Then read [interface-contract.md](interface-contract.md) and
> [perf-findings.md](perf-findings.md), and read the tool source to check what
> the scripts cannot:
>
> - Is the JSON shape stable across result counts (object vs array)?
> - Is exit `3` actually reachable on an empty result, not just present in the source?
> - Does argument validation complete before any network or DB call?
> - Does `--dry-run` cover every destructive path, or only the obvious one?
> - Do the `epilog` examples still match the current flag set?
> - Is stdout clean JSON on every success path, with all logging on stderr?
>
> If the tool is standalone (a skill script, a `bash/scripts/` helper, anything
> you might copy to another machine), also check it against
> [pep723.md](pep723.md).
>
> Return findings as `severity | file:line | code | one-line fix`, most severe
> first. Omit passes and LOW findings unless nothing else fired.

## Reviewer B — unconstrained review

> You are reviewing `<tool-path>`, a script an AI agent calls as a tool. Here
> is what it does and who calls it: `<context brief>`.
>
> Read the source and its tests, and review it as a critical engineer would:
> correctness bugs, race conditions, error handling that swallows failures,
> misleading help text, a design that forces the caller into two round-trips
> for one logical operation, assumptions that break at scale or on the second
> call, state that leaks between invocations.
>
> Deliberately do not restrict yourself to a checklist — report anything you
> would raise in a code review, including things a linter would never catch.
> For each finding give the location, what goes wrong, and a concrete failure
> scenario (specific inputs or state → wrong output). Rank by how likely it is
> to bite in real use, not by how interesting it is.

## Reviewer C — minimization

> You are auditing `<tool-path>` for unnecessary surface. Context:
> `<context brief>`.
>
> Read [minimization.md](minimization.md) and follow it: inventory every flag,
> subcommand, output field, and module-level function; classify each; and
> propose cuts and unifications with evidence from call-site greps and git
> history.
>
> Report only candidates for removal, unification, or clarification — do not
> propose additions or improvements, another reviewer covers that. For each
> candidate give the item, its class, the evidence, and the blast radius. Sort
> by lines removed, descending.
