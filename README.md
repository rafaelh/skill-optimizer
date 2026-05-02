# skill-optimizer

This is a skill optimizer intended for use with Claude skills, but since Anthropic use the [agentskills.io/](https://agentskills.io/) standard, it will likely be useful for any system supporting skills.

## What does it do?

- Validates your skill against the above specification and fixes it if it doesn't comply
- Optimizes context, splitting out content into `context.md` files
- Looks for opportunities to introduce helper scripts; this gives your skills tools that operate deterministically and return data in a format agents can use. They also bake in some _basic_ prompt injection defences

## Installing

This repo is a Claude Code plugin marketplace. From inside Claude Code, add the marketplace and install the plugin:

```
/plugin marketplace add rafaelh/skill-optimizer
/plugin install skill-optimizer@rafaelh
```

Once installed, the `skill-optimizer` skill activates automatically when you ask Claude to create, audit, or fix a skill (e.g. "audit my SKILL.md", "why isn't this skill activating?").
