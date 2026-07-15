# Good and Bad Tests

## Good Tests

**Integration-style**: Test through real interfaces, not mocks of internal parts.

```python
# GOOD: Tests observable behavior through the public command interface
def test_player_can_pick_up_item(engine, setup_player_in_location):
    setup_player_in_location(engine, "town_square")
    engine.world.get_location("town_square").items.append(sword)

    engine.process_command("take sword")

    assert any(i.name == "sword" for i in engine.player.inventory)
```

Characteristics:

- Tests behavior users/callers care about
- Uses public API only (`engine.process_command`, `engine.player.inventory`)
- Survives internal refactors
- Describes WHAT, not HOW
- One logical assertion per test

## Bad Tests

**Implementation-detail tests**: Coupled to internal structure.

```python
# BAD: Tests that a specific internal method was called
def test_pick_up_calls_internal_method(engine, monkeypatch):
    called = []
    monkeypatch.setattr(engine, "_add_to_inventory", lambda item: called.append(item))

    engine.process_command("take sword")

    assert len(called) == 1  # Breaks on any rename or refactor of _add_to_inventory
```

Red flags:

- Mocking internal collaborators (engine services, module helpers)
- Patching private methods or attributes
- Asserting on call counts/order rather than outcomes
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Reaching into engine internals to verify instead of using the public interface

```python
# BAD: Bypasses the module interface to verify quest state
def test_quest_stage_advances(engine):
    engine.process_command("kill goblin")
    # Reaches into private quest log internals
    assert engine._quest_module._active_quests["goblin_hunt"]._stage_index == 1

# GOOD: Verifies through the public query interface
def test_completing_objective_advances_quest_stage(engine, setup_player_in_location):
    setup_player_in_location(engine, "forest")
    engine.process_command("kill goblin")

    state = engine.get_game_state()
    assert state.active_quest_stage("goblin_hunt") == "return_to_innkeeper"
```
