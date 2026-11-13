---
name: accounts
description: Set up secondary Claude, Codex, Grok or OpenCode accounts that take frugal's delegated work, so the main account's limits last. Use when the user asks to add, list, remove or log in a secondary account, or asks why delegation is off.
---

# frugal: secondary accounts

Accounts live in `~/.frugal/accounts.json`. While it lists at least one account, frugal exposes the `delegate` tool, adds the delegation rule to each prompt, and sends test runs, long explorations, long edit runs and subagents to those accounts. With none, nothing changes.

Run the matching command with Bash and show its output; `<base>` is this skill's base directory.

Add and log in open a terminal that closes itself when the login ends, then check the login and print the result with the signed-in email when known. They wait for the user, so run them with Bash `run_in_background`; when it finishes, show the result and tell the user if the email is not the one they meant. An account stays out of the pool until its login checks out.

- List: `python "<base>/../../accounts/server.py" list`
- Add a Claude login: `python "<base>/../../accounts/server.py" add claude <id>` (its config lives in `~/.claude-<id>`; a login terminal opens)
- Add Codex: `python "<base>/../../accounts/server.py" add codex <id>` (uses the Codex login on this machine)
- Add Grok: `python "<base>/../../accounts/server.py" add grok <id>` (uses the Grok Build login on this machine; needs `npm install -g @xai-official/grok`). Grok never gets heads-up notes, which carry the session transcript.
- Add OpenCode: `python "<base>/../../accounts/server.py" add opencode <id> <provider/model>` (needs `npm install -g opencode-ai`; its login and sessions live in `~/.frugal/opencode-<id>`; list models with `opencode models`). It never gets heads-up notes either. Free models need no login. Add one account per model; several are fine.
- OpenCode models that may train on what they receive (Big Pickle, MiMo, Flint, Iceman, Inkling, Nemotron) are blocked by default. Allow them: `python "<base>/../../accounts/server.py" opencode-training on`; block again with `off`. Only the user decides this: ask before turning it on.
- Log in again: `python "<base>/../../accounts/server.py" login <id>`
- Remove: `python "<base>/../../accounts/server.py" remove <id>`

After the first account is added, the `delegate` tool appears in the next session (or after `/mcp` reconnects the frugal server).

MCP servers only Claude may use: list their names under `"claude_only_mcp"` in `~/.frugal/accounts.json`. Codex runs get those servers disabled and lose any environment variable carrying the server's name (its token); Grok runs never load Claude's MCP servers or hooks. OpenCode does not read Claude's MCP servers. A task that needs one of the session's MCP servers names it in delegate's `mcp`: it then runs only on a Claude account, which gets just that server.
