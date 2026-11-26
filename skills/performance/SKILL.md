---
name: performance
description: Keep Claude Code's subprocesses from slowing the PC, and clean up processes left by closed chats. Use when the user asks about performance, CPU, RAM, a lighter or faster mode, or stray bash, node, python or git processes.
---

# frugal: performance

Modes live in `~/.frugal/performance.json`. A missing file means balanced. This works with no secondary accounts and with no Frugal Deck. Percentages are of the whole machine. `parallel` is how many shell commands may run at once; 0 means no limit. While a chat is over that count, or its process tree is over the RAM budget, the next Bash or PowerShell command waits (at most 10 minutes). Performance never waits.

- light: cpu 10%, ram 10%, gpu 0%, parallel 1, priority below normal
- balanced: cpu 40%, ram 40%, gpu 30%, parallel 3, priority normal
- performance: cpu 90%, ram 85%, gpu 90%, parallel 0, priority normal; always leave at least 1 logical core and 2 GB RAM free
- custom: honoured only when Frugal Deck is installed (`~/.frugal-deck/settings.json`). Otherwise it is treated as balanced.

Custom budgets and per-chat process views live in Frugal Deck. When env `FRUGAL_DECK` is set, Deck manages the chat's job; this plugin does not add a second one.

Run the matching command with Bash and show its output. `<base>` is this skill's base directory.

- Status: `python "<base>/../../accounts/server.py" perf status`
- Switch: `python "<base>/../../accounts/server.py" perf set light` (or `balanced` or `performance`)
- `perf set custom` is rejected here: custom is a Frugal Deck feature

Stragglers are processes left behind by a chat whose Claude is gone. Show the list first:

`python "<base>/../../accounts/server.py" perf reap`

Only after that list is shown, and only those processes: `python "<base>/../../accounts/server.py" perf reap --kill`

Suggest: run `python "<base>/../../accounts/server.py" perf profile`, then recommend one mode with one line of reasoning. If Frugal Deck is installed, also recommend custom numbers (cpu, ram, gpu, parallel). When secondary accounts exist, delegate that recommendation with the delegate tool.
