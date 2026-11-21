<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/frugal-mark-amber.svg">
  <img src="docs/brand/frugal-mark.svg" alt="frugal logo" width="72">
</picture>

# frugal

[![Version](https://img.shields.io/badge/version-0.30.0-blue)](#versions)
[![License: MIT](https://img.shields.io/github/license/frugal-hq/frugal)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](#install)

A low-token working mode for Claude Code. Type `/frugal` and it stays on for the rest of the session.

Most of a session's bill is input: every call re-reads the system prompt, the history, and every file and tool result in context. frugal keeps that context small and makes fewer calls, and keeps replies short.

## Install

In Claude Code:

```
/plugin marketplace add frugal-hq/frugal
/plugin install frugal@frugal
```

Open a new session and type `/frugal`. Say `normal mode` to turn it off.

<details>
<summary>Let another agent install it (Codex, Cursor, Cowork, ...)</summary>

Paste this into the agent:

```text
Install the frugal plugin from https://github.com/frugal-hq/frugal for me.

1. If you are Claude Code: run `claude plugin marketplace add frugal-hq/frugal`, then `claude plugin install frugal@frugal`. Tell me to start a new session and type /frugal.
2. Otherwise, if you support Agent Skills (folders containing a SKILL.md): clone the repo to a temporary folder and copy every folder under skills/ into your user skills directory. Tell me how to invoke the frugal skill.
3. Otherwise: add the text of skills/frugal/SKILL.md without its front matter, then skills/frugal/core.md and skills/frugal/code.md, to your persistent instructions file (for example AGENTS.md) and tell me which file you changed.

Change nothing else. If the caveman or ponytail plugins are enabled, tell me, but do not disable them yourself.
```

Outside Claude Code only the rules install: no subagents, hooks, or secondary accounts.

</details>

frugal covers what caveman and ponytail do. If they are enabled, disable them: their rules are added to every call.

## What it does

- **Small, loaded per task.** `/frugal` loads a 3KB skill. Rules for longer tasks (`core.md`), code (`code.md`) and modules (analysis, delegation, reviews, session handoffs) load only when a task needs them, and a hook never pastes rules already in context. Force a module with `/frugal analysis` or `/frugal coding agents`.
- **Short replies, exact facts.** No filler, terms and numbers kept exact, APIs and versions verified instead of guessed. It builds the sensible default instead of stopping to ask.
- **Minimal code that keeps quality.** Reuse first, stdlib before new dependencies, root-cause fixes, one runnable check. It never cuts validation, security, accessibility, or error handling that prevents data loss.
- **Cheaper models for bounded work.** Searches go to a `haiku` subagent and bounded edits to `sonnet`, passed per call, so your settings do not change.
- **Session handoffs.** Past 150k tokens of context, and again every 50k, it ends at a natural break with a block to paste into a fresh chat.
- **Opt-in extras.** A humanizer for text people read, and a `commits` skill that follows your repo's commit convention.

## Secondary accounts

Other Claude logins, Codex, Grok, and OpenCode can take the bounded work, so the main account's limits last. Set them up with `/frugal:accounts`:

| Command | Needs |
|---|---|
| `add claude <id>` | Its own config in `~/.claude-<id>`, so it never touches the main login |
| `add codex <id>` | The Codex login on this machine |
| `add grok <id>` | `npm install -g @xai-official/grok` |
| `add opencode <id> <provider/model>` | `npm install -g opencode-ai`; free models need no login |

With an account set up, the `delegate` tool sends each task to the least-used account, in parallel for independent pieces. Heavy work goes to Claude, then Codex, then Grok, never to free OpenCode models. A failed try moves to another provider, a timed-out one moves up a level, and every try is logged in `~/.frugal/jobs.jsonl`. MCP servers reach only Claude accounts, and only the ones a task names. Needs `python` on the PATH; [Frugal Deck](https://github.com/frugal-hq/deck) shows the tasks of each chat.

## Results

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/charts/cost-0.18.4-dark.svg">
  <img src="docs/charts/cost-0.18.4-light.svg" alt="frugal 0.18.4 feature work cost with the other arm at 100: against no plugins, Haiku 4.5 93.4, Sonnet 5.5 86.9, Opus 5.5 61.8; against ponytail 5.2.0, Haiku 4.5 91.7, Sonnet 5.5 94.2, Opus 5.5 93.8" width="720">
</picture>

On ponytail's own harness (39 tasks, 3 runs each), frugal 0.18.4 passed every check on all three models. It cost 38% less than plain Claude Code on Opus and 5.8 to 8.3% less than ponytail 5.2.0 on every model. Plain Claude Code missed checks on Haiku in 10 of 117 runs.

frugal 0.29.0 (2026-11-20, 39 tasks, 2 runs each) cost 2.4% less than plain Claude Code on Haiku and 4.5% less on Sonnet, with half the lines; on Haiku it missed 2 checks in 78 runs against 4 for plain Claude Code. The gap narrowed since 0.18.4 mostly because plain Claude Code got cheaper (22% on Sonnet).

**Where it loses.** Short one-off tasks cost 7 to 10% more than no plugins (0.25.2, four tasks, n=5): loading frugal adds 650 to 1,450 input tokens per call, which a 3-call task cannot win back. Long sessions and heavy tasks pay it back.

Every number comes from headless Claude Code runs, checked automatically, with all arms in the same batch. Methods, per-task tables, and how to reproduce them are in [BENCHMARKS.md](BENCHMARKS.md).

## Versions

- **0.29.0**: A delegated `run` check gets 10, 20 or 30 minutes by level instead of a fixed 5. After a failed try another provider goes first, and a timeout moves the next try up to heavy. Jobs of a session that ends are logged as cancelled instead of staying running. Checks run with a UTF-8 locale (`grep -P` failed without one), and a stopped Docker Desktop is reported as a machine problem, not a failed test. A check that fails the same way after two accounts stops there, and a failed edit names the files it left in the tree. The hook pastes the core and session rules only when they are not already in context (the 10th message, or after a compaction), sends one line on later rounds, loads the code rules again after a compaction, and no longer takes words like `add` or `test` as code. The context notice repeats every 50k tokens past 150k. Code rules ask for quiet test and build output; session rules warn that switching model or MCP servers mid-chat breaks the prompt cache.
- **0.28.6**: Each failed try of a delegated job is logged in `jobs.jsonl` under `failures`, with its account, model, exit code, seconds and reason, including a check that failed. The reply still names only the last one; Frugal Deck shows them all.
- **0.28.5**: An `edit` delegate no longer hangs before its agent starts. On Windows, a command the server started inherited its stdin, the pipe Claude Code sends requests on, and stayed blocked until the next request arrived.
- **0.28.4**: A delegated job logs its process id in `jobs.jsonl` while it runs, and the end of its stderr when it times out or is cancelled, so a hung agent can be found and diagnosed.

<details>
<summary>Older versions</summary>

- **0.28.3**: The handoff notice past each 150k tokens of context is sent again on the next message when Claude Code dropped it, instead of waiting for the next 150k.
- **0.28.2**: A `run` delegate whose check fails is logged as failed, even when the agent explaining the failure says it is done, and that agent is told not to change any file. The tool description says a `run` executes only `check`, never the task. A Bash command started in the background is no longer sent to `delegate`, which would block on it and end it at its timeout.
- **0.28.1**: `delegate` logs the account and model as soon as one takes a task, and again when another takes over after a failed try, so Frugal Deck shows who runs each task while it runs. The selftest passes inside a Frugal Deck chat.
- **0.28.0**: New `commits` skill: when you ask to commit or commit and push, it follows the repo's convention first, otherwise asks once to opt into frugal's commit style (Conventional Commits, one short line, no trailers), one change per commit, version bump before docs before the rest, and a plan shown before each commit and push. In Frugal Deck the opt-in can be saved per workspace, group or folder. In a Frugal Deck chat, `delegate` hands a task the MCP servers that chat has (the account's own its allow and deny lists pass, plus the Deck's, read from `FRUGAL_DECK_MCP`), never passes that variable on to any agent, accepts a server named as its tools spell it (`claude_ai_Pylon`), and refuses servers the Deck turned off for delegates. A subagent brief is sent back twice before it runs, since nearly every first retry was work a delegate could do. An edit brief over 2,500 characters with no level runs on heavy, and an edit that times out returns the files it changed instead of starting over on another account. Claude delegates run this session's frugal through `--plugin-dir`, so a secondary account never runs an older install.
- **0.27.11**: Each delegate job in `~/.frugal/jobs.jsonl` logs its level and the tokens the agent reports, with cache reads, for Frugal Deck's tasks panel. Grok and OpenCode now answer in JSON so their tokens and cost can be read, and Codex's token count comes from its log. Before, only Claude jobs logged a cost and none logged tokens or level.
- **0.27.10**: A session started from a handoff reads the `Read first:` files in its first batch of tool calls, before replying or asking anything. Before, a new session could reply without having read them.
- **0.27.9**: `delegate` sends MCP progress while it runs, and a cancelled call kills its agents instead of retrying on other accounts. Each call runs in its own thread, so pings, cancels and parallel calls no longer wait for the one in progress.
- **0.27.8**: Heavy and max delegate work goes to Claude secondaries first, then Codex, then Grok; free OpenCode models now take only light and standard work. Before, the least used account took it, so a heavy edit could land on a small free model.
- **0.27.7**: `delegate` takes the brief as `task`, `brief` or `prompt`, and a `run` needs only its `check`: each rejected call re-read the whole context. The heavy-command note no longer asks for "a brief". Edits to the session's own memory, plans, settings and scratchpad no longer count toward the edit budget.
- **0.27.6**: The fourth edit in a prompt that has not delegated goes to `delegate` once, as kind `edit`, with a brief, a check and a diff review here; calling again goes through. Across 31,524 past prompts, 1,340 made more than three edits and only 51 of them delegated.
- **0.27.5**: The heavy-command hook no longer sends reads to delegate: quoted text (a commit message, a `node -e` script), Gradle file and folder names (`gradle.properties`, `build.gradle.kts`, `./gradle`) and lookups (`which tsc`) no longer count as runs. Replayed on 4,874 real commands it cut 29 false positives and lost no real run. A piped check notes that its exit code is the last stage's.
- **0.27.4**: MCP tools are listed with a `frugal` user agent, so a server behind Cloudflare's browser check (error 1010) no longer refuses the listing and blocks every delegate that names it.
- **0.27.3**: `delegate` without `cwd` runs in the folder its check starts with `cd` into, and refuses to run under `C:\Windows`, where an elevated session starts and no secondary account can write. A server whose tools cannot be listed is asked twice, then its last listed tools are used, so a delegate stays read-only while the server connects.
- **0.27.2**: each delegated brief adds a line to `~/.frugal/jobs.jsonl` when it starts and when it ends, with the session, account, model, cost (Claude accounts) and outcome: done, failed or not delegated. Frugal Deck shows them in the chat's details panel.
- **0.27.1**: with secondary accounts, each subagent brief goes to `delegate` once, so every subagent of a parallel batch gets the same note; before, only the first of the session did and the rest ran on the main account. A second call with the same brief goes through.
- **0.27.0**: without secondary accounts, every bounded task of three or more tool calls goes to a subagent on the cheapest model that fits (`haiku` with lower effort for search and reading, `sonnet` for edits; `frugal-worker` defaults to `sonnet`). With them, a sixth read or search in one prompt still runs, with a note to hand the rest to `delegate`, instead of being blocked and repeated. Delegates get this session's MCP login as a header and only the tools a server marks read-only: no account logs in twice, and a delegate can read a ticket but never answer one.
- **0.26.1**: the handoff block has a `Read first:` line with up to 3 paths, and a `Project:` line that keeps everything important about the project, folder, client or site the chat is about, even past the word limit. Project instructions (CLAUDE.md, AGENTS.md, conventions the user set) win over frugal's defaults and travel into handoffs, delegated briefs (a new `Rules:` line) and subagent prompts.
- **0.26.0**: Grok and OpenCode secondary accounts; `claude_only_mcp` keeps chosen MCP servers with Claude; heads-up notes never go to Grok or OpenCode; OpenCode models that may train are blocked unless `opencode-training` is on; add and login verify the signed-in email before an account joins the pool; OpenCode runs in the requested folder.
- **0.25.3**: delegate's `check` runs in bash (Git Bash on Windows), not cmd.exe; with kind `run` it runs once, before any agent, and the reply is its real exit code and output, with an agent explaining only a failure; a check that cannot run (missing tool, Docker permission, timeout) answers "Do it here"; edit agents get the check in their brief. The hook no longer sends `ssh` commands or mentions of `bench.py` to delegate, and says how to get out of it.
- **0.25.2**: delegated commands no longer inherit the MCP server's stdin; parallel sessions lock frugal's files before writing them; session state older than a week is removed.
- **0.25.1**: heads-up checks use the light level and stop after 8 per session, so they do not spend the account delegate needs.
- **0.25.0**: heads-up notes in Claude Desktop from a side agent on a secondary account, with `/frugal:heads-up` to turn them off.
- **0.24.1**: a delegated run or check that times out kills its whole process tree, so it no longer hangs on Windows.
- **0.24.0**: the handoff comes from context size (every 150k tokens) instead of message count; a pasted handoff loads frugal; the session hook reads the whole prompt and survives a plugin reinstall.
- **0.23.0**: delegated Claude runs load frugal, and Codex runs get its core rules.
- **0.22.2**: explorations hand off after three reads, so the main session can read what an edit needs.
- **0.22.1**: a logged-out secondary account leaves the pool, and every prompt names the command to sign it in again.
- **0.22.0**: with secondary accounts, every bounded task goes to them; delegated work defaults to the standard level; explorations hand off after two reads; delegated Claude edits can run commands; `delegate` takes a working directory.
- **0.21.0 to 0.21.1**: secondary Claude and Codex accounts take delegated work through the plugin's own MCP server, hooks, and `/frugal:accounts`; the frugal-mod commands left the plugin.
- **0.20.0**: commands for the experimental frugal-mod (removed in 0.21.1).
- **0.18.5**: `frugal-worker` runs on the session model unless a cheaper one is passed; on Haiku it had escalated to Sonnet in 2 of 117 runs.
- **0.18.4**: unrequested features, commands, options, and limits stay out of new code (an extra worth having goes in a `frugal:` comment); trust-boundary validation rejects bad input, with size or rate limits only when asked; code goes in the working directory, never the scratchpad. On 25 tasks (n=3) it wrote 8% fewer lines on Sonnet and cost 2.8% less on Sonnet and 4.4% less on Haiku than 0.18.3, with every safety check passed.
- **0.18.3**: code goes into files through the edit tools, not only into the reply, and a file is reported written only after the call that wrote it. On Haiku, `reuse-slug` wrote the file in 6 of 6 runs against 2 of 3 for 0.18.2. On Sonnet (39 tasks) it cost 3.4% less than ponytail with every check passed.
- **0.18.2**: a bug-fix guard goes in the shared helper, not the function the report names, and a sibling with the same bug is fixed, never only mentioned. On Haiku, `trace-transfer` passed 14 of 18 runs against 9 of 18 for 0.17.0.
- **0.18.1**: the ceiling rule covers new code only, not bug fixes; the error rule covers CLI scripts only (`let file and argument errors raise`), after 0.18.0 left a `null` body unguarded in one Haiku run of `todo-null`.
- **0.18.0**: a known ceiling (naive heuristic, missing option, flag, format, or syntax) ships as the simple version with a `frugal:` comment naming the upgrade; CLI input stays on `sys.argv` until a flag is requested; no try/except that only rewords a traceback. On ponytail's harness (15 open-ended tasks, n=3) it wrote 17% fewer lines on Sonnet and Opus and 7% fewer on Haiku, with safety unchanged.
- **0.17.0**: requested code goes in a file in the working directory; a check is never run by a subagent; a new script with no language named is Python with the stdlib. On ponytail's harness (Haiku 4.5, 39 tasks) it passed every correctness check and cost 13% less than ponytail.
- **0.16.0**: plain questions go straight to an answer, which cut Opus's output on caveman's eval by a third; `t0_ok` measures the fixed cost.
- **0.15.0**: suggests the model and effort that fit each task (a subagent for bounded work, a prompt for a new chat otherwise); Fable in the delegation table; `review` and `compress` modules; usage totals in the CLI; code rules for the worker subagent and for every code task on Haiku and Sonnet; native UI elements named; root-cause rule in the core; the session hook ignores background-task notifications.
- **0.14.0**: a hook loads the session rules at the 10th message; frugal builds the default instead of stopping to ask. Benchmarked 6% cheaper than plain Claude Code on Opus 5.5 with 17% less output and the same pass rate.
- **0.13.0**: code rules in `code.md`, read with `core.md` only for code tasks.
- **0.12.0**: one direct read instruction first in the core (Haiku follows it); always-on code essentials; delegated parts verified by risk and escalated only on failure.
- **0.11.0, 0.11.1**: small always-on core; `core.md` and modules only for tasks over 3 tool calls or from the 10th message.
- **0.10.0**: delegation only for large inputs, never redone after delegating.
- **0.9.0**: always-on AI-tell rule; humanizer patterns 26 to 30 and a scoring step.
- **0.8.0, 0.8.1**: `tight` removed; modules read by path instead of listed as skills.
- **0.7.0 to 0.7.2**: first plugin build: per-task modules, delegation to haiku and sonnet subagents, session handoff, experimental `tight` profile.
- **0.3.0 to 0.6.0**: modular skills, standalone (no caveman or ponytail needed), opt-in humanizer.
- **0.2.0**: light and heavy work triaged per message; coding and analysis profiles dropped.
- **0.1.0, 0.1.1**: single skill on top of caveman and ponytail at `ultra`, with task profiles; the most expensive setup in the benchmarks.

</details>

## Licenses

frugal is MIT (see `LICENSE`). `humanizer` keeps its own MIT `LICENSE` file in its folder. `bench/ponytail_agentic` is ponytail's benchmark, MIT (see `LICENSE.ponytail`).
