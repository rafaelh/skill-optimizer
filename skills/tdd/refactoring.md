# Refactor Candidates

Only refactor when tests are green. Scope refactoring to code touched in the current cycle — don't fix unrelated code mid-cycle.

After a TDD cycle, look for:

- **Duplication** → Extract function/class
- **Long methods** → Break into private helpers. Don't write new tests for helpers — let existing tests cover them through the public interface.
- **Shallow modules** → Combine thin modules that just pass through, or deepen them by moving caller-side logic inside.
- **Feature envy** → A function that repeatedly accesses another object's data probably belongs on that object. Move the logic to where the data lives.
- **Primitive obsession** → Repeated use of raw primitives (strings, ints) to represent a concept signals a missing value object. Introduce a class to carry validation and behavior.
- **Existing code** the new code reveals as problematic
