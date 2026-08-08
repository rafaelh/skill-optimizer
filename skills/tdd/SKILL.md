---
name: tdd
description: Use this skill when the user wants to build features or fix bugs using TDD (test-driven development), mentions "red-green-refactor", or asks for test-first development. Trigger even when the user doesn't say "TDD" explicitly, but mentions test-first workflows or iterative test-then-implement cycles. The following examples are non-exhaustive — infer similar phrasings that imply test-first or test-driven intent: "I want to write my tests first", "help me add this feature with proper test coverage from the start", "test-first approach", "drive the design from tests", "write tests before implementing", or "how do I develop this methodically with tests".
---

# Test-Driven Development

## Philosophy

**Core principle**: Tests should verify behavior through public interfaces, not implementation details. Code can change entirely; tests shouldn't.

**Good tests** are integration-style: they exercise real code paths through public APIs. They describe _what_ the system does, not _how_ it does it. A good test reads like a specification - "user can checkout with valid cart" tells you exactly what capability exists. These tests survive refactors because they don't care about internal structure.

**Bad tests** are coupled to implementation. They mock internal collaborators, test private methods, or verify through external means (like querying a database directly instead of using the interface). The warning sign: your test breaks when you refactor, but behavior hasn't changed. If you rename an internal function and tests fail, those tests were testing implementation, not behavior.

Load [tests.md](tests.md) when you need examples of good vs. bad tests. Load [mocking.md](mocking.md) when deciding what to mock.

## Anti-Pattern: Horizontal Slices

**DO NOT write all tests first, then all implementation.** This is "horizontal slicing" - treating RED as "write all tests" and GREEN as "write all code."

This produces **ineffective tests**:

- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You end up testing the _shape_ of things (data structures, function signatures) rather than user-facing behavior
- Tests become insensitive to real changes - they pass when behavior breaks, fail when behavior is fine
- You pre-emptively create constraints by committing to a test structure before understanding the implementation

**Correct approach**: Vertical slices via tracer bullets. One test → one implementation → repeat. Each test responds to what you learned from the previous cycle. Because you just wrote the code, you know exactly what behavior matters and how to verify it.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical):
  RED→GREEN: test1→impl1
  RED→GREEN: test2→impl2
  RED→GREEN: test3→impl3
  ...
```

## Priority of Constraints

When constraints conflict, follow this priority order (higher wins):
1. **One test at a time** — always vertical slices, never horizontal
2. **Test observable behavior** — use public interfaces, not implementation details
3. **Minimal code** — only enough to pass the current test

Example: if writing minimal code would bypass the public interface (e.g., hardcoding a return value that skips real behavior), prefer testing observable behavior (#2) over minimal code (#3).

## Workflow

### 1. Planning

When exploring the codebase, check CLAUDE.md and AGENTS.md so that test names and interface vocabulary match the project's conventions.

Before writing any code:

- [ ] Confirm with user what interface changes are needed
- [ ] Confirm with user which behaviors to test, as a numbered list they approve — you can't test everything, so prioritize critical paths and complex logic. One behavior gets one test; input variants are `parametrize` cases on that test, not additional tests.
- [ ] Identify opportunities for [deep modules](deep-modules.md) (small interface, deep implementation)
- [ ] Design interfaces for [testability](interface-design.md)
- [ ] List the behaviors to test (not implementation steps)
- [ ] Get user approval on the plan

Ask: "What should the public interface look like? Which behaviors are most important to test?"

**You can't test everything.** Confirm with the user exactly which behaviors matter most. Focus testing effort on critical paths and complex logic, not every possible edge case.

### 2. Tracer Bullet

Write ONE test that confirms ONE thing about the system:

```
RED:   Write test for first behavior → test fails
GREEN: Write minimal code to pass → test passes
```

This is your tracer bullet - proves the path works end-to-end.

### 3. Incremental Loop

For each remaining behavior:

```
RED:   Write next test → fails
GREEN: Minimal code to pass → passes
```

Rules:

- One test at a time
- Only enough code to pass current test
- Don't anticipate future tests
- Keep tests focused on observable behavior

### 4. Refactor the Implementation

After all tests pass, look for [refactor candidates](refactoring.md):

- [ ] Extract duplication
- [ ] Deepen modules (move complexity behind simple interfaces)
- [ ] Apply SOLID principles where natural
- [ ] Consider what new code reveals about existing code
- [ ] Run tests after each refactor step

**Never refactor while RED.** Get to GREEN first.

### 5. Refactor the Tests

Steps 2–4 build the tests one cycle at a time, so the test files accumulate what iterative development always leaves behind: the same fixture constructed five slightly different ways, a `fake_api_request`
copy-pasted per test, helpers that outlived the cycle that needed them. **Once all the requested
functionality is built, do one final pass over the test files themselves** — the production code is done and untouched from here.

Run the suite first; this pass starts and ends GREEN, and the tests keep asserting exactly what they asserted before. Nothing here is a behaviour change.

**Readability outranks DRY here.** A test is read on the day it fails, usually by someone who didn't write it — so a test that spells out its own input beats one that hides it three fixtures away. Extract setup that is genuinely identical and incidental to the behaviour; leave duplication that is the subject of the test.

**One behaviour, one test.** That is not a licence to keep every test the cycles produced. Collapse tests that differ only by input into `parametrize` cases — the inputs stay visible in the case list, so this costs no readability — and delete any test a later, broader one fully subsumes. Test code in this repo runs roughly 1:1 with production code by line count; every test kept is a line someone maintains, and a suite nobody can read is not coverage.

**Verify the tests still bite.** Consolidation can quietly neuter a test (a fixture that no longer supplies the edge-case value, a parametrize case asserting the shared default). Briefly break one line of the implementation each consolidated test covers and confirm it goes RED, then revert.

## Verification

Run after each RED→GREEN cycle and after each refactor step:

```bash
# Run affected tests (scope to the skill you changed)
.venv/bin/pytest skills/<skill>/scripts/tests/ -q

# Lint
.venv/bin/ruff check skills/<skill>/scripts/

# Format
.venv/bin/ruff format --check skills/<skill>/scripts/

# Type-check touched files
.venv/bin/pyright skills/<skill>/scripts/<file>.py
```

## Checklist Per Cycle

```
[ ] Test describes behavior, not implementation
[ ] Test uses public interface only
[ ] Test would survive internal refactor
[ ] Code is minimal for this test
[ ] No speculative features added
[ ] ruff check passes
[ ] ruff format --check passes
```
