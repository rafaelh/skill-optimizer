---
name: plan
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when the user wants to plan something, stress-test a plan, get questioned on their design.
---

Interview the user one question at a time until every decision point in the plan has an explicit answer.

## Process

1. **Enumerate unresolved decisions.** A decision is any choice not yet pinned down: alternatives without a pick, ambiguous scope, missing constraints, hand-waved "we'll figure it out later" parts, hidden assumptions phrased as "obviously we'd…".
2. **Order by dependency.** Decisions that constrain others come first (sync vs async before API shape; data model before query patterns). Don't ask about leaves before roots.
3. **Ask one question at a time, with your recommended answer and a one-line rationale.** The user's job is to review a recommendation, not redo the analysis from scratch.
4. **After each answer, re-scan for new branches** the answer opened up and append them to the queue.
5. **Stop** when every decision has an explicit answer and the user could hand the plan to someone else to implement without follow-up questions.
6. **Offer the TDD handoff** for code-bearing plans: _"Plan looks complete — want me to drive the implementation with `/tdd`?"_ The TDD skill picks up the plan's named modules, interfaces, and `CONTEXT.md` vocabulary directly into test names. Skip the offer for plans that don't produce code (an investigation, a decision memo, a meeting agenda). **Do not re-offer `/plan` from inside TDD** — the chain runs one direction per user goal.

## Gotchas

- **If a question can be answered by reading code, read the code.** Don't ask "how does auth work today" — go look. Reserve questions for choices only the user can make.
- **Always lead with a recommendation.** A bare "what do you think about X?" makes the user do all the work. Pick an answer, give the tradeoff, then ask them to confirm or redirect.
- **Decision-forcing, not exploratory.** "Sync or async, and why?" beats "tell me about your concurrency requirements." If a question doesn't pin down a choice, rewrite it.
- **One question per turn.** Bundling lets branches collapse together unanswered and the user picks the easy ones.
- **Surface hidden assumptions.** "Obviously we'd do X" is a decision worth confirming, not skipping.
