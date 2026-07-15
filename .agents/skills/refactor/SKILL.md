---
name: refactor
description: >
  Use this skill when code works but is harder to read, maintain, or extend than it should be:
  deeply nested logic, long functions, unclear names, duplicated logic, or complexity that
  accumulated from iterative changes. Also trigger during code review when readability issues
  are flagged, after a feature lands under time pressure, or when consolidating related logic
  scattered across files.
---

# Code Refactor

## Overview

Simplify code by reducing complexity while preserving exact behavior. The goal is not fewer lines — it's code that is easier to read, understand, modify, and debug. Every simplification must pass a simple test: "Would a new team member understand this faster than the original?"

## When to Use

- After a feature is working and tests pass, but the implementation feels heavier than it needs to be
- During code review when readability or complexity issues are flagged
- When you encounter deeply nested logic, long functions, or unclear names
- When refactoring code written under time pressure
- When consolidating related logic scattered across files
- After merging changes that introduced duplication or inconsistency
- At step 4 of a TDD cycle (you've reached GREEN — if you arrived from `/tdd`, skip scope discussion and go straight to Step 2 below)

**Only start when all tests pass.** Never refactor on RED — you can't tell whether a failing test is due to the refactor or the original code.

**When NOT to use:**

- Code is already clean and readable — don't simplify for the sake of it
- You don't understand what the code does yet — comprehend before you simplify
- The code is performance-critical and the "simpler" version would be measurably slower
- You're about to rewrite the module entirely — simplifying throwaway code wastes effort
- The change moves code between architectural layers — that requires ADR review

## The Five Principles

### 1. Preserve Behavior Exactly

Don't change what the code does — only how it expresses it. All inputs, outputs, side effects, error behavior, and edge cases must remain identical. If you're not sure a simplification preserves behavior, don't make it.

Note: Guard clauses are valid only when behavior is preserved exactly — the same input must still reach the same outcome/exception as before. Restructuring conditionals is fine only when it does not change which exception is raised for any input.

```
ASK BEFORE EVERY CHANGE:
→ Does this produce the same output for every input?
→ Does this maintain the same error behavior?
→ Does this preserve the same side effects and ordering?
→ Do all existing tests still pass without modification?
```

### 2. Follow Project Conventions

Simplification means making code more consistent with the codebase, not imposing external preferences. Before simplifying:

```
1. Read CLAUDE.md / AGENTS.md / copilot-instructions.md
2. Study how neighboring code handles similar patterns
3. Match the project's style for:
   - Import ordering and module system
   - Logging calls (%s substitution, not f-strings)
   - Naming conventions
   - Error handling patterns
   - Type annotation depth
```

Simplification that breaks project consistency is not simplification — it's churn.

### 3. Prefer Clarity Over Cleverness

Explicit code is better than compact code when the compact version requires a mental pause to parse.

```python
# UNCLEAR: Dense conditional chain
status = "new" if is_new else "updated" if is_updated else "archived" if is_archived else "active"

# CLEAR: Readable early-return function
def get_status(item: Item) -> str:
    if item.is_new:
        return "new"
    if item.is_updated:
        return "updated"
    if item.is_archived:
        return "archived"
    return "active"
```

```python
# UNCLEAR: Nested comprehension with implicit accumulation
counts = {k: sum(1 for x in items if x.category == k) for k in {x.category for x in items}}

# CLEAR: Explicit loop with named intermediate
counts: dict[str, int] = {}
for item in items:
    counts[item.category] = counts.get(item.category, 0) + 1
```

### 4. Maintain Balance

Simplification has a failure mode: over-simplification. Watch for these traps:

- **Inlining too aggressively** — removing a helper that gave a concept a name makes the call site harder to read
- **Combining unrelated logic** — two simple functions merged into one complex function is not simpler
- **Removing "unnecessary" abstraction** — some abstractions exist for extensibility or testability, not complexity
- **Optimizing for line count** — fewer lines is not the goal; easier comprehension is

### 5. Scope to What Changed

Default to simplifying recently modified code. Avoid drive-by refactors of unrelated code unless explicitly asked to broaden scope. Unscoped simplification creates noise in diffs and risks unintended regressions.

**Deletion test.** Before noting a piece of code as friction worth addressing, ask: "If I removed this, would complexity concentrate somewhere else?" If the answer is no, it's a wish-list item — skip it. If yes, it's real friction worth fixing. Apply this filter to keep the refactor focused on genuine improvements rather than aesthetic preferences.


## The Simplification Process

### Step 1: Understand Before Touching (Chesterton's Fence)

Before changing or removing anything, understand why it exists. This is Chesterton's Fence: if you see a fence across a road and don't understand why it's there, don't tear it down. First understand the reason, then decide if the reason still applies.

```
BEFORE SIMPLIFYING, ANSWER:
- What is this code's responsibility?
- What calls it? What does it call?
- What are the edge cases and error paths?
- Are there tests that define the expected behavior?
- Why might it have been written this way? (Performance? Platform constraint? Historical reason?)
- Check git blame: what was the original context for this code?
```

If you can't answer these, you're not ready to simplify. Read more context first.

### Step 2: Identify Simplification Opportunities

Scan for these patterns — each one is a concrete signal, not a vague smell:

**Structural complexity:**

| Pattern | Signal | Simplification |
|---------|--------|----------------|
| Deep nesting (3+ levels) | Hard to follow control flow | Extract conditions into guard clauses or helper functions |
| Long functions (50+ lines) | Multiple responsibilities | Split into focused functions with descriptive names |
| Nested ternaries | Requires mental stack to parse | Replace with if/else chains or lookup dicts |
| Boolean parameter flags | `do_thing(True, False, True)` | Replace with keyword-only arguments, an enum, or separate functions |
| Repeated conditionals | Same `if` check in multiple places | Extract to a well-named predicate function |
| Shallow modules | Thin pass-through that just delegates to another function without adding any logic | Inline the pass-through, or deepen it by moving caller-side logic inside |
| Feature envy | A function that repeatedly accesses another object's data | Move the logic to where the data lives |
| Primitive obsession | Raw `str`/`dict` used everywhere to represent a concept that has validation rules | Introduce a Pydantic model or named type to carry the concept and its constraints |

**Naming and readability:**

| Pattern | Signal | Simplification |
|---------|--------|----------------|
| Generic names | `data`, `result`, `temp`, `val`, `item` | Rename to describe the content: `user_profile`, `validation_errors` |
| Abbreviated names | `usr`, `cfg`, `btn`, `evt` | Use full words unless the abbreviation is universal (`id`, `url`, `api`) |
| Misleading names | Function named `get` that also mutates state | Rename to reflect actual behavior |
| Comments explaining "what" | `# increment counter` above `count += 1` | Delete the comment — the code is clear enough |
| Comments explaining "why" | `# Retry because the API is flaky under load` | Keep these — they carry intent the code can't express |

**Redundancy:**

| Pattern | Signal | Simplification |
|---------|--------|----------------|
| Duplicated logic | Same 5+ lines in multiple places | Extract to a shared function |
| Dead code | Unreachable branches, unused variables, commented-out blocks | Remove (after confirming it's truly dead) |
| Unnecessary abstractions | Wrapper that adds no value | Inline the wrapper, call the underlying function directly |
| Over-engineered patterns | Factory-for-a-factory, strategy-with-one-strategy | Replace with the simple direct approach |

### Step 3: Apply Changes Incrementally

Make one simplification at a time. Run tests after each change. **Submit refactoring changes separately from feature or bug fix changes.** A PR that refactors and adds a feature is two PRs — split them.

```
FOR EACH SIMPLIFICATION:
1. Make the change
2. Run the test suite
3. If tests pass → commit (or continue to next simplification)
4. If tests fail → revert and reconsider
```

Avoid batching multiple simplifications into a single untested change. If something breaks, you need to know which simplification caused it.

**The Rule of 500:** If a refactoring would touch more than 500 lines, invest in automation (codemods, sed scripts, AST transforms) rather than making the changes by hand. Manual edits at that scale are error-prone and exhausting to review.

### Step 4: Verify the Result

After all simplifications, step back and evaluate the whole:

```
COMPARE BEFORE AND AFTER:
- Is the simplified version genuinely easier to understand?
- Did you introduce any new patterns inconsistent with the codebase?
- Is the diff clean and reviewable?
- Would a teammate approve this change?
```

If the "simplified" version is harder to understand or review, revert. Not every simplification attempt succeeds.

## Python-specific guidance

```python
# SIMPLIFY: Verbose dictionary building
# Before
result = {}
for item in items:
    result[item.id] = item.name
# After
result = {item.id: item.name for item in items}

# SIMPLIFY: Nested conditionals with early return
# Before
def process(data):
    if data is not None:
        if data.is_valid():
            if data.has_permission():
                return do_work(data)
            else:
                raise PermissionError("No permission")
        else:
            raise ValueError("Invalid data")
    else:
        raise TypeError("Data is None")
# After
def process(data):
    if data is None:
        raise TypeError("Data is None")
    if not data.is_valid():
        raise ValueError("Invalid data")
    if not data.has_permission():
        raise PermissionError("No permission")
    return do_work(data)

# Note: This guard-clause rewrite is behavior-preserving because each input still maps
# to the same result/exception as the nested version.
```

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "It's working, no need to touch it" | Working code that's hard to read will be hard to fix when it breaks. Simplifying now saves time on every future change. |
| "Fewer lines is always simpler" | A 1-line nested ternary is not simpler than a 5-line if/else. Simplicity is about comprehension speed, not line count. |
| "I'll just quickly simplify this unrelated code too" | Unscoped simplification creates noisy diffs and risks regressions in code you didn't intend to change. Stay focused. |
| "The types make it self-documenting" | Types document structure, not intent. A well-named function explains *why* better than a type signature explains *what*. |
| "This abstraction might be useful later" | Don't preserve speculative abstractions. If it's not used now, it's complexity without value. Remove it and re-add when needed. |
| "The original author must have had a reason" | Maybe. Check git blame — apply Chesterton's Fence. But accumulated complexity often has no reason; it's just the residue of iteration under pressure. |
| "I'll refactor while adding this feature" | Separate refactoring from feature work. Mixed changes are harder to review, revert, and understand in history. |

## Red Flags

- Simplification that requires modifying tests to pass (you likely changed behavior)
- "Simplified" code that is longer and harder to follow than the original
- Renaming things to match your preferences rather than project conventions
- Removing error handling because "it makes the code cleaner"
- Simplifying code you don't fully understand
- Batching many simplifications into one large, hard-to-review commit
- Refactoring code outside the scope of the current task without being asked

## Verification

Run these commands in order after completing a simplification pass:

```bash
# Lint and auto-fix (scope to the skill you changed)
.venv/bin/ruff check --fix skills/<skill>/scripts/

# Format
.venv/bin/ruff format skills/<skill>/scripts/

# Type-check
.venv/bin/mypy skills/<skill>/scripts/<file>.py

# Run affected tests (scope to the skill you changed)
.venv/bin/pytest skills/<skill>/scripts/tests/

# Full suite
.venv/bin/pytest
```

Checklist:

- [ ] All existing tests pass without modification
- [ ] `ruff check` passes with no new issues
- [ ] `ruff format --check` passes (no formatting drift)
- [ ] `mypy` passes for the touched files
- [ ] Each simplification is a reviewable, incremental change
- [ ] The diff is clean — no unrelated changes mixed in
- [ ] Simplified code follows project conventions (checked against CLAUDE.md / AGENTS.md)
- [ ] No error handling was removed or weakened
- [ ] No dead code was left behind (unused imports, unreachable branches)
- [ ] A teammate or review agent would approve the change as a net improvement
