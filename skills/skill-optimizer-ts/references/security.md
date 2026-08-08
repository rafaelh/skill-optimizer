# Skill security (OWASP Agentic Skills Top 10)

A skill is executable trust: its SKILL.md becomes agent instructions and its bundled scripts run with the agent's privileges. A skill that leaks a secret, ships malicious instructions, or pulls a compromised dependency is a supply-chain risk for everyone who installs it. This guide maps the [OWASP Agentic Skills Top 10](https://owasp.org/www-project-agentic-skills-top-10/) and its [skill development guide](https://owasp.org/www-project-agentic-skills-top-10/skill-development-guide) onto concrete checks for an agent skill, regardless of the target platform (Claude Code, Copilot, Codex, VS Code).

## When to read this

- You're auditing a skill you didn't write, or one you intend to publish/share.
- `audit_security.ts` reported a finding and you want the context behind the `AST##` code.
- You're auditing a skill that handles credentials, fetches remote content, runs shell commands, or deserializes data.
- A skill grants `Bash` in `allowed-tools` and you're deciding how tightly to scope it.

## What the auditor checks vs. what you check

`audit_security.ts <skill-dir>` is a static, high-signal scan. It catches the mechanically-detectable subset of the Top 10 and exits non-zero only on FAIL-level findings (hardcoded secrets). Everything else is a WARN you triage. The static scan is necessary but **not sufficient** — it is itself an instance of AST08 (poor scanning is the risk; running a scan is one control, not the whole story). The process risks below have no static signal and need a human pass.

| ID    | Risk                    | Auto-checked?                                                                                                | Code(s)                                                                                                                                                                   |
| ----- | ----------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AST01 | Malicious Skills        | Partial — hidden/bidi unicode; instruction-override, replacement prompts, concealment, exfiltration phrasing | `security.body.hidden-unicode`, `security.body.instruction-override`, `security.body.prompt-replacement`, `security.body.conceal-from-user`, `security.body.exfiltration` |
| AST02 | Supply Chain Compromise | Partial — `curl\|sh`, unpinned package.json deps                                                             | `security.exec.curl-pipe-shell`, `security.deps.unpinned`                                                                                                                 |
| AST03 | Over-Privileged Skills  | Yes — unrestricted/wildcard `Bash`                                                                           | `security.tools.unrestricted-bash`, `security.tools.broad-bash-glob`                                                                                                      |
| AST04 | Insecure Metadata       | Yes — hardcoded secrets/keys                                                                                 | `security.secret.hardcoded` (FAIL)                                                                                                                                        |
| AST05 | Unsafe Deserialization  | Yes — `eval`, `new Function`, `vm.runInThisContext`/`runInNewContext`                                        | `security.script.unsafe-deserialization`                                                                                                                                  |
| AST06 | Weak Isolation          | Yes — `child_process.exec`/`execSync`, `{shell: true}`, destructive fs                                       | `security.script.shell-injection`, `security.script.dangerous-fs`                                                                                                         |
| AST07 | Update Drift            | No — needs human review                                                                                      | —                                                                                                                                                                         |
| AST08 | Poor Scanning           | This tool is the control                                                                                     | —                                                                                                                                                                         |
| AST09 | No Governance           | No — needs human review                                                                                      | —                                                                                                                                                                         |
| AST10 | Cross-Platform Reuse    | No — see also cross-skill-design.md                                                                          | —                                                                                                                                                                         |

## The auto-checked risks

**AST03 — Over-Privileged Skills (least privilege).** Grant the narrowest `allowed-tools` that works. A bare `Bash` token or a `Bash(*)` wildcard is full shell access; scope it to the command prefix the skill actually runs, e.g. `Bash(npx tsx *)` or `Bash(git status *)`. Prefer the dedicated `Read`/`Edit`/`Write` tools over shelling out. Drop any tool the skill no longer uses.

**AST04 — Insecure Metadata (no embedded secrets).** Never ship a secret in SKILL.md, a reference, or a bundled script — skills are distributed verbatim and a committed key is a leaked key. Read secrets from environment variables at runtime (`process.env.X`), document which vars are required, and keep placeholders obviously fake (`your-token-here`, `<API_KEY>`). The auditor FAILs on AWS/GitHub/Slack/Google tokens, private-key blocks, and credential-shaped assignments to a literal.

**AST05 — Unsafe Deserialization.** `eval()`, `new Function(...)`, and `vm.runInThisContext`/`vm.runInNewContext` all execute attacker-controlled strings as code. Use `JSON.parse`, a schema validator (zod, ajv), or a safe YAML parser instead. If dynamic evaluation is genuinely required, isolate it and confirm the input is trusted.

**AST06 — Weak Isolation (injection-resistant).** `child_process.exec()`/`execSync()` always spawn a shell over a string command — prefer `execFile`/`spawn` with an argument array and `shell: false` (the default) instead of setting `shell: true`. Validate and normalize any path built from input (`path.resolve(...)`, then confirm it's inside the working area) to block traversal. Add `--dry-run` to destructive operations, and think twice before `fs.rmSync(path, { recursive: true })` or a `rimraf` call on anything derived from user input.

**AST02 — Supply Chain Compromise.** Don't pipe a download into a shell (`curl … | sh`): fetch, verify a checksum or signature, then run. Pin `package.json` dependencies to exact versions (`"left-pad": "1.3.0"`, not `^1.3.0` or `latest`) so a compromised upstream release can't be pulled in silently via `npm ci`. Review dependencies for known vulnerabilities (`npm audit`) before adding them.

**AST01 — Malicious Skills.** The static check catches invisible/bidi-override unicode (Trojan Source) and prose that reads as an attack — countermanding the operator (`ignore all previous instructions`), presenting a replacement system prompt, telling the agent to conceal actions from the user, or directing credentials/keys somewhere. It matches prose only: fenced code blocks and inline spans are blanked first, so a doc that _exhibits_ a payload as an example — like the phrase just quoted above — must wrap it in backticks or a fence, or the scanner reads it as a live instruction. It is still a tripwire, not an adversary-proof filter — phrasing that doesn't match the patterns, or a payload deliberately hidden inside a fenced block, passes clean. The broader risk stays human: read the SKILL.md as the _instructions an agent will follow_. Watch for steps that exfiltrate data via channels the scanner doesn't model, fetch-and-run remote code, or quietly escalate scope beyond the skill's stated job.

## The process risks (human review)

These have no static signal — confirm them by reading and by how the skill is maintained:

- **AST07 Update Drift** — does the skill pin its tooling (`package-lock.json` committed, `engines.node` set) and document a review cadence, or will it silently drift as dependencies and the platform move? Stale `compatibility` claims and an uncommitted lockfile are the tell.
- **AST09 No Governance** — is there a clear owner, a changelog, and a way to report issues? An unmaintained skill is an accumulating liability.
- **AST10 Cross-Platform Reuse** — a skill copied across agents/platforms inherits assumptions (available tools, sandbox, path conventions, whether Node is even installed) that may not hold. Re-audit on each target; see [cross-skill-design.md](cross-skill-design.md) for the related triggering pitfalls.

## Pre-publication checklist

Before sharing or publishing a skill, confirm:

- [ ] `audit_security.ts <skill-dir>` reports no FAIL findings, and each WARN is either fixed or consciously accepted (note why).
- [ ] No secrets in SKILL.md, references, or scripts; secrets come from env vars.
- [ ] `allowed-tools` is scoped to the minimum (no bare/wildcard `Bash`).
- [ ] No unsafe dynamic evaluation or `{shell: true}` on input-derived data.
- [ ] `package.json` dependencies pinned to exact versions and reviewed; `package-lock.json` committed.
- [ ] SKILL.md instructions read cleanly as agent instructions — no hidden text, no fetch-and-run, no scope creep.
- [ ] Owner, changelog, and issue path are documented (governance).

## Interpreting findings honestly

`audit_security.ts` is a token/regex scanner. It blanks TypeScript strings, template literals, and comments (via the TypeScript compiler's scanner) before the code-construct checks run — so `eval(` in a comment isn't a hit — and skips `tests/`, `node_modules/`, and `*.test.ts` files (mock secrets and sample dangerous code live there). It still produces **true positives that you accept after triage** — an intentional `new Function()` in a template engine, a deliberately loose pin on an optional SDK. A WARN is a prompt to make a conscious decision, not an automatic defect. Record the decision; don't silently ignore it.
