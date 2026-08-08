---
name: architecture
description: Architecture work across five modes — (1) audit code for ADR and separation-of-concerns violations, (2) find deepening / refactoring opportunities, (3) write or update ADRs, (4) write or update CONTEXT.md / CONTEXT-MAP.md domain docs, (5) design module interfaces. Informed by the project's domain glossary (CONTEXT.md or equivalent) and its architecture decision records (docs/adr/ or equivalent). Use when the user wants to improve architecture, check whether code conforms to the ADRs or the layer separation, find refactoring opportunities, consolidate tightly-coupled modules, record an architectural decision, refresh stale or missing context docs, or design an interface. If the user doesn't say which, default to auditing for violations and finding deepening opportunities.
---

# Architecture

Architecture work over one shared vocabulary and one source of truth: the repo's architecture decision records and its domain docs — by default the ADRs in `docs/adr/`, a `CONTEXT.md` per context and `CONTEXT-MAP.md` at the root, but follow whatever the repo already uses ([Adapt to the repo and environment](#adapt-to-the-repo-and-environment)). The skill runs in **five modes** that compose freely — an audit finding is fixed by a deepening, a deepening may need an interface design, and the decision behind it is recorded as an ADR with new vocabulary captured in the glossary.

The aims throughout are readability, testability, and AI-navigability.

**Where the environment provides subagents, delegate broad reading to them and keep interactive steps — presenting candidates, the design interview, writing docs — in the main context.** A subagent returns conclusions, not the files it read, so the sweeping modes stay light. Without subagents, read directly but hold the same discipline: surface distilled findings, not file dumps.

## Modes

| Mode             | What it does | Section                                         |
|------------------|--------------|-------------------------------------------------|
| **Audit**        | Find code that contravenes the ADRs, the layer separation, or good architectural practice, and give fix guidance | [Mode: Audit](#mode-audit) |
| **Deepen**       | Explore for deepening opportunities — turning shallow modules into deep ones | [Mode: Deepen](#mode-deepen) |
| **ADR**          | Write or update an ADR | [Mode: ADR](#mode-adr) |
| **Context docs** | Write or update `CONTEXT.md` / `CONTEXT-MAP.md` when missing or inaccurate | [Mode: Context docs](#mode-context-docs) |
| **Interface**    | Design a module interface | [Mode: Interface](#mode-interface) |

**Pick the mode(s) from how the skill was invoked:**

- "does this conform to our architecture / check for violations / what breaks the layer rules" → **Audit**
- "find refactoring opportunities / improve the architecture / this module feels shallow / consolidate these" → **Deepen**
- "record this as an ADR / document this decision / that ADR is wrong" → **ADR**
- "the context docs are stale / document this domain / CONTEXT.md is missing a term" → **Context docs**
- "design an interface for X / what's the right shape for this module" → **Interface**
- **If the user doesn't clearly specify, default to Audit + Deepen.**

The agent may also enter Context-docs mode on its own initiative when the criteria in that section are met — offer first, don't write unprompted. ADR mode is user-initiated only (see [Mode: ADR](#mode-adr)) — most candidate decisions are low-value implementation detail, so don't propose one unless asked.

## Scope first

Before doing anything, decide the blast radius:

- **Global** (user asked broadly to look at architecture): explore the whole codebase.
- **Targeted** (user arrived from a friction note raised by another workflow — a test-first cycle, a code review, a refactor — or pointed at a specific area/file): scope to that area only. The right question is "what's the smallest thing that resolves the noted friction?", not "what's everything worth changing here?"

If unclear, ask. Manufacturing global findings during a small feature is the failure mode this skill must avoid.

## Adapt to the repo and environment

This skill is meant to be dropped into any repository. Resolve three things once, up front, and say what you found — never assume the defaults are absent just because they aren't where you looked first.

**1. Where decisions are recorded.** Look for `docs/adr/`, then `docs/decisions/`, `doc/adr/`, `adr/`, `architecture/decisions/`, or any `decisions`/`adr` directory near the docs root. **Follow the convention the repo already uses** — its location, numbering, and template — even where it differs from [adr-format.md](assets/adr-format.md). That file's format applies only when the repo records no decisions at all.

**2. Where the domain vocabulary lives.** Look for `CONTEXT-MAP.md` and `CONTEXT.md`, then for an equivalent under another name: `GLOSSARY.md`, `DOMAIN.md`, `UBIQUITOUS-LANGUAGE.md`, or a glossary section inside `README.md`, `CLAUDE.md`, or `AGENTS.md`. Read whichever exists and write back to it in its own style. Create `CONTEXT.md` / `CONTEXT-MAP.md` per [context-format.md](assets/context-format.md) only when the repo has no glossary anywhere.

**3. What this environment can actually do.** Every handoff below is an optimisation, not a requirement — degrade rather than stall, and don't tell the user to run a command that doesn't exist here.

| Handoff | If unavailable |
|---|---|
| Subagents (`Agent` tool, `Explore` / `general-purpose` types) | Read directly in the main context, narrowing by scope; report conclusions only |
| A planning/design-interview skill (`/plan`) | Run the interview inline — one decision at a time, no batched question lists |
| A refactoring skill (`/refactor`) | Describe the same-layer cleanup and offer to apply it |
| A test-first skill (`/tdd`) | Describe the behaviour change and the test that should drive it |

Also honour the repo's own conventions where they're stated — `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, a `docs/architecture/` page. A rule the repo has already written down outranks this skill's defaults.

## Glossary

Use these terms exactly in every suggestion, in every mode. **Read [language.md](references/language.md) before hunting (Audit) or presenting candidates (Deepen)** — the one-liners below are enough to talk with, not to judge with. That file holds the full definitions, the terms not to substitute, and the six principles you assess against: the deletion test, the interface as test surface, one-adapter-vs-two, depth-without-cohesion, internal vs. external seams, readability as tiebreaker.

- **Module** — anything with an interface and an implementation (function, class, package, slice).
- **Interface** — everything a caller must know to use the module: types, invariants, error modes, ordering, config. Not just the type signature.
- **Implementation** — the code inside.
- **Depth** — leverage at the interface: a lot of behaviour behind a small interface. **Deep** = high leverage. **Shallow** = interface nearly as complex as the implementation.
- **Seam** — where an interface lives; a place behaviour can be altered without editing in place. (Use this, not "boundary.")
- **Adapter** — a concrete thing satisfying an interface at a seam.
- **Leverage** — what callers get from depth.
- **Locality** — what maintainers get from depth: change, bugs, knowledge concentrated in one place.
- **Cohesion** — whether a module has a single, clear reason to exist. Depth without cohesion is a junk drawer behind a small door.
- **Cognitive load** — how many things a developer must hold in their head to understand or change a piece of code.

This skill is _informed_ by the project's domain model. The domain language gives names to good seams; ADRs record decisions the skill should not re-litigate.

## Read first (Audit and Deepen)

Both code-reading modes start the same way: locate the domain glossary and the decision records ([Adapt to the repo](#adapt-to-the-repo-and-environment)), then read the glossary covering the area you're touching and the decisions relevant to it. If they are **genuinely missing or inaccurate** — not merely filed somewhere you didn't check — that's a signal in its own right: flag it, and offer to switch to **Context docs** or **ADR** mode to fix it. Otherwise proceed with assumptions based on the codebase, stating them.

---

## Mode: Audit

Find code that contravenes the ADRs, the layer separation, or good architectural practice — and give guidance on how to bring it into conformance. This mode _hunts for violations_ (Deepen hunts for shallowness); the two overlap because the usual fix for a violation is a deepening or a layer move.

### 1. Load the rules

Read the decision records and the context map (or the single glossary, if that's what the repo has) — locations per [Adapt to the repo](#adapt-to-the-repo-and-environment). Where a repo has few or no recorded decisions, the rules you audit against are the layer separation visible in the code and the principles in [language.md](references/language.md) — **read that file now if you haven't**, since it is standing in for the missing ADRs. Say which set you're using.

### 2. Hunt

**Fan out multiple finder subagents** — for a scoped audit, run them in parallel, one message with multiple Agent calls. Give each finder the specific rules and its scope, and have it return **structured findings**: `file:line`, the rule broken, why, and fix guidance. The main agent sees only the distilled findings, never the files each finder read. Without subagents, sweep the scope yourself one area at a time and keep the same finding shape.

When false positives are costly, add a **verify pass** — one subagent per candidate finding, prompted to *refute* it; drop the ones it refutes. This is the guard against the "always finds something" failure mode.

### 3. Report

Lead with the highest-leverage findings — the violations whose fix removes the most friction, or that are spreading (a leaked seam N callers already reach through). For each: **file:line**, **which ADR or principle it violates**, **why it's a problem**, and **fix guidance** — usually "deepen X into Y" (hand to Deepen) or "move this logic from layer A to layer B."

**Distinguish a code bug from a stale rule.** If the code is right and the *ADR* is what's outdated, say so and offer **ADR** mode to update the decision — don't force the code to conform to a rule that no longer serves. This is the same "docs stale vs. architecture muddy" judgement the Deepen mode makes about vocabulary.

By default this mode **gives guidance, it doesn't apply fixes.** Route the actual change to whichever workflow fits the finding — a planning skill for design-heavy work, a refactoring skill for same-layer cleanup, a test-first skill for behaviour change — using this environment's equivalents, or the inline fallbacks in [Adapt to the repo](#adapt-to-the-repo-and-environment).

---

## Mode: Deepen

Surface architectural friction and propose **deepening opportunities** — refactors that turn shallow modules into deep ones.

### 1. Explore

After the [Read first](#read-first-audit-and-deepen) step, walk the codebase — with a search-oriented subagent (`subagent_type=Explore`) where one exists, otherwise directly. Exploration *locates* candidates (high coupling, module size, low test coverage, naming); the judgement (is this shallow? does deleting it concentrate complexity?) stays with you, or with a `general-purpose` subagent when it turns on reading the implementation. Focus on high coupling, low test coverage, or shallow modules, and note where you experience friction:

- Where does understanding one concept require bouncing between many small modules? (high **cognitive load**)
- Where are modules **shallow** — interface nearly as complex as the implementation?
- Where have pure functions been extracted just for testability, but the real bugs hide in how they're called (no **locality**)?
- Where do tightly-coupled modules leak across their seams?
- Which parts are untested, or hard to test through their current interface?
- Where is unnecessary indirection making the code harder to follow — wrapper classes, middleman modules, premature generalization? (over-abstraction)
- Where do modules have misleading or vague names that don't match what they do?
- Where do dependencies flow the wrong way — circular, or stable-on-volatile?
- Where do callers consistently misuse a module — a sign the interface promises one thing but requires callers to know something extra?
- Where does a module do many unrelated things behind a single interface? (low **cohesion**)
- Where does the codebase's vocabulary drift from `CONTEXT.md`? Drift signals either stale docs or muddy architecture; both call for the same fix — flag it and consider **[Context docs](#mode-context-docs)** mode, which lists the drift shapes to look for.

Apply the **deletion test** to anything you suspect is shallow or over-abstracted: would deleting it concentrate complexity, or just move it? A "yes, concentrates" is the signal you want.

### 2. Present candidates

Present a numbered list of deepening opportunities. For each:

- **Files** — which files/modules are involved
- **Problem** — why the current architecture causes friction
- **Solution** — plain-English description of what would change
- **Benefits** — in terms of locality, leverage, readability, and how tests would improve

**Use CONTEXT.md vocabulary for the domain, and [Glossary](#glossary) vocabulary for the architecture.** If `CONTEXT.md` defines "Order," talk about "the Order intake module" — not "the FooBarHandler," and not "the Order service."

**ADR conflicts**: if a candidate contradicts an existing ADR, only surface it when the friction is real enough to warrant revisiting the ADR. Mark it clearly (_"contradicts ADR-0001 — but worth reopening because…"_). Don't list every theoretical refactor an ADR forbids.

Do NOT propose interfaces yet. Ask: "Which of these would you like to explore?"

### 3. Planning loop

Once the user picks a candidate, hand the design conversation to this environment's planning skill (`/plan`, invoked via the Skill tool) — it interviews the user one decision at a time; this skill stays paused until that finishes, then resumes to surface the doc-update side effects below. (You're not "becoming" the planning skill — you hand off, then resume.) Where no such skill exists, run the interview yourself: one open question at a time, resolving each branch before opening the next.

Within the plan, walk the design tree — constraints, dependencies, the shape of the deepened module, what sits behind the seam, what tests survive. Read [references/deepening.md](references/deepening.md) when classifying the deepened module's dependencies (in-process / local-substitutable / remote-but-owned / true-external) and choosing its test strategy. Enter **[Interface](#mode-interface)** mode when the interface has real tension.

As decisions crystallise, apply **[Context docs](#mode-context-docs)** mode inline — treat vocabulary capture as a deliberate output of the plan, not an afterthought. Before finalising, ask explicitly: *"what vocabulary does this change, and where does that get recorded?"* Only reach for **[ADR](#mode-adr)** mode if the user asks for a decision to be recorded — don't default to writing one for every deepening.

---

## Mode: ADR

Record an architectural decision, or correct/supersede an existing one. Format and numbering: [adr-format.md](assets/adr-format.md).

**Enter this mode when the user asks to record or change a decision.** Don't propose writing an ADR on your own initiative — most candidate decisions surfaced during audits, deepenings, or planning are low-value implementation detail, not load-bearing architecture. If a genuinely hard-to-reverse, surprising, trade-off-laden decision comes up in another mode, it's fine to name that ("this looks like a decision worth recording") without offering to write the ADR yourself — let the user ask.

**Process:**

1. Read the repo's existing decision records ([Adapt to the repo](#adapt-to-the-repo-and-environment)) to get the next number and avoid duplicating a decision already recorded. If the repo has none, create `docs/adr/` lazily.
2. Write in the repo's existing ADR style if it has one; otherwise per [adr-format.md](assets/adr-format.md) — an ADR can be a single paragraph; the value is recording *that* a decision was made and *why*.
3. **Updating an existing ADR:** if a decision is reversed or refined, prefer a new ADR that supersedes the old one (note it in both) over silently rewriting history — unless the change is a small correction to an as-yet-unactioned decision.

---

## Mode: Context docs

Create or fix `CONTEXT.md` / `CONTEXT-MAP.md` when they're missing or inaccurate. Format: [context-format.md](assets/context-format.md). `CONTEXT-MAP.md` names every context, links its `CONTEXT.md`, and records cross-context relationships and vocabulary clashes. Where the repo already keeps its glossary under another name or in another place ([Adapt to the repo](#adapt-to-the-repo-and-environment)), edit that file in its own style rather than starting a parallel set of docs beside it; the cases below then apply to whatever plays each role.

**Enter this mode when** the user asks, when a code-reading mode surfaces drift between the code and the docs, or when a refactor changes the domain vocabulary. Read `CONTEXT-MAP.md` first to see which contexts are registered, then the relevant per-context `CONTEXT.md`.

To detect drift across a context, **delegate the scan to a subagent where one is available** (`subagent_type=general-purpose` — judging whether a term has drifted or a definition still matches the code is a conformance call, not a pure location task): have it compare the terms the code actually uses against that context's glossary and return a list of drift items — terms in code but not the glossary, one word meaning different things in adjacent modules, definitions that no longer match the code. Do the writing yourself; only the scan is worth offloading.

The cases and what each requires:

- **A concept the code uses isn't in `CONTEXT.md`** → add the term to the relevant per-context `CONTEXT.md`. Create the file lazily if it doesn't exist, and register the new context in `CONTEXT-MAP.md`.
- **A term is fuzzy or has drifted** → sharpen its definition in place.
- **A new cross-context relationship** (a new data flow between domains, a new event boundary) → add a bullet to `CONTEXT-MAP.md`'s "Relationships" section.
- **A vocabulary collision between contexts** (same word, different meaning) → add it to `CONTEXT-MAP.md`'s "Cross-context vocabulary clashes" with a resolution.
- **A new bounded subdomain** → create a new `CONTEXT.md` and register it in `CONTEXT-MAP.md`'s "Contexts" list.
- **A concept absorbed, merged, or removed** → strike it from `CONTEXT.md` (and any `CONTEXT-MAP.md` cross-references) so future readers don't hunt for a term that no longer exists.

---

## Mode: Interface

Design a module's interface. Use when the user asks directly, or when a deepening / audit fix surfaces real interface tension — more than one legitimate shape with meaningful trade-offs.

- **Real tension** (multiple viable shapes): use the multi-agent "Design It Twice" process in [references/interface-design.md](references/interface-design.md) — your first idea is unlikely to be the best.
- **Obvious shape**: sketch one or two alternatives inline; don't spin up the full process.

Read [deepening.md](references/deepening.md) when classifying the module's dependencies (in-process / local-substitutable / remote-but-owned / true-external) — the category determines the seam and how the interface is tested. Keep the [Glossary](#glossary) vocabulary throughout.

## Failure modes to avoid

Before reporting, re-check the four guards already stated above: scope ([Scope first](#scope-first)), false-positive shallowness (the deletion test / verify pass), unprompted doc-writing (the offer-first rule in [ADR](#mode-adr) / [Context docs](#mode-context-docs)), and imported conventions — don't report a repo's docs as missing, or its layering as wrong, because it organises them differently than this skill's defaults ([Adapt to the repo](#adapt-to-the-repo-and-environment)).
