# Deep Modules

A **deep module** has a small interface and substantial implementation — few methods, simple params, complex logic hidden inside. Prefer these.

A **shallow module** has a large interface and thin implementation — many methods, complex params, logic that mostly passes through. Avoid these.

## Abstracting Complexity from the Caller

The goal of a deep module is to absorb complexity so the caller doesn't have to manage it. Every decision pushed onto the caller is complexity that leaked through the interface.

Signs complexity has leaked:
- The caller must call methods in a specific order
- The caller must handle internal error states (e.g. "not yet initialized")
- The caller must pass flags that control internal behavior
- The caller needs to know implementation details to use the interface correctly

Push that complexity inside the module instead:
- Enforce ordering internally via lazy initialization or state machines
- Translate internal errors into meaningful caller-facing exceptions
- Replace flags with well-named methods or separate types
- Make the interface correct for any valid input without requiring caller knowledge of internals

## Design checklist

When designing interfaces, ask:
- Can I reduce the number of methods?
- Can I simplify the parameters?
- Can I hide more complexity inside?
- Does the caller need to know anything about the implementation to use this correctly?
