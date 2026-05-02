```
  ___________   .__.__  .__    ________          __  .__        .__
 /   _____/  | _|__|  | |  |   \_____  \ _______/  |_|__| _____ |__|_______ ___________
 \_____  \|  |/ /  |  | |  |    /   |   \\____ \   __\  |/     \|  \___   // __ \_  __ \
 /        \    <|  |  |_|  |__ /    |    \  |_> >  | |  |  Y Y  \  |/    /\  ___/|  | \/
/_______  /__|_ \__|____/____/ \_______  /   __/|__| |__|__|_|  /__/_____ \\___  >__|
        \/     \/                      \/|__|                 \/         \/    \/
```

This is a skill optimizer intended for use with Claude skills, but since Anthropic use the [agentskills.io/](https://agentskills.io/) standard, it will likely be useful for any system supporting skills.

## What does it do?

- Validates your skill against the above specification and fixes it if it doesn't comply
- Rewrites the `description` field so the skill actually activates — imperative phrasing, intent-focused, with concrete trigger contexts
- Diagnoses why a skill isn't triggering and applies the fix
- Applies content patterns: adds a Gotchas section, converts declarations into procedures, picks defaults instead of offering menus, matches prescriptiveness to how fragile the task is
- Optimizes context via progressive disclosure, splitting detail out into `references/` files with explicit load triggers
- Looks for opportunities to introduce helper scripts; this gives your skills tools that operate deterministically and return data in a format agents can use. Any scripts created/reviewed will recieve some _basic_ prompt injection defences
- Flags anti-patterns (generic filler, mega-skills, unsanitized script echoes, references with no load trigger, etc.)
- Optional trigger eval — runs your skill against a set of realistic prompts to verify it activates when it should

## Installing

This repo is a Claude Code plugin marketplace. From inside Claude Code, add the marketplace and install the plugin:

```
/plugin marketplace add rafaelh/skill-optimizer
/plugin install skill-optimizer@rafaelh
```

Once installed, the `skill-optimizer` skill activates automatically when you ask Claude to create, audit, or fix a skill (e.g. "audit my SKILL.md", "why isn't this skill activating?").
