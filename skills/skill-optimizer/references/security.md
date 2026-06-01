# Skill security (OWASP Agentic Skills Top 10)

A skill is executable trust: its SKILL.md becomes agent instructions and its bundled scripts run with the agent's privileges. A skill that leaks a secret, ships malicious instructions, or pulls a compromised dependency is a supply-chain risk for everyone who installs it. This guide maps the [OWASP Agentic Skills Top 10](https://owasp.org/www-project-agentic-skills-top-10/) and its [skill development guide](https://owasp.org/www-project-agentic-skills-top-10/skill-development-guide) onto concrete checks for a Claude skill.

## When to read this

- You're auditing a skill you didn't write, or one you intend to publish/share.
- `audit_security.py` reported a finding and you want the context behind the `AST##` code.
- You're scaffolding a skill that handles credentials, fetches remote content, runs shell commands, or deserializes data.
- A skill grants `Bash` in `allowed-tools` and you're deciding how tightly to scope it.

## What the auditor checks vs. what you check

`audit_security.py <skill-dir>` is a static, high-signal scan. It catches the mechanically-detectable subset of the Top 10 and exits non-zero only on FAIL-level findings (hardcoded secrets). Everything else is a WARN you triage. The static scan is necessary but **not sufficient** — it is itself an instance of AST08 (poor scanning is the risk; running a scan is one control, not the whole story). The process risks below have no static signal and need a human pass.

| ID    | Risk                  | Auto-checked? | Code(s) |
|-------|-----------------------|---------------|---------|
| AST01 | Malicious Skills      | Partial — hidden/bidi unicode in instructions | `security.body.hidden-unicode` |
| AST02 | Supply Chain Compromise | Partial — `curl\|sh`, unpinned PEP 723 deps | `security.exec.curl-pipe-shell`, `security.deps.unpinned` |
| AST03 | Over-Privileged Skills | Yes — unrestricted/wildcard `Bash` | `security.tools.unrestricted-bash`, `security.tools.broad-bash-glob` |
| AST04 | Insecure Metadata     | Yes — hardcoded secrets/keys | `security.secret.hardcoded` (FAIL) |
| AST05 | Unsafe Deserialization | Yes — pickle, `yaml.load`, eval/exec, marshal | `security.script.unsafe-deserialization` |
| AST06 | Weak Isolation        | Yes — `shell=True`, `os.system`, destructive fs | `security.script.shell-injection`, `security.script.dangerous-fs` |
| AST07 | Update Drift          | No — needs human review | — |
| AST08 | Poor Scanning         | This tool is the control | — |
| AST09 | No Governance         | No — needs human review | — |
| AST10 | Cross-Platform Reuse  | No — see also cross-skill-design.md | — |

## The auto-checked risks

**AST03 — Over-Privileged Skills (least privilege).** Grant the narrowest `allowed-tools` that works. A bare `Bash` token or a `Bash(*)` wildcard is full shell access; scope it to the command prefix the skill actually runs, e.g. `Bash(python3 *)` or `Bash(git status *)`. Prefer the dedicated `Read`/`Edit`/`Write` tools over shelling out. Drop any tool the skill no longer uses.

**AST04 — Insecure Metadata (no embedded secrets).** Never ship a secret in SKILL.md, a reference, or a bundled script — skills are distributed verbatim and a committed key is a leaked key. Read secrets from environment variables at runtime (`os.environ["X"]`), document which vars are required, and keep placeholders obviously fake (`your-token-here`, `<API_KEY>`). The auditor FAILs on AWS/GitHub/Slack/Google tokens, private-key blocks, and credential-shaped assignments to a literal.

**AST05 — Unsafe Deserialization.** `pickle.loads`, `marshal.loads`, `yaml.load` (without `SafeLoader`), and `eval`/`exec` all execute attacker-controlled bytes. Use `json`, `yaml.safe_load`, or a schema-validated parser. If `exec`/`eval` is genuinely required (e.g. a profiler), isolate it and confirm the input is trusted.

**AST06 — Weak Isolation (injection-resistant).** `subprocess(..., shell=True)` and `os.system()` concatenate strings into a shell — pass an argument list with `shell=False` instead. Validate and normalize any path built from input (`Path(...).resolve()`, then confirm it's inside the working area) to block traversal. Add `--dry-run` to destructive operations.

**AST02 — Supply Chain Compromise.** Don't pipe a download into a shell (`curl … | sh`): fetch, verify a checksum or signature, then run. Pin PEP 723 dependencies to exact versions (`package==1.2.3`) so a compromised upstream release can't be pulled in silently. Review dependencies for known vulnerabilities before adding them.

**AST01 — Malicious Skills.** The static check catches invisible/bidi-override unicode (Trojan Source) hidden in instructions, but the broader risk is human: read the SKILL.md as the *instructions an agent will follow*. Watch for steps that exfiltrate data, fetch-and-run remote code, or quietly escalate scope beyond the skill's stated job.

## The process risks (human review)

These have no static signal — confirm them by reading and by how the skill is maintained:

- **AST07 Update Drift** — does the skill pin its tooling and document a review cadence, or will it silently drift as dependencies and the platform move? Stale `compatibility` claims and unpinned tools are the tell.
- **AST09 No Governance** — is there a clear owner, a changelog, and a way to report issues? An unmaintained skill is an accumulating liability.
- **AST10 Cross-Platform Reuse** — a skill copied across agents/platforms inherits assumptions (available tools, sandbox, path conventions) that may not hold. Re-audit on each target; see [cross-skill-design.md](cross-skill-design.md) for the related triggering pitfalls.

## Pre-publication checklist

Before sharing or publishing a skill, confirm:

- [ ] `audit_security.py <skill-dir>` reports no FAIL findings, and each WARN is either fixed or consciously accepted (note why).
- [ ] No secrets in SKILL.md, references, or scripts; secrets come from env vars.
- [ ] `allowed-tools` is scoped to the minimum (no bare/wildcard `Bash`).
- [ ] No unsafe deserialization or `shell=True` on input-derived data.
- [ ] PEP 723 dependencies pinned to exact versions and reviewed.
- [ ] SKILL.md instructions read cleanly as agent instructions — no hidden text, no fetch-and-run, no scope creep.
- [ ] Owner, changelog, and issue path are documented (governance).

## Interpreting findings honestly

`audit_security.py` is a regex/token scanner. It blanks Python strings and comments before the code-construct checks (so `eval(` in a docstring isn't a hit) and skips `tests/` (mock secrets and sample dangerous code live there). It still produces **true positives that you accept after triage** — an intentional `exec()` in a profiler, a deliberately loose pin on an optional SDK. A WARN is a prompt to make a conscious decision, not an automatic defect. Record the decision; don't silently ignore it.
