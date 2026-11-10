---
name: heads-up
description: Turn frugal's heads-up notes on or off. In Claude Desktop, after a turn with 6 or more tool calls, a side agent on a secondary account reads the session and flags one thing worth knowing.
argument-hint: off | off all | on | on all
disable-model-invocation: true
---

frugal's prompt hook has already applied `/frugal:heads-up $ARGUMENTS`; tell the user in one line what it reported.

If it reported nothing, heads-up needs a secondary account: say so and point to `/frugal:accounts`.

- `off` / `on`: this session only.
- `off all` / `on all`: every session.
