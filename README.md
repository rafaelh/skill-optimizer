```
      __       _______    _______  _____  ___  ___________       ________  __   ___   __    ___      ___        ________  
     /""\     /" _   "|  /"     "|(\"   \|"  \("     _   ")     /"       )|/"| /  ") |" \  |"  |    |"  |      /"       ) 
    /    \   (: ( \___) (: ______)|.\\   \    |)__/  \\__/     (:   \___/ (: |/   /  ||  | ||  |    ||  |     (:   \___/  
   /' /\  \   \/ \       \/    |  |: \.   \\  |   \\_ /         \___  \   |    __/   |:  | |:  |    |:  |      \___  \    
  //  __'  \  //  \ ___  // ___)_ |.  \    \. |   |.  |          __/  \\  (// _  \   |.  |  \  |___  \  |___    __/  \\   
 /   /  \\  \(:   _(  _|(:      "||    \    \ |   \:  |         /" \   :) |: | \  \  /\  |\( \_|:  \( \_|:  \  /" \   :)  
(___/    \___)\_______)  \_______) \___|\____\)    \__|        (_______/  (__|  \__)(__\_|_)\_______)\_______)(_______/
```

This repo contains agent skills that I use on a day to day basis outside of standard repo skills. They all comply with (and to some extent enforce) the [agentskills.io](https://agentskills.io/) specification.

Most of these tools use python scripts, though there are some that use typescript.

## Skills

<details>
  <summary>skill-optimizer</summary>

  ### skill-optimizer

  Audits, optimizes, validates, and trigger-evals Claude Agent Skills (SKILL.md files).

  **Validation and analysis**

  - Validates SKILL.md frontmatter, body structure, & script references against the spec
  - Analyzes token budget, section balance, progressive disclosure quality, & gotchas coverage
  - Recommends whether to introduce helper scripts, and what patterns they should follow
  - Detects description overlap between sibling skills (bag-of-words cosine similarity)

  **Description optimization**

  - Rewrites the `description` field with imperative phrasing and concrete trigger contexts
  - Runs a trigger-rate eval: invokes `claude -p` against a labeled query set and counts how often the skill activates
  - Iterates candidate descriptions against train failures, scores against a held-out validation set, and proposes the winner

  **Script quality** *(Python only)*

  - Checks helper scripts for performance anti-patterns (AST static analysis + optional cProfile runtime profiling)
  - Validates the agent-tool interface contract: `--format`, `--quiet`, `--dry-run` flags; exit codes `0/1/2/3`; no `input()`, no free-form stdout errors
  - Counts tokens via the Anthropic SDK when available, heuristic fallback otherwise

  **Security**

  - Audits a skill against the [OWASP Agentic Skills Top 10](https://owasp.org/www-project-agentic-skills-top-10/): over-privileged `allowed-tools`, hardcoded secrets, unsafe deserialization, shell injection, supply-chain (fetch-and-run, unpinned deps), and hidden-unicode instructions
  - Security checks cover SKILL.md and reference files regardless of language; Python-specific checks (unsafe deserialization, shell injection, dependency pinning) apply only to `.py` scripts

  Scripts live in `skills/skill-optimizer/scripts/` and accept `--json` for machine-readable output.

| Script | Purpose |
|---|---|
| `validate_skill.py` | Frontmatter + body validation, exit 1 on any failure |
| `analyze_skill.py` | Token count, section balance, progressive disclosure quality |
| `recommend_scripts.py` | Advises on helper scripts — what to add and what patterns to follow |
| `detect_skill_overlap.py` | Cosine similarity between skill descriptions; single-skill or all-pairs mode |
| `eval_triggers.py` | Trigger-rate eval against a labeled query set; train/validation split |
| `optimize_description.py` | Multi-round description optimizer; propose-only by default, `--apply` to write |
| `count_tokens.py` | Token counter; exact via Anthropic SDK, heuristic fallback |
| `perf_check.py` | AST-based performance checker + optional cProfile profiling |
| `audit_security.py` | OWASP Agentic Skills Top 10 audit; FAILs on hardcoded secrets, WARNs on the rest |

</detail>

<details>
  <summary>skill-optimizer-ts</summary>

### skill-optimizer-ts

The TypeScript twin of `skill-optimizer` — same audit/eval workflow, for skills whose bundled scripts are TypeScript (run via `npx tsx`) instead of Python. For teams without Python available, most commonly Windows machines.

**Validation, analysis, and description optimization** — same feature set as `skill-optimizer` above, retargeted at TypeScript.

**Script quality** *(TypeScript only)* — `validate_agent_tool.ts` and `perf_check.ts` are folded directly into this skill (no dependency on `agent-tool-builder`, which is Python-only). Performance checks target JS/TS anti-patterns: string `+=` in a loop, `new RegExp(literal)` recompiled per iteration, array `.includes()`/`.indexOf()` membership tests, `readFileSync` in a loop, `.sort()`/`.reverse()` in a loop.

**Security** — same OWASP Agentic Skills Top 10 coverage, with the script-construct checks retargeted: `eval()`/`new Function()`/`vm.runInThisContext` (unsafe deserialization), `child_process.exec`/`{shell: true}` (shell injection), `fs.rmSync(..., {recursive: true})` (dangerous fs), and `package.json` dependency pinning (supply chain) in place of PEP 723 checks.

Scripts live in `skills/skill-optimizer-ts/scripts/` and accept `--format json|text` for machine-readable output.

| Script | Purpose |
|---|---|
| `validate_skill.ts` | Frontmatter + body validation, exit 1 on any failure |
| `analyze_skill.ts` | Token count, section balance, progressive disclosure quality |
| `recommend_scripts.ts` | Advises on helper scripts — what to add and what patterns to follow |
| `detect_skill_overlap.ts` | Cosine similarity between skill descriptions; single-skill or all-pairs mode |
| `eval_triggers.ts` | Trigger-rate eval against a labeled query set; train/validation split |
| `optimize_description.ts` | Multi-round description optimizer; propose-only by default, `--apply` to write |
| `count_tokens.ts` | Token counter; exact via `@anthropic-ai/sdk` (optional dep), heuristic fallback |
| `perf_check.ts` | AST-based (TypeScript compiler API) performance checker, static only |
| `audit_security.ts` | OWASP Agentic Skills Top 10 audit; FAILs on hardcoded secrets, WARNs on the rest |
| `validate_agent_tool.ts` | Interface contract validation — flags, exit codes, output shape (folded in from agent-tool-builder) |

</detail>

<details>
  <summary>agent-tool-builder</summary>
  
### agent-tool-builder

Builds and reviews **Python** scripts intended to be called by AI agents as tools. Enforces a standard interface contract (structured JSON output, predictable exit codes, `--format`, `--quiet`, `--dry-run`) and catches performance anti-patterns before they cost agent round-trips.

> If your agent tools are written in TypeScript, Bash, or another language, this skill's validation and scaffolding won't apply. You'd need a separate contract enforcer for that language's conventions.

**Design interview**

- Runs a decision-forcing interview to pin down the tool's data model, operations, flag set, JSON output shape, and failure modes before writing a line of code

**Interface contract enforcement**

- Validates mandatory flags (`--format`, `--quiet`), conditional flags (`--dry-run`, `--limit`, `--cursor`), exit code conventions, and stderr discipline
- Ensures output is parseable JSON by default — agents should never need to parse human-readable text

**Scaffolding**

- Scaffolds new agent tools from a PEP 723 template with argparse, `--format json/text/csv`, structured error output, and stub tests

**Performance auditing**

- Detects O(n²) string concatenation, recompiled regexes, and list-membership anti-patterns via AST analysis + optional cProfile profiling

Scripts live in `skills/agent-tool-builder/scripts/` and accept `--json` for machine-readable output.

| Script | Purpose |
|---|---|
| `validate_agent_tool.py` | Interface contract validation — flags, exit codes, output shape |
| `perf_check.py` | AST-based performance checker + optional cProfile profiling |
| `init_tool.py` | Scaffold a new agent tool from the bundled PEP 723 template |

</details>


## Installing
<details>
  <summary>Install Details</summary> 
This repo is a Claude Code plugin marketplace. From inside Claude Code, add the marketplace and install whichever skills you need:

```
/plugin marketplace add rafaelh/skill-optimizer
/plugin install skill-optimizer@rafaelh-skill-optimizer
/plugin install skill-optimizer-ts@rafaelh-skill-optimizer
/plugin install agent-tool-builder@rafaelh-skill-optimizer
/reload-plugins
```

Once installed, skills activate automatically based on context — `skill-optimizer` or `skill-optimizer-ts` when you ask Claude to audit a SKILL.md (pick based on whether your bundled scripts are Python or TypeScript); `agent-tool-builder` when you ask Claude to write or improve a Python script an agent will call. `skill-optimizer` and `skill-optimizer-ts` cover overlapping ground by design — their descriptions each carry an explicit "NOT for X — see Y" disambiguator so only one activates per request.

After installing `skill-optimizer-ts`, run `npm ci` once inside `skills/skill-optimizer-ts/` to resolve `tsx`/`typescript`/`vitest` from the committed lockfile.

## Requirements

**skill-optimizer / agent-tool-builder (Python):**
- Python 3.14+ (all bundled scripts are stdlib-only except where PEP 723 metadata declares third-party deps)
- `ANTHROPIC_API_KEY` in environment (optional — enables exact token counts via the SDK)

**skill-optimizer-ts (TypeScript):**
- Node.js 18.3+ (20+ recommended), plus a one-time `npm ci` in the skill directory
- `ANTHROPIC_API_KEY` in environment (optional — enables exact token counts via `@anthropic-ai/sdk`)

**Both:**
- Claude Code CLI (for trigger evals and description optimization)
</details>

